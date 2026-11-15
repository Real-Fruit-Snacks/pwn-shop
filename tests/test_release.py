"""The parts of build.py that CI's publish steps use: plan, compare and notes."""

import tomllib

import pytest
import tomli_w

import build
from conftest import PLAIN_MANIFEST, PY_MANIFEST, make_python_recipe, make_recipe, make_runtime_recipe


def card_of(**changes) -> dict:
    base = {
        "name": "kerbrute",
        "version": "1.0.3",
        "build": 1,
        "kind": "kali",
        "description": "Kerberos user enumeration",
        "tags": ["kerberos"],
        "upstream": "https://github.com/ropnop/kerbrute",
        "licence": "Apache-2.0",
        "arch": "x86_64",
        "needs_root": False,
        "commands": ["kerbrute"],
        "file": "kerbrute-1.0.3-1.zip",
        "size": 100,
        "sha256": "a" * 64,
    }
    return {**base, **changes}


def write_card(path, card):
    path.write_bytes(tomli_w.dumps(card).encode())
    return path


# ---- card_changes


def test_identical_cards_have_no_changes():
    assert build.card_changes(card_of(), card_of()) == []


def test_changed_fields_are_named_in_order():
    changed = build.card_changes(card_of(), card_of(licence="MIT", description="x", size=5))
    assert changed == ["description", "licence", "size"]


def test_a_field_missing_on_one_side_counts():
    published = card_of()
    del published["arch"]
    assert build.card_changes(card_of(), published) == ["arch"]


def test_ignored_fields_are_left_out():
    changed = build.card_changes(card_of(), card_of(size=5, sha256="b" * 64), ignore=("size", "sha256"))
    assert changed == []


# ---- compare

def recipe_for(tmp_path, net, *, python=False):
    if python:
        folder = make_python_recipe(tmp_path / "pytool", net, [])
        return build.load_recipe(folder)
    return build.load_recipe(make_recipe(tmp_path / "kerbrute", net))


def test_compare_passes_when_the_cards_match(tmp_path, net):
    recipe = recipe_for(tmp_path, net)
    one = write_card(tmp_path / "a.toml", card_of())
    two = write_card(tmp_path / "b.toml", card_of())
    build.compare_cards(one, two, recipe)


def test_compare_fails_on_a_changed_hash_without_wheels(tmp_path, net):
    recipe = recipe_for(tmp_path, net)
    one = write_card(tmp_path / "a.toml", card_of(sha256="c" * 64))
    two = write_card(tmp_path / "b.toml", card_of())
    with pytest.raises(build.BuildError) as err:
        build.compare_cards(one, two, recipe)
    text = str(err.value)
    assert "sha256" in text
    assert "raise build in recipes/kerbrute/recipe.toml" in text
    assert "1.0.3-1" in text


def test_compare_fails_on_a_changed_hash_with_wheels(tmp_path, net):
    recipe = recipe_for(tmp_path, net, python=True)
    one = write_card(tmp_path / "a.toml", card_of(size=1, sha256="c" * 64))
    two = write_card(tmp_path / "b.toml", card_of())
    with pytest.raises(build.BuildError) as err:
        build.compare_cards(one, two, recipe)
    assert "sha256" in str(err.value) and "size" in str(err.value)


def test_compare_ignores_size_and_hash_when_a_wheel_is_built_from_source(tmp_path, net):
    recipe = build.load_recipe(
        make_python_recipe(tmp_path / "pytool", net, [], build_from_source=("beta",))
    )
    one = write_card(tmp_path / "a.toml", card_of(size=1, sha256="c" * 64))
    two = write_card(tmp_path / "b.toml", card_of())
    build.compare_cards(one, two, recipe, pkg(tmp_path / "1.zip"), pkg(tmp_path / "2.zip"))


def test_compare_with_build_from_source_still_fails_on_other_fields(tmp_path, net):
    recipe = build.load_recipe(
        make_python_recipe(tmp_path / "pytool", net, [], build_from_source=("beta",))
    )
    one = write_card(tmp_path / "a.toml", card_of(size=1, description="new words"))
    two = write_card(tmp_path / "b.toml", card_of())
    with pytest.raises(build.BuildError) as err:
        build.compare_cards(one, two, recipe, pkg(tmp_path / "1.zip"), pkg(tmp_path / "2.zip"))
    assert "description" in str(err.value)
    assert "size" not in str(err.value)


def test_compare_command_names_the_recipe_and_exits_1(tmp_path, net, capsys):
    make_recipe(tmp_path / "recipes" / "kerbrute", net)
    one = write_card(tmp_path / "a.toml", card_of(licence="MIT"))
    two = write_card(tmp_path / "b.toml", card_of())
    code = build.main(["compare", str(one), str(two), str(one), str(two), "--recipes", str(tmp_path / "recipes")])
    assert code == 1
    assert "licence" in capsys.readouterr().err


def test_compare_command_succeeds_on_a_match(tmp_path, net, capsys):
    make_recipe(tmp_path / "recipes" / "kerbrute", net)
    one = write_card(tmp_path / "a.toml", card_of())
    two = write_card(tmp_path / "b.toml", card_of())
    assert build.main(["compare", str(one), str(two), str(one), str(two), "--recipes", str(tmp_path / "recipes")]) == 0
    assert capsys.readouterr().out.startswith("ok kerbrute 1.0.3-1")


def test_compare_command_refuses_a_card_with_no_recipe(tmp_path, net, capsys):
    (tmp_path / "recipes").mkdir()
    one = write_card(tmp_path / "a.toml", card_of())
    two = write_card(tmp_path / "b.toml", card_of())
    assert build.main(["compare", str(one), str(two), str(one), str(two), "--recipes", str(tmp_path / "recipes")]) == 1
    assert "kerbrute" in capsys.readouterr().err


# ---- plan


def test_plan_lists_name_tag_and_zip(tmp_path, net, capsys):
    make_recipe(tmp_path / "recipes" / "kerbrute", net)
    other = dict(PLAIN_MANIFEST, command=[{"name": "other", "type": "binary", "path": "files/kerbrute"}])
    make_recipe(tmp_path / "recipes" / "other", net, version="2.0", build_no=3, manifest=other)
    assert build.main(["plan", "--recipes", str(tmp_path / "recipes")]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "kerbrute kerbrute-v1.0.3-1 kerbrute-1.0.3-1.zip",
        "other other-v2.0-3 other-2.0-3.zip",
    ]


def test_plan_with_no_recipes_is_an_error(tmp_path, capsys):
    (tmp_path / "recipes").mkdir()
    assert build.main(["plan", "--recipes", str(tmp_path / "recipes")]) == 1
    assert "no recipes" in capsys.readouterr().err


# ---- notes


def test_notes_come_from_the_card_and_are_plain():
    text = build.release_notes(card_of(sha256="d" * 64, size=8286607))
    lines = text.splitlines()
    assert lines[0] == "kerbrute 1.0.3, build 1"
    assert "Kerberos user enumeration" in text
    assert "Upstream: https://github.com/ropnop/kerbrute" in text
    assert "Licence: Apache-2.0" in text
    assert f"SHA-256: {'d' * 64}" in text
    assert "kerbrute-1.0.3-1.zip" in text
    assert "!" not in text
    assert text.endswith("\n")
    assert "\r" not in text


def test_notes_command_reads_a_card(tmp_path, capsys):
    card = write_card(tmp_path / "kerbrute-1.0.3-1.toml", card_of())
    assert build.main(["notes", str(card)]) == 0
    assert capsys.readouterr().out.startswith("kerbrute 1.0.3, build 1\n")


def test_notes_command_refuses_a_file_that_is_not_a_card(tmp_path, capsys):
    path = tmp_path / "x.toml"
    path.write_text("name = 'x'\n")
    assert build.main(["notes", str(path)]) == 1
    assert "not a card" in capsys.readouterr().err


def test_a_real_card_round_trips_into_notes(tmp_path, net):
    recipe = build.load_recipe(make_recipe(tmp_path / "kerbrute", net))
    zip_path = build.build_one(recipe, tmp_path / "dist", tmp_path / "cache", fetch_fn=net)
    card = tomllib.loads(zip_path.with_suffix(".toml").read_text())
    assert build.card_changes(card, card) == []
    assert card["sha256"] in build.release_notes(card)


def test_python_manifest_helper_marks_wheel_packages(tmp_path, net):
    assert "python" in PY_MANIFEST
    assert build.has_bundled_wheels(recipe_for(tmp_path, net, python=True))
    assert not build.has_bundled_wheels(recipe_for(tmp_path / "x", net))


# ---- the contents of two zips with wheels


LOCK = "alpha==1.0 --hash=sha256:" + "a" * 64 + "\nbeta==2.0 --hash=sha256:" + "b" * 64 + "\n"
MEMBERS = {
    "LICENSE": "licence",
    "files/tool.py": "print('hi')",
    "wheels/alpha-1.0-py3-none-any.whl": "alpha",
    "wheels/beta-2.0-py3-none-any.whl": "beta",
}


FIXED = (1980, 1, 1, 0, 0, 0)


def pkg(path, *, manifest="name = 'x'\nexamples = ['one']\n", lock=LOCK, members=None):
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(zipfile.ZipInfo("manifest.toml", FIXED), manifest)
        if lock is not None:
            zf.writestr(zipfile.ZipInfo("wheels.lock", FIXED), lock)
        for name, data in (MEMBERS if members is None else members).items():
            zf.writestr(zipfile.ZipInfo(name, FIXED), data)
    return path


def own_card(tmp_path, recipe, zip_path):
    # the two zips of one build have one file name, in different folders
    data = {**build.card(recipe, zip_path), "file": "pytool-1.0.3-1.zip"}
    return write_card(tmp_path / (zip_path.stem + ".toml"), data)


@pytest.fixture
def wheel_recipe(tmp_path, net):
    return recipe_for(tmp_path, net, python=True)


@pytest.fixture
def source_recipe(tmp_path, net):
    return build.load_recipe(
        make_python_recipe(tmp_path / "srctool", net, [], build_from_source=("beta",))
    )


def compare_zips(tmp_path, recipe, built, published):
    """Compares the way CI does: each zip with the card made from it."""
    build.compare_cards(
        own_card(tmp_path, recipe, built), own_card(tmp_path, recipe, published), recipe,
        built, published,
    )


def compare_source(tmp_path, recipe, built, published):
    """For a recipe with build_from_source, whose cards are not compared by size and hash."""
    one = write_card(tmp_path / "a.toml", card_of())
    two = write_card(tmp_path / "b.toml", card_of())
    build.compare_cards(one, two, recipe, built, published)


def test_equal_packages_pass(tmp_path, wheel_recipe):
    compare_zips(tmp_path, wheel_recipe, pkg(tmp_path / "1.zip"), pkg(tmp_path / "2.zip"))


def test_a_changed_script_with_the_same_build_fails(tmp_path, wheel_recipe):
    new = pkg(tmp_path / "1.zip", members={**MEMBERS, "files/tool.py": "print('changed')"})
    with pytest.raises(build.BuildError) as err:
        compare_zips(tmp_path, wheel_recipe, new, pkg(tmp_path / "2.zip"))
    assert "sha256" in str(err.value)
    assert "raise build in recipes/pytool/recipe.toml" in str(err.value)


def test_a_changed_licence_with_the_same_build_fails(tmp_path, wheel_recipe):
    new = pkg(tmp_path / "1.zip", members={**MEMBERS, "LICENSE": "other terms"})
    with pytest.raises(build.BuildError):
        compare_zips(tmp_path, wheel_recipe, new, pkg(tmp_path / "2.zip"))


def test_a_changed_example_in_the_manifest_fails(tmp_path, wheel_recipe):
    new = pkg(tmp_path / "1.zip", manifest="name = 'x'\nexamples = ['two']\n")
    with pytest.raises(build.BuildError):
        compare_zips(tmp_path, wheel_recipe, new, pkg(tmp_path / "2.zip"))


def test_without_build_from_source_the_zips_are_not_opened(tmp_path, wheel_recipe):
    one = write_card(tmp_path / "a.toml", card_of())
    build.compare_cards(one, one, wheel_recipe, tmp_path / "nope1.zip", tmp_path / "nope2.zip")


# with build_from_source = ["beta"]: every member is compared except beta's wheels and its
# line in wheels.lock


def test_equal_packages_pass_with_build_from_source(tmp_path, source_recipe):
    compare_source(tmp_path, source_recipe, pkg(tmp_path / "1.zip"), pkg(tmp_path / "2.zip"))


def test_a_differing_listed_wheel_and_its_lock_line_pass(tmp_path, source_recipe):
    new = pkg(
        tmp_path / "1.zip",
        lock=LOCK.replace("beta==2.0", "beta==2.1").replace("b" * 64, "c" * 64),
        members={**MEMBERS, "wheels/beta-2.0-py3-none-any.whl": "other bytes",
                 "wheels/beta-2.1-py3-none-any.whl": "newer"},
    )
    compare_source(tmp_path, source_recipe, new, pkg(tmp_path / "2.zip"))


def test_a_differing_script_fails_with_build_from_source(tmp_path, source_recipe):
    new = pkg(tmp_path / "1.zip", members={**MEMBERS, "files/tool.py": "print('changed')"})
    with pytest.raises(build.BuildError) as err:
        compare_source(tmp_path, source_recipe, new, pkg(tmp_path / "2.zip"))
    assert "files/tool.py" in str(err.value)


def test_a_differing_licence_fails_with_build_from_source(tmp_path, source_recipe):
    new = pkg(tmp_path / "1.zip", members={**MEMBERS, "LICENSE": "other"})
    with pytest.raises(build.BuildError) as err:
        compare_source(tmp_path, source_recipe, new, pkg(tmp_path / "2.zip"))
    assert "LICENSE" in str(err.value)


def test_a_differing_unlisted_wheel_fails_with_build_from_source(tmp_path, source_recipe):
    new = pkg(tmp_path / "1.zip", members={**MEMBERS, "wheels/alpha-1.0-py3-none-any.whl": "x"})
    with pytest.raises(build.BuildError) as err:
        compare_source(tmp_path, source_recipe, new, pkg(tmp_path / "2.zip"))
    assert "wheels/alpha-1.0-py3-none-any.whl" in str(err.value)


def test_a_member_in_only_one_zip_fails_with_build_from_source(tmp_path, source_recipe):
    new = pkg(tmp_path / "1.zip", members={**MEMBERS, "files/extra": "x"})
    with pytest.raises(build.BuildError) as err:
        compare_source(tmp_path, source_recipe, new, pkg(tmp_path / "2.zip"))
    assert "files/extra" in str(err.value)


def test_a_changed_manifest_fails_with_build_from_source(tmp_path, source_recipe):
    new = pkg(tmp_path / "1.zip", manifest="name = 'x'\nexamples = ['two']\n")
    with pytest.raises(build.BuildError) as err:
        compare_source(tmp_path, source_recipe, new, pkg(tmp_path / "2.zip"))
    assert "manifest.toml" in str(err.value)
    assert "wheels.lock" not in str(err.value)


def test_a_changed_pin_in_wheels_lock_fails_with_build_from_source(tmp_path, source_recipe):
    new = pkg(tmp_path / "1.zip", lock=LOCK.replace("alpha==1.0", "alpha==1.1"))
    with pytest.raises(build.BuildError) as err:
        compare_source(tmp_path, source_recipe, new, pkg(tmp_path / "2.zip"))
    assert "wheels.lock" in str(err.value)
    assert "manifest.toml" not in str(err.value)


def test_a_different_hash_for_another_name_still_fails(tmp_path, source_recipe):
    new = pkg(tmp_path / "1.zip", lock=LOCK.replace("a" * 64, "c" * 64))
    with pytest.raises(build.BuildError):
        compare_source(tmp_path, source_recipe, new, pkg(tmp_path / "2.zip"))


def test_a_published_zip_with_no_wheels_lock_fails_with_build_from_source(tmp_path, source_recipe):
    old = pkg(tmp_path / "2.zip", lock=None)
    with pytest.raises(build.BuildError) as err:
        compare_source(tmp_path, source_recipe, pkg(tmp_path / "1.zip"), old)
    assert "wheels.lock" in str(err.value)


def test_a_zip_with_the_member_twice_is_refused(tmp_path, source_recipe):
    import warnings
    import zipfile

    path = tmp_path / "1.zip"
    with zipfile.ZipFile(path, "w") as zf, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        zf.writestr("manifest.toml", "a")
        zf.writestr("manifest.toml", "b")
        zf.writestr("wheels.lock", LOCK)
    with pytest.raises(build.BuildError) as err:
        compare_source(tmp_path, source_recipe, path, pkg(tmp_path / "2.zip"))
    assert "more than once" in str(err.value)


def test_an_oversized_manifest_is_refused(tmp_path, source_recipe):
    big = pkg(tmp_path / "1.zip", manifest="#" * (build.MANIFEST_LIMIT + 1))
    with pytest.raises(build.BuildError) as err:
        compare_source(tmp_path, source_recipe, big, pkg(tmp_path / "2.zip"))
    assert "too large" in str(err.value)


def test_build_from_source_without_zips_is_an_error(tmp_path, source_recipe):
    one = write_card(tmp_path / "a.toml", card_of())
    with pytest.raises(build.BuildError):
        build.compare_cards(one, one, source_recipe)


def test_compare_command_takes_the_zips(tmp_path, net, capsys):
    make_python_recipe(tmp_path / "recipes" / "pytool", net, [], build_from_source=("beta",))
    one = write_card(tmp_path / "a.toml", card_of(name="pytool", file="pytool-1.0.3-1.zip"))
    a, b = pkg(tmp_path / "1.zip"), pkg(tmp_path / "2.zip", manifest="x = 1\n")
    args = ["compare", str(one), str(one), str(a), str(b), "--recipes", str(tmp_path / "recipes")]
    assert build.main(args) == 1
    assert "manifest.toml" in capsys.readouterr().err
    args[4] = str(a)
    assert build.main(args) == 0


# ---- notes keep the description literal


def test_a_description_with_markup_sits_in_a_fenced_block():
    text = build.release_notes(card_of(description="[click](http://evil.invalid) @someone ``` x"))
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if set(line) == {"`"})
    fence = lines[start]
    assert len(fence) >= 4
    assert lines[start + 1] == "[click](http://evil.invalid) @someone ``` x"
    assert lines[start + 2] == fence
    assert "@someone" not in "\n".join(lines[:start] + lines[start + 3 :])


# ---- runtimes and the tools that require one


def test_notes_name_what_a_tool_requires():
    text = build.release_notes(card_of(requires=["python-3.13"]))
    assert (
        "Licence: Apache-2.0\nRequires: python-3.13\nPackage: kerbrute-1.0.3-1.zip (100 bytes)\n"
    ) in text
    assert "Requires" not in build.release_notes(card_of())


def test_notes_say_a_runtime_is_installed_with_its_tools():
    card = card_of(name="python-3.13", version="3.13.18", kind="runtime", commands=[],
                   file="python-3.13-3.13.18-1.zip")
    text = build.release_notes(card)
    assert text.splitlines()[0] == "python-3.13 3.13.18, build 1"
    assert (
        "A runtime: pwn-broker installs it with the tools that require it.\n"
        "Package: python-3.13-3.13.18-1.zip (100 bytes)\n"
    ) in text
    assert "!" not in text


def test_compare_finds_a_changed_requires(tmp_path, net):
    recipe = recipe_for(tmp_path, net)
    one = write_card(tmp_path / "a.toml", card_of(requires=["python-3.13"]))
    two = write_card(tmp_path / "b.toml", card_of())
    with pytest.raises(build.BuildError, match="requires"):
        build.compare_cards(one, two, recipe)


def test_compare_command_handles_a_runtime(tmp_path, net, capsys):
    make_runtime_recipe(tmp_path / "recipes" / "python-3.13", net)
    card = card_of(name="python-3.13", version="3.13.18", kind="runtime", commands=[],
                   file="python-3.13-3.13.18-1.zip")
    one = write_card(tmp_path / "a.toml", card)
    two = write_card(tmp_path / "b.toml", card)
    args = ["compare", str(one), str(two), str(one), str(two), "--recipes", str(tmp_path / "recipes")]
    assert build.main(args) == 0
    assert capsys.readouterr().out.startswith("ok python-3.13 3.13.18-1 matches")
