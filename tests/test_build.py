import stat
import tomllib
import zipfile

import pytest
import tomli_w

import build
from conftest import digest, make_recipe, tar_gz, zip_bytes


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
