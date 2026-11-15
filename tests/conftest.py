import hashlib
import io
import re
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest
import tomli_w

import build


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeNet:
    """Serves bytes by URL through the fetch signature; never touches the network."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.calls: list[str] = []

    def add(self, url: str, data: bytes) -> build.Source:
        self.files[url] = data
        return build.Source(url, len(data), digest(data), "files/tool")

    def __call__(self, url: str, path: Path, limit: int) -> None:
        self.calls.append(url)
        path.write_bytes(self.files[url])


@pytest.fixture
def net():
    return FakeNet()


def tar_gz(members: dict[str, bytes], links: dict[str, str] | None = None,
           dirs: list[str] = ()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for name in dirs:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            tf.addfile(info)
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tf.addfile(info)
    return buf.getvalue()


def zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


PLAIN_MANIFEST = {
    "kind": "kali",
    "description": "Kerberos user enumeration",
    "tags": ["kerberos"],
    "upstream": "https://github.com/ropnop/kerbrute",
    "licence": "Apache-2.0",
    "arch": "x86_64",
    "needs_root": False,
    "examples": ["kerbrute userenum"],
    "command": [{"name": "kerbrute", "type": "binary", "path": "files/kerbrute"}],
}


def make_recipe(folder: Path, net: FakeNet, *, sources: list[dict] | None = None,
                manifest: dict | None = None, licence: bool = True, version="1.0.3", name=None, build_no=1) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    if sources is None:
        data = b"#!/bin/sh\necho hi\n"
        url = "https://example.invalid/kerbrute"
        net.add(url, data)
        sources = [{"url": url, "size": len(data), "sha256": digest(data), "to": "files/kerbrute"}]
    head = {"version": version, "build": build_no, "source": sources}
    if name is not None:
        head = {"name": name, **head}
    (folder / "recipe.toml").write_bytes(tomli_w.dumps(head).encode())
    if manifest is None:
        manifest = PLAIN_MANIFEST
    (folder / "manifest.toml").write_bytes(tomli_w.dumps(manifest).encode())
    if licence:
        (folder / "LICENSE").write_bytes(b"licence text\n")
    return folder


@pytest.fixture
def recipe(tmp_path, net):
    return build.load_recipe(make_recipe(tmp_path / "recipes" / "kerbrute", net))


# ---- Python tool helpers

def _record_hash(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def wheel_bytes(name: str, version: str, *, tag: str = "py3-none-any", body: str = "",
                scripts: dict[str, str] | None = None, purelib: bool = True,
                extra: dict[str, bytes] | None = None) -> bytes:
    """A small but installable wheel; `scripts` maps a console script to module:function."""
    dist = name.replace("-", "_")
    info = f"{dist}-{version}.dist-info"
    entries = {
        f"{dist}/__init__.py": (body or f"VERSION = {version!r}\n").encode(),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        f"{info}/WHEEL": (
            f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: {str(purelib).lower()}\n"
            f"Tag: {tag}\n"
        ).encode(),
        **(extra or {}),
    }
    if scripts:
        lines = "".join(f"{k} = {v}\n" for k, v in scripts.items())
        entries[f"{info}/entry_points.txt"] = f"[console_scripts]\n{lines}".encode()
    record = "".join(
        f"{path},sha256={_record_hash(data)},{len(data)}\n" for path, data in entries.items()
    )
    entries[f"{info}/RECORD"] = (record + f"{info}/RECORD,,\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in entries.items():
            zf.writestr(zipfile.ZipInfo(path, (1980, 1, 1, 0, 0, 0)), data)
    return buf.getvalue()


def wheel_name(name: str, version: str, tag: str = "py3-none-any") -> str:
    return f"{name.replace('-', '_')}-{version}-{tag}.whl"


def make_wheel(folder: Path, name: str, version: str, **kwargs) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    tag = kwargs.get("tag", "py3-none-any")
    path = folder / wheel_name(name, version, tag)
    path.write_bytes(wheel_bytes(name, version, **kwargs))
    return path


def lock_text(pins: list[tuple[str, str, list[bytes]]]) -> str:
    """requirements.lock text as uv writes it: pinned, hashed, continued lines, no markers."""
    out = []
    for name, version, files in pins:
        hashes = " \\\n".join(f"    --hash=sha256:{digest(f)}" for f in files)
        out.append(f"{name}=={version} \\\n{hashes}\n")
    return "".join(out)


PY_MANIFEST = {
    "kind": "kali",
    "description": "A tiny Python tool",
    "tags": ["test"],
    "upstream": "https://example.invalid/tinytool",
    "licence": "MIT",
    "arch": "x86_64",
    "needs_root": False,
    "examples": ["tinytool"],
    "command": [{"name": "tinytool", "type": "python-entry", "entry": "tinytool"}],
    "requires": ["python-3.13"],
    "python": {},
}


RUNTIME_MANIFEST = {
    "schema": 2,
    "kind": "runtime",
    "description": "CPython 3.13, for the tools that require it",
    "tags": ["python"],
    "upstream": "https://example.invalid/python-build-standalone",
    "licence": "PSF-2.0",
    "arch": "x86_64",
    "examples": ["python"],
    "runtime": {"archive": "files/python.tar.gz", "python": "python/bin/python3.13", "series": "3.13"},
}
RUNTIME_ARCHIVE = tar_gz({"python/bin/python3.13": b"not run\n"})


def make_runtime_recipe(folder: Path, net: FakeNet, *, manifest: dict | None = None,
                        sources: list[dict] | None = None, **kwargs) -> Path:
    """A runtime recipe whose one source is a small tar.gz served by net."""
    if sources is None:
        url = f"https://example.invalid/{folder.name}.tar.gz"
        net.add(url, RUNTIME_ARCHIVE)
        sources = [{"url": url, "size": len(RUNTIME_ARCHIVE), "sha256": digest(RUNTIME_ARCHIVE),
                    "to": "files/python.tar.gz"}]
    return make_recipe(folder, net, sources=sources, manifest=manifest or RUNTIME_MANIFEST,
                       version="3.13.18", **kwargs)


def make_python_recipe(folder: Path, net, pins: list[tuple[str, str, list[bytes]]], *,
                       manifest: dict | None = None, lock: bool = True,
                       build_from_source: tuple[str, ...] = ()) -> Path:
    """A tool that requires python-3.13, with that runtime's recipe beside it."""
    if not (folder.parent / "python-3.13").exists():
        make_runtime_recipe(folder.parent / "python-3.13", net)
    make_recipe(folder, net, sources=[], manifest=dict(manifest or PY_MANIFEST))
    if build_from_source:
        head = tomllib.loads((folder / "recipe.toml").read_text())
        head["build_from_source"] = list(build_from_source)
        (folder / "recipe.toml").write_bytes(tomli_w.dumps(head).encode())
    (folder / "requirements.in").write_bytes(b"tinytool\n")
    if lock:
        (folder / "requirements.lock").write_bytes(lock_text(pins).encode())
    return folder


class FakeTools:
    """Stands in for `run`: answers pip download, pip wheel and uv from prepared files.

    downloads maps (python, distribution) to the (file name, bytes) list the download drops; a
    missing key means pip finds no wheel. sdists and built work the same way by distribution.
    """

    def __init__(self, downloads=None, sdists=None, built=None, uv=None):
        self.downloads = downloads or {}
        self.sdists = sdists or {}
        self.built = built or {}
        self.uv = uv or {}
        self.calls: list[list[str]] = []
        self.cwds: list = []
        self.envs: list = []
        self.timeouts: list = []

    @staticmethod
    def _norm(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        self.cwds.append(kwargs.get("cwd"))
        self.envs.append(kwargs.get("env"))
        self.timeouts.append(kwargs.get("timeout"))
        done = subprocess.CompletedProcess

        def arg(flag):
            return cmd[cmd.index(flag) + 1]

        if cmd[0] == "uv":
            data = self.uv.get(arg("--python-version"))
            if data is None:
                return done(cmd, 1, "", "no solution")
            Path(arg("-o")).write_bytes(data)
            return done(cmd, 0, "", "")
        sub = cmd[3]
        if sub == "download":
            dist = self._norm(Path(arg("-r")).read_text().split("==")[0])
            if "--no-binary" in cmd:
                item = self.sdists.get(dist)
            else:
                item = self.downloads.get((arg("--python-version"), dist))
            if item is None:
                return done(cmd, 1, "", f"ERROR: No matching distribution found for {dist}")
            for name, data in item if isinstance(item, list) else [item]:
                (Path(arg("--dest")) / name).write_bytes(data)
            return done(cmd, 0, "", "")
        if sub == "wheel":
            dist = self._norm(Path(cmd[-1]).name.split("-")[0])
            item = self.built.get(dist)
            if item is None:
                return done(cmd, 1, "", "ERROR: build failed")
            (Path(arg("--wheel-dir")) / item[0]).write_bytes(item[1])
            return done(cmd, 0, "", "")
        raise AssertionError(f"unexpected command {cmd}")
