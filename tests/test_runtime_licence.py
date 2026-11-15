"""tools/runtime-licence.py, on small made-up archives."""

import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "runtime-licence.py"


@pytest.fixture(scope="module")
def lic():
    spec = importlib.util.spec_from_file_location("runtime_licence", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_tar(path: Path, members: dict[str, bytes], *, mode: str = "w:gz", links=()) -> Path:
    with tarfile.open(path, mode) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for name in links:
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = "elsewhere"
            tf.addfile(info)
    return path


def python_json(extensions: dict, license_path="licenses/LICENSE.cpython.txt") -> bytes:
    built = {
        name: [{"variant": "default", "license_paths": paths} for paths in variants]
        for name, variants in extensions.items()
    }
    return json.dumps(
        {
            "python_version": "3.13.18",
            "license_path": license_path,
            "build_info": {"extensions": built},
        }
    ).encode()


PIP = "python/lib/python3.13/site-packages/pip-26.3.dist-info/licenses"


def runtime_archive(tmp_path: Path, extra: dict | None = None, **kwargs) -> Path:
    members = {
        "python/lib/python3.13/LICENSE.txt": b"cpython licence\n",
        "python/lib/python3.13/os.py": b"code\n",
        f"{PIP}/LICENSE.txt": b"pip licence\n",
        f"{PIP}/src/pip/_vendor/rich/LICENSE": b"rich licence\n",
        f"{PIP}/src/pip/_vendor/certifi/LICENSE": b"certifi licence",
        "python/lib/python3.13/site-packages/pip/_vendor/rich/LICENSE": b"rich licence\n",
        **(extra or {}),
    }
    return make_tar(tmp_path / "runtime.tar.gz", members, **kwargs)


def full_distribution(tmp_path: Path, extensions=None, extra=None, *, json_bytes=None) -> Path:
    if extensions is None:
        extensions = {
            "_ssl": [["licenses/LICENSE.openssl-3.txt"]],
            "_hashlib": [["licenses/LICENSE.openssl-3.txt"]],
            "zlib": [["licenses/LICENSE.zlib-ng.txt", "licenses/LICENSE.zlib.txt"]],
            "_bz2": [["licenses/LICENSE.bzip2.txt"]],
            "math": [[]],
        }
    members = {
        "python/PYTHON.json": json_bytes if json_bytes is not None else python_json(extensions),
        "python/licenses/LICENSE.cpython.txt": b"cpython and what it incorporates\n",
        "python/licenses/LICENSE.openssl-3.txt": b"openssl licence\n",
        "python/licenses/LICENSE.zlib.txt": b"zlib licence\n",
        "python/licenses/LICENSE.zlib-ng.txt": b"zlib-ng licence\n",
        "python/licenses/LICENSE.bzip2.txt": b"bzip2 licence\n",
        "python/licenses/LICENSE.unused.txt": b"for nothing this build has\n",
        "python/install/bin/python3": b"\x7fELF",
        **(extra or {}),
    }
    # a plain tar: the script reads one compressed with zstd the same way
    return make_tar(tmp_path / "full.tar", members, mode="w")


LICENSE_RST = b"Licenses and Acknowledgements\n\nmimalloc\n--------\n\nMIT, Microsoft\n"
HACL_NOTICE = (
    b"/* MIT License\n *\n * Copyright (c) 2016-2022 INRIA\n *\n"
    b" * Permission is hereby granted, free of charge.\n */"
)
HACL_SOURCE = HACL_NOTICE + b"\n\n\n#include \"internal/Hacl_Hash_SHA2.h\"\n\nvoid f(void) {}\n"


def sources(tmp_path: Path, rst: bytes = LICENSE_RST, hacl: bytes = HACL_SOURCE):
    (tmp_path / "license.rst").write_bytes(rst)
    (tmp_path / "Hacl_Hash_SHA2.c").write_bytes(hacl)
    return tmp_path / "license.rst", tmp_path / "Hacl_Hash_SHA2.c"


def run(lic, tmp_path, runtime, full, **kwargs):
    return lic.licence(runtime, full, *sources(tmp_path, **kwargs))


def headings(text: bytes) -> list[str]:
    return [ln for ln in text.decode().splitlines() if ln.startswith("==== ")]


def test_the_sections_come_in_order_with_their_headings(lic, tmp_path):
    out = run(lic, tmp_path, runtime_archive(tmp_path), full_distribution(tmp_path))
    assert headings(out) == [
        "==== CPython: python/lib/python3.13/LICENSE.txt in files/python.tar.gz",
        "==== CPython, the licence PYTHON.json names: python/licenses/LICENSE.cpython.txt in full.tar",
        "==== The software incorporated in CPython: Doc/license.rst at CPython v3.13.18",
        "==== HACL* (compiled into libpython): the leading comment of "
        "Modules/_hacl/Hacl_Hash_SHA2.c at CPython v3.13.18",
        "==== bzip2 (for _bz2): python/licenses/LICENSE.bzip2.txt in full.tar",
        "==== openssl-3 (for _hashlib, _ssl): "
        "python/licenses/LICENSE.openssl-3.txt in full.tar",
        "==== zlib-ng (for zlib): python/licenses/LICENSE.zlib-ng.txt in full.tar",
        "==== zlib (for zlib): python/licenses/LICENSE.zlib.txt in full.tar",
        f"==== pip: {PIP}/LICENSE.txt in files/python.tar.gz",
        f"==== pip/_vendor/certifi: {PIP}/src/pip/_vendor/certifi/LICENSE in files/python.tar.gz",
        f"==== pip/_vendor/rich: {PIP}/src/pip/_vendor/rich/LICENSE in files/python.tar.gz",
    ]


def test_each_body_is_copied_byte_for_byte_under_its_heading(lic, tmp_path):
    out = run(lic, tmp_path, runtime_archive(tmp_path), full_distribution(tmp_path))
    assert out.startswith(
        b"==== CPython: python/lib/python3.13/LICENSE.txt in files/python.tar.gz\n"
        b"\n"
        b"cpython licence\n"
        b"\n"
        b"==== CPython, the licence PYTHON.json names"
    )
    assert b"Doc/license.rst at CPython v3.13.18\n\n" + LICENSE_RST + b"\n==== HACL*" in out
    # the HACL* notice is the file's leading comment, and none of its code
    assert b"Hacl_Hash_SHA2.c at CPython v3.13.18\n\n" + HACL_NOTICE + b"\n\n==== bzip2" in out
    assert b"#include" not in out
    # a body with no line break at its end gets one, after it, before the next heading
    assert b"\n\ncertifi licence\n\n==== pip/_vendor/rich:" in out
    assert out.endswith(b"\n\nrich licence\n")


def test_a_licence_no_component_uses_is_left_out(lic, tmp_path):
    out = run(lic, tmp_path, runtime_archive(tmp_path), full_distribution(tmp_path))
    assert b"for nothing this build has" not in out
    assert b"unused" not in out


def test_the_same_inputs_give_the_same_bytes(lic, tmp_path):
    first = run(lic, tmp_path, runtime_archive(tmp_path), full_distribution(tmp_path))
    assert run(lic, tmp_path, tmp_path / "runtime.tar.gz", tmp_path / "full.tar") == first


def test_the_assembly_is_plain(lic):
    sections = [lic.Section("A", "a.txt in x", b"one\n"), lic.Section("B", "b in y", b"two")]
    assert lic.assemble(sections) == b"==== A: a.txt in x\n\none\n\n==== B: b in y\n\ntwo\n"


def test_a_licence_path_the_distribution_lacks_is_refused(lic, tmp_path):
    full = full_distribution(tmp_path, {"_lzma": [["licenses/LICENSE.liblzma.txt"]]})
    with pytest.raises(lic.LicenceError, match="LICENSE.liblzma.txt"):
        run(lic, tmp_path, runtime_archive(tmp_path), full)


@pytest.mark.parametrize(
    "path", ["../LICENSE.x.txt", "/etc/passwd", "licenses/../x", "licenses/sub/LICENSE.x.txt"]
)
def test_a_licence_path_outside_the_licences_folder_is_refused(lic, tmp_path, path):
    full = full_distribution(tmp_path, {"_x": [[path]]})
    with pytest.raises(lic.LicenceError, match="licenses/"):
        run(lic, tmp_path, runtime_archive(tmp_path), full)


def test_a_distribution_without_python_json_is_refused(lic, tmp_path):
    full = make_tar(tmp_path / "full.tar", {"python/licenses/LICENSE.cpython.txt": b"x"}, mode="w")
    with pytest.raises(lic.LicenceError, match="PYTHON.json"):
        run(lic, tmp_path, runtime_archive(tmp_path), full)


def test_python_json_that_is_not_json_is_refused(lic, tmp_path):
    full = full_distribution(tmp_path, json_bytes=b"{not json")
    with pytest.raises(lic.LicenceError, match="PYTHON.json"):
        run(lic, tmp_path, runtime_archive(tmp_path), full)


def test_a_runtime_archive_without_cpythons_licence_is_refused(lic, tmp_path):
    runtime = make_tar(tmp_path / "runtime.tar.gz", {f"{PIP}/LICENSE.txt": b"pip\n"})
    with pytest.raises(lic.LicenceError, match="LICENSE.txt"):
        run(lic, tmp_path, runtime, full_distribution(tmp_path))


def test_a_runtime_archive_without_pips_licences_is_refused(lic, tmp_path):
    runtime = make_tar(
        tmp_path / "runtime.tar.gz", {"python/lib/python3.13/LICENSE.txt": b"cpython\n"}
    )
    with pytest.raises(lic.LicenceError, match="pip"):
        run(lic, tmp_path, runtime, full_distribution(tmp_path))


def test_a_licence_that_is_a_link_is_refused(lic, tmp_path):
    runtime = runtime_archive(tmp_path, links=[f"{PIP}/src/pip/_vendor/idna/LICENSE.md"])
    with pytest.raises(lic.LicenceError, match="not a regular file"):
        run(lic, tmp_path, runtime, full_distribution(tmp_path))


def test_a_licence_too_large_is_refused(lic, tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "MEMBER_LIMIT", 10)
    with pytest.raises(lic.LicenceError, match="larger than"):
        run(lic, tmp_path, runtime_archive(tmp_path), full_distribution(tmp_path))


ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def test_a_zstd_full_distribution_is_read_through_the_decompressor(lic, tmp_path, monkeypatch):
    # the stand-in "compressed" file is the magic followed by a plain tar
    plain = full_distribution(tmp_path).read_bytes()
    packed = tmp_path / "full.tar.zst"
    packed.write_bytes(ZSTD_MAGIC + plain)
    seen = []

    def decompress(fh, path):
        seen.append((fh.tell(), path))
        assert fh.read(4) == ZSTD_MAGIC
        return io.BytesIO(fh.read())

    monkeypatch.setattr(lic, "_zstd_stream", decompress)
    out = run(lic, tmp_path, runtime_archive(tmp_path), packed)
    assert seen == [(0, packed)]
    assert b"in full.tar.zst\n\nopenssl licence\n" in out


def test_without_zstandard_a_zstd_file_is_refused_with_what_to_install(lic, tmp_path, monkeypatch):
    packed = tmp_path / "full.tar.zst"
    packed.write_bytes(ZSTD_MAGIC + b"\x00" * 16)
    monkeypatch.setitem(sys.modules, "zstandard", None)
    with pytest.raises(lic.LicenceError, match="full.tar.zst is zstd-compressed; install the zstandard"):
        run(lic, tmp_path, runtime_archive(tmp_path), packed)


def test_a_real_zstd_tar_is_read(lic, tmp_path):
    zstandard = pytest.importorskip("zstandard")
    plain = full_distribution(tmp_path).read_bytes()
    packed = tmp_path / "full.tar.zst"
    packed.write_bytes(zstandard.ZstdCompressor().compress(plain))
    out = run(lic, tmp_path, runtime_archive(tmp_path), packed)
    assert b"in full.tar.zst\n\nopenssl licence\n" in out


def test_main_prints_the_file_and_says_what_failed(lic, tmp_path, capsysbinary):
    runtime, full = runtime_archive(tmp_path), full_distribution(tmp_path)
    rst, hacl = sources(tmp_path)
    assert lic.main([str(runtime), str(full), str(rst), str(hacl)]) == 0
    printed = capsysbinary.readouterr()
    assert printed.out == lic.licence(runtime, full, rst, hacl)
    # what upstream's folder holds that the file leaves out is said every time, and why
    assert printed.err == (
        b"warn left out python/licenses/LICENSE.unused.txt: "
        b"PYTHON.json names it for no part of this build\n"
    )
    assert lic.main([str(runtime), str(tmp_path / "missing.tar"), str(rst), str(hacl)]) == 1
    assert capsysbinary.readouterr().err.startswith(b"fail ")
    assert lic.main([str(runtime), str(full)]) == 2


def test_the_left_out_licences_are_listed(lic, tmp_path):
    extra = {"python/licenses/LICENSE.tix.txt": b"tix\n"}
    _, left = lic.compose(
        runtime_archive(tmp_path), full_distribution(tmp_path, extra=extra), *sources(tmp_path)
    )
    assert left == [
        "left out python/licenses/LICENSE.tix.txt: PYTHON.json names it for no part of this build",
        "left out python/licenses/LICENSE.unused.txt: PYTHON.json names it for no part of this build",
    ]


@pytest.mark.parametrize(
    ("hacl", "message"),
    [
        (b"#include <x.h>\n/* MIT */\n", "does not start with a comment"),
        (b"/* MIT License\n * Copyright (c) INRIA\n * Permission is granted\n", "never ends"),
        (b"/* generated code */\nint x;\n", "is not a licence notice"),
        (b"/* Copyright (c) INRIA, with no permission text */\n", "is not a licence notice"),
    ],
)
def test_a_hacl_source_without_a_leading_licence_notice_is_refused(lic, tmp_path, hacl, message):
    with pytest.raises(lic.LicenceError, match=message):
        run(lic, tmp_path, runtime_archive(tmp_path), full_distribution(tmp_path), hacl=hacl)


@pytest.mark.parametrize("version", [None, "3.13", "v3.13.18", 3])
def test_the_cpython_tag_comes_from_python_json(lic, tmp_path, version):
    data = json.loads(python_json({}))
    if version is None:
        del data["python_version"]
    else:
        data["python_version"] = version
    full = full_distribution(tmp_path, json_bytes=json.dumps(data).encode())
    with pytest.raises(lic.LicenceError, match="python_version"):
        run(lic, tmp_path, runtime_archive(tmp_path), full)


def test_a_source_file_too_large_or_missing_is_refused(lic, tmp_path, monkeypatch):
    runtime, full = runtime_archive(tmp_path), full_distribution(tmp_path)
    with pytest.raises(lic.LicenceError, match="license.rst cannot be read"):
        lic.licence(runtime, full, tmp_path / "license.rst", tmp_path / "nothing.c")
    monkeypatch.setattr(lic, "SOURCE_LIMIT", 10)
    with pytest.raises(lic.LicenceError, match="license.rst is larger than 10 bytes"):
        run(lic, tmp_path, runtime, full)
