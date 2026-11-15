"""tools/runtime-licence.py, on small made-up archives."""

import importlib.util
import io
import json
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
        {"license_path": license_path, "build_info": {"extensions": built}}
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


def headings(text: bytes) -> list[str]:
    return [ln for ln in text.decode().splitlines() if ln.startswith("==== ")]


def test_the_sections_come_in_order_with_their_headings(lic, tmp_path):
    out = lic.licence(runtime_archive(tmp_path), full_distribution(tmp_path))
    assert headings(out) == [
        "==== CPython: python/lib/python3.13/LICENSE.txt in files/python.tar.gz",
        "==== CPython and the software incorporated in it: "
        "python/licenses/LICENSE.cpython.txt in full.tar",
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
    out = lic.licence(runtime_archive(tmp_path), full_distribution(tmp_path))
    assert out.startswith(
        b"==== CPython: python/lib/python3.13/LICENSE.txt in files/python.tar.gz\n"
        b"\n"
        b"cpython licence\n"
        b"\n"
        b"==== CPython and the software incorporated in it"
    )
    # a body with no line break at its end gets one, after it, before the next heading
    assert b"\n\ncertifi licence\n\n==== pip/_vendor/rich:" in out
    assert out.endswith(b"\n\nrich licence\n")


def test_a_licence_no_component_uses_is_left_out(lic, tmp_path):
    out = lic.licence(runtime_archive(tmp_path), full_distribution(tmp_path))
    assert b"for nothing this build has" not in out
    assert b"unused" not in out


def test_the_same_inputs_give_the_same_bytes(lic, tmp_path):
    first = lic.licence(runtime_archive(tmp_path), full_distribution(tmp_path))
    assert lic.licence(tmp_path / "runtime.tar.gz", tmp_path / "full.tar") == first


def test_the_assembly_is_plain(lic):
    sections = [lic.Section("A", "a.txt in x", b"one\n"), lic.Section("B", "b in y", b"two")]
    assert lic.assemble(sections) == b"==== A: a.txt in x\n\none\n\n==== B: b in y\n\ntwo\n"


def test_a_licence_path_the_distribution_lacks_is_refused(lic, tmp_path):
    full = full_distribution(tmp_path, {"_lzma": [["licenses/LICENSE.liblzma.txt"]]})
    with pytest.raises(lic.LicenceError, match="LICENSE.liblzma.txt"):
        lic.licence(runtime_archive(tmp_path), full)


@pytest.mark.parametrize(
    "path", ["../LICENSE.x.txt", "/etc/passwd", "licenses/../x", "licenses/sub/LICENSE.x.txt"]
)
def test_a_licence_path_outside_the_licences_folder_is_refused(lic, tmp_path, path):
    full = full_distribution(tmp_path, {"_x": [[path]]})
    with pytest.raises(lic.LicenceError, match="licenses/"):
        lic.licence(runtime_archive(tmp_path), full)


def test_a_distribution_without_python_json_is_refused(lic, tmp_path):
    full = make_tar(tmp_path / "full.tar", {"python/licenses/LICENSE.cpython.txt": b"x"}, mode="w")
    with pytest.raises(lic.LicenceError, match="PYTHON.json"):
        lic.licence(runtime_archive(tmp_path), full)


def test_python_json_that_is_not_json_is_refused(lic, tmp_path):
    full = full_distribution(tmp_path, json_bytes=b"{not json")
    with pytest.raises(lic.LicenceError, match="PYTHON.json"):
        lic.licence(runtime_archive(tmp_path), full)


def test_a_runtime_archive_without_cpythons_licence_is_refused(lic, tmp_path):
    runtime = make_tar(tmp_path / "runtime.tar.gz", {f"{PIP}/LICENSE.txt": b"pip\n"})
    with pytest.raises(lic.LicenceError, match="LICENSE.txt"):
        lic.licence(runtime, full_distribution(tmp_path))


def test_a_runtime_archive_without_pips_licences_is_refused(lic, tmp_path):
    runtime = make_tar(
        tmp_path / "runtime.tar.gz", {"python/lib/python3.13/LICENSE.txt": b"cpython\n"}
    )
    with pytest.raises(lic.LicenceError, match="pip"):
        lic.licence(runtime, full_distribution(tmp_path))


def test_a_licence_that_is_a_link_is_refused(lic, tmp_path):
    runtime = runtime_archive(tmp_path, links=[f"{PIP}/src/pip/_vendor/idna/LICENSE.md"])
    with pytest.raises(lic.LicenceError, match="not a regular file"):
        lic.licence(runtime, full_distribution(tmp_path))


def test_a_licence_too_large_is_refused(lic, tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "MEMBER_LIMIT", 10)
    with pytest.raises(lic.LicenceError, match="larger than"):
        lic.licence(runtime_archive(tmp_path), full_distribution(tmp_path))


def test_main_prints_the_file_and_says_what_failed(lic, tmp_path, capsysbinary):
    runtime, full = runtime_archive(tmp_path), full_distribution(tmp_path)
    assert lic.main([str(runtime), str(full)]) == 0
    assert capsysbinary.readouterr().out == lic.licence(runtime, full)
    assert lic.main([str(runtime), str(tmp_path / "missing.tar")]) == 1
    assert capsysbinary.readouterr().err.startswith(b"fail ")
