"""The parts of build.py that CI's publish steps use: plan, compare and notes."""

import tomllib

import pytest
import tomli_w

import build
from conftest import PY_MANIFEST, make_python_recipe, make_recipe


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


def test_compare_ignores_size_and_hash_with_wheels(tmp_path, net):
    recipe = recipe_for(tmp_path, net, python=True)
    one = write_card(tmp_path / "a.toml", card_of(size=1, sha256="c" * 64))
    two = write_card(tmp_path / "b.toml", card_of())
    build.compare_cards(one, two, recipe)


def test_compare_with_wheels_still_fails_on_other_fields(tmp_path, net):
    recipe = recipe_for(tmp_path, net, python=True)
    one = write_card(tmp_path / "a.toml", card_of(size=1, description="new words"))
    two = write_card(tmp_path / "b.toml", card_of())
    with pytest.raises(build.BuildError) as err:
        build.compare_cards(one, two, recipe)
    assert "description" in str(err.value)
    assert "size" not in str(err.value)


def test_compare_command_names_the_recipe_and_exits_1(tmp_path, net, capsys):
    make_recipe(tmp_path / "recipes" / "kerbrute", net)
    one = write_card(tmp_path / "a.toml", card_of(licence="MIT"))
    two = write_card(tmp_path / "b.toml", card_of())
    code = build.main(["compare", str(one), str(two), "--recipes", str(tmp_path / "recipes")])
    assert code == 1
    assert "licence" in capsys.readouterr().err


def test_compare_command_succeeds_on_a_match(tmp_path, net, capsys):
    make_recipe(tmp_path / "recipes" / "kerbrute", net)
    one = write_card(tmp_path / "a.toml", card_of())
    two = write_card(tmp_path / "b.toml", card_of())
    assert build.main(["compare", str(one), str(two), "--recipes", str(tmp_path / "recipes")]) == 0
    assert capsys.readouterr().out.startswith("ok kerbrute 1.0.3-1")


def test_compare_command_refuses_a_card_with_no_recipe(tmp_path, net, capsys):
    (tmp_path / "recipes").mkdir()
    one = write_card(tmp_path / "a.toml", card_of())
    two = write_card(tmp_path / "b.toml", card_of())
    assert build.main(["compare", str(one), str(two), "--recipes", str(tmp_path / "recipes")]) == 1
    assert "kerbrute" in capsys.readouterr().err


# ---- plan


def test_plan_lists_name_tag_and_zip(tmp_path, net, capsys):
    make_recipe(tmp_path / "recipes" / "kerbrute", net)
    make_recipe(tmp_path / "recipes" / "other", net, version="2.0", build_no=3)
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
