import hashlib
import io
import tarfile
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


def make_recipe(folder: Path, net: FakeNet, *, sources: list[dict] | None = None,
                manifest: dict | None = None, licence: bool = True, version="1.0.3") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    if sources is None:
        data = b"#!/bin/sh\necho hi\n"
        url = "https://example.invalid/kerbrute"
        net.add(url, data)
        sources = [{"url": url, "size": len(data), "sha256": digest(data), "to": "files/kerbrute"}]
    (folder / "recipe.toml").write_bytes(
        tomli_w.dumps({"version": version, "build": 1, "source": sources}).encode()
    )
    if manifest is None:
        manifest = {
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
    (folder / "manifest.toml").write_bytes(tomli_w.dumps(manifest).encode())
    if licence:
        (folder / "LICENSE").write_bytes(b"licence text\n")
    return folder


@pytest.fixture
def recipe(tmp_path, net):
    return build.load_recipe(make_recipe(tmp_path / "recipes" / "kerbrute", net))
