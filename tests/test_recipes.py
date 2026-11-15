"""Checks on the committed recipes that need no network and no build."""

import re
from pathlib import Path

import pytest

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


def test_sources_are_pinned(loaded):
    for source in loaded.sources:
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
