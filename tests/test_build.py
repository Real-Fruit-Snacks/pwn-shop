import hashlib
import os
import stat
import subprocess
import sys
import tomllib
import venv
import zipfile
from pathlib import Path

import pytest
import tomli_w

import build
from conftest import (
    PY_MANIFEST, FakeTools, digest, lock_text, make_python_recipe, make_recipe, make_wheel,
    tar_gz, wheel_bytes, wheel_name, zip_bytes,
)


def built(recipe, tmp_path, net):
    staged = build.stage(recipe, tmp_path / "work", tmp_path / "cache", fetch=net)
    return build.write_zip(staged, tmp_path / "out" / "kerbrute-1.0.3-1.zip")


# ---- recipes


def test_load_recipe_fills_manifest(recipe):
    assert (recipe.name, recipe.version, recipe.build) == ("kerbrute", "1.0.3", 1)
    assert list(recipe.manifest)[:4] == ["schema", "name", "version", "build"]
    assert build.tag(recipe) == "kerbrute-v1.0.3-1"


def test_manifest_disagreeing_with_recipe_is_refused(tmp_path, net):
    folder = make_recipe(tmp_path / "r" / "kerbrute", net)
    data = tomllib.loads((folder / "manifest.toml").read_text())
    data["version"] = "9.9"
    (folder / "manifest.toml").write_text(tomli_w.dumps(data))
    with pytest.raises(build.BuildError, match="differs"):
        build.load_recipe(folder)


@pytest.mark.parametrize(
    "to", ["/files/x", "files/../x", "other/x", "files", "C:/x", "files\\x", "files//x"]
)
def test_bad_target_path_refused(tmp_path, net, to):
    src = [{"url": "https://e.invalid/x", "size": 1, "sha256": "0" * 64, "to": to}]
    with pytest.raises(build.BuildError):
        build.load_recipe(make_recipe(tmp_path / "r" / "kerbrute", net, sources=src))


def test_bad_member_path_refused(tmp_path, net):
    src = [
        {"url": "https://e.invalid/x.zip", "size": 1, "sha256": "0" * 64, "to": "files/x",
         "member": "../x"}
    ]
    with pytest.raises(build.BuildError):
        build.load_recipe(make_recipe(tmp_path / "r" / "kerbrute", net, sources=src))


# ---- fetching


def src_for(net, data, url="https://e.invalid/a"):
    s = net.add(url, data)
    return build.Source(url, s.size, s.sha256, "files/a")


def test_fetch_source_caches_by_hash(tmp_path, net):
    s = src_for(net, b"abc")
    first = build.fetch_source(s, tmp_path / "c", fetch=net)
    assert first.name == s.sha256
    build.fetch_source(s, tmp_path / "c", fetch=net)
    assert len(net.calls) == 1


def test_fetch_source_replaces_a_corrupt_cache_entry(tmp_path, net):
    s = src_for(net, b"abc")
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / s.sha256).write_bytes(b"xyz")
    assert build.fetch_source(s, tmp_path / "c", fetch=net).read_bytes() == b"abc"


def test_wrong_size_refused(tmp_path, net):
    s = src_for(net, b"abc")
    s.size = 4
    with pytest.raises(build.BuildError, match="size"):
        build.fetch_source(s, tmp_path / "c", fetch=net)
    assert not list((tmp_path / "c").iterdir())


def test_wrong_hash_refused(tmp_path, net):
    s = src_for(net, b"abc")
    s.sha256 = "0" * 64
    with pytest.raises(build.BuildError, match="SHA-256"):
        build.fetch_source(s, tmp_path / "c", fetch=net)
    assert not list((tmp_path / "c").iterdir())


def test_default_fetch_accepts_only_https(tmp_path):
    with pytest.raises(build.BuildError, match="https"):
        build.fetch("http://example.invalid/x", tmp_path / "x", 10)
    with pytest.raises(build.BuildError, match="https"):
        build.fetch("file:///etc/passwd", tmp_path / "x", 10)


def test_redirect_to_http_refused():
    handler = build._HttpsOnly()
    with pytest.raises(build.BuildError, match="redirect"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://example.invalid/x")


# ---- staging and the zip


def test_bare_file_zip_listing_and_determinism(recipe, tmp_path, net):
    first = built(recipe, tmp_path, net)
    data = first.read_bytes()
    other = build.write_zip(
        build.stage(recipe, tmp_path / "work2", tmp_path / "cache", fetch=net),
        tmp_path / "out2" / first.name,
    )
    assert other.read_bytes() == data
    with zipfile.ZipFile(first) as zf:
        assert zf.namelist() == ["LICENSE", "files/kerbrute", "manifest.toml"]
        for info in zf.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.external_attr >> 16 == stat.S_IFREG | 0o644
            assert info.create_system == 3
        assert zf.read("files/kerbrute") == b"#!/bin/sh\necho hi\n"
        assert zf.read("LICENSE") == b"licence text\n"
    build.validate(first)


def test_manifest_bytes_are_stable_and_lf(recipe, tmp_path, net):
    staged = build.stage(recipe, tmp_path / "w", tmp_path / "c", fetch=net)
    raw = (staged / "manifest.toml").read_bytes()
    assert b"\r" not in raw
    assert raw == tomli_w.dumps(recipe.manifest).encode()
    assert tomllib.loads(raw.decode())["schema"] == 1


def test_member_from_tar_gz_and_zip(tmp_path, net):
    tgz = tar_gz({"dir/proxy": b"PROXY", "dir/other": b"x"})
    zp = zip_bytes({"agent.exe": b"AGENT"})
    net.files["https://e.invalid/a.tar.gz"] = tgz
    net.files["https://e.invalid/b.zip"] = zp
    sources = [
        {"url": "https://e.invalid/a.tar.gz", "size": len(tgz), "sha256": digest(tgz),
         "to": "files/proxy", "member": "dir/proxy"},
        {"url": "https://e.invalid/b.zip", "size": len(zp), "sha256": digest(zp),
         "to": "files/agent.exe", "member": "agent.exe"},
    ]
    r = build.load_recipe(make_recipe(tmp_path / "r" / "kerbrute", net, sources=sources))
    staged = build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)
    assert (staged / "files" / "proxy").read_bytes() == b"PROXY"
    assert (staged / "files" / "agent.exe").read_bytes() == b"AGENT"


def one_member_recipe(tmp_path, net, url, data, member):
    net.files[url] = data
    src = [{"url": url, "size": len(data), "sha256": digest(data), "to": "files/a",
            "member": member}]
    return build.load_recipe(make_recipe(tmp_path / "r" / "kerbrute", net, sources=src))


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_missing_member_is_an_error(tmp_path, net, kind):
    data = tar_gz({"a": b"1"}) if kind == "tar" else zip_bytes({"a": b"1"})
    url = "https://e.invalid/x." + ("tar.gz" if kind == "tar" else "zip")
    r = one_member_recipe(tmp_path, net, url, data, "nope")
    with pytest.raises(build.BuildError, match="no member"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


def test_link_member_is_refused(tmp_path, net):
    data = tar_gz({"a": b"1"}, links={"l": "/etc/passwd"})
    r = one_member_recipe(tmp_path, net, "https://e.invalid/x.tgz", data, "l")
    with pytest.raises(build.BuildError, match="regular file"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


def test_directory_member_is_refused(tmp_path, net):
    data = tar_gz({"d/f": b"1"}, dirs=["d"])
    r = one_member_recipe(tmp_path, net, "https://e.invalid/x.tgz", data, "d")
    with pytest.raises(build.BuildError, match="regular file"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


def test_member_of_unknown_archive_type_refused(tmp_path, net):
    r = one_member_recipe(tmp_path, net, "https://e.invalid/x.rar", b"1", "a")
    with pytest.raises(build.BuildError, match="tar.gz"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


def test_missing_licence_refused(tmp_path, net):
    r = build.load_recipe(make_recipe(tmp_path / "r" / "kerbrute", net, licence=False))
    with pytest.raises(build.BuildError, match="LICENSE"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


# ---- card, sums, index


def test_card_sums_and_card_file(recipe, tmp_path, net):
    z = built(recipe, tmp_path, net)
    c = build.card(recipe, z)
    assert c["file"] == "kerbrute-1.0.3-1.zip"
    assert c["size"] == z.stat().st_size
    assert c["sha256"] == digest(z.read_bytes())
    assert c["commands"] == ["kerbrute"]
    assert c["kind"] == "kali" and c["needs_root"] is False and c["build"] == 1
    path = build.write_card(recipe, z)
    assert path.name == "kerbrute-1.0.3-1.toml" and path.parent == z.parent
    assert tomllib.loads(path.read_text()) == c
    sums = build.write_sums(z)
    assert sums.name == "SHA256SUMS.txt"
    assert sums.read_bytes() == f"{c['sha256']}  {z.name}\n".encode()


def cardf(name="kerbrute", version="1.0.3", build_no=1):
    return {
        "name": name, "version": version, "build": build_no, "kind": "kali",
        "description": f"{name} {version}", "tags": ["t"], "upstream": "https://u",
        "licence": "MIT", "arch": "x86_64", "needs_root": False, "commands": [name],
        "file": f"{name}-{version}-{build_no}.zip", "size": 10, "sha256": "a" * 64,
    }


def test_index_groups_and_takes_last_card():
    cards = [cardf(version="1.0.2"), cardf(name="other"), cardf(version="1.0.3")]
    idx = build.build_index(cards, base_url=None)
    assert idx["schema"] == 1
    assert [t["name"] for t in idx["tool"]] == ["kerbrute", "other"]
    t = idx["tool"][0]
    assert t["latest"] == "1.0.3-1" and t["description"] == "kerbrute 1.0.3"
    assert [b["version"] for b in t["build"]] == ["1.0.2", "1.0.3"]
    assert t["build"][0]["url"] == "kerbrute-1.0.2-1.zip"


def test_index_url_pattern_replaces_tag_and_file():
    idx = build.build_index([cardf()], base_url="https://h/{tag}/{file}?x={tag}")
    url = idx["tool"][0]["build"][0]["url"]
    assert url == "https://h/kerbrute-v1.0.3-1/kerbrute-1.0.3-1.zip?x=kerbrute-v1.0.3-1"


def test_index_refuses_duplicate_build():
    with pytest.raises(build.BuildError, match="more than once"):
        build.build_index([cardf(), cardf()], base_url=None)


def test_card_order_by_name_numeric_version_build():
    cards = [cardf(version="1.10.0"), cardf(version="1.9.0"), cardf(name="a"),
             cardf(version="1.9.0", build_no=2)]
    ordered = [(c["name"], c["version"], c["build"]) for c in build.card_order(cards)]
    assert ordered == [
        ("a", "1.0.3", 1), ("kerbrute", "1.9.0", 1), ("kerbrute", "1.9.0", 2),
        ("kerbrute", "1.10.0", 1),
    ]


def test_index_command_reads_cards_folder(tmp_path):
    for c in (cardf(version="1.10.0"), cardf(version="1.9.0")):
        (tmp_path / f"{c['file'][:-4]}.toml").write_text(tomli_w.dumps(c))
    out = tmp_path / "index.toml"
    args = ["index", str(tmp_path), "--base-url", "https://h/{tag}/{file}", "--out", str(out)]
    assert build.main(args) == 0
    data = tomllib.loads(out.read_text())
    assert data["tool"][0]["latest"] == "1.10.0-1"
    assert data["tool"][0]["build"][0]["url"] == "https://h/kerbrute-v1.9.0-1/kerbrute-1.9.0-1.zip"
    assert b"\r" not in out.read_bytes()


def test_build_command_writes_zip_card_sums_and_bare_index(tmp_path, net, monkeypatch):
    make_recipe(tmp_path / "recipes" / "kerbrute", net)
    monkeypatch.setattr(build, "fetch", net)
    out = tmp_path / "dist"
    rc = build.main(["build", "--recipes", str(tmp_path / "recipes"), "--out", str(out),
                     "--cache", str(tmp_path / "cache")])
    assert rc == 0
    assert (out / "kerbrute-1.0.3-1.zip").is_file()
    assert (out / "kerbrute-1.0.3-1.toml").is_file()
    assert (out / "sums" / "kerbrute-v1.0.3-1" / "SHA256SUMS.txt").is_file()
    idx = tomllib.loads((out / "index.toml").read_text())
    assert idx["tool"][0]["build"][0]["url"] == "kerbrute-1.0.3-1.zip"


# ---- validate


def pkg(tmp_path, manifest=None, files=None, *, name="kerbrute-1.0.3-1.zip", drop=(),
        infos=(), method=zipfile.ZIP_STORED):
    m = {
        "schema": 1, "name": "kerbrute", "version": "1.0.3", "build": 1, "kind": "kali",
        "description": "d", "tags": [], "upstream": "u", "licence": "l", "arch": "x86_64",
        "needs_root": False, "examples": [],
        "command": [{"name": "kerbrute", "type": "binary", "path": "files/kerbrute"}],
    }
    m.update(manifest or {})
    entries = {"manifest.toml": tomli_w.dumps(m).encode(), "LICENSE": b"l\n",
               "files/kerbrute": b"x"}
    entries.update(files or {})
    for d in drop:
        entries.pop(d)
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", method) as zf:
        for n, data in entries.items():
            zf.writestr(n, data)
        for info, data in infos:
            zf.writestr(info, data)
    return path


PY_ENTRY = {
    "command": [{"name": "t", "type": "python-entry", "entry": "t"}],
    "python": {"versions": ["3.13"], "lock": "wheels.lock", "wheels": "wheels"},
}


def test_validate_accepts_a_good_package(tmp_path):
    build.validate(pkg(tmp_path))


def test_validate_accepts_python_package(tmp_path):
    files = {"wheels.lock": b"x", "wheels/a.whl": b"w"}
    build.validate(pkg(tmp_path, PY_ENTRY, files, drop=("files/kerbrute",)))


BAD_MANIFESTS = {
    "schema": {"schema": 2},
    "name": {"name": "Bad_Name"},
    "version": {"version": "1 0"},
    "build": {"build": 0},
    "kind": {"kind": "nope"},
    "needs_root": {"needs_root": "yes"},
    "description": {"description": ""},
    "tags": {"tags": "a"},
    "cmd_type": {"command": [{"name": "k", "type": "x", "path": "files/kerbrute"}]},
    "cmd_name": {"command": [{"name": "-k", "type": "binary", "path": "files/kerbrute"}]},
    "cmd_dup": {"command": [{"name": "k", "type": "binary", "path": "files/kerbrute"}] * 2},
    "cmd_path_outside": {"command": [{"name": "k", "type": "binary", "path": "LICENSE"}]},
    "cmd_path_dotdot": {"command": [{"name": "k", "type": "binary", "path": "files/../x"}]},
    "cmd_missing_file": {"command": [{"name": "k", "type": "binary", "path": "files/zzz"}]},
    "kali_no_cmd": {"command": []},
    "kali_with_upload": {"upload": [{"path": "files/kerbrute", "as": "k"}]},
    "upload_with_cmd": {"kind": "upload", "upload": [{"path": "files/kerbrute", "as": "k"}]},
    "upload_no_upload": {"kind": "upload", "command": []},
    "upload_as_slash": {"kind": "both", "upload": [{"path": "files/kerbrute", "as": "a/b"}]},
    "upload_missing": {"kind": "both", "upload": [{"path": "files/none", "as": "a"}]},
    "python_needed": {"command": [{"name": "k", "type": "python-entry", "entry": "k"}]},
    "python_unneeded": {
        "python": {"versions": ["3.13"], "lock": "wheels.lock", "wheels": "wheels"}
    },
    "entry_slash": {
        "command": [{"name": "k", "type": "python-entry", "entry": "a/b"}],
        "python": {"versions": ["3.13"], "lock": "wheels.lock", "wheels": "wheels"},
    },
    "home_dotdot": {"data": {"home": ["../x"]}},
}


@pytest.mark.parametrize("case", list(BAD_MANIFESTS))
def test_validate_refuses_bad_manifest(tmp_path, case):
    with pytest.raises(build.BuildError):
        build.validate(pkg(tmp_path, BAD_MANIFESTS[case], {"wheels.lock": b"x", "wheels/a": b"w"}))


def test_validate_python_lock_and_wheels_missing(tmp_path):
    with pytest.raises(build.BuildError, match="lock"):
        build.validate(pkg(tmp_path, PY_ENTRY, {"wheels/a": b"w"}))
    with pytest.raises(build.BuildError, match="no wheels"):
        build.validate(pkg(tmp_path, PY_ENTRY, {"wheels.lock": b"x"}))


def test_validate_python_lock_under_files_refused(tmp_path):
    m = {
        "command": PY_ENTRY["command"],
        "python": {"versions": ["3.13"], "lock": "files/l", "wheels": "wheels"},
    }
    with pytest.raises(build.BuildError, match="files/"):
        build.validate(pkg(tmp_path, m, {"files/l": b"x", "wheels/a": b"w"}))


@pytest.mark.parametrize(
    "name", ["../x", "/abs", "files/../x", "a\\b", "C:/x", "files//x", "./files/x"]
)
def test_validate_refuses_unsafe_entry_names(tmp_path, name):
    with pytest.raises(build.BuildError):
        build.validate(pkg(tmp_path, files={name: b"x"}))


@pytest.mark.parametrize("name", ["README", "extra/x", "manifest.toml.bak"])
def test_validate_refuses_entries_outside_the_layout(tmp_path, name):
    with pytest.raises(build.BuildError, match="layout"):
        build.validate(pkg(tmp_path, files={name: b"x"}))


def test_validate_refuses_missing_manifest_and_bad_toml(tmp_path):
    with pytest.raises(build.BuildError, match="missing"):
        build.validate(pkg(tmp_path, drop=("manifest.toml",)))
    with pytest.raises(build.BuildError, match="cannot be read"):
        build.validate(pkg(tmp_path, files={"manifest.toml": b"= nope"}))


def test_validate_refuses_compressed_entry(tmp_path):
    with pytest.raises(build.BuildError, match="compressed"):
        build.validate(pkg(tmp_path, files={"files/kerbrute": b"x" * 500},
                           method=zipfile.ZIP_DEFLATED))


def test_validate_refuses_duplicates(tmp_path):
    with pytest.warns(UserWarning):
        p = pkg(tmp_path, infos=[(zipfile.ZipInfo("LICENSE"), b"again")])
    with pytest.raises(build.BuildError, match="twice"):
        build.validate(p)


def test_validate_refuses_symlink_entry(tmp_path):
    info = zipfile.ZipInfo("files/link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(build.BuildError, match="link"):
        build.validate(pkg(tmp_path, infos=[(info, b"/etc/passwd")]))


def test_validate_refuses_file_used_as_folder(tmp_path):
    with pytest.raises(build.BuildError, match="inside the file"):
        build.validate(pkg(tmp_path, files={"files/kerbrute/x": b"1"}))


def test_validate_refuses_too_many_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "MAX_MEMBERS", 2)
    with pytest.raises(build.BuildError, match="entries"):
        build.validate(pkg(tmp_path))


def test_validate_refuses_oversize_unpack(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "MAX_UNPACKED_BYTES", 5)
    with pytest.raises(build.BuildError, match="unpacks"):
        build.validate(pkg(tmp_path, files={"files/kerbrute": b"123456"}))


def test_validate_refuses_oversize_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "MANIFEST_LIMIT", 10)
    with pytest.raises(build.BuildError, match="too large"):
        build.validate(pkg(tmp_path))


def test_validate_refuses_wrong_file_name_and_non_zip(tmp_path):
    with pytest.raises(build.BuildError, match="named"):
        build.validate(pkg(tmp_path, name="other.zip"))
    junk = tmp_path / "kerbrute-1.0.3-1.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(build.BuildError, match="zip"):
        build.validate(junk)


def test_main_reports_failure_with_a_fail_line(tmp_path, capsys):
    assert build.main(["index", str(tmp_path)]) == 1
    assert capsys.readouterr().err.startswith("fail ")


# ---- review fixes: recipe fields, atomic build, version order, members


@pytest.mark.parametrize(
    "field, value",
    [
        ("name", "../escaped"),
        ("name", "a/b"),
        ("name", "a\\b"),
        ("name", "Upper"),
        ("version", "1/2"),
        ("version", "1 2"),
        ("version", "1\\2"),
        ("build_no", 0),
        ("build_no", -1),
    ],
)
def test_hostile_recipe_fields_refused_before_anything_is_written(tmp_path, net, field, value):
    folder = make_recipe(tmp_path / "recipes" / "kerbrute", net, **{field: value})
    out = tmp_path / "out"
    with pytest.raises(build.BuildError, match="kerbrute"):
        build.load_recipe(folder)
    assert build.main(["build", "--recipes", str(tmp_path / "recipes"), "--out", str(out)]) == 1
    assert not out.exists()
    assert not (tmp_path / "escaped-1.0.3-1.zip").exists()


def test_folder_name_must_equal_recipe_name(tmp_path, net):
    folder = make_recipe(tmp_path / "recipes" / "other", net, name="kerbrute")
    with pytest.raises(build.BuildError, match="other"):
        build.load_recipe(folder)


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_failed_validate_leaves_the_output_folder_untouched(tmp_path, net, monkeypatch):
    recipes = tmp_path / "recipes"
    make_recipe(recipes / "kerbrute", net)
    monkeypatch.setattr(build, "fetch", net)
    out = tmp_path / "dist"
    args = ["build", "--recipes", str(recipes), "--out", str(out), "--cache", str(tmp_path / "c")]
    assert build.main(args) == 0
    before = snapshot(out)
    manifest = recipes / "kerbrute" / "manifest.toml"
    data = tomllib.loads(manifest.read_text())
    data["kind"] = "nope"
    manifest.write_text(tomli_w.dumps(data))
    assert build.main(args) == 1
    assert snapshot(out) == before


def test_build_one_leaves_nothing_when_staging_fails(tmp_path, net):
    r = build.load_recipe(make_recipe(tmp_path / "recipes" / "kerbrute", net, licence=False))
    out = tmp_path / "dist"
    with pytest.raises(build.BuildError):
        build.build_one(r, out, tmp_path / "c", fetch_fn=net)
    assert not out.exists() or not any(out.iterdir())


@pytest.mark.parametrize(
    "lower, higher",
    [
        ("1.0.0-rc1", "1.0.0"),
        ("1.0.0a", "1.0.1"),
        ("2024.12.16", "2026.11.14"),
        ("0.9.2", "0.10.0"),
        ("1.0.0-rc1", "1.0.0-rc2"),
        ("1.0.0.dev1", "1.0.0"),
        ("v1", "0.1"),
        ("2.0", "2.0.0"),
    ],
)
def test_version_order(lower, higher):
    assert build._version_key(lower) < build._version_key(higher)


def test_version_without_leading_number_sorts_first():
    assert build._version_key("nightly") < build._version_key("0.0.1")


def test_equal_numbers_order_is_stable():
    a = [cardf(version="2.0.0"), cardf(version="2.0")]
    assert build.card_order(a) == build.card_order(list(reversed(a)))


@pytest.mark.parametrize("versions", [["1.0.0-rc1", "1.0.0"], ["1.0.0", "1.0.0-rc1"]])
def test_latest_is_the_release_not_the_prerelease(versions):
    cards = build.card_order([cardf(version=v) for v in versions])
    assert build.build_index(cards, base_url=None)["tool"][0]["latest"] == "1.0.0-1"


def test_duplicate_member_refused_in_tar(tmp_path, net):
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for body in (b"one", b"two"):
            info = tarfile.TarInfo("proxy")
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    r = one_member_recipe(tmp_path, net, "https://e.invalid/x.tgz", buf.getvalue(), "proxy")
    with pytest.raises(build.BuildError, match="more than one"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


def test_duplicate_member_refused_in_zip(tmp_path, net):
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("proxy", b"one")
        with pytest.warns(UserWarning):
            zf.writestr("proxy", b"two")
    r = one_member_recipe(tmp_path, net, "https://e.invalid/x.zip", buf.getvalue(), "proxy")
    with pytest.raises(build.BuildError, match="more than one"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


def test_dot_slash_member_in_tar_is_selected_by_plain_name(tmp_path, net):
    data = tar_gz({"./proxy": b"PROXY"})
    r = one_member_recipe(tmp_path, net, "https://e.invalid/x.tgz", data, "proxy")
    staged = build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)
    assert (staged / "files" / "a").read_bytes() == b"PROXY"


def test_recipe_member_may_not_start_with_dot_slash(tmp_path, net):
    src = [{"url": "https://e.invalid/x.tgz", "size": 1, "sha256": "0" * 64, "to": "files/a",
            "member": "./proxy"}]
    with pytest.raises(build.BuildError):
        build.load_recipe(make_recipe(tmp_path / "r" / "kerbrute", net, sources=src))


@pytest.mark.parametrize("error", [RuntimeError("File is encrypted"), NotImplementedError("compression 99")])
def test_unreadable_zip_member_is_a_build_error(tmp_path, net, monkeypatch, error):
    data = zip_bytes({"a": b"x" * 50})
    r = one_member_recipe(tmp_path, net, "https://e.invalid/x.zip", data, "a")

    def boom(*a, **k):
        raise error

    monkeypatch.setattr(zipfile.ZipFile, "open", boom)
    with pytest.raises(build.BuildError, match="archive"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


def test_index_reads_only_card_named_files(tmp_path):
    (tmp_path / "kerbrute-1.0.3-1.toml").write_text(tomli_w.dumps(cardf()))
    (tmp_path / "notes.toml").write_text("not = 'a card'\n")
    (tmp_path / "pyproject.toml").write_text("x = 1\n")
    out = tmp_path / "index.toml"
    assert build.main(["index", str(tmp_path), "--out", str(out)]) == 0
    assert tomllib.loads(out.read_text())["tool"][0]["name"] == "kerbrute"


def test_card_missing_a_field_names_file_and_field(tmp_path, capsys):
    c = cardf()
    del c["commands"]
    (tmp_path / "kerbrute-1.0.3-1.toml").write_text(tomli_w.dumps(c))
    assert build.main(["index", str(tmp_path), "--out", str(tmp_path / "i.toml")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("fail ") and "kerbrute-1.0.3-1.toml" in err and "commands" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("arg", ["a/b", "..", "../x", "/abs", "a\\b", "C:x", "."])
def test_tool_argument_must_be_a_plain_folder_name(tmp_path, capsys, arg):
    (tmp_path / "recipes").mkdir()
    for cmd in ("build", "check"):
        argv = [cmd, arg, "--recipes", str(tmp_path / "recipes"), "--out", str(tmp_path / "o")]
        assert build.main(argv) == 1
        err = capsys.readouterr().err
        assert err.count("\n") == 1 and "plain name" in err


def test_targets_differing_only_by_case_refused(tmp_path, net):
    src = [
        {"url": "https://e.invalid/a", "size": 1, "sha256": "0" * 64, "to": "files/Tool"},
        {"url": "https://e.invalid/b", "size": 1, "sha256": "1" * 64, "to": "files/tool"},
    ]
    with pytest.raises(build.BuildError, match="case"):
        build.load_recipe(make_recipe(tmp_path / "r" / "kerbrute", net, sources=src))


def test_every_subcommand_turns_build_errors_into_one_fail_line(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    bad = tmp_path / "recipes" / "broken"
    bad.mkdir(parents=True)
    (bad / "recipe.toml").write_text("version = ")
    runs = [
        ["index", str(empty)],
        ["index", str(empty), "--out", str(tmp_path / "nodir" / "i.toml")],
        ["build", "--recipes", str(tmp_path / "recipes"), "--out", str(tmp_path / "o")],
        ["check", "--recipes", str(tmp_path / "recipes")],
        ["lock", "--recipes", str(tmp_path / "recipes")],
        ["build", "nope", "--recipes", str(tmp_path / "recipes")],
    ]
    for argv in runs:
        assert build.main(argv) == 1, argv
        err = capsys.readouterr().err
        assert err.startswith("fail ") and err.count("\n") == 1 and "Traceback" not in err, argv


# ---- Python tools


def staged_dir(tmp_path):
    (tmp_path / "staged").mkdir(exist_ok=True)
    return tmp_path / "staged"


def py_recipe(tmp_path, net, pins, **kw):
    return build.load_recipe(make_python_recipe(tmp_path / "recipes" / "tinytool", net, pins, **kw))


def pure(name, version):
    return wheel_name(name, version), wheel_bytes(name, version)


def test_constants():
    assert build.PYTHONS == ("3.12", "3.13")
    assert build.PLATFORM == "manylinux_2_28_x86_64"


def test_write_wheels_lock_one_line_per_distribution(tmp_path):
    staged = staged_dir(tmp_path)
    make_wheel(staged / "wheels", "My_Dep.x", "2.0", tag="cp312-cp312-manylinux2014_x86_64")
    make_wheel(staged / "wheels", "my-dep.x", "2.0", tag="cp313-cp313-manylinux_2_28_x86_64")
    make_wheel(staged / "wheels", "aaa", "1.0")
    path = build.write_wheels_lock(staged)
    assert path == staged / "wheels.lock"
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n") and b"#" not in raw
    lines = raw.decode().splitlines()
    assert [ln.split()[0] for ln in lines] == ["aaa==1.0", "my-dep-x==2.0"]
    hashes = sorted(
        hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (staged / "wheels").iterdir()
        if p.name.lower().startswith("my_dep.x-")
    )
    assert lines[1].split()[1:] == [f"--hash=sha256:{h}" for h in hashes]
    assert len(lines[1].split()) == 3


def test_write_wheels_lock_refuses_two_versions_of_one_distribution(tmp_path):
    staged = staged_dir(tmp_path)
    make_wheel(staged / "wheels", "dep", "1.0")
    make_wheel(staged / "wheels", "dep", "1.1")
    with pytest.raises(build.BuildError, match="dep"):
        build.write_wheels_lock(staged)


def test_write_wheels_lock_refuses_no_wheels(tmp_path):
    with pytest.raises(build.BuildError, match="no wheels"):
        build.write_wheels_lock(staged_dir(tmp_path))


@pytest.mark.parametrize(
    "tag, python, fits",
    [
        ("py3-none-any", "3.12", True),
        ("py2.py3-none-any", "3.13", True),
        ("py2-none-any", "3.12", False),
        ("py312-none-any", "3.13", True),
        ("py313-none-any", "3.12", False),
        ("cp312-cp312-manylinux_2_28_x86_64", "3.12", True),
        ("cp312-cp312-manylinux_2_28_x86_64", "3.13", False),
        ("cp311-abi3-manylinux2014_x86_64", "3.13", True),
        ("cp313-abi3-manylinux2014_x86_64", "3.12", False),
        ("cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64", "3.12", True),
        ("py3-none-manylinux_2_17_x86_64", "3.12", True),
        ("cp312-cp312-win_amd64", "3.12", False),
        ("cp312-cp312-macosx_11_0_arm64", "3.12", False),
        ("cp312-cp312-musllinux_1_2_x86_64", "3.12", False),
        ("cp312-cp312-manylinux2014_aarch64", "3.12", False),
        ("cp312-cp312-manylinux_2_34_x86_64", "3.12", False),
        ("pp312-pypy312_pp73-manylinux2014_x86_64", "3.12", False),
        ("cp312-cp312-manylinux1_x86_64", "3.12", True),
        ("cp312-cp312-manylinux1_x86_64", "3.13", False),
        ("cp312-cp312-manylinux_2_5_x86_64.manylinux1_x86_64", "3.12", True),
        ("cp312-cp312-manylinux_2_5_x86_64.manylinux1_x86_64", "3.13", False),
        ("cp313-cp313-manylinux_2_24_x86_64", "3.12", False),
        ("cp313-cp313-manylinux_2_24_x86_64", "3.13", True),
        ("cp39-abi3-manylinux2010_x86_64", "3.12", True),
        ("cp39-abi3-manylinux2010_x86_64", "3.13", True),
        ("cp312-cp312-manylinux_2_34_x86_64", "3.13", False),
        ("cp313-cp313t-manylinux_2_28_x86_64", "3.12", False),
        ("cp313-cp313t-manylinux_2_28_x86_64", "3.13", False),
        ("cp312-cp312-manylinux_2_28_aarch64", "3.12", False),
        ("cp312-cp312-manylinux_2_28_aarch64", "3.13", False),
        ("cp312-cp312-manylinux_2_29_x86_64", "3.12", False),
        ("cp312-cp312-linux_x86_64", "3.12", False),
    ],
)
def test_wheel_tags_that_fit(tag, python, fits):
    assert build._wheel_fits(f"x-1.0-{tag}.whl", python) is fits


def two_pythons(*files):
    return {(py, dist): list(items) for py in build.PYTHONS for dist, items in files}


def test_collect_wheels_runs_pip_for_each_python_and_bundles_the_union(tmp_path, net):
    t312, t313 = "cp312-cp312-manylinux_2_28_x86_64", "cp313-cp313-manylinux_2_28_x86_64"
    dep312 = (wheel_name("dep", "1.0", t312), wheel_bytes("dep", "1.0", tag=t312))
    dep313 = (wheel_name("dep", "1.0", t313), wheel_bytes("dep", "1.0", tag=t313))
    tool = pure("tinytool", "1.0")
    pins = [("dep", "1.0", [dep312[1], dep313[1]]), ("tinytool", "1.0", [tool[1]])]
    fake = FakeTools(
        downloads={
            ("3.12", "dep"): [dep312], ("3.13", "dep"): [dep313],
            ("3.12", "tinytool"): [tool], ("3.13", "tinytool"): [tool],
        }
    )
    r = py_recipe(tmp_path, net, pins)
    staged = staged_dir(tmp_path)
    got = build.collect_wheels(r, staged, run=fake)
    assert [p.name for p in got] == sorted([dep312[0], dep313[0], tool[0]])
    assert all(p.parent == staged / "wheels" for p in got)
    assert (staged / "wheels" / tool[0]).read_bytes() == tool[1]
    downloads = [c for c in fake.calls if c[3] == "download"]
    assert len(downloads) == 4
    first = downloads[0]
    assert first[:4] == [sys.executable, "-m", "pip", "download"]
    for flag in ("--no-deps", "--require-hashes", "--only-binary", "--implementation"):
        assert flag in first
    assert first[first.index("--only-binary") + 1] == ":all:"
    assert first[first.index("--platform") + 1] == "manylinux_2_28_x86_64"
    assert first[first.index("--implementation") + 1] == "cp"
    versions = [c[c.index("--python-version") + 1] for c in downloads]
    assert sorted(versions) == ["3.12", "3.12", "3.13", "3.13"]
    assert "--no-index" not in first


def test_each_pip_download_carries_only_its_own_pin(tmp_path, net):
    seen = []
    tool = pure("tinytool", "1.0")
    dep = pure("dep", "2.0")

    class Spy(FakeTools):
        def __call__(self, cmd, **kw):
            if cmd[3] == "download":
                seen.append(Path(cmd[cmd.index("-r") + 1]).read_text())
            return super().__call__(cmd, **kw)

    pins = [("dep", "2.0", [dep[1]]), ("tinytool", "1.0", [tool[1]])]
    fake = Spy(downloads=two_pythons(("tinytool", [tool]), ("dep", [dep])))
    build.collect_wheels(py_recipe(tmp_path, net, pins), staged_dir(tmp_path), run=fake)
    assert len(seen) == 4
    assert all(s.count("==") == 1 and s.endswith("\n") for s in seen)
    assert sum(s.startswith("dep==2.0 --hash=sha256:") for s in seen) == 2


def test_collect_wheels_local_folder_adds_no_index(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads=two_pythons(("tinytool", [tool])))
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    build.collect_wheels(r, staged_dir(tmp_path), run=fake, find_links=tmp_path / "local")
    first = fake.calls[0]
    assert first[first.index("--no-index") + 1] == "--find-links"
    assert first[first.index("--find-links") + 1] == str(tmp_path / "local")


@pytest.mark.parametrize(
    "line",
    [
        "colorama==0.4.6 ; sys_platform == 'win32'",
        "dep==1.0 ; python_version < '3.13'",
        "dep==1.0;python_version>'3.12'",
    ],
)
def test_lock_line_with_a_marker_is_refused(tmp_path, net, line):
    tool = pure("tinytool", "1.0")
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    h = "--hash=sha256:" + "a" * 64
    (r.folder / "requirements.lock").write_text(f"{line} \\\n    {h}\n")
    fake = FakeTools()
    with pytest.raises(build.BuildError, match="marker") as err:
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    assert "python build.py lock tinytool" in str(err.value)
    assert fake.calls == []


HASH_A = "--hash=sha256:" + "a" * 64
HASH_B = "--hash=sha256:" + "b" * 64


@pytest.mark.parametrize(
    "text",
    [
        f"dep>=1.0 {HASH_A}\n",
        "dep==1.0\n",
        "dep==1.0 --hash=sha1:abc\n",
        "-e git+https://x/y#egg=dep\n",
        "--index-url https://x/simple\n",
        "dep @ https://x/dep.whl\n",
        f"dep==1.0 {HASH_A}\nDep==2.0 {HASH_B}\n",
        "# only a comment\n",
        "",
    ],
)
def test_lock_must_be_pinned_hashed_and_plain(tmp_path, net, text):
    tool = pure("tinytool", "1.0")
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    (r.folder / "requirements.lock").write_text(text)
    with pytest.raises(build.BuildError):
        build.collect_wheels(r, staged_dir(tmp_path), run=FakeTools())


def test_comments_and_continued_lines_in_the_lock_are_read(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads=two_pythons(("tinytool", [tool])))
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    text = lock_text([("tinytool", "1.0", [tool[1]])]).replace("\n", "\n    # via -r x\n", 1)
    (r.folder / "requirements.lock").write_text("# header\n" + text)
    assert len(build.collect_wheels(r, staged_dir(tmp_path), run=fake)) == 1


def test_missing_lock_says_to_run_lock(tmp_path, net):
    r = py_recipe(tmp_path, net, [], lock=False)
    with pytest.raises(build.BuildError, match="python build.py lock tinytool"):
        build.collect_wheels(r, staged_dir(tmp_path), run=FakeTools())


@pytest.mark.parametrize(
    "tag",
    ["cp312-cp312-win_amd64", "cp311-cp311-manylinux_2_28_x86_64",
     "cp312-cp312-musllinux_1_2_x86_64", "cp313-cp313-manylinux_2_28_x86_64"],
)
def test_wheel_that_does_not_fit_the_target_is_an_error(tmp_path, net, tag):
    data = wheel_bytes("dep", "1.0", tag=tag)
    item = (wheel_name("dep", "1.0", tag), data)
    # the same wheel is offered for both Pythons, as a download that ignored the target would
    fake = FakeTools(downloads=two_pythons(("dep", [item])))
    r = py_recipe(tmp_path, net, [("dep", "1.0", [data])])
    with pytest.raises(build.BuildError, match="dep"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)


def test_downloaded_wheel_with_a_hash_not_in_the_lock_is_an_error(tmp_path, net):
    good = pure("dep", "1.0")
    other = (good[0], wheel_bytes("dep", "1.0", body="x = 1\n"))
    fake = FakeTools(downloads=two_pythons(("dep", [other])))
    r = py_recipe(tmp_path, net, [("dep", "1.0", [good[1]])])
    with pytest.raises(build.BuildError, match="SHA-256"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)


def test_downloaded_wheel_of_another_version_is_an_error(tmp_path, net):
    wrong = pure("dep", "2.0")
    fake = FakeTools(downloads=two_pythons(("dep", [wrong])))
    r = py_recipe(tmp_path, net, [("dep", "1.0", [wrong[1]])])
    with pytest.raises(build.BuildError, match="dep"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)


def src_recipe(tmp_path, net, pins, *listed):
    return py_recipe(tmp_path, net, pins, build_from_source=tuple(listed))


def test_listed_dependency_is_built_once_from_its_pinned_source(tmp_path, net):
    sdist = ("docopt-0.6.2.tar.gz", b"source archive")
    built = pure("docopt", "0.6.2")
    fake = FakeTools(sdists={"docopt": sdist}, built={"docopt": built})
    r = src_recipe(tmp_path, net, [("docopt", "0.6.2", [sdist[1]])], "Docopt")
    got = build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    assert [p.name for p in got] == [built[0]]
    assert not any("--only-binary" in c for c in fake.calls)
    sources = [c for c in fake.calls if "--no-binary" in c]
    wheels = [c for c in fake.calls if c[3] == "wheel"]
    assert len(sources) == 1 and len(wheels) == 1
    src = sources[0]
    for flag in ("--no-deps", "--require-hashes"):
        assert flag in src
    assert src[src.index("--no-binary") + 1] == "docopt"
    assert "--platform" not in src
    assert wheels[0][:4] == [sys.executable, "-m", "pip", "wheel"] and "--no-deps" in wheels[0]
    assert wheels[0][-1].endswith("docopt-0.6.2.tar.gz")


def test_listed_dependency_is_built_once_whatever_pythons_holds(tmp_path, net, monkeypatch):
    monkeypatch.setattr(build, "PYTHONS", ("3.12", "3.13", "3.14"))
    sdist = ("docopt-0.6.2.tar.gz", b"source archive")
    fake = FakeTools(sdists={"docopt": sdist}, built={"docopt": pure("docopt", "0.6.2")})
    r = src_recipe(tmp_path, net, [("docopt", "0.6.2", [sdist[1]])], "docopt")
    build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    assert len([c for c in fake.calls if "--no-binary" in c]) == 1
    assert len([c for c in fake.calls if c[3] == "wheel"]) == 1
    assert len(fake.calls) == 2


def test_listed_name_that_is_not_in_the_lock_is_refused(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads=two_pythons(("tinytool", [tool])))
    r = src_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])], "missing-dep")
    with pytest.raises(build.BuildError, match="missing-dep"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    assert fake.calls == []


def test_build_from_source_must_be_a_list_of_names(tmp_path, net):
    folder = make_python_recipe(tmp_path / "recipes" / "tinytool", net, [])
    head = tomllib.loads((folder / "recipe.toml").read_text())
    head["build_from_source"] = "docopt"
    (folder / "recipe.toml").write_bytes(tomli_w.dumps(head).encode())
    with pytest.raises(build.BuildError, match="build_from_source"):
        build.load_recipe(folder)


def test_recipe_without_build_from_source_lists_nothing(recipe):
    assert recipe.build_from_source == []


def test_source_archive_with_a_hash_not_in_the_lock_is_an_error(tmp_path, net):
    sdist = ("docopt-0.6.2.tar.gz", b"tampered")
    fake = FakeTools(sdists={"docopt": sdist}, built={"docopt": pure("docopt", "0.6.2")})
    r = src_recipe(tmp_path, net, [("docopt", "0.6.2", [b"original"])], "docopt")
    with pytest.raises(build.BuildError, match="SHA-256"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)


def source_build_error(tmp_path, net, wheel, name="native"):
    sdist = (f"{name}-1.0.tar.gz", b"source")
    fake = FakeTools(sdists={name: sdist}, built={name: wheel})
    r = src_recipe(tmp_path, net, [(name, "1.0", [sdist[1]])], name)
    with pytest.raises(build.BuildError) as err:
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    return str(err.value)


def test_source_built_wheel_with_a_platform_name_is_an_error(tmp_path, net):
    tag = "cp313-cp313-win_amd64"
    wheel = (wheel_name("native", "1.0", tag), wheel_bytes("native", "1.0", tag=tag))
    message = source_build_error(tmp_path, net, wheel)
    assert "native" in message and "pure Python" in message


def test_source_built_wheel_that_says_it_is_not_purelib_is_an_error(tmp_path, net):
    wheel = (wheel_name("native", "1.0"), wheel_bytes("native", "1.0", purelib=False))
    message = source_build_error(tmp_path, net, wheel)
    assert "native" in message and "Root-Is-Purelib" in message


@pytest.mark.parametrize("member", ["native/_x.so", "native/_x.PYD", "native/x.dll", "native/x.dylib"])
def test_source_built_wheel_holding_a_binary_is_an_error(tmp_path, net, member):
    wheel = (wheel_name("native", "1.0"), wheel_bytes("native", "1.0", extra={member: b"\x7fELF"}))
    message = source_build_error(tmp_path, net, wheel)
    assert "native" in message and member in message


def test_source_built_wheel_without_wheel_metadata_is_an_error(tmp_path, net):
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("native/__init__.py", "")
    wheel = (wheel_name("native", "1.0"), buf.getvalue())
    assert "WHEEL" in source_build_error(tmp_path, net, wheel)


def test_failed_wheel_download_for_an_unlisted_name_stops_the_build(tmp_path, net):
    tool = pure("tinytool", "1.0")
    sdist = ("gone-3.1.tar.gz", b"x")
    fake = FakeTools(
        downloads={(py, "tinytool"): [tool] for py in build.PYTHONS},
        sdists={"gone": sdist}, built={"gone": pure("gone", "3.1")},
    )
    r = py_recipe(tmp_path, net, [("gone", "3.1", [sdist[1]]), ("tinytool", "1.0", [tool[1]])])
    with pytest.raises(build.BuildError, match="gone 3.1") as err:
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    message = str(err.value)
    assert "No matching distribution" in message and "build_from_source" in message
    assert not any("--no-binary" in c for c in fake.calls)
    assert not any(c[3] == "wheel" for c in fake.calls)


def test_a_failure_on_one_python_does_not_change_what_is_bundled(tmp_path, net):
    # 3.12 has no wheel; 3.13 has one. Nothing may fall back to source for 3.12.
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads={("3.13", "tinytool"): [tool]}, sdists={"tinytool": ("tinytool-1.0.tar.gz", b"s")},
                     built={"tinytool": tool})
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    with pytest.raises(build.BuildError, match="build_from_source"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    assert not any(c[3] == "wheel" for c in fake.calls)


# ---- how every command runs


def test_every_accepted_platform_is_passed_to_pip_newest_first(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads=two_pythons(("tinytool", [tool])))
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    cmd = fake.calls[0]
    given = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--platform"]
    assert given == list(build.PLATFORMS) and given[0] == build.PLATFORM
    assert given[:3] == ["manylinux_2_28_x86_64", "manylinux_2_27_x86_64", "manylinux_2_26_x86_64"]
    assert given[-5:] == ["manylinux2014_x86_64", "manylinux_2_12_x86_64", "manylinux2010_x86_64",
                          "manylinux_2_5_x86_64", "manylinux1_x86_64"]
    assert "manylinux_2_17_x86_64" in given and "manylinux_2_18_x86_64" in given
    assert "manylinux_2_29_x86_64" not in given


def test_commands_run_with_a_clean_environment_and_a_timeout(tmp_path, net, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://elsewhere.invalid/simple")
    monkeypatch.setenv("PIP_REQUIRE_VIRTUALENV", "1")
    monkeypatch.setenv("UV_INDEX_URL", "https://elsewhere.invalid/simple")
    monkeypatch.setenv("PWN_KEEP", "yes")
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads=two_pythons(("tinytool", [tool])), uv={"3.12": UV_LOCK, "3.13": UV_LOCK})
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    build.lock(r, run=fake)
    assert len(fake.envs) == 4
    for cmd, env, timeout in zip(fake.calls, fake.envs, fake.timeouts):
        assert timeout == build.COMMAND_TIMEOUT == 900
        assert env["PWN_KEEP"] == "yes"
        extra = {k for k in env if k.upper().startswith(("PIP_", "UV_"))}
        assert "PIP_INDEX_URL" not in env and "UV_INDEX_URL" not in env
        assert "PIP_REQUIRE_VIRTUALENV" not in env
        assert env["PIP_CONFIG_FILE"] == os.devnull
        assert env["PIP_DISABLE_PIP_VERSION_CHECK"] == "1" and env["PIP_NO_INPUT"] == "1"
        if cmd[0] == "uv":
            assert env["UV_NO_CONFIG"] == "1"
        else:
            assert "UV_NO_CONFIG" not in env
        assert extra <= {"PIP_CONFIG_FILE", "PIP_DISABLE_PIP_VERSION_CHECK", "PIP_NO_INPUT",
                         "UV_NO_CONFIG"}


@pytest.mark.parametrize("which", ["pip", "uv"])
def test_a_timeout_is_a_build_error_naming_the_command(tmp_path, net, which):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    tool = pure("tinytool", "1.0")
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    with pytest.raises(build.BuildError, match="timed out") as err:
        if which == "pip":
            build.collect_wheels(r, staged_dir(tmp_path), run=slow)
        else:
            build.lock(r, run=slow)
    assert ("pip" if which == "pip" else "uv") in str(err.value)
    assert "download" in str(err.value) or which == "uv"


def test_wheel_missing_for_only_one_python_is_an_error(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads={("3.12", "tinytool"): [tool]})
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    with pytest.raises(build.BuildError, match="tinytool 1.0"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)


def test_stage_collects_wheels_and_sets_the_python_table(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads=two_pythons(("tinytool", [tool])))
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    staged = build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net, run=fake)
    manifest = tomllib.loads((staged / "manifest.toml").read_text())
    assert manifest["python"] == {
        "versions": ["3.12", "3.13"], "lock": "wheels.lock", "wheels": "wheels"
    }
    assert (staged / "wheels" / tool[0]).read_bytes() == tool[1]
    lock = (staged / "wheels.lock").read_text()
    assert lock == f"tinytool==1.0 --hash=sha256:{digest(tool[1])}\n"
    assert r.manifest["python"] == {}


@pytest.mark.parametrize(
    "table", [{"versions": ["3.13"]}, {"lock": "other.lock"}, {"wheels": "w"}]
)
def test_manifest_python_fields_that_disagree_are_refused(tmp_path, net, table):
    manifest = {**PY_MANIFEST, "python": table}
    tool = pure("tinytool", "1.0")
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])], manifest=manifest)
    with pytest.raises(build.BuildError, match="python"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net, run=FakeTools())


def test_manifest_python_fields_that_agree_are_accepted(tmp_path, net):
    table = {"versions": ["3.12", "3.13"], "lock": "wheels.lock", "wheels": "wheels"}
    tool = pure("tinytool", "1.0")
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])], manifest={**PY_MANIFEST, "python": table})
    fake = FakeTools(downloads=two_pythons(("tinytool", [tool])))
    build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net, run=fake)


def test_tool_without_python_table_collects_nothing(recipe, tmp_path, net):
    fake = FakeTools()
    staged = build.stage(recipe, tmp_path / "w", tmp_path / "c", fetch=net, run=fake)
    assert fake.calls == [] and not (staged / "wheels").exists()
    assert not (staged / "wheels.lock").exists()


# ---- lock

UV_LOCK = (
    "click==8.1.7 \\\n    --hash=sha256:" + "a" * 64 + " \\\n    --hash=sha256:" + "b" * 64 + "\n"
    "tinytool==1.0 \\\n    --hash=sha256:" + "c" * 64 + "\n"
).encode()


def test_lock_runs_uv_for_linux_on_each_python_and_writes_the_lock(tmp_path, net):
    r = py_recipe(tmp_path, net, [], lock=False)
    fake = FakeTools(uv={"3.12": UV_LOCK.replace(b"\n", b"\r\n"), "3.13": UV_LOCK})
    path = build.lock(r, run=fake)
    assert path == r.folder / "requirements.lock"
    assert path.read_bytes() == UV_LOCK
    assert [c[0] for c in fake.calls] == ["uv", "uv"]
    first = fake.calls[0]
    assert first[:4] == ["uv", "pip", "compile", "requirements.in"]
    assert "--generate-hashes" in first and "--universal" not in first
    assert first[first.index("--python-platform") + 1] == "x86_64-unknown-linux-gnu"
    assert [c[c.index("--python-version") + 1] for c in fake.calls] == ["3.12", "3.13"]
    assert all(cwd == r.folder for cwd in fake.cwds)


def test_lock_refuses_dependencies_that_differ_between_pythons(tmp_path, net):
    r = py_recipe(tmp_path, net, [], lock=False)
    other = UV_LOCK + b"typing-extensions==4.16.0 \\\n    --hash=sha256:" + b"d" * 64 + b"\n"
    fake = FakeTools(uv={"3.12": UV_LOCK, "3.13": other})
    with pytest.raises(build.BuildError, match="typing-extensions"):
        build.lock(r, run=fake)
    assert not (r.folder / "requirements.lock").exists()


def test_lock_refuses_output_with_markers(tmp_path, net):
    r = py_recipe(tmp_path, net, [], lock=False)
    bad = b"colorama==0.4.6 ; sys_platform == 'win32' \\\n    --hash=sha256:" + b"a" * 64 + b"\n"
    with pytest.raises(build.BuildError, match="marker"):
        build.lock(r, run=FakeTools(uv={"3.12": bad, "3.13": bad}))


def test_lock_needs_requirements_in_and_a_python_tool(tmp_path, net, recipe):
    r = py_recipe(tmp_path, net, [], lock=False)
    (r.folder / "requirements.in").unlink()
    with pytest.raises(build.BuildError, match="requirements.in"):
        build.lock(r, run=FakeTools())
    with pytest.raises(build.BuildError, match="Python"):
        build.lock(recipe, run=FakeTools())


def test_lock_without_uv_is_one_fail_line(tmp_path, net, capsys):
    make_python_recipe(tmp_path / "recipes" / "tinytool", net, [], lock=False)

    def no_uv(cmd, **kw):
        raise FileNotFoundError(2, "not found")

    argv = ["lock", "tinytool", "--recipes", str(tmp_path / "recipes")]
    assert build.main(argv, run=no_uv) == 1
    err = capsys.readouterr().err
    assert err.startswith("fail ") and err.count("\n") == 1 and "install uv" in err


def test_lock_command_reports_the_file(tmp_path, net, capsys):
    make_python_recipe(tmp_path / "recipes" / "tinytool", net, [], lock=False)
    fake = FakeTools(uv={"3.12": UV_LOCK, "3.13": UV_LOCK})
    argv = ["lock", "tinytool", "--recipes", str(tmp_path / "recipes")]
    assert build.main(argv, run=fake) == 0
    assert capsys.readouterr().out.startswith("ok ")
    assert (tmp_path / "recipes" / "tinytool" / "requirements.lock").read_bytes() == UV_LOCK


def test_the_local_wheel_folder_is_not_a_command_line_option(tmp_path):
    for argv in (["build", "--find-links", str(tmp_path)], ["build", "--no-index"]):
        with pytest.raises(SystemExit):
            build.main(argv)


# ---- end to end, with a real pip and a real virtual environment


def venv_python(folder):
    return folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def test_python_tool_builds_from_local_wheels_and_installs_offline(tmp_path, net):
    local = tmp_path / "local"
    dep = make_wheel(local, "tinydep", "1.0", body='WORD = "dependency"\n')
    tool = make_wheel(
        local, "tinytool", "1.0", scripts={"tinytool": "tinytool:main"},
        body="import tinydep\n\ndef main():\n    print('tinytool', tinydep.WORD)\n",
    )
    pins = [("tinydep", "1.0", [dep.read_bytes()]), ("tinytool", "1.0", [tool.read_bytes()])]
    make_python_recipe(tmp_path / "recipes" / "tinytool", net, pins)
    recipe = build.load_recipe(tmp_path / "recipes" / "tinytool")
    zips = [
        build.build_one(recipe, tmp_path / f"out{i}", tmp_path / "c", find_links=local)
        for i in (1, 2)
    ]
    assert zips[0].read_bytes() == zips[1].read_bytes()
    build.validate(zips[0])
    with zipfile.ZipFile(zips[0]) as zf:
        assert zf.namelist() == [
            "LICENSE", "manifest.toml", "wheels.lock",
            "wheels/tinydep-1.0-py3-none-any.whl", "wheels/tinytool-1.0-py3-none-any.whl",
        ]
        unpacked = tmp_path / "unpacked"
        zf.extractall(unpacked)
    assert (unpacked / "wheels.lock").read_text().startswith("tinydep==1.0 --hash=sha256:")

    env = tmp_path / "venv"
    try:
        venv.EnvBuilder(with_pip=True).create(env)
    except (subprocess.CalledProcessError, OSError) as exc:
        pytest.skip(f"cannot create a virtual environment here ({exc})")
    python = venv_python(env)
    install = [
        str(python), "-m", "pip", "install", "--no-index", "--find-links", str(unpacked / "wheels"),
        "--only-binary", ":all:", "--require-hashes", "--disable-pip-version-check", "--no-input",
        "-r", str(unpacked / "wheels.lock"),
    ]
    done = subprocess.run(install, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    script = python.parent / ("tinytool.exe" if os.name == "nt" else "tinytool")
    out = subprocess.run([str(script)], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "tinytool dependency"
