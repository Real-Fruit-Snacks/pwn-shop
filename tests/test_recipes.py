"""Checks on the committed recipes that need no network and no build."""

import hashlib
import re
import shutil
from pathlib import Path

import pytest
import tomli_w

import build

RECIPES = Path(__file__).resolve().parent.parent / "recipes"
FOLDERS = sorted(p for p in RECIPES.iterdir() if p.is_dir())
PLACEHOLDERS = {"ip", "hostname", "domain", "lhost"}


def test_there_are_recipes():
    assert FOLDERS


@pytest.fixture(params=FOLDERS, ids=lambda p: p.name)
def loaded(request):
    return build.load_recipe(request.param)


def test_recipe_has_a_licence(loaded):
    text = (loaded.folder / "LICENSE").read_bytes()
    assert text.strip()
    assert b"\r" not in text


def test_manifest_passes_the_manifest_rules(loaded):
    # the builder fills the [python] table when it stages a package
    manifest, _ = build._python_manifest(loaded)
    build._check_manifest(manifest)


# The SHA-256 of manifest.toml inside each published schema 1 build that is still rebuilt. The
# staged manifest must not change, or the rebuild would differ from what was published.
PUBLISHED_MANIFESTS = {
    "kerbrute": "6763e79cc260e80783473c03daafb09cdcd139cac0a6d92e95bad6be79ba1332",
    "pspy": "fc25f8f08fd3a101149754a567e3466fa3c0d4a3400c9022674e5af12b10d30b",
    "ligolo-ng": "5f9f01035f0db052735f8cefe7efcdfa8e797b0e2393af69efb9a8299a9d68b7",
    "peass-ng": "e5623d5035ef01de19d2321e8c1c52ccbb50b2c11444e75136dbf2705a9b3e58",
}


@pytest.mark.parametrize("name", sorted(PUBLISHED_MANIFESTS))
def test_a_schema_1_recipe_stages_its_published_manifest(name):
    recipe = build.load_recipe(RECIPES / name)
    manifest, is_python = build._python_manifest(recipe)
    assert not is_python and manifest["schema"] == 1
    staged = tomli_w.dumps(manifest).encode("utf-8")
    assert hashlib.sha256(staged).hexdigest() == PUBLISHED_MANIFESTS[name]


def test_sources_are_pinned(loaded):
    sources = [*loaded.sources, *(wheel.source for wheel in loaded.wheel_sources)]
    if loaded.dotnet:
        sources += [loaded.dotnet.source, loaded.dotnet.reference]
    for source in sources:
        assert source.url.startswith("https://")
        assert re.fullmatch(r"[0-9a-f]{64}", source.sha256)
        assert source.size > 0


def test_python_tools_carry_their_lock(loaded):
    if "python" in loaded.manifest:
        assert (loaded.folder / build.REQUIREMENTS_IN).is_file()
        pins = build.read_requirements_lock(loaded.folder / build.REQUIREMENTS_LOCK, loaded.name)
        assert pins
        assert all(n in {build._norm_name(p.name) for p in pins} for n in loaded.build_from_source)


def test_manifest_text_for_the_user(loaded):
    m = loaded.manifest
    assert not re.search(r"[!\u2600-\U0001faff]", m["description"])
    assert m["description"][0].isupper() and not m["description"].endswith(".")
    if m["kind"] == "runtime":
        # a runtime is never listed, searched or run, so it has no tags and no examples
        assert "tags" not in m and "examples" not in m
        return
    if m["kind"] == "notes":
        # notes are listed and searched, but nothing is run at the prompt: :Pb is the note's
        assert m["tags"] and all(re.fullmatch(r"[a-z0-9]+", t) for t in m["tags"])
        assert "examples" not in m
        return
    assert m["tags"] and all(re.fullmatch(r"[a-z0-9]+", t) for t in m["tags"])
    assert 2 <= len(m["examples"]) <= 3
    for example in m["examples"]:
        assert set(re.findall(r"\{(\w+)\}", example)) <= PLACEHOLDERS


def test_no_two_recipes_share_a_command():
    seen: dict[str, str] = {}
    for folder in FOLDERS:
        recipe = build.load_recipe(folder)
        for command in recipe.manifest.get("command", []):
            assert command["name"] not in seen, f"{recipe.name} and {seen[command['name']]}"
            seen[command["name"]] = recipe.name


# ---- file names and lock lines

_HASH = r"--hash=sha256:[0-9a-f]{64}"
_LOCK_LINE = re.compile(rf"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9][A-Za-z0-9.!+_-]*(?: {_HASH})+")


def unsourced_files(recipe) -> set[str]:
    """Files the manifest names that no source of the recipe puts in the package."""
    manifest, _ = build._python_manifest(recipe)
    named, _wheels, _lock = build._check_manifest(manifest)
    sourced = {s.to for s in recipe.sources}
    if recipe.dotnet:
        sourced.add(recipe.dotnet.source.to)
    return {
        name for name in named
        if name not in sourced
        and not any(s.unpack and name.startswith(s.to + "/") for s in recipe.sources)
    }


def lock_problems(lock: Path, build_from_source: list[str]) -> list[str]:
    problems = []
    logical, current = [], ""
    for raw in lock.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            current += line[:-1].strip() + " "
            continue
        logical.append((current + line).strip())
        current = ""
    if current.strip():
        logical.append(current.strip())
    pinned = set()
    for line in logical:
        if line.startswith("-"):
            problems.append(f"option line: {line[:40]}")
        elif ";" in line:
            problems.append(f"environment marker: {line[:40]}")
        elif not _LOCK_LINE.fullmatch(line):
            problems.append(f"not name==version with sha256 hashes: {line[:40]}")
        else:
            pinned.add(build._norm_name(line.split("==")[0]))
    if not pinned:
        problems.append("pins nothing")
    problems += [f"build_from_source {n} is not pinned" for n in build_from_source if n not in pinned]
    return problems


def test_manifest_files_come_from_sources(loaded):
    assert unsourced_files(loaded) == set()


def test_a_notes_root_comes_from_an_unpacked_source(loaded):
    if loaded.manifest["kind"] != "notes":
        return
    root = loaded.manifest["notes"]["root"]
    assert any(
        s.unpack and (root == s.to or root.startswith(s.to + "/")) for s in loaded.sources
    ), f"{root} is not in a folder an unpacked source fills"


def test_lock_lines_are_plain_hashed_pins(loaded):
    lock = loaded.folder / build.REQUIREMENTS_LOCK
    if "python" in loaded.manifest or lock.exists():
        assert lock_problems(lock, loaded.build_from_source) == []


def copy_recipe(name: str, dest: Path) -> Path:
    """Copies a recipe, and the runtime recipes it requires, under dest."""
    shutil.copytree(RECIPES / name, dest / name)
    for runtime in build.load_recipe(RECIPES / name).requires:
        if not (dest / runtime).exists():
            shutil.copytree(RECIPES / runtime, dest / runtime)
    return dest / name


def test_unsourced_command_path_is_caught(tmp_path):
    folder = copy_recipe("kerbrute", tmp_path)
    manifest = folder / "manifest.toml"
    manifest.write_text(
        manifest.read_text().replace("files/kerbrute_linux_amd64", "files/kerbrute"), encoding="utf-8"
    )
    assert unsourced_files(build.load_recipe(folder)) == {"files/kerbrute"}


def test_unsourced_upload_path_is_caught(tmp_path):
    folder = copy_recipe("pspy", tmp_path)
    manifest = folder / "manifest.toml"
    manifest.write_text(manifest.read_text().replace('"files/pspy32s"', '"files/other"'), encoding="utf-8")
    assert unsourced_files(build.load_recipe(folder)) == {"files/other"}


def test_unsourced_script_is_caught(tmp_path):
    folder = copy_recipe("targetedkerberoast", tmp_path)
    manifest = folder / "manifest.toml"
    manifest.write_text(
        manifest.read_text().replace('script = "files/targetedKerberoast.py"', 'script = "files/x.py"'),
        encoding="utf-8",
    )
    assert unsourced_files(build.load_recipe(folder)) == {"files/x.py"}


def broken_lock(tmp_path, edit):
    folder = copy_recipe("certipy", tmp_path)
    lock = folder / build.REQUIREMENTS_LOCK
    lines = lock.read_text(encoding="utf-8").splitlines()
    lock.write_text("\n".join(edit(lines)) + "\n", encoding="utf-8")
    return lock


def test_lock_marker_is_caught(tmp_path):
    lock = broken_lock(tmp_path, lambda ls: [ls[0].replace("\\", "; sys_platform == 'win32' \\", 1), *ls[1:]])
    assert any("environment marker" in p for p in lock_problems(lock, []))


def test_lock_pin_without_hash_is_caught(tmp_path):
    lock = broken_lock(tmp_path, lambda ls: [*ls, "extra==1.0"])
    assert any("sha256" in p for p in lock_problems(lock, []))


def test_lock_option_line_is_caught(tmp_path):
    lock = broken_lock(tmp_path, lambda ls: ["--index-url https://example.invalid/simple", *ls])
    assert any("option line" in p for p in lock_problems(lock, []))


def test_lock_unpinned_source_build_is_caught(tmp_path):
    lock = broken_lock(tmp_path, lambda ls: ls)
    assert lock_problems(lock, ["nothere"]) == ["build_from_source nothere is not pinned"]
