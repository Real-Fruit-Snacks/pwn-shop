import gzip
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
    NOTES_MANIFEST, PLAYBOOK, PLAIN_MANIFEST, PY_MANIFEST, RUNTIME_ARCHIVE, RUNTIME_MANIFEST, FakeTools, digest, lock_text,
    make_python_recipe, make_recipe, make_runtime_recipe, make_wheel,
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


# ---- a whole zip as a source (unpack = true)


def crafted_zip(entries: list[tuple]) -> bytes:
    """A zip whose entries are written exactly as given: (name, data, mode, flag bits). The
    name is set after the ZipInfo is made, so zipfile does not tidy it."""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data, mode, flags in entries:
            info = zipfile.ZipInfo("placeholder", (1980, 1, 1, 0, 0, 0))
            info.filename = name
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    out = buf.getvalue()
    if any(flags for *_, flags in entries):
        # zipfile clears the flag bits it does not use when writing, so set them in the bytes:
        # at offset 6 of each local header and offset 8 of each central directory record
        raw = bytearray(out)
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            at = raw.find(signature)
            while at != -1:
                raw[at + offset] |= max(flags for *_, flags in entries)
                at = raw.find(signature, at + 4)
        out = bytes(raw)
    return out


def plain(name, data=b"x"):
    return (name, data, stat.S_IFREG | 0o644, 0)


def unpack_recipe(tmp_path, net, data, *, to="files", url="https://e.invalid/p.zip", extra=(),
                  **row):
    net.files[url] = data
    src = [{"url": url, "size": len(data), "sha256": digest(data), "to": to, "unpack": True,
            **row}, *extra]
    return build.load_recipe(make_recipe(tmp_path / "r" / "kerbrute", net, sources=src))


def stage_it(recipe, tmp_path, net):
    return build.stage(recipe, tmp_path / "w", tmp_path / "c", fetch=net)


def test_unpack_puts_every_file_under_to(tmp_path, net):
    data = zip_bytes({"playbooks/a/x.md": b"X", "playbooks/b.md": b"B", "LICENSE": b"L"})
    staged = stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)
    assert (staged / "files" / "playbooks" / "a" / "x.md").read_bytes() == b"X"
    assert (staged / "files" / "playbooks" / "b.md").read_bytes() == b"B"
    assert (staged / "files" / "LICENSE").read_bytes() == b"L"


def test_unpack_into_a_subfolder(tmp_path, net):
    data = zip_bytes({"x.md": b"X"})
    staged = stage_it(unpack_recipe(tmp_path, net, data, to="files/notes"), tmp_path, net)
    assert (staged / "files" / "notes" / "x.md").read_bytes() == b"X"


def test_unpack_makes_directory_entries_and_copies_files(tmp_path, net):
    data = crafted_zip([("d/", b"", stat.S_IFDIR | 0o755, 0), plain("d/f.md", b"F")])
    staged = stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)
    assert (staged / "files" / "d" / "f.md").read_bytes() == b"F"


def test_unpack_takes_no_member(tmp_path, net):
    with pytest.raises(build.BuildError, match="takes no member"):
        unpack_recipe(tmp_path, net, zip_bytes({"a": b"1"}), member="a")


def test_unpack_needs_a_zip(tmp_path, net):
    with pytest.raises(build.BuildError, match="must be a zip"):
        unpack_recipe(tmp_path, net, tar_gz({"a": b"1"}), url="https://e.invalid/p.tar.gz")


@pytest.mark.parametrize("value", ["yes", 1])
def test_unpack_is_true_or_false(tmp_path, net, value):
    with pytest.raises(build.BuildError, match="unpack: must be true or false"):
        unpack_recipe(tmp_path, net, zip_bytes({"a": b"1"}), unpack=value)


@pytest.mark.parametrize("to", ["other", "/files", "files/../x", "files\\x"])
def test_unpack_to_must_be_files_or_under_it(tmp_path, net, to):
    with pytest.raises(build.BuildError):
        unpack_recipe(tmp_path, net, zip_bytes({"a": b"1"}), to=to)


def test_a_file_source_still_may_not_go_to_files_itself(tmp_path, net):
    src = [{"url": "https://e.invalid/x", "size": 1, "sha256": "0" * 64, "to": "files"}]
    with pytest.raises(build.BuildError):
        build.load_recipe(make_recipe(tmp_path / "r" / "kerbrute", net, sources=src))


@pytest.mark.parametrize("name", ["../x", "/etc/x", "C:x", "a\\b", "a/./b", "a//b", "a/../../x"])
def test_unpack_refuses_unsafe_paths(tmp_path, net, name):
    data = crafted_zip([plain("ok.md"), plain(name)])
    with pytest.raises(build.BuildError, match="would write outside files"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)
    assert not (tmp_path / "x").exists()
    assert not (tmp_path / "w" / "x").exists()


def test_unpack_refuses_a_link(tmp_path, net):
    data = crafted_zip([("l", b"/etc/passwd", stat.S_IFLNK | 0o777, 0)])
    with pytest.raises(build.BuildError, match="'l' is a link or special file"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


def test_unpack_refuses_an_encrypted_entry(tmp_path, net):
    data = crafted_zip([("e.md", b"x", stat.S_IFREG | 0o644, 0x1)])
    with pytest.raises(build.BuildError, match="'e.md' is encrypted"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


def test_unpack_refuses_case_twins(tmp_path, net):
    data = crafted_zip([plain("playbooks/A.md"), plain("playbooks/a.md")])
    with pytest.raises(build.BuildError, match="more than one source.*without case"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


def test_unpack_refuses_landing_on_another_source(tmp_path, net):
    licence = b"other licence"
    net.files["https://e.invalid/LICENSE"] = licence
    other = {"url": "https://e.invalid/LICENSE", "size": len(licence), "sha256": digest(licence),
             "to": "files/LICENSE"}
    data = zip_bytes({"LICENSE": b"L", "x.md": b"X"})
    with pytest.raises(build.BuildError, match="files/LICENSE is written by more than one source"):
        stage_it(unpack_recipe(tmp_path, net, data, extra=[other]), tmp_path, net)


def test_a_file_source_may_not_land_inside_an_unpacked_one(tmp_path, net):
    one = b"one"
    net.files["https://e.invalid/one"] = one
    first = {"url": "https://e.invalid/one", "size": len(one), "sha256": digest(one),
             "to": "files/x.md"}
    data = zip_bytes({"X.md": b"X"})
    recipe = build.load_recipe(make_recipe(
        tmp_path / "r" / "kerbrute", net,
        sources=[first, {"url": "https://e.invalid/p.zip", "size": len(data),
                         "sha256": digest(data), "to": "files", "unpack": True}],
    ))
    net.files["https://e.invalid/p.zip"] = data
    with pytest.raises(build.BuildError, match="files/X.md is written by more than one source"):
        stage_it(recipe, tmp_path, net)


def test_unpack_refuses_one_folder_in_two_cases(tmp_path, net):
    data = crafted_zip([plain("Playbooks/a.md"), plain("playbooks/b.md")])
    with pytest.raises(build.BuildError,
                       match="files/playbooks and files/Playbooks name one folder in different cases"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


def test_unpack_refuses_a_folder_in_another_case_than_a_file_source(tmp_path, net):
    one = b"one"
    net.files["https://e.invalid/one"] = one
    first = {"url": "https://e.invalid/one", "size": len(one), "sha256": digest(one),
             "to": "files/Tools/x"}
    data = zip_bytes({"tools/y": b"Y"})
    with pytest.raises(build.BuildError, match="name one folder in different cases"):
        stage_it(unpack_recipe(tmp_path, net, data, extra=[first]), tmp_path, net)


@pytest.mark.parametrize("entries", [
    [plain("a"), ("A/", b"", stat.S_IFDIR | 0o755, 0)],
    [("A/", b"", stat.S_IFDIR | 0o755, 0), plain("a")],
    [plain("a"), plain("A/b")],
    [plain("A/b"), plain("a")],
])
def test_unpack_refuses_a_file_and_a_folder_of_one_name(tmp_path, net, entries):
    with pytest.raises(build.BuildError, match="is both a file and a folder"):
        stage_it(unpack_recipe(tmp_path, net, crafted_zip(entries)), tmp_path, net)


@pytest.mark.parametrize("name", ["a.md.", "a.md ", "dir./a.md", "dir /a.md"])
def test_unpack_refuses_a_part_ending_in_a_dot_or_a_space(tmp_path, net, name):
    data = crafted_zip([plain("a.md"), plain(name)])
    with pytest.raises(build.BuildError, match="ends in a dot or a space"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


@pytest.mark.parametrize("name", ["x\x01.md", "x\x7f.md", "a\x00b.md"])
def test_unpack_refuses_a_control_character(tmp_path, net, name):
    data = crafted_zip([plain(name)])
    with pytest.raises(build.BuildError, match="control character"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


@pytest.mark.parametrize(("name", "fault"), [
    ("a" * 256 + ".md", "with a part longer than 255 bytes"),
    ("/".join(["d"] * 64) + "/x.md", "with more than 64 parts"),
    ("/".join(["d" * 200] * 21) + "/x.md", "longer than 4096 bytes"),
])
def test_unpack_refuses_what_pwn_broker_finds_too_long(tmp_path, net, name, fault):
    data = crafted_zip([plain(name)])
    with pytest.raises(build.BuildError, match=fault):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


def test_unpack_refuses_too_many_entries(tmp_path, net, monkeypatch):
    monkeypatch.setattr(build, "MAX_MEMBERS", 2)
    data = zip_bytes({"a": b"1", "b": b"2", "c": b"3"})
    with pytest.raises(build.BuildError, match="3 entries; the limit is 2"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


def test_unpack_refuses_too_many_bytes(tmp_path, net, monkeypatch):
    monkeypatch.setattr(build, "MAX_UNPACKED_BYTES", 15)
    data = zip_bytes({"a": b"1" * 10, "b": b"2" * 10})
    with pytest.raises(build.BuildError, match="unpacks to more than 15 bytes"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


def test_unpack_counts_the_bytes_it_copies_not_the_header(tmp_path, net, monkeypatch):
    monkeypatch.setattr(build, "MAX_UNPACKED_BYTES", 15)
    data = zip_bytes({"a": b"1" * 20})
    infolist = zipfile.ZipFile.infolist

    def small_headers(self):
        infos = infolist(self)
        for info in infos:
            info.file_size = 1
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", small_headers)
    # zipfile reads no more than a header states, and then the CRC fails; either way it stops
    with pytest.raises(build.BuildError, match="unpacks to more than 15 bytes|Bad CRC-32"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


def test_unpack_refuses_an_empty_zip(tmp_path, net):
    data = crafted_zip([("d/", b"", stat.S_IFDIR | 0o755, 0)])
    with pytest.raises(build.BuildError, match="holds no files"):
        stage_it(unpack_recipe(tmp_path, net, data), tmp_path, net)


def test_unpacked_package_validates_and_rebuilds_the_same(tmp_path, net):
    data = zip_bytes({"kerbrute": b"#!/bin/sh\n", "doc/readme.md": b"R"})
    recipe = unpack_recipe(tmp_path, net, data)
    first = build.build_one(recipe, tmp_path / "out1", tmp_path / "c", fetch_fn=net)
    second = build.build_one(recipe, tmp_path / "out2", tmp_path / "c", fetch_fn=net)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as zf:
        assert "files/doc/readme.md" in zf.namelist()


def test_the_one_file_in_a_plain_gz_is_a_member(tmp_path, net):
    # chisel publishes its Linux binary this way: one file, gzipped on its own
    data = gzip.compress(b"BINARY", mtime=0)
    r = one_member_recipe(tmp_path, net, "https://e.invalid/dl/tool_1.2_linux_amd64.gz", data, "tool_1.2_linux_amd64")
    staged = build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)
    assert (staged / "files" / "a").read_bytes() == b"BINARY"


@pytest.mark.parametrize("member", ["tool", "tool_1.2_linux_amd64.gz", "other_1.2_linux_amd64"])
def test_a_plain_gz_holds_one_file_named_as_the_url_names_it(tmp_path, net, member):
    data = gzip.compress(b"BINARY", mtime=0)
    r = one_member_recipe(tmp_path, net, "https://e.invalid/dl/tool_1.2_linux_amd64.gz", data, member)
    with pytest.raises(build.BuildError, match="holds one file, 'tool_1.2_linux_amd64'"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


@pytest.mark.parametrize("data", [b"not gzip at all", gzip.compress(b"BINARY", mtime=0)[:-6]])
def test_a_gz_that_cannot_be_read_is_a_build_error(tmp_path, net, data):
    r = one_member_recipe(tmp_path, net, "https://e.invalid/tool.gz", data, "tool")
    with pytest.raises(build.BuildError, match="cannot read the archive"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


def test_a_gz_that_unpacks_to_too_much_is_refused(tmp_path, net, monkeypatch):
    monkeypatch.setattr(build, "MAX_UNPACKED_BYTES", 1000)
    data = gzip.compress(bytes(100_000), mtime=0)
    r = one_member_recipe(tmp_path, net, "https://e.invalid/tool.gz", data, "tool")
    with pytest.raises(build.BuildError, match="larger than 1000 bytes"):
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


GOOD_LOCK = b"a==1.0 --hash=sha256:" + b"a" * 64 + b"\n"


def test_validate_accepts_python_package(tmp_path):
    files = {"wheels.lock": GOOD_LOCK, "wheels/a.whl": b"w"}
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
        build.validate(pkg(tmp_path, PY_ENTRY, {"wheels.lock": GOOD_LOCK}))


@pytest.mark.parametrize(
    ("lock", "message"),
    [(b"", "empty"), (GOOD_LOCK.rstrip(b"\n"), "line break")],
)
def test_validate_refuses_an_empty_lock_and_one_with_no_final_line_break(tmp_path, lock, message):
    # pwn-broker refuses both at install, so neither may be published
    files = {"wheels.lock": lock, "wheels/a.whl": b"w"}
    with pytest.raises(build.BuildError, match=message):
        build.validate(pkg(tmp_path, PY_ENTRY, files))


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


# what pwn-broker's toolpkg refuses when it unpacks a package, so a zip built here never gets to it
@pytest.mark.parametrize(
    "name",
    [
        "files/pb/a:b.md",
        "files/a\x01b",
        "files/a\nb",
        "files/a\x7fb",
        "files/" + "/".join(["d"] * 70) + "/x",
        "files/" + "n" * 256,
        "files/" + "/".join(["p" * 200] * 21),
    ],
)
def test_validate_refuses_a_name_pwn_broker_refuses(tmp_path, name):
    with pytest.raises(build.BuildError):
        build.validate(pkg(tmp_path, files={name: b"x"}))


def test_validate_refuses_an_entry_that_does_not_hold_the_bytes_it_declares(tmp_path):
    path = pkg(tmp_path, files={"files/extra": b"12345"})
    data = bytearray(path.read_bytes())
    # the central directory's record of files/extra: its compressed size, 20 bytes in
    record = data.rindex(b"PK\x01\x02", 0, data.rindex(b"files/extra"))
    data[record + 20 : record + 24] = (6).to_bytes(4, "little")
    path.write_bytes(bytes(data))
    with pytest.raises(build.BuildError, match="does not hold the bytes it declares"):
        build.validate(path)


def test_validate_counts_the_folders_a_package_would_make(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "MAX_MEMBERS", 5)
    with pytest.raises(build.BuildError, match="files and folders"):
        build.validate(pkg(tmp_path, files={"files/a/b/c/d": b"x"}))


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


# ---- recipe fields, atomic build, version order, members


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
    assert not hasattr(build, "PYTHONS")
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
        ("cp314-cp314-manylinux_2_28_x86_64", "3.14", True),
        ("cp314-cp314-manylinux_2_28_x86_64", "3.13", False),
        ("cp313-cp313-manylinux_2_28_x86_64", "3.14", False),
        ("cp314-cp314t-manylinux_2_28_x86_64", "3.14", False),
        ("cp39-abi3-manylinux2014_x86_64", "3.14", True),
        ("cp314-abi3-manylinux2014_x86_64", "3.13", False),
        ("py314-none-any", "3.14", True),
        ("py314-none-any", "3.13", False),
        ("py3-none-any", "3.14", True),
    ],
)
def test_wheel_tags_that_fit(tag, python, fits):
    assert build._wheel_fits(f"x-1.0-{tag}.whl", python) is fits


SERIES = "3.13"


def offered(*files):
    """What pip download finds for the runtime's series, by distribution."""
    return {(SERIES, dist): list(items) for dist, items in files}


def test_collect_wheels_runs_pip_for_the_runtimes_series_and_bundles_each_pin(tmp_path, net):
    t = "cp313-cp313-manylinux_2_28_x86_64"
    dep = (wheel_name("dep", "1.0", t), wheel_bytes("dep", "1.0", tag=t))
    tool = pure("tinytool", "1.0")
    pins = [("dep", "1.0", [dep[1]]), ("tinytool", "1.0", [tool[1]])]
    fake = FakeTools(downloads=offered(("dep", [dep]), ("tinytool", [tool])))
    r = py_recipe(tmp_path, net, pins)
    staged = staged_dir(tmp_path)
    got = build.collect_wheels(r, staged, run=fake)
    assert [p.name for p in got] == sorted([dep[0], tool[0]])
    assert all(p.parent == staged / "wheels" for p in got)
    assert (staged / "wheels" / tool[0]).read_bytes() == tool[1]
    downloads = [c for c in fake.calls if c[3] == "download"]
    assert len(downloads) == 2
    first = downloads[0]
    assert first[:4] == [sys.executable, "-m", "pip", "download"]
    for flag in ("--no-deps", "--require-hashes", "--only-binary", "--implementation"):
        assert flag in first
    assert first[first.index("--only-binary") + 1] == ":all:"
    assert first[first.index("--platform") + 1] == "manylinux_2_28_x86_64"
    assert first[first.index("--implementation") + 1] == "cp"
    versions = [c[c.index("--python-version") + 1] for c in downloads]
    assert versions == ["3.13", "3.13"]
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
    fake = Spy(downloads=offered(("tinytool", [tool]), ("dep", [dep])))
    build.collect_wheels(py_recipe(tmp_path, net, pins), staged_dir(tmp_path), run=fake)
    assert len(seen) == 2
    assert all(s.count("==") == 1 and s.endswith("\n") for s in seen)
    assert sum(s.startswith("dep==2.0 --hash=sha256:") for s in seen) == 1


def test_collect_wheels_local_folder_adds_no_index(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads=offered(("tinytool", [tool])))
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
    fake = FakeTools(downloads=offered(("tinytool", [tool])))
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
    ["cp313-cp313-win_amd64", "cp311-cp311-manylinux_2_28_x86_64",
     "cp313-cp313-musllinux_1_2_x86_64", "cp312-cp312-manylinux_2_28_x86_64"],
)
def test_wheel_that_does_not_fit_the_target_is_an_error(tmp_path, net, tag):
    data = wheel_bytes("dep", "1.0", tag=tag)
    item = (wheel_name("dep", "1.0", tag), data)
    # offered as a download that ignored the target would offer it
    fake = FakeTools(downloads=offered(("dep", [item])))
    r = py_recipe(tmp_path, net, [("dep", "1.0", [data])])
    with pytest.raises(build.BuildError, match="dep"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)


def test_downloaded_wheel_with_a_hash_not_in_the_lock_is_an_error(tmp_path, net):
    good = pure("dep", "1.0")
    other = (good[0], wheel_bytes("dep", "1.0", body="x = 1\n"))
    fake = FakeTools(downloads=offered(("dep", [other])))
    r = py_recipe(tmp_path, net, [("dep", "1.0", [good[1]])])
    with pytest.raises(build.BuildError, match="SHA-256"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)


def test_downloaded_wheel_of_another_version_is_an_error(tmp_path, net):
    wrong = pure("dep", "2.0")
    fake = FakeTools(downloads=offered(("dep", [wrong])))
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


def direct_source_recipe(tmp_path, net, raw=b"source archive"):
    sha = digest(raw)
    source = build.Source(
        "https://example.invalid/docopt.zip", len(raw), sha, "files/docopt.archive"
    )
    r = src_recipe(tmp_path, net, [("docopt", "0.6.2", [raw])], "docopt")
    r.wheel_sources = [build.WheelSource("docopt", "0.6.2", source, {"BUILD_FLAG": "1"})]
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / sha).write_bytes(raw)
    return r, cache, sha


def test_direct_source_archive_is_built_with_its_recipe_environment(tmp_path, net):
    r, cache, sha = direct_source_recipe(tmp_path, net)
    built = pure("docopt", "0.6.2")
    fake = FakeTools(built={"source": built})
    got = build.collect_wheels(r, staged_dir(tmp_path), run=fake, cache=cache)
    assert [p.name for p in got] == [built[0]]
    assert len(fake.calls) == 1 and fake.calls[0][3] == "wheel"
    assert fake.envs[0]["BUILD_FLAG"] == "1"


def test_direct_requirements_are_pinned_and_record_is_updated(tmp_path, net):
    import base64

    r, _cache, _sha = direct_source_recipe(tmp_path, net)
    info = "docopt-0.6.2.dist-info"
    metadata = (
        b"Metadata-Version: 2.1\nName: docopt\nVersion: 0.6.2\n"
        b"Requires-Dist: docopt @ git+https://example.invalid/docopt\n"
        b"Requires-Dist: requests>=2\n"
    )
    path = make_wheel(
        tmp_path, "docopt", "0.6.2", extra={f"{info}/METADATA": metadata}
    )
    build._pin_wheel_requirements(path, r.wheel_sources)
    with zipfile.ZipFile(path) as zf:
        changed = zf.read(f"{info}/METADATA")
        record = zf.read(f"{info}/RECORD").decode()
    assert b"Requires-Dist: docopt==0.6.2\n" in changed
    assert b"Requires-Dist: requests>=2\n" in changed
    digest = base64.urlsafe_b64encode(hashlib.sha256(changed).digest()).rstrip(b"=").decode()
    assert f"{info}/METADATA,sha256={digest},{len(changed)}" in record


def test_direct_archive_lock_becomes_an_ordinary_hashed_pin(tmp_path, net):
    r, _cache, sha = direct_source_recipe(tmp_path, net)
    text = (
        f"docopt @ {r.wheel_sources[0].source.url} \\\n    --hash=sha256:{sha}\n"
        f"other==1.0 {HASH_A}\n"
    )
    locked = build._pin_wheel_sources(text, r)
    assert locked == f"docopt==0.6.2 --hash=sha256:{sha}\nother==1.0 {HASH_A}\n"
    assert build.read_requirements_lock(
        r.folder / build.REQUIREMENTS_LOCK, r.name
    )[0].version == "0.6.2"


def test_direct_archive_lock_with_another_url_is_refused(tmp_path, net):
    r, _cache, sha = direct_source_recipe(tmp_path, net)
    with pytest.raises(build.BuildError, match="different archive"):
        build._pin_wheel_sources(
            f"docopt @ https://example.invalid/other.zip --hash=sha256:{sha}\n", r
        )


def test_lock_uses_overrides_and_source_environment(tmp_path, net):
    r, _cache, sha = direct_source_recipe(tmp_path, net)
    (r.folder / build.OVERRIDES_IN).write_text("docopt @ https://example.invalid/docopt.zip\n")
    direct = f"docopt @ {r.wheel_sources[0].source.url} --hash=sha256:{sha}\n".encode()
    fake = FakeTools(uv={SERIES: direct})
    build.lock(r, run=fake)
    assert "--overrides" in fake.calls[0]
    assert fake.envs[0]["BUILD_FLAG"] == "1"
    assert (r.folder / build.REQUIREMENTS_LOCK).read_text() == (
        f"docopt==0.6.2 --hash=sha256:{sha}\n"
    )


def test_listed_name_that_is_not_in_the_lock_is_refused(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads=offered(("tinytool", [tool])))
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


@pytest.mark.parametrize("tag", ["py2-none-any", "py314-none-any"])
def test_source_built_wheel_for_another_python_is_an_error(tmp_path, net, tag):
    # pure Python, but not for the runtime's 3.13
    wheel = (wheel_name("native", "1.0", tag), wheel_bytes("native", "1.0", tag=tag))
    message = source_build_error(tmp_path, net, wheel)
    assert tag in message and "does not fit CPython 3.13" in message


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
        downloads=offered(("tinytool", [tool])),
        sdists={"gone": sdist}, built={"gone": pure("gone", "3.1")},
    )
    r = py_recipe(tmp_path, net, [("gone", "3.1", [sdist[1]]), ("tinytool", "1.0", [tool[1]])])
    with pytest.raises(build.BuildError, match="gone 3.1") as err:
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    message = str(err.value)
    assert "No matching distribution" in message and "build_from_source" in message
    assert not any("--no-binary" in c for c in fake.calls)
    assert not any(c[3] == "wheel" for c in fake.calls)


def test_dotnet_build_uses_the_pinned_sdk_and_copies_only_the_output(tmp_path):
    source_bytes = zip_bytes({"Rubeus-test/Rubeus/Rubeus.csproj": b"project"})
    reference_bytes = zip_bytes({"build/.NETFramework/v4.0/mscorlib.dll": b"reference"})
    source_sha, reference_sha = digest(source_bytes), digest(reference_bytes)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / source_sha).write_bytes(source_bytes)
    (cache / reference_sha).write_bytes(reference_bytes)
    spec = build.DotnetBuild(
        "9.0.320",
        build.Source("https://example.invalid/rubeus.zip", len(source_bytes), source_sha, "files/Rubeus.exe"),
        build.Source("https://example.invalid/net40.zip", len(reference_bytes), reference_sha, "files/ref"),
        "Rubeus/Rubeus.csproj", "Rubeus/bin/Release/Rubeus.exe", "build/.NETFramework/v4.0",
    )

    class Dotnet:
        def __init__(self):
            self.calls = []

        def __call__(self, cmd, **kwargs):
            self.calls.append(cmd)
            if cmd[1] == "--version":
                return subprocess.CompletedProcess(cmd, 0, "9.0.320\n", "")
            project = Path(cmd[2])
            output = project.parent / "bin/Release/Rubeus.exe"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"compiled")
            return subprocess.CompletedProcess(cmd, 0, "built\n", "")

    fake = Dotnet()
    root = tmp_path / "root"
    root.mkdir()
    build._build_dotnet(spec, root, cache, run=fake)
    assert (root / "files/Rubeus.exe").read_bytes() == b"compiled"
    assert [p.name for p in root.rglob("*") if p.is_file()] == ["Rubeus.exe"]
    assert "-p:Deterministic=true" in fake.calls[1]
    assert Path(fake.calls[1][2]).as_posix().endswith("Rubeus/Rubeus.csproj")


# ---- how every command runs


def test_every_accepted_platform_is_passed_to_pip_newest_first(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads=offered(("tinytool", [tool])))
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
    fake = FakeTools(downloads=offered(("tinytool", [tool])), uv={SERIES: UV_LOCK})
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    build.lock(r, run=fake)
    assert [c[0] for c in fake.calls] == [sys.executable, "uv"]
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


def test_a_missing_wheel_is_an_error_naming_the_series(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads={("3.12", "tinytool"): [tool]})
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    with pytest.raises(build.BuildError, match="no wheel could be downloaded for Python 3.13"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)


def test_stage_collects_wheels_and_sets_the_python_table(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads=offered(("tinytool", [tool])))
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    staged = build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net, run=fake)
    manifest = tomllib.loads((staged / "manifest.toml").read_text())
    assert manifest["python"] == {"lock": "wheels.lock", "wheels": "wheels"}
    assert manifest["schema"] == 2 and manifest["requires"] == ["python-3.13"]
    assert (staged / "wheels" / tool[0]).read_bytes() == tool[1]
    lock = (staged / "wheels.lock").read_text()
    assert lock == f"tinytool==1.0 --hash=sha256:{digest(tool[1])}\n"
    assert r.manifest["python"] == {}


@pytest.mark.parametrize(
    "table",
    [{"versions": ["3.13"]}, {"versions": []}, {"lock": "other.lock"}, {"wheels": "w"}],
)
def test_manifest_python_fields_that_disagree_are_refused(tmp_path, net, table):
    manifest = {**PY_MANIFEST, "python": table}
    tool = pure("tinytool", "1.0")
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])], manifest=manifest)
    with pytest.raises(build.BuildError, match="python"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net, run=FakeTools())


def test_manifest_python_fields_that_agree_are_accepted(tmp_path, net):
    table = {"lock": "wheels.lock", "wheels": "wheels"}
    tool = pure("tinytool", "1.0")
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])], manifest={**PY_MANIFEST, "python": table})
    fake = FakeTools(downloads=offered(("tinytool", [tool])))
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


def test_lock_runs_uv_for_linux_on_the_runtimes_series_and_writes_the_lock(tmp_path, net):
    r = py_recipe(tmp_path, net, [], lock=False)
    fake = FakeTools(uv={SERIES: UV_LOCK.replace(b"\n", b"\r\n")})
    path = build.lock(r, run=fake)
    assert path == r.folder / "requirements.lock"
    assert path.read_bytes() == UV_LOCK
    assert [c[0] for c in fake.calls] == ["uv"]
    first = fake.calls[0]
    assert first[:4] == ["uv", "pip", "compile", "requirements.in"]
    assert "--generate-hashes" in first and "--universal" not in first
    assert first[first.index("--python-platform") + 1] == "x86_64-unknown-linux-gnu"
    assert [c[c.index("--python-version") + 1] for c in fake.calls] == ["3.13"]
    assert all(cwd == r.folder for cwd in fake.cwds)


def test_lock_refuses_output_with_markers(tmp_path, net):
    r = py_recipe(tmp_path, net, [], lock=False)
    bad = b"colorama==0.4.6 ; sys_platform == 'win32' \\\n    --hash=sha256:" + b"a" * 64 + b"\n"
    with pytest.raises(build.BuildError, match="marker"):
        build.lock(r, run=FakeTools(uv={SERIES: bad}))
    assert not (r.folder / "requirements.lock").exists()


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
    fake = FakeTools(uv={SERIES: UV_LOCK})
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


# ---- upstream failures have their own class and exit status


def test_fetch_and_pin_failures_are_their_own_error_class():
    assert issubclass(build.FetchError, build.BuildError)
    assert build.FetchError is not build.BuildError


def test_a_wrong_size_or_hash_is_a_fetch_error(tmp_path, net):
    s = src_for(net, b"abc")
    s.size = 4
    with pytest.raises(build.FetchError, match="size"):
        build.fetch_source(s, tmp_path / "c", fetch=net)
    s = src_for(net, b"abc")
    s.sha256 = "0" * 64
    with pytest.raises(build.FetchError, match="SHA-256"):
        build.fetch_source(s, tmp_path / "c", fetch=net)


def test_a_failed_download_is_a_fetch_error(tmp_path):
    def gone(url, path, limit):
        raise OSError("404 not found")

    s = build.Source("https://e.invalid/a", 3, "0" * 64, "files/a")
    with pytest.raises(build.FetchError, match="cannot download"):
        build.fetch_source(s, tmp_path / "c", fetch=gone)


def test_a_download_over_its_size_is_a_fetch_error(tmp_path, monkeypatch):
    class Response:
        def __init__(self):
            self.chunks = [b"abcd"]

        def read(self, n):
            return self.chunks.pop() if self.chunks else b""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(build.urllib.request, "build_opener", lambda *handlers: Opener())
    with pytest.raises(build.FetchError, match="more than"):
        build.fetch("https://e.invalid/a", tmp_path / "a", 3)


def test_a_refused_scheme_is_not_a_fetch_error(tmp_path):
    with pytest.raises(build.BuildError) as err:
        build.fetch("http://e.invalid/a", tmp_path / "a", 3)
    assert not isinstance(err.value, build.FetchError)


def test_a_missing_pinned_wheel_or_a_changed_one_is_a_fetch_error(tmp_path, net):
    good = pure("dep", "1.0")
    r = py_recipe(tmp_path, net, [("dep", "1.0", [good[1]])])
    with pytest.raises(build.FetchError, match="No matching distribution"):
        build.collect_wheels(r, staged_dir(tmp_path), run=FakeTools())
    other = (good[0], wheel_bytes("dep", "1.0", body="x = 1\n"))
    fake = FakeTools(downloads=offered(("dep", [other])))
    with pytest.raises(build.FetchError, match="SHA-256"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)


def test_a_wheel_of_another_version_is_not_a_fetch_error(tmp_path, net):
    wrong = pure("dep", "2.0")
    fake = FakeTools(downloads=offered(("dep", [wrong])))
    r = py_recipe(tmp_path, net, [("dep", "1.0", [wrong[1]])])
    with pytest.raises(build.BuildError) as err:
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)
    assert not isinstance(err.value, build.FetchError)


def test_pip_missing_says_so_and_does_not_blame_the_recipe(tmp_path, net):
    def no_pip(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "python: No module named pip")

    tool = pure("tinytool", "1.0")
    r = py_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    with pytest.raises(build.BuildError) as err:
        build.collect_wheels(r, staged_dir(tmp_path), run=no_pip)
    text = str(err.value)
    assert "pip is not installed" in text
    assert "build_from_source" not in text
    assert not isinstance(err.value, build.FetchError)


def test_main_exits_3_when_an_upstream_file_cannot_be_fetched(tmp_path, net, monkeypatch, capsys):
    recipes = tmp_path / "recipes"
    make_recipe(recipes / "kerbrute", net)
    net.files["https://example.invalid/kerbrute"] = b"changed upstream"
    monkeypatch.setattr(build, "fetch", net)
    args = ["build", "kerbrute", "--recipes", str(recipes), "--out", str(tmp_path / "o"),
            "--cache", str(tmp_path / "c")]
    assert build.main(args) == 3
    err = capsys.readouterr().err
    assert err.startswith("fail ") and err.count("\n") == 1


def test_main_exits_1_for_any_other_build_error(tmp_path, net, monkeypatch, capsys):
    recipes = tmp_path / "recipes"
    make_recipe(recipes / "kerbrute", net, licence=False)
    monkeypatch.setattr(build, "fetch", net)
    args = ["build", "--recipes", str(recipes), "--out", str(tmp_path / "o"),
            "--cache", str(tmp_path / "c")]
    assert build.main(args) == 1
    assert capsys.readouterr().err.startswith("fail ")


# ---- a built zip is readable by others


def test_the_zip_is_made_readable_before_it_is_moved_into_place(tmp_path, net, monkeypatch):
    calls = []
    real = os.chmod

    def spy(path, mode):
        calls.append((Path(path).name, mode))
        real(path, mode)

    monkeypatch.setattr(os, "chmod", spy)
    recipe = build.load_recipe(make_recipe(tmp_path / "recipes" / "kerbrute", net))
    build.build_one(recipe, tmp_path / "dist", tmp_path / "c", fetch_fn=net)
    assert any(name.startswith(".build-") and mode == 0o644 for name, mode in calls)


@pytest.mark.skipif(sys.platform == "win32", reason="file modes need a POSIX file system")
def test_the_built_zip_is_not_left_owner_only(tmp_path, net):
    recipe = build.load_recipe(make_recipe(tmp_path / "recipes" / "kerbrute", net))
    final = build.build_one(recipe, tmp_path / "dist", tmp_path / "c", fetch_fn=net)
    assert stat.S_IMODE(final.stat().st_mode) == 0o644


# ---- manifest rules pwn-broker applies at install

H = "--hash=sha256:" + "a" * 64


def lock_case(tmp_path, lock):
    return pkg(tmp_path, PY_ENTRY, {"wheels.lock": lock, "wheels/a": b"w"}, drop=("files/kerbrute",))


def test_validate_accepts_the_shape_write_wheels_lock_makes(tmp_path):
    lock = f"a-b==1.0 {H} {H}\nc.d==2!1.0+local_1 {H}\n".encode()
    build.validate(lock_case(tmp_path, lock))


@pytest.mark.parametrize(
    "lock",
    [
        f"a==1.0 {H}\n\nb==1.0 {H}\n".encode(),
        f"a==1.0 {H}\n\n".encode(),
        f"# comment\na==1.0 {H}\n".encode(),
        f"a==1.0 {H}\r\nb==1.0 {H}\r\n".encode(),
        b"a==1.0\n",
        f"a==1.0 --hash=sha256:{'A' * 64}\n".encode(),
        f"a==1.0 --hash=sha256:{'a' * 63}\n".encode(),
        f"a>=1.0 {H}\n".encode(),
        f"-a==1.0 {H}\n".encode(),
        f"a==1.0  {H}\n".encode(),
        f"a==1.0 {H} \n".encode(),
        f"a==1.0 {H} \\\n  {H}\n".encode(),
        f"a==1.0 ; python_version > '3' {H}\n".encode(),
        b"\xff\xfe\n",
        b"\n",
    ],
)
def test_validate_refuses_a_bad_wheels_lock(tmp_path, lock):
    with pytest.raises(build.BuildError, match="wheels.lock"):
        build.validate(lock_case(tmp_path, lock))


def test_validate_refuses_a_wheels_lock_over_1_mib(tmp_path):
    line = f"a==1.0 {H}\n"
    big = (line * (1024 * 1024 // len(line) + 1)).encode()
    assert len(big) > 1024 * 1024
    with pytest.raises(build.BuildError, match="too large"):
        build.validate(lock_case(tmp_path, big))


def test_validate_refuses_a_wrong_arch(tmp_path):
    with pytest.raises(build.BuildError, match="x86_64"):
        build.validate(pkg(tmp_path, {"arch": "arm64"}))


def test_validate_refuses_two_uploads_with_one_name(tmp_path):
    rows = [{"path": "files/kerbrute", "as": "a"}, {"path": "files/other", "as": "a"}]
    with pytest.raises(build.BuildError, match="more than once"):
        build.validate(pkg(tmp_path, {"kind": "both", "upload": rows}, {"files/other": b"y"}))


def command_with(**extra):
    return {"command": [{"name": "k", "type": "binary", "path": "files/kerbrute", **extra}]}


@pytest.mark.parametrize("name", ["1x", "a-b", "a b", "", "x\n"])
def test_validate_refuses_a_bad_env_name(tmp_path, name):
    with pytest.raises(build.BuildError, match="env"):
        build.validate(pkg(tmp_path, command_with(env={name: "v"})))


@pytest.mark.parametrize("name", ["data", "box", "run", "pid", "start"])
def test_validate_refuses_a_reserved_env_name(tmp_path, name):
    with pytest.raises(build.BuildError, match="reserved"):
        build.validate(pkg(tmp_path, command_with(env={name: "v"})))


def test_validate_accepts_an_env_name_and_value(tmp_path):
    env = {"_A1": "{data}/x", "DATA": "1"}
    build.validate(pkg(tmp_path, command_with(env=env, args=["-d", "{data}"])))


@pytest.mark.parametrize("bad", ["a\nb", "a\0b", "\n"])
def test_validate_refuses_a_newline_or_nul_in_args_env_and_paths(tmp_path, bad):
    cases = [
        command_with(args=[bad]),
        command_with(env={"A": bad}),
        {"command": [{"name": "k", "type": "binary", "path": f"files/{bad}"}]},
        {"kind": "both", "upload": [{"path": f"files/{bad}", "as": "a"}]},
        {"data": {"home": [bad]}},
    ]
    for manifest in cases:
        with pytest.raises(build.BuildError):
            build.validate(pkg(tmp_path, manifest, {f"files/{bad}": b"x"}))


@pytest.mark.parametrize("bad", ["a\nb", "a\0b"])
def test_validate_refuses_a_newline_or_nul_in_a_script_or_entry(tmp_path, bad):
    script = {"command": [{"name": "k", "type": "python-script", "script": f"files/{bad}"}],
              "python": PY_ENTRY["python"]}
    entry = {"command": [{"name": "k", "type": "python-entry", "entry": bad}],
             "python": PY_ENTRY["python"]}
    files = {"wheels.lock": f"a==1.0 {H}\n".encode(), "wheels/a": b"w", f"files/{bad}": b"x"}
    for manifest in (script, entry):
        with pytest.raises(build.BuildError):
            build.validate(pkg(tmp_path, manifest, files))


RESERVED = (
    "sh bash dash zsh fish env "
    "sudo sudoedit su doas pkexec run0 passwd login newgrp visudo "
    "ssh scp sftp ssh-agent ssh-add ssh-keygen gpg "
    "python python3 pip pip3 "
    "chown cp rm kill ip ping tar vim openvpn x-terminal-emulator pwn-broker record asciinema nmap "
    "sed cut mkdir cat ls mv ln vi grep awk find head tail chmod id uname test "
    "whoami hostname sleep touch tee which ps tput dircolors xargs timeout nohup "
    "less more pager man nano editor "
    "apt apt-get dpkg systemctl mount umount "
    "curl wget git"
).split()


def test_reserved_commands_equal_the_apps_list():
    # a literal copy of pwn-broker's toolpkg.RESERVED_COMMANDS
    assert set(build.RESERVED_COMMANDS) == set(RESERVED)
    assert len(build.RESERVED_COMMANDS) == len(set(build.RESERVED_COMMANDS))


@pytest.mark.parametrize("name", RESERVED)
def test_validate_refuses_a_reserved_command_name(tmp_path, name):
    manifest = {"command": [{"name": name, "type": "binary", "path": "files/kerbrute"}]}
    with pytest.raises(build.BuildError, match="reserved"):
        build.validate(pkg(tmp_path, manifest))


def test_validate_accepts_names_that_only_look_reserved(tmp_path):
    names = ("shell", "Sh", "pythonic", "pipx", "python-ldap")
    manifest = {"command": [{"name": n, "type": "binary", "path": "files/kerbrute"} for n in names]}
    build.validate(pkg(tmp_path, manifest))


@pytest.mark.parametrize(
    "name", ["python3.13", "python3.13t", "python2", "python3-config", "pip3.13", "pip2"]
)
def test_validate_refuses_python_and_pip_under_a_versioned_name(tmp_path, name):
    manifest = {"command": [{"name": name, "type": "binary", "path": "files/kerbrute"}]}
    with pytest.raises(build.BuildError, match="reserved"):
        build.validate(pkg(tmp_path, manifest))


@pytest.mark.parametrize(
    "version", ["1\x00x", "1.0\x1b[2J", "..", ".", "-1", "1:2", "1.0\u202e", "1" * 101, "1 2", "1/2"]
)
def test_validate_refuses_a_version_the_app_would_refuse(tmp_path, version):
    with pytest.raises(build.BuildError, match="version"):
        build.validate(pkg(tmp_path, {"version": version}))


@pytest.mark.parametrize("version", ["1.0.3", "2026.11.14", "3.13.18", "1.0~rc1", "1.0+git5", "v1_2"])
def test_the_version_rule_allows_what_packages_use(version):
    assert build._VERSION.fullmatch(version)


@pytest.mark.parametrize(
    "entry",
    [".ssh", ".ssh/id", ".ssh/a/b", ".gnupg", ".gnupg/x", ".config", ".local", ".local/share",
     ".local/bin", ".cache", ".bashrc", ".zshrc", ".profile", ".bash_profile", ".bash_history",
     ".zsh_history", ".vimrc", ".gitconfig", "Desktop", "Documents", "Downloads",
     ".aws", ".aws/credentials", ".kube/config", ".docker/config.json", ".netrc", ".pgpass",
     ".password-store", ".mozilla", ".pki", ".local/share/keyrings", ".config/autostart",
     ".local/share/applications", ".config/gh", ".zshenv", ".zprofile", ".zlogin", ".bash_logout",
     ".bash_aliases", ".bashrc.d/x.sh", ".xprofile", ".xinitrc", ".xsession", ".Xauthority",
     ".inputrc", ".git-credentials", ".config/git/config", ".config/environment.d",
     ".config/fish/config.fish", ".config/chromium", ".tmux.conf", ".vim/plugin/x.vim",
     ".local/state", ".local/bin/tool", "Pictures", "Music", "Videos", "Public", "Templates"],
)
def test_validate_refuses_a_shared_home_folder(tmp_path, entry):
    with pytest.raises(build.BuildError, match="home"):
        build.validate(pkg(tmp_path, {"data": {"home": [entry]}}))


@pytest.mark.parametrize(
    "entry",
    [".kerbrute", ".kerbrute/sub/deeper", ".config/kerbrute", ".local/share/kerbrute",
     ".local/state/kerbrute", ".cache/kerbrute/x", ".Kerbrute"],
)
def test_validate_accepts_a_home_folder_named_after_the_tool(tmp_path, entry):
    build.validate(pkg(tmp_path, {"data": {"home": [entry]}}))


@pytest.mark.parametrize("entry", [".ligolo-proxy", ".config/ligolo-proxy", ".cache/Ligolo-Proxy/x"])
def test_validate_accepts_a_home_folder_named_after_a_command(tmp_path, entry):
    manifest = {
        "command": [{"name": "ligolo-proxy", "type": "binary", "path": "files/kerbrute"}],
        "data": {"home": [entry]},
    }
    build.validate(pkg(tmp_path, manifest))


@pytest.mark.parametrize(
    "entry",
    [".ligolo-proxy", ".config/tool", ".local/share/tool", ".cache/tool", ".sshx", ".ssh-tool",
     "Documents/tool", "Documents/kerbrute", ".kerb", ".kerbrutex", "kerbrute", ".config/sub/kerbrute",
     ".config/.kerbrute", ".local/kerbrute", ".Config/kerbrute"],
)
def test_validate_refuses_a_home_folder_that_is_not_the_tools_own(tmp_path, entry):
    # pwn-broker moves what a tool claims into the box at finish and deletes it from home
    with pytest.raises(build.BuildError, match="is not this tool's own folder"):
        build.validate(pkg(tmp_path, {"data": {"home": [entry]}}))


@pytest.mark.parametrize(
    ("command", "entry"),
    [
        ("config", ".config/google-chrome"),
        ("Config", ".config/xfce4"),
        ("local", ".local/share/kwalletd"),
        ("local", ".local/lib/python3.13/site-packages"),
        ("cache", ".cache/huggingface"),
        ("cache", ".Cache/huggingface"),
        ("bashrc", ".bashrc/x"),
        ("gnupg", ".GnuPG/private-keys-v1.d"),
    ],
)
def test_validate_refuses_what_is_inside_a_shared_place_to_a_command_named_like_it(
    tmp_path, command, entry
):
    manifest = {
        "command": [{"name": command, "type": "binary", "path": "files/kerbrute"}],
        "data": {"home": [entry]},
    }
    with pytest.raises(build.BuildError, match="data.home"):
        build.validate(pkg(tmp_path, manifest))


@pytest.mark.parametrize(
    ("command", "entry"),
    [("config", ".config/config"), ("cache", ".cache/cache/x"), ("local", ".local/share/local")],
)
def test_validate_accepts_its_own_folder_inside_a_shared_place_for_a_command_named_like_it(
    tmp_path, command, entry
):
    manifest = {
        "command": [{"name": command, "type": "binary", "path": "files/kerbrute"}],
        "data": {"home": [entry]},
    }
    build.validate(pkg(tmp_path, manifest))


# ---- two tools may not share a command


def card_with(name, commands, **kw):
    return {
        "name": name, "version": "1.0", "build": 1, "kind": "kali", "description": "d",
        "tags": [], "upstream": "u", "licence": "l", "needs_root": False, "commands": commands,
        "file": f"{name}-1.0-1.zip", "size": 1, "sha256": "a" * 64, **kw,
    }


def test_the_index_refuses_two_tools_with_one_command():
    cards = [card_with("one", ["shared"]), card_with("two", ["shared", "own"])]
    with pytest.raises(build.BuildError, match="shared") as err:
        build.build_index(cards, base_url=None)
    assert "one" in str(err.value) and "two" in str(err.value)


def test_the_index_allows_one_tool_to_keep_a_command_across_builds():
    cards = [card_with("one", ["x"]), card_with("one", ["x"], build=2)]
    build.build_index(cards, base_url=None)


def test_plan_refuses_two_recipes_with_one_command(tmp_path, net, capsys):
    make_recipe(tmp_path / "recipes" / "kerbrute", net)
    make_recipe(tmp_path / "recipes" / "other", net)
    assert build.main(["plan", "--recipes", str(tmp_path / "recipes")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("fail ") and "kerbrute" in err and "other" in err


def test_what_write_wheels_lock_writes_passes_validate(tmp_path):
    staged = tmp_path / "staged"
    make_wheel(staged / "wheels", "My_Dep.x", "2.0.post1", tag="cp312-cp312-manylinux2014_x86_64")
    make_wheel(staged / "wheels", "my-dep.x", "2.0.post1", tag="cp313-cp313-manylinux_2_28_x86_64")
    make_wheel(staged / "wheels", "aaa", "1!2.0+local.1")
    lock = build.write_wheels_lock(staged)
    files = {"wheels.lock": lock.read_bytes(), "wheels/a": b"w"}
    build.validate(pkg(tmp_path, PY_ENTRY, files, drop=("files/kerbrute",)))


# ---- the rules shared with pwn-broker

# literal copies of pwn-broker's toolpkg._NAME.pattern, toolpkg.KINDS and toolpkg.SCHEMAS (0.1.14)
NAME_PATTERN = r"[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*"
APP_KINDS = ("kali", "upload", "both", "runtime", "notes")


def test_name_pattern_and_kinds_equal_the_apps():
    assert build._NAME.pattern == NAME_PATTERN
    assert build.KINDS == APP_KINDS
    assert build.SCHEMAS == (1, 2, 3, 4)


def pkg_manifest(**over):
    m = {
        "schema": 1, "name": "kerbrute", "version": "1.0.3", "build": 1, "kind": "kali",
        "description": "d", "tags": [], "upstream": "u", "licence": "l", "arch": "x86_64",
        "command": [{"name": "kerbrute", "type": "binary", "path": "files/kerbrute"}],
    }
    return {k: v for k, v in {**m, **over}.items() if v is not None}


def runtime_manifest(**over):
    m = {
        "schema": 2, "name": "python-3.13", "version": "3.13.18", "build": 1, "kind": "runtime",
        "description": "d", "tags": [], "upstream": "u", "licence": "PSF-2.0", "arch": "x86_64",
        "runtime": {"archive": "files/python.tar.gz", "python": "python/bin/python3.13",
                    "series": "3.13"},
    }
    return {k: v for k, v in {**m, **over}.items() if v is not None}


def tool_manifest(**over):
    m = pkg_manifest(
        schema=2, name="certipy", requires=["python-3.13"],
        command=[{"name": "certipy", "type": "python-entry", "entry": "certipy"}],
        python={"lock": "wheels.lock", "wheels": "wheels"},
    )
    return {k: v for k, v in {**m, **over}.items() if v is not None}


@pytest.mark.parametrize("name", ["python-3.13", "a.b-c.d", "x1", "3", "a-"])
def test_names_with_dots_between_other_characters_are_accepted(tmp_path, net, name):
    folder = make_recipe(tmp_path / "recipes" / name, net)
    assert build.load_recipe(folder).name == name
    build._check_manifest(pkg_manifest(name=name))


@pytest.mark.parametrize("name", ["a..b", "a.", ".a", "-a", "A", "a_b", "a.B", "a/b", ""])
def test_names_the_app_refuses_are_refused(tmp_path, net, name):
    folder = make_recipe(tmp_path / "recipes" / "kerbrute", net, name=name)
    with pytest.raises(build.BuildError, match="kerbrute"):
        build.load_recipe(folder)
    with pytest.raises(build.BuildError, match="name"):
        build._check_manifest(pkg_manifest(name=name))


def test_the_name_rule_is_said_in_full(tmp_path, net):
    folder = make_recipe(tmp_path / "recipes" / "kerbrute", net, name="a..b")
    with pytest.raises(build.BuildError, match="no two dots together and none at the end"):
        build.load_recipe(folder)


def test_the_three_kinds_of_manifest_pass():
    assert build._check_manifest(pkg_manifest()) == (["files/kerbrute"], None, [])
    assert build._check_manifest(runtime_manifest()) == (["files/python.tar.gz"], None, [])
    assert build._check_manifest(tool_manifest()) == ([], "wheels", ["wheels.lock"])


NO_RUNTIME_TABLES = [
    ("command", [{"name": "py", "type": "binary", "path": "files/py"}], r"\[\[command\]\]"),
    ("command", [], r"\[\[command\]\]"),
    ("upload", [{"path": "files/a", "as": "a"}], r"\[\[upload\]\]"),
    ("data", {"home": [".py"]}, r"\[data\]"),
    ("python", {"lock": "wheels.lock", "wheels": "wheels"}, r"\[python\]"),
]


@pytest.mark.parametrize(("key", "value", "table"), NO_RUNTIME_TABLES)
def test_a_runtime_manifest_with_a_tool_table_is_refused(key, value, table):
    with pytest.raises(build.BuildError, match=f"{key}: a runtime has no {table}"):
        build._check_manifest(runtime_manifest(**{key: value}))


BAD_SCHEMA_2 = [
    (runtime_manifest(schema=1), "kind: a runtime needs schema 2"),
    (pkg_manifest(requires=["python-3.13"]), "requires: a schema 1 package cannot require anything"),
    (tool_manifest(schema=1), "requires: a schema 1 package cannot require anything"),
    (runtime_manifest(requires=["python-3.12"]), "requires: a runtime requires nothing"),
    (tool_manifest(requires=["Python"]), "requires: 'Python' is not a valid package name"),
    (tool_manifest(requires=["a..b"]), r"requires: 'a\.\.b' is not a valid package name"),
    (tool_manifest(requires="python-3.13"), "requires: must be a list of strings"),
    (tool_manifest(requires=[1]), "requires: must be a list of strings"),
    (tool_manifest(requires=["certipy"]), "requires: certipy cannot require itself"),
    (tool_manifest(requires=["python-3.13", "python-3.13"]),
     "requires: 'python-3.13' appears more than once"),
    (tool_manifest(requires=[]), "a Python tool requires exactly one runtime; this one names 0"),
    (tool_manifest(requires=["python-3.13", "python-3.12"]), "exactly one runtime; this one names 2"),
    (pkg_manifest(schema=2, requires=["python-3.13"]),
     "requires: a tool that is not Python requires nothing"),
    (pkg_manifest(schema=2),
     "schema: a package that is not a runtime and requires nothing must be schema 1"),
    (tool_manifest(python={"versions": ["3.13"], "lock": "wheels.lock", "wheels": "wheels"}),
     "python.versions: schema 2 has no versions"),
    (runtime_manifest(runtime=None), r"runtime: a runtime package needs a \[runtime\] table"),
    (runtime_manifest(runtime="files/python.tar.gz"), "runtime: must be a table"),
    (pkg_manifest(schema=3), "schema: only a notes package is schema 3"),
    (pkg_manifest(schema=4), "schema: schema 4 needs ordinary dependencies or optional backends"),
    (pkg_manifest(schema=5), "schema: must be 1, 2, 3 or 4"),
    (pkg_manifest(schema=True), "schema: must be 1, 2, 3 or 4"),
]


@pytest.mark.parametrize(("manifest", "message"), BAD_SCHEMA_2)
def test_schema_2_rules_are_the_apps(manifest, message):
    with pytest.raises(build.BuildError, match=message):
        build._check_manifest(manifest)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"archive": ""}, "runtime.archive"),
        ({"archive": "python.tar.gz"}, "runtime.archive"),
        ({"archive": "wheels/python.tar.gz"}, "runtime.archive"),
        ({"archive": "files/../python.tar.gz"}, "runtime.archive"),
        ({"archive": "/files/python.tar.gz"}, "runtime.archive"),
        ({"archive": "files\\python.tar.gz"}, "runtime.archive"),
        ({"archive": 5}, "runtime.archive"),
        ({"python": ""}, "runtime.python"),
        ({"python": "python/../../bin/python3"}, "runtime.python"),
        ({"python": "/usr/bin/python3"}, "runtime.python"),
        ({"python": "C:/python"}, "runtime.python"),
        ({"series": ""}, "runtime.series"),
        ({"series": "3"}, "runtime.series"),
        ({"series": "3.13.1"}, "runtime.series"),
        ({"series": "py3.13"}, "runtime.series"),
        ({"series": "3.13\n"}, "runtime.series"),
        ({"series": 3.13}, "runtime.series"),
    ],
)
def test_runtime_table_rules(change, field):
    manifest = runtime_manifest()
    manifest["runtime"] = {**manifest["runtime"], **change}
    with pytest.raises(build.BuildError, match=field):
        build._check_manifest(manifest)


def test_a_runtime_table_on_a_package_that_is_not_a_runtime_is_ignored(tmp_path, net):
    # as pwn-broker does: only a runtime's [runtime] table is read
    extra = {"runtime": {"archive": "nowhere", "series": "x"}}
    build._check_manifest(pkg_manifest(**extra))
    folder = make_recipe(tmp_path / "recipes" / "kerbrute", net, manifest={**PLAIN_MANIFEST, **extra})
    recipe = build.load_recipe(folder)
    assert recipe.manifest["schema"] == 1 and recipe.series is None


def runtime_pkg(tmp_path, manifest=None, files=None):
    path = tmp_path / "python-3.13-3.13.18-1.zip"
    entries = {"manifest.toml": tomli_w.dumps(manifest or runtime_manifest()).encode(),
               "LICENSE": b"l\n", **(files or {})}
    with zipfile.ZipFile(path, "w") as zf:
        for n, data in entries.items():
            zf.writestr(n, data)
    return path


def test_validate_accepts_a_runtime_and_a_tool_that_requires_one(tmp_path):
    build.validate(runtime_pkg(tmp_path, files={"files/python.tar.gz": b"archive"}))
    lock = {"wheels.lock": GOOD_LOCK, "wheels/a.whl": b"w"}
    build.validate(pkg(tmp_path, tool_manifest(), lock, name="certipy-1.0.3-1.zip", drop=("files/kerbrute",)))


def test_validate_refuses_a_runtime_without_its_archive(tmp_path):
    with pytest.raises(build.BuildError, match="files/python.tar.gz"):
        build.validate(runtime_pkg(tmp_path))


def test_validate_refuses_a_runtime_that_has_a_command(tmp_path):
    files = {"files/python.tar.gz": b"archive", "files/kerbrute": b"x"}
    with pytest.raises(build.BuildError, match=r"command: a runtime has no \[\[command\]\]"):
        build.validate(runtime_pkg(tmp_path, runtime_manifest(command=PY_ENTRY["command"]), files))


# ---- a runtime recipe


def runtime_recipe(tmp_path, net, **kw):
    return build.load_recipe(make_runtime_recipe(tmp_path / "recipes" / "python-3.13", net, **kw))


def test_a_runtime_is_schema_2_and_carries_its_series(tmp_path, net):
    r = runtime_recipe(tmp_path, net)
    assert r.manifest["schema"] == 2 and r.series == "3.13" and r.requires == []
    assert build.tag(r) == "python-3.13-v3.13.18-1"


def test_a_runtime_builds_its_archive_byte_for_byte_and_nothing_else(tmp_path, net):
    r = runtime_recipe(tmp_path, net)
    fake = FakeTools()
    zip_path = build.build_one(r, tmp_path / "dist", tmp_path / "c", fetch_fn=net, run=fake)
    assert fake.calls == []
    assert zip_path.name == "python-3.13-3.13.18-1.zip"
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == ["LICENSE", "files/python.tar.gz", "manifest.toml"]
        assert zf.read("files/python.tar.gz") == RUNTIME_ARCHIVE
        manifest = tomllib.loads(zf.read("manifest.toml").decode())
    assert manifest["schema"] == 2 and manifest["kind"] == "runtime"
    assert manifest["runtime"] == RUNTIME_MANIFEST["runtime"]
    assert "python" not in manifest and "requires" not in manifest
    c = tomllib.loads(zip_path.with_suffix(".toml").read_text())
    assert c["kind"] == "runtime" and c["commands"] == [] and "requires" not in c


def test_a_runtime_needs_a_licence(tmp_path, net):
    r = runtime_recipe(tmp_path, net, licence=False)
    with pytest.raises(build.BuildError, match="LICENSE"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)


def runtime_source(to="files/python.tar.gz", **extra):
    return {"url": "https://e.invalid/p.tar.gz", "size": 1, "sha256": "0" * 64, "to": to, **extra}


@pytest.mark.parametrize(
    ("sources", "message"),
    [
        ([], "exactly one"),
        ([runtime_source(), runtime_source("files/other")], "exactly one"),
        ([runtime_source("files/python.tgz")], "runtime.archive is files/python.tar.gz"),
        ([runtime_source(member="python/bin/python3")], "carried whole"),
    ],
)
def test_a_runtime_has_one_source_at_its_archive_path(tmp_path, net, sources, message):
    with pytest.raises(build.BuildError, match=message) as err:
        runtime_recipe(tmp_path, net, sources=sources)
    assert "python-3.13" in str(err.value)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": 1}, "kind: a runtime needs schema 2"),
        ({"requires": ["python-3.12"]}, "requires: a runtime requires nothing"),
        ({"command": [{"name": "py", "type": "binary", "path": "files/py"}]}, "a runtime has no"),
        ({"python": {}}, r"python: a runtime has no \[python\]"),
        ({"data": {"home": [".py"]}}, r"data: a runtime has no \[data\]"),
        ({"runtime": {"archive": "files/python.tar.gz", "python": "p", "series": "3"}}, "runtime.series"),
    ],
)
def test_a_runtime_recipe_is_held_to_the_manifest_rules(tmp_path, net, change, message):
    with pytest.raises(build.BuildError, match=message):
        runtime_recipe(tmp_path, net, manifest={**RUNTIME_MANIFEST, **change})


def test_lock_refuses_a_runtime(tmp_path, net):
    with pytest.raises(build.BuildError, match="not a Python tool"):
        build.lock(runtime_recipe(tmp_path, net), run=FakeTools())


# ---- a tool that requires a runtime


def tool_recipe(tmp_path, net, pins, **kw):
    return py_recipe(tmp_path, net, pins, **kw)


def test_a_tool_takes_its_series_from_the_runtime_it_requires(tmp_path, net):
    r = tool_recipe(tmp_path, net, [])
    assert r.manifest["schema"] == 2 and r.requires == ["python-3.13"] and r.series == "3.13"
    assert list(r.manifest)[:4] == ["schema", "name", "version", "build"]


def test_a_tool_on_a_runtime_collects_wheels_for_that_series_alone(tmp_path, net):
    tool = pure("tinytool", "1.0")
    t = "cp313-cp313-manylinux_2_28_x86_64"
    dep = (wheel_name("dep", "1.0", t), wheel_bytes("dep", "1.0", tag=t))
    fake = FakeTools(downloads={("3.13", "tinytool"): [tool], ("3.13", "dep"): [dep]})
    r = tool_recipe(tmp_path, net, [("dep", "1.0", [dep[1]]), ("tinytool", "1.0", [tool[1]])])
    zip_path = build.build_one(r, tmp_path / "dist", tmp_path / "c", fetch_fn=net, run=fake)
    downloads = [c for c in fake.calls if c[3] == "download"]
    assert [c[c.index("--python-version") + 1] for c in downloads] == ["3.13", "3.13"]
    with zipfile.ZipFile(zip_path) as zf:
        assert sorted(n for n in zf.namelist() if n.startswith("wheels/")) == sorted(
            f"wheels/{w[0]}" for w in (tool, dep)
        )
        manifest = tomllib.loads(zf.read("manifest.toml").decode())
    assert manifest["schema"] == 2 and manifest["requires"] == ["python-3.13"]
    assert manifest["python"] == {"lock": "wheels.lock", "wheels": "wheels"}
    c = tomllib.loads(zip_path.with_suffix(".toml").read_text())
    assert c["requires"] == ["python-3.13"] and c["kind"] == "kali"


@pytest.mark.parametrize(
    "tag", ["cp312-cp312-manylinux_2_28_x86_64", "cp314-cp314-manylinux_2_28_x86_64", "py314-none-any"]
)
def test_a_wheel_for_another_series_is_refused(tmp_path, net, tag):
    data = wheel_bytes("dep", "1.0", tag=tag)
    fake = FakeTools(downloads={("3.13", "dep"): [(wheel_name("dep", "1.0", tag), data)]})
    r = tool_recipe(tmp_path, net, [("dep", "1.0", [data])])
    with pytest.raises(build.BuildError, match="does not fit CPython 3.13"):
        build.collect_wheels(r, staged_dir(tmp_path), run=fake)


def test_a_source_built_wheel_serves_the_one_series(tmp_path, net):
    sdist = ("docopt-0.6.2.tar.gz", b"source archive")
    fake = FakeTools(sdists={"docopt": sdist}, built={"docopt": pure("docopt", "0.6.2")})
    r = tool_recipe(tmp_path, net, [("docopt", "0.6.2", [sdist[1]])], build_from_source=("docopt",))
    assert [p.name for p in build.collect_wheels(r, staged_dir(tmp_path), run=fake)] == [
        "docopt-0.6.2-py3-none-any.whl"
    ]
    assert len(fake.calls) == 2


def test_lock_resolves_for_the_runtimes_series_alone(tmp_path, net):
    r = tool_recipe(tmp_path, net, [], lock=False)
    fake = FakeTools(uv={"3.13": UV_LOCK.replace(b"\n", b"\r\n")})
    assert build.lock(r, run=fake).read_bytes() == UV_LOCK
    assert [c[c.index("--python-version") + 1] for c in fake.calls] == ["3.13"]
    assert fake.cwds == [r.folder]


def test_versions_in_a_schema_2_python_table_are_refused(tmp_path, net):
    manifest = {**PY_MANIFEST, "python": {"versions": ["3.13"]}}
    r = tool_recipe(tmp_path, net, [], manifest=manifest)
    with pytest.raises(build.BuildError, match="python.versions: schema 2 has no versions"):
        build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net, run=FakeTools())


def test_a_runtime_recipe_beside_the_tool_is_not_built_with_it(tmp_path, net):
    tool = pure("tinytool", "1.0")
    fake = FakeTools(downloads={("3.13", "tinytool"): [tool]})
    r = tool_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    build.build_one(r, tmp_path / "dist", tmp_path / "c", fetch_fn=net, run=fake)
    assert sorted(p.name for p in (tmp_path / "dist").glob("*.zip")) == ["tinytool-1.0.3-1.zip"]
    assert net.calls == []


def requiring(tmp_path, net, requires, *, python=True, schema=None):
    manifest = dict(PY_MANIFEST if python else PLAIN_MANIFEST, requires=requires)
    if schema is not None:
        manifest["schema"] = schema
    make_runtime_recipe(tmp_path / "recipes" / "python-3.13", net)
    make_recipe(tmp_path / "recipes" / "kerbrute", net)
    folder = tmp_path / "recipes" / "tinytool"
    make_recipe(folder, net, sources=[], manifest=manifest)
    return folder


@pytest.mark.parametrize(
    ("requires", "message"),
    [
        (["python-3.12"], "requires python-3.12, but .* has no recipe for it"),
        (["kerbrute"], "requires kerbrute, which is not a runtime"),
        (["tinytool"], "tinytool cannot require itself"),
        (["python-3.13", "python-3.13"], "'python-3.13' appears more than once"),
        (["Python-3.13"], "'Python-3.13' is not a valid package name"),
        (["../python-3.13"], "is not a valid package name"),
        ([], "exactly one runtime; this one names 0"),
        (["python-3.13", "kerbrute"], "exactly one runtime; this one names 2"),
        ("python-3.13", "requires: must be a list of strings"),
    ],
)
def test_what_a_tool_requires_is_checked(tmp_path, net, requires, message):
    with pytest.raises(build.BuildError, match=message) as err:
        build.load_recipe(requiring(tmp_path, net, requires))
    assert str(err.value).startswith("tinytool: ")


def test_a_python_tool_must_require_a_runtime(tmp_path, net, capsys):
    # the published schema 1 builds of a Python tool are not rebuilt; a new one needs a runtime
    for schema in (None, 1):
        manifest = {k: v for k, v in PY_MANIFEST.items() if k != "requires"}
        if schema is not None:
            manifest["schema"] = schema
        folder = make_recipe(tmp_path / f"r{schema}" / "tinytool", net, sources=[], manifest=manifest)
        with pytest.raises(build.BuildError, match="tinytool: a Python tool must require a runtime"):
            build.load_recipe(folder)
        argv = ["build", "--recipes", str(folder.parent), "--out", str(tmp_path / "out")]
        assert build.main(argv) == 1
        assert "requires = [\"python-3.13\"]" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_a_tool_that_is_not_python_requires_nothing(tmp_path, net):
    with pytest.raises(build.BuildError, match="a tool that is not Python requires nothing"):
        build.load_recipe(requiring(tmp_path, net, ["python-3.13"], python=False))


def test_python_commands_without_a_python_table_are_told_the_table_is_missing(tmp_path, net):
    # Python by its commands, as pwn-broker decides it, not by the table it lacks
    manifest = {k: v for k, v in PY_MANIFEST.items() if k != "python"}
    folder = make_recipe(tmp_path / "recipes" / "tinytool", net, sources=[], manifest=manifest)
    make_runtime_recipe(tmp_path / "recipes" / "python-3.13", net)
    with pytest.raises(build.BuildError) as err:
        build.load_recipe(folder)
    assert str(err.value) == (
        "tinytool: python: a python-entry or python-script command needs a [python] table"
    )


def tool_requiring(folder: Path, net, wanted: str) -> Path:
    command = {"name": folder.name, "type": "python-entry", "entry": folder.name}
    manifest = {**PY_MANIFEST, "requires": [wanted], "command": [command]}
    return make_recipe(folder, net, sources=[], manifest=manifest)


SUBCOMMANDS = [["plan"], ["build"], ["check"], ["lock"]]


@pytest.mark.parametrize("argv", SUBCOMMANDS, ids=lambda a: a[0])
def test_two_tools_that_require_each_other_are_refused_without_recursing(tmp_path, net, capsys, argv):
    recipes = tmp_path / "recipes"
    tool_requiring(recipes / "aa", net, "bb")
    tool_requiring(recipes / "bb", net, "aa")
    with pytest.raises(build.BuildError, match="aa: requires bb, which is not a runtime"):
        build.load_recipe(recipes / "aa")
    extra = [] if argv == ["plan"] else ["--out", str(tmp_path / "out")]
    assert build.main([*argv, "--recipes", str(recipes), *extra]) == 1
    err = capsys.readouterr().err
    assert err.startswith("fail aa: requires bb, which is not a runtime")
    assert "Traceback" not in err and "RecursionError" not in err


@pytest.mark.parametrize("argv", SUBCOMMANDS, ids=lambda a: a[0])
def test_a_tool_that_requires_itself_is_refused(tmp_path, net, capsys, argv):
    recipes = tmp_path / "recipes"
    tool_requiring(recipes / "aa", net, "aa")
    extra = [] if argv == ["plan"] else ["--out", str(tmp_path / "out")]
    assert build.main([*argv, "--recipes", str(recipes), *extra]) == 1
    assert capsys.readouterr().err.startswith("fail aa: requires: aa cannot require itself")


def test_a_required_runtime_is_read_from_its_manifest_alone(tmp_path, net):
    # the runtime's own recipe.toml is not loaded, so nothing in it can lead anywhere else
    folder = requiring(tmp_path, net, ["python-3.13"])
    (tmp_path / "recipes" / "python-3.13" / "recipe.toml").write_text("not = [toml", encoding="utf-8")
    assert build.load_recipe(folder).series == "3.13"


def test_a_required_runtime_with_a_bad_runtime_table_is_named(tmp_path, net):
    folder = requiring(tmp_path, net, ["python-3.13"])
    manifest = {**RUNTIME_MANIFEST, "runtime": {**RUNTIME_MANIFEST["runtime"], "series": "three"}}
    (tmp_path / "recipes" / "python-3.13" / "manifest.toml").write_bytes(tomli_w.dumps(manifest).encode())
    with pytest.raises(build.BuildError, match="tinytool: requires python-3.13: runtime.series"):
        build.load_recipe(folder)


@pytest.mark.parametrize("damage", ["missing", "not toml"])
def test_a_required_runtime_whose_manifest_cannot_be_read_names_the_tool(tmp_path, net, damage):
    folder = requiring(tmp_path, net, ["python-3.13"])
    manifest = tmp_path / "recipes" / "python-3.13" / "manifest.toml"
    if damage == "missing":
        manifest.unlink()
    else:
        manifest.write_text("kind = [runtime", encoding="utf-8")
    with pytest.raises(build.BuildError) as err:
        build.load_recipe(folder)
    assert str(err.value).startswith("tinytool: requires python-3.13: ")
    assert "manifest.toml" in str(err.value)


def test_a_stated_schema_must_agree_with_what_the_recipe_is(tmp_path, net):
    with pytest.raises(build.BuildError, match="a schema 1 package cannot require anything"):
        build.load_recipe(requiring(tmp_path, net, ["python-3.13"], schema=1))
    folder = make_recipe(tmp_path / "other" / "kerbrute", net, manifest={**PLAIN_MANIFEST, "schema": 2})
    with pytest.raises(build.BuildError, match="requires nothing must be schema 1"):
        build.load_recipe(folder)
    folder = make_recipe(tmp_path / "third" / "kerbrute", net, manifest={**PLAIN_MANIFEST, "schema": 3})
    with pytest.raises(build.BuildError, match="schema: only a notes package is schema 3"):
        build.load_recipe(folder)
    folder = make_recipe(tmp_path / "fourth" / "kerbrute", net, manifest={**PLAIN_MANIFEST, "schema": 4})
    with pytest.raises(build.BuildError, match="schema 4 needs ordinary dependencies or optional backends"):
        build.load_recipe(folder)
    folder = make_recipe(tmp_path / "fifth" / "kerbrute", net, manifest={**PLAIN_MANIFEST, "schema": 5})
    with pytest.raises(build.BuildError, match="manifest schema must be 1, 2, 3 or 4"):
        build.load_recipe(folder)


def test_a_tool_requiring_a_broken_runtime_recipe_is_refused(tmp_path, net):
    folder = requiring(tmp_path, net, ["python-3.13"])
    (tmp_path / "recipes" / "python-3.13" / "manifest.toml").write_text("kind = 'runtime'\n")
    with pytest.raises(build.BuildError, match="python-3.13"):
        build.load_recipe(folder)


def test_a_schema_1_recipe_stages_the_manifest_it_always_did(tmp_path, net):
    r = build.load_recipe(make_recipe(tmp_path / "recipes" / "kerbrute", net))
    staged = build.stage(r, tmp_path / "w", tmp_path / "c", fetch=net)
    expected = {"schema": 1, "name": "kerbrute", "version": "1.0.3", "build": 1, **PLAIN_MANIFEST}
    assert (staged / "manifest.toml").read_bytes() == tomli_w.dumps(expected).encode()


# ---- cards and the index


def test_a_card_carries_requires_only_when_there_is_one(tmp_path, net, recipe):
    tool = pure("tinytool", "1.0")
    r = tool_recipe(tmp_path, net, [("tinytool", "1.0", [tool[1]])])
    fake = FakeTools(downloads={("3.13", "tinytool"): [tool]})
    zip_path = build.build_one(r, tmp_path / "dist", tmp_path / "c", fetch_fn=net, run=fake)
    c = build.card(r, zip_path)
    assert list(c)[list(c).index("commands") + 1] == "requires"
    assert c["requires"] == ["python-3.13"] and c["kind"] == "kali"
    assert "requires" not in build.card(recipe, zip_path)


def test_a_published_card_without_requires_is_read(tmp_path):
    (tmp_path / "kerbrute-1.0.3-1.toml").write_text(tomli_w.dumps(cardf()))
    out = tmp_path / "index.toml"
    assert build.main(["index", str(tmp_path), "--out", str(out)]) == 0
    data = tomllib.loads(out.read_text())
    assert data["schema"] == 1 and "requires" not in data["tool"][0]["build"][0]


def test_a_card_with_bad_requires_is_refused(tmp_path, capsys):
    c = {**cardf(), "requires": "python-3.13"}
    (tmp_path / "kerbrute-1.0.3-1.toml").write_text(tomli_w.dumps(c))
    assert build.main(["index", str(tmp_path), "--out", str(tmp_path / "i.toml")]) == 1
    assert "requires" in capsys.readouterr().err


def runtime_card(version="3.13.18", build_no=1, **over):
    c = cardf(name="python-3.13", version=version, build_no=build_no)
    return {**c, "kind": "runtime", "commands": [], **over}


def test_the_index_is_schema_2_with_a_runtime_and_a_tool_that_requires_it():
    cards = [cardf(name="certipy", build_no=2), {**cardf(name="certipy", build_no=3), "requires": ["python-3.13"]},
             cardf(), runtime_card()]
    idx = build.build_index(build.card_order(cards), base_url=None)
    assert idx["schema"] == 2
    tools = {t["name"]: t for t in idx["tool"]}
    certipy = tools["certipy"]["build"]
    assert "requires" not in certipy[0] and certipy[1]["requires"] == ["python-3.13"]
    assert list(certipy[1])[list(certipy[1]).index("commands") + 1] == "requires"
    runtime = tools["python-3.13"]["build"][0]
    assert runtime["kind"] == "runtime" and runtime["commands"] == [] and "requires" not in runtime
    assert runtime["file"] == "python-3.13-3.13.18-1.zip"
    assert "requires" not in tools["kerbrute"]["build"][0]


def test_the_index_is_schema_2_with_a_runtime_alone():
    assert build.build_index([runtime_card()], base_url=None)["schema"] == 2


@pytest.mark.parametrize(
    ("cards", "message"),
    [
        ([runtime_card(commands=["python3.13"])], "a runtime has no commands"),
        ([runtime_card(requires=["python-3.12"])], "a runtime requires nothing"),
        ([runtime_card(), {**runtime_card("3.13.19"), "kind": "kali"}], "mix kind runtime with other kinds"),
        ([{**cardf(), "requires": ["Bad"]}], "'Bad' is not a valid package name"),
        ([{**cardf(), "requires": ["a", "a"]}], "'a' appears more than once"),
        ([{**cardf(), "requires": ["kerbrute"]}], "kerbrute cannot require itself"),
        ([{**cardf(), "kind": "other"}], "kind must be one of kali, upload, both, runtime, notes"),
    ],
)
def test_the_index_refuses_what_the_app_refuses(cards, message):
    with pytest.raises(build.BuildError, match=message):
        build.build_index(cards, base_url=None)


def test_a_runtime_card_file_is_read_into_the_index(tmp_path):
    c = runtime_card()
    (tmp_path / "python-3.13-3.13.18-1.toml").write_text(tomli_w.dumps(c))
    out = tmp_path / "index.toml"
    assert build.main(["index", str(tmp_path), "--out", str(out)]) == 0
    assert tomllib.loads(out.read_text())["tool"][0]["name"] == "python-3.13"


# ---- the command line with runtimes


def test_build_check_and_plan_handle_a_runtime_and_its_tool(tmp_path, net, monkeypatch, capsys):
    tool = pure("tinytool", "1.0")
    make_python_recipe(tmp_path / "recipes" / "tinytool", net, [("tinytool", "1.0", [tool[1]])])
    monkeypatch.setattr(build, "fetch", net)
    fake = FakeTools(downloads={("3.13", "tinytool"): [tool]})
    out = tmp_path / "dist"
    common = ["--recipes", str(tmp_path / "recipes"), "--out", str(out)]
    assert build.main(["build", *common, "--cache", str(tmp_path / "c")], run=fake) == 0
    idx = tomllib.loads((out / "index.toml").read_text())
    assert idx["schema"] == 2
    assert [t["name"] for t in idx["tool"]] == ["python-3.13", "tinytool"]
    assert build.main(["check", *common]) == 0
    assert build.main(["plan", "--recipes", str(tmp_path / "recipes")]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "ok python-3.13-3.13.18-1.zip" in lines and "ok tinytool-1.0.3-1.zip" in lines
    assert lines[-2:] == [
        "python-3.13 python-3.13-v3.13.18-1 python-3.13-3.13.18-1.zip",
        "tinytool tinytool-v1.0.3-1 tinytool-1.0.3-1.zip",
    ]


def _tinytool_alone(tmp_path, net, monkeypatch):
    """A tool on a runtime, built by name into a folder that does not hold the runtime."""
    tool = pure("tinytool", "1.0")
    make_python_recipe(tmp_path / "recipes" / "tinytool", net, [("tinytool", "1.0", [tool[1]])])
    monkeypatch.setattr(build, "fetch", net)
    fake = FakeTools(downloads={("3.13", "tinytool"): [tool]})
    out = tmp_path / "fresh"
    argv = ["build", "tinytool", "--recipes", str(tmp_path / "recipes"), "--out", str(out),
            "--cache", str(tmp_path / "c")]
    return argv, fake, out


def test_skip_index_builds_a_tool_without_its_runtime(tmp_path, net, monkeypatch, capsys):
    # CI rebuilds each published package alone, to compare it with the published one
    argv, fake, out = _tinytool_alone(tmp_path, net, monkeypatch)
    assert build.main([*argv, "--skip-index"], run=fake) == 0
    assert (out / "tinytool-1.0.3-1.zip").is_file() and (out / "tinytool-1.0.3-1.toml").is_file()
    assert not (out / "index.toml").exists()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [f"ok built {out / 'tinytool-1.0.3-1.zip'}"]
    assert captured.err == ""


def test_a_tool_built_without_its_runtime_says_how_to_finish_the_folder(
    tmp_path, net, monkeypatch, capsys
):
    argv, fake, out = _tinytool_alone(tmp_path, net, monkeypatch)
    assert build.main(argv, run=fake) == 1
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [f"ok built {out / 'tinytool-1.0.3-1.zip'}"]
    assert captured.err == (
        "fail tinytool 1.0.3-1: requires python-3.13, which is not in the index. "
        f"{out / 'index.toml'} was not written: build python-3.13 into {out} as well, "
        "or pass --skip-index\n"
    )
    assert (out / "tinytool-1.0.3-1.zip").is_file()
    assert not (out / "index.toml").exists()


def test_the_index_command_gives_no_build_advice_for_a_missing_runtime(tmp_path, capsys):
    (tmp_path / "kerbrute-1.0.3-1.toml").write_text(
        tomli_w.dumps({**cardf(), "requires": ["python-3.13"]})
    )
    assert build.main(["index", str(tmp_path), "--out", str(tmp_path / "index.toml")]) == 1
    assert capsys.readouterr().err == (
        "fail kerbrute 1.0.3-1: requires python-3.13, which is not in the index\n"
    )


def test_lock_with_no_names_skips_a_runtime(tmp_path, net, capsys):
    make_python_recipe(tmp_path / "recipes" / "tinytool", net, [], lock=False)
    fake = FakeTools(uv={"3.13": UV_LOCK})
    assert build.main(["lock", "--recipes", str(tmp_path / "recipes")], run=fake) == 0
    assert [c[0] for c in fake.calls] == ["uv"]
    assert capsys.readouterr().out.count("ok wrote") == 1



# ---- the index holds what it requires


def requiring_card(name="certipy", build_no=3, wanted="python-3.13"):
    return {**cardf(name=name, build_no=build_no), "requires": [wanted]}


def test_the_index_refuses_a_build_that_requires_a_runtime_it_does_not_hold():
    with pytest.raises(build.BuildError, match="certipy 1.0.3-3: requires python-3.13, which is not in the index"):
        build.build_index([requiring_card(), cardf()], base_url=None)


def test_the_index_refuses_a_build_that_requires_a_tool():
    cards = [requiring_card(wanted="kerbrute"), cardf()]
    with pytest.raises(build.BuildError, match="certipy 1.0.3-3: requires kerbrute, which is not a runtime"):
        build.build_index(cards, base_url=None)


def test_the_index_holds_the_real_case():
    cards = [cardf(name="certipy", build_no=n) for n in (1, 2)] + [requiring_card("certipy", 3)]
    cards += [cardf(name="targetedkerberoast", build_no=n) for n in (1, 2)]
    cards += [requiring_card("targetedkerberoast", 3)]
    cards += [cardf(name=n) for n in ("kerbrute", "ligolo-ng", "peass-ng", "pspy")] + [runtime_card()]
    idx = build.build_index(build.card_order(cards), base_url=None)
    assert idx["schema"] == 2 and len(idx["tool"]) == 7


def test_the_index_refuses_a_card_whose_name_breaks_the_name_rule():
    with pytest.raises(build.BuildError, match="name 'Bad_Name' must be lower-case"):
        build.build_index([cardf(name="Bad_Name")], base_url=None)


# ---- ordinary dependencies and optional backends (schema 4)


def backend_row(name="netexec", **over):
    row = {
        "name": name, "description": "network checks", "commands": ["nxc", "netexec"],
        "package": "netexec",
    }
    return {k: v for k, v in {**row, **over}.items() if v is not None}


def test_a_schema_4_manifest_and_package_validate(tmp_path):
    manifest = pkg_manifest(
        schema=4, depends=["netexec"],
        backend=[backend_row("nmap", package=None, commands=["nmap"], apt=["nmap"])],
    )
    assert build._check_manifest(manifest) == (["files/kerbrute"], None, [])
    build.validate(pkg(tmp_path, manifest))


def test_schema_4_python_tools_keep_the_runtime_requirement():
    manifest = tool_manifest(schema=4, depends=["netexec"])
    assert build._check_manifest(manifest) == ([], "wheels", ["wheels.lock"])
    with pytest.raises(build.BuildError, match="requires exactly one runtime"):
        build._check_manifest(tool_manifest(schema=4, requires=[], depends=["netexec"]))


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        (pkg_manifest(depends=["netexec"]), "ordinary dependencies need schema 4"),
        (pkg_manifest(schema=2, depends=["netexec"]), "ordinary dependencies need schema 4"),
        (pkg_manifest(schema=4, depends=[]), "schema 4 needs ordinary dependencies"),
        (pkg_manifest(schema=4, backend=[]), "schema 4 needs ordinary dependencies"),
        (pkg_manifest(schema=4, depends=["kerbrute"]), "cannot depend on itself"),
        (pkg_manifest(schema=4, depends=["Bad"]), "not a valid package name"),
        (pkg_manifest(schema=4, depends=["netexec", "netexec"]), "appears more than once"),
        (pkg_manifest(schema=4, depends=["netexec"], backend=[backend_row()]),
         "both a dependency and an optional backend"),
        (pkg_manifest(schema=4, backend=[backend_row(package=None)]), "needs a pwn-shop package or apt"),
        (pkg_manifest(schema=4, backend=[backend_row(commands=["/usr/bin/nxc"])]),
         "needs plain executable names"),
        (pkg_manifest(schema=4, backend=[backend_row(commands=["nxc", "nxc"])]),
         "a command appears more than once"),
        (pkg_manifest(schema=4, backend=[backend_row(apt=["Nmap"])]),
         "lower-case Debian names"),
        (pkg_manifest(schema=4, backend=[backend_row(unknown=True)]), "unknown key 'unknown'"),
        (pkg_manifest(schema=4, backend=[backend_row(), backend_row("nmap", commands=["nmap"])]),
         "package: 'netexec' appears more than once"),
        (pkg_manifest(schema=4, depends=["netexec"], requires=["python-3.13"]),
         "a tool that is not Python requires nothing"),
        (runtime_manifest(schema=4, depends=["netexec"]),
         "a runtime has no ordinary dependencies or backends"),
    ],
)
def test_schema_4_manifest_rules(manifest, message):
    with pytest.raises(build.BuildError, match=message):
        build._check_manifest(manifest)


def test_a_schema_4_recipe_and_card_carry_normalized_metadata(tmp_path, net):
    manifest = {
        **PLAIN_MANIFEST,
        "depends": ["netexec"],
        "backend": [backend_row("nmap", package=None, commands=["nmap"], apt=["nmap"])],
    }
    folder = make_recipe(tmp_path / "recipes" / "kerbrute", net, manifest=manifest)
    recipe = build.load_recipe(folder)
    assert recipe.manifest["schema"] == 4
    zip_path = tmp_path / "kerbrute-1.0.3-1.zip"
    zip_path.write_bytes(b"zip")
    card = build.card(recipe, zip_path)
    assert card["depends"] == ["netexec"]
    assert card["backend"] == [{
        "name": "nmap", "description": "network checks", "commands": ["nmap"], "apt": ["nmap"]
    }]
    assert list(card)[list(card).index("commands") + 1:list(card).index("commands") + 3] == [
        "depends", "backend"
    ]


def test_the_index_is_schema_4_and_keeps_mixed_notes_builds():
    root = card_with(
        "ad-sweep", ["ad-sweep"], depends=["netexec"],
        backend=[backend_row("nmap", package=None, commands=["nmap"], apt=["nmap"])],
    )
    cards = [root, card_with("netexec", ["nxc"]), notes_card()]
    idx = build.build_index(build.card_order(cards), base_url=None)
    assert idx["schema"] == 4
    tools = {t["name"]: t for t in idx["tool"]}
    row = tools["ad-sweep"]["build"][0]
    assert row["depends"] == ["netexec"] and row["backend"][0]["apt"] == ["nmap"]
    assert tools["playbooks"]["build"][0]["kind"] == "notes"
    assert "depends" not in tools["netexec"]["build"][0]


def test_schema_4_cards_are_read_back_into_an_index(tmp_path):
    root = card_with("ad-sweep", ["ad-sweep"], depends=["netexec"])
    for card in (root, card_with("netexec", ["nxc"])):
        name = f"{card['name']}-{card['version']}-{card['build']}.toml"
        (tmp_path / name).write_text(tomli_w.dumps(card), encoding="utf-8")
    out = tmp_path / "index.toml"
    assert build.main(["index", str(tmp_path), "--out", str(out)]) == 0
    index = tomllib.loads(out.read_text())
    assert index["schema"] == 4
    assert index["tool"][0]["build"][0]["depends"] == ["netexec"]


@pytest.mark.parametrize("key,value", [("depends", "netexec"), ("backend", "netexec")])
def test_bad_schema_4_card_metadata_is_refused(tmp_path, capsys, key, value):
    card = {**cardf(), key: value}
    (tmp_path / "kerbrute-1.0.3-1.toml").write_text(tomli_w.dumps(card), encoding="utf-8")
    assert build.main(["index", str(tmp_path), "--out", str(tmp_path / "i.toml")]) == 1
    assert key in capsys.readouterr().err


@pytest.mark.parametrize(
    ("cards", "message"),
    [
        ([card_with("ad-sweep", ["ad-sweep"], depends=["netexec"])], "not in the index"),
        ([card_with("ad-sweep", ["ad-sweep"], depends=["python-3.13"]), runtime_card()],
         "not an installable tool"),
        ([card_with("ad-sweep", ["ad-sweep"], depends=["playbooks"]),
          card_with("playbooks", [], kind="notes")], "not an installable tool"),
        ([card_with("ad-sweep", ["ad-sweep"], backend=[backend_row()])], "not in the index"),
        ([card_with("one", ["one"], depends=["two"]), card_with("two", ["two"], depends=["one"])],
         r"dependency cycle: one -> two -> one"),
        ([card_with("one", ["one"], depends=[]), card_with("two", ["two"])],
         "schema 4 needs ordinary dependencies"),
    ],
)
def test_the_index_validates_ordinary_targets_and_cycles(cards, message):
    with pytest.raises(build.BuildError, match=message):
        build.build_index(build.card_order(cards), base_url=None)


def test_optional_backend_edges_do_not_create_a_hard_dependency_cycle():
    first = card_with("one", ["one"], backend=[backend_row("two", package="two")])
    second = card_with("two", ["two"], backend=[backend_row("one", package="one")])
    assert build.build_index(build.card_order([first, second]), base_url=None)["schema"] == 4


# ---- notes packages (schema 3)


def notes_zip(files=None) -> bytes:
    return zip_bytes(files if files is not None else
                     {"LICENSE": b"CC BY 4.0\n", "playbooks/02-enum/smb.md": PLAYBOOK})


def notes_recipe(tmp_path, net, manifest=None, data=None, where="r"):
    data = notes_zip() if data is None else data
    url = "https://e.invalid/playbooks-2026.11.20.zip"
    net.files[url] = data
    src = [{"url": url, "size": len(data), "sha256": digest(data), "to": "files", "unpack": True}]
    folder = make_recipe(tmp_path / where / "playbooks", net, sources=src, version="2026.11.20",
                         manifest=NOTES_MANIFEST if manifest is None else manifest)
    return folder


def notes_manifest(**over):
    m = {"schema": 3, "name": "playbooks", "version": "2026.11.20", "build": 1, **NOTES_MANIFEST}
    return {k: v for k, v in {**m, **over}.items() if v is not None}


def notes_card(version="2026.11.20", build_no=1, **over):
    return {
        "name": "playbooks", "version": version, "build": build_no, "kind": "notes",
        "description": "d", "tags": ["notes"], "upstream": "https://u", "licence": "CC-BY-4.0",
        "arch": "any", "needs_root": False, "commands": [],
        "file": f"playbooks-{version}-{build_no}.zip", "size": 10, "sha256": "b" * 64, **over,
    }


def test_a_notes_recipe_is_schema_3(tmp_path, net):
    recipe = build.load_recipe(notes_recipe(tmp_path, net))
    assert recipe.manifest["schema"] == 3
    assert build.load_recipe(notes_recipe(tmp_path, net, {**NOTES_MANIFEST, "schema": 3}, where="s"))


@pytest.mark.parametrize("schema", [1, 2])
def test_a_notes_recipe_stating_another_schema_is_refused(tmp_path, net, schema):
    with pytest.raises(build.BuildError, match="kind: notes needs schema 3"):
        build.load_recipe(notes_recipe(tmp_path, net, {**NOTES_MANIFEST, "schema": schema}))


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"command": [{"name": "x", "type": "binary", "path": "files/x"}]},
         "command: a notes package has no commands"),
        ({"upload": [{"path": "files/x", "as": "x"}]}, "upload: a notes package has no uploads"),
        ({"python": {"lock": "wheels.lock", "wheels": "wheels"}}, "python: a notes package has no Python"),
        ({"requires": ["python-3.13"]}, "requires: a notes package requires nothing"),
        ({"depends": ["netexec"]}, "depends: a notes package has no ordinary dependencies"),
        ({"backend": [backend_row()]}, "backend: a notes package has no optional backends"),
        ({"data": {"home": [".playbooks"]}}, "data: a notes package keeps no tool data"),
        ({"runtime": {"archive": "files/x"}}, "runtime: a notes package is not a runtime"),
        ({"needs_root": True}, "needs_root: a notes package never needs root"),
        ({"arch": "x86_64"}, r'arch: a notes package is for any machine \(arch = "any"\)'),
        ({"notes": None}, r"notes: a notes package needs a \[notes\] table"),
        ({"notes": "files/playbooks"}, "notes: must be a table"),
        ({"notes": {"root": "playbooks"}}, "notes.root: 'playbooks' must start with files/"),
        ({"notes": {"root": "files/../x"}}, "notes.root"),
        ({"notes": {}}, "notes.root: must be a non-empty string"),
        ({"schema": 2}, "kind: notes needs schema 3"),
        ({"description": ""}, "description: must be a non-empty string"),
    ],
)
def test_notes_manifest_rules_are_the_apps(over, message):
    with pytest.raises(build.BuildError, match=message):
        build._check_manifest(notes_manifest(**over))


def test_a_good_notes_manifest_names_no_files():
    assert build._check_manifest(notes_manifest()) == ([], None, [])


def test_any_arch_is_only_for_notes():
    with pytest.raises(build.BuildError, match="arch: 'any' must be x86_64"):
        build._check_manifest(pkg_manifest(arch="any"))


def notes_pkg(tmp_path, files):
    entries = {"manifest.toml": tomli_w.dumps(notes_manifest()).encode(), "LICENSE": b"l\n", **files}
    path = tmp_path / "playbooks-2026.11.20-1.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_validate_needs_an_md_under_the_notes_root(tmp_path):
    with pytest.raises(build.BuildError, match="files/playbooks: the package holds no .md files"):
        build.validate(notes_pkg(tmp_path, {"files/playbooks/readme.txt": b"x", "files/other.md": b"x"}))
    build.validate(notes_pkg(tmp_path, {"files/playbooks/02-enum/smb.md": PLAYBOOK}))


def test_the_index_is_schema_3_with_a_notes_build():
    idx = build.build_index(build.card_order([cardf(), runtime_card(), notes_card()]), base_url=None)
    assert idx["schema"] == 3
    notes = next(t for t in idx["tool"] if t["name"] == "playbooks")["build"][0]
    assert notes["kind"] == "notes" and notes["commands"] == [] and notes["needs_root"] is False


def test_an_index_without_notes_keeps_its_schema():
    assert build.build_index([cardf()], base_url=None)["schema"] == 1
    assert build.build_index(build.card_order([cardf(), runtime_card()]), base_url=None)["schema"] == 2


@pytest.mark.parametrize(
    ("cards", "message"),
    [
        ([notes_card(), {**notes_card("2026.12.01"), "kind": "kali", "commands": ["playbooks"]}],
         "playbooks: its builds mix kind notes with other kinds"),
        ([notes_card(commands=["pb"])], "a notes package has no commands"),
        ([notes_card(requires=["python-3.13"]), runtime_card()], "requires: a notes package requires nothing"),
        ([notes_card(needs_root=True)], "a notes package never needs root"),
    ],
)
def test_the_index_refuses_what_the_app_refuses_of_notes(cards, message):
    with pytest.raises(build.BuildError, match=message):
        build.build_index(build.card_order(cards), base_url=None)


def test_a_notes_package_builds_end_to_end(tmp_path, net):
    recipe = build.load_recipe(notes_recipe(tmp_path, net))
    out = tmp_path / "dist"
    zip_path = build.build_one(recipe, out, tmp_path / "c", fetch_fn=net)
    assert zip_path.name == "playbooks-2026.11.20-1.zip"
    with zipfile.ZipFile(zip_path) as zf:
        assert sorted(zf.namelist()) == [
            "LICENSE", "files/LICENSE", "files/playbooks/02-enum/smb.md", "manifest.toml"
        ]
        assert zf.read("files/playbooks/02-enum/smb.md") == PLAYBOOK
    card = tomllib.loads((out / "playbooks-2026.11.20-1.toml").read_text())
    assert (card["kind"], card["commands"], card["arch"], card["needs_root"]) == ("notes", [], "any", False)
    index = tomllib.loads(build.write_index_from_cards(out, out / "index.toml", base_url=None).read_text())
    assert index["schema"] == 3
    assert index["tool"][0]["build"][0]["kind"] == "notes"


def test_a_notes_recipe_whose_zip_holds_no_playbooks_fails_to_build(tmp_path, net):
    recipe = build.load_recipe(notes_recipe(tmp_path, net, data=notes_zip({"LICENSE": b"x"})))
    with pytest.raises(build.BuildError, match="files/playbooks: the package holds no .md files"):
        build.build_one(recipe, tmp_path / "dist", tmp_path / "c", fetch_fn=net)
