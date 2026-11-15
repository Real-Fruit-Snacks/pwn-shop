"""Prints the LICENSE of a Python runtime package, from the two upstream archives.

    runtime-licence.py <runtime archive> <full distribution>

The runtime archive is the install_only_stripped build the recipe carries as
files/python.tar.gz. The full distribution is the pgo+lto-full build of the same CPython from
the same python-build-standalone release (a .tar.zst; reading it needs the zstandard package),
under its upstream file name, which the headings give as where each licence came from.
The stripped archive has no licences of the libraries it bundles; the full one has them in
python/licenses/, and its python/PYTHON.json says which belongs to which part of the build.

The file printed holds, each under a one-line heading and copied byte for byte:

1. CPython's licence, from the runtime archive;
2. CPython's licence with the software incorporated in it, as PYTHON.json names it;
3. each licence PYTHON.json names for an extension module, in order of path;
4. pip's licence and those of the libraries it vendors, from the runtime archive.

To check the committed file, compare the output with recipes/python-<series>/LICENSE. The
archives are only read: nothing is unpacked and nothing in them is run.
"""

import json
import posixpath
import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

MEMBER_LIMIT = 1024 * 1024
PYTHON_JSON = "python/PYTHON.json"
PYTHON_JSON_LIMIT = 16 * 1024 * 1024
RUNTIME = "files/python.tar.gz"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_CPYTHON = re.compile(r"python/lib/python3\.[0-9]+/LICENSE\.txt")
_PIP = re.compile(r"python/lib/python3\.[0-9]+/site-packages/pip-[^/]+\.dist-info/licenses/(.+)")
_LICENCE_PATH = re.compile(r"licenses/LICENSE\.([A-Za-z0-9][A-Za-z0-9._-]*)\.txt")


class LicenceError(Exception):
    pass


@dataclass
class Section:
    name: str
    where: str
    body: bytes


def assemble(sections: list[Section]) -> bytes:
    parts = []
    for section in sections:
        body = section.body if section.body.endswith(b"\n") else section.body + b"\n"
        parts.append(f"==== {section.name}: {section.where}\n\n".encode("utf-8") + body)
    return b"\n".join(parts)


def _open(path: Path):
    """The archive as a tar read in one pass, whether it is zstd, gzip or plain."""
    fh = open(path, "rb")
    try:
        if fh.read(4) == _ZSTD_MAGIC:
            fh.seek(0)
            try:
                import zstandard
            except ImportError as exc:
                raise LicenceError(
                    f"{path.name} is zstd-compressed; install the zstandard package to read it"
                ) from exc
            stream = zstandard.ZstdDecompressor().stream_reader(fh)
            return fh, tarfile.open(fileobj=stream, mode="r|")
        fh.seek(0)
        return fh, tarfile.open(fileobj=fh, mode="r|*")
    except BaseException:
        fh.close()
        raise


def _read(path: Path, wanted) -> dict[str, bytes]:
    """The bytes of every member for which wanted(name) gives a limit."""
    found: dict[str, bytes] = {}
    try:
        fh, tf = _open(path)
        with fh, tf:
            for member in tf:
                limit = wanted(member.name)
                if limit is None:
                    continue
                if not member.isfile():
                    raise LicenceError(f"{path.name}: {member.name} is not a regular file")
                if member.size > limit:
                    raise LicenceError(f"{path.name}: {member.name} is larger than {limit} bytes")
                if member.name in found:
                    raise LicenceError(f"{path.name}: {member.name} appears more than once")
                src = tf.extractfile(member)
                found[member.name] = src.read(limit + 1) if src is not None else b""
    except (OSError, tarfile.TarError, EOFError) as exc:
        raise LicenceError(f"{path.name} cannot be read ({exc})") from exc
    except Exception as exc:
        if isinstance(exc, LicenceError):
            raise
        # a damaged zstd stream raises the module's own error
        raise LicenceError(f"{path.name} cannot be read ({exc})") from exc
    return found


def _licence_path(value: object) -> str:
    if not isinstance(value, str) or not _LICENCE_PATH.fullmatch(value):
        raise LicenceError(
            f"PYTHON.json names {value!r}, which is not a file directly in licenses/"
        )
    return value


def _components(data: dict) -> tuple[str, dict[str, list[str]]]:
    """CPython's licence path, and each other licence path with the modules that use it."""
    try:
        cpython = _licence_path(data["license_path"])
        extensions = data["build_info"]["extensions"]
        users: dict[str, set[str]] = {}
        for module, variants in extensions.items():
            for variant in variants:
                for path in variant.get("license_paths") or []:
                    path = _licence_path(path)
                    if path != cpython:
                        users.setdefault(path, set()).add(module)
    except (KeyError, TypeError, AttributeError) as exc:
        raise LicenceError(f"PYTHON.json is not in the form expected ({exc!r})") from exc
    return cpython, {path: sorted(mods) for path, mods in sorted(users.items())}


def _one(found: dict[str, bytes], pattern: re.Pattern, what: str) -> tuple[str, bytes]:
    names = [n for n in found if pattern.fullmatch(n)]
    if len(names) != 1:
        raise LicenceError(f"the runtime archive has {len(names)} copies of {what}, not one")
    return names[0], found[names[0]]


def licence(runtime: Path, full: Path) -> bytes:
    def from_runtime(name: str) -> int | None:
        return MEMBER_LIMIT if _CPYTHON.fullmatch(name) or _PIP.fullmatch(name) else None

    def from_full(name: str) -> int | None:
        if name == PYTHON_JSON:
            return PYTHON_JSON_LIMIT
        return MEMBER_LIMIT if name.startswith("python/licenses/") else None

    ours = _read(runtime, from_runtime)
    theirs = _read(full, from_full)
    if PYTHON_JSON not in theirs:
        raise LicenceError(f"{full.name} has no {PYTHON_JSON}")
    try:
        data = json.loads(theirs[PYTHON_JSON].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LicenceError(f"{PYTHON_JSON} cannot be read ({exc})") from exc
    if not isinstance(data, dict):
        raise LicenceError(f"{PYTHON_JSON} is not in the form expected")
    cpython_path, components = _components(data)

    def from_full_distribution(component: str, path: str) -> Section:
        name = posixpath.join("python", path)
        if name not in theirs:
            raise LicenceError(f"PYTHON.json names {path}, which {full.name} does not have")
        return Section(component, f"{name} in {full.name}", theirs[name])

    name, body = _one(ours, _CPYTHON, "CPython's LICENSE.txt")
    sections = [
        Section("CPython", f"{name} in {RUNTIME}", body),
        from_full_distribution("CPython and the software incorporated in it", cpython_path),
    ]
    for path, modules in components.items():
        component = _LICENCE_PATH.fullmatch(path).group(1)
        sections.append(from_full_distribution(f"{component} (for {', '.join(modules)})", path))
    pip = sorted((n, _PIP.fullmatch(n).group(1)) for n in ours if _PIP.fullmatch(n))
    if not pip:
        raise LicenceError("the runtime archive has no licences in pip's dist-info")
    # pip's own licence first, then the vendored libraries' under licenses/src/
    pip.sort(key=lambda row: ("/" in row[1], row[0]))
    for name, inner in pip:
        component = posixpath.dirname(inner).removeprefix("src/") if "/" in inner else "pip"
        sections.append(Section(component, f"{name} in {RUNTIME}", ours[name]))
    return assemble(sections)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: runtime-licence.py <runtime archive> <full distribution>", file=sys.stderr)
        return 2
    try:
        text = licence(Path(args[0]), Path(args[1]))
    except LicenceError as exc:
        print(f"fail {exc}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(text)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
