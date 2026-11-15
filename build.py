"""Builds pwn-broker tool packages, their cards and the index from recipes.

Standard library and tomli-w only. The formats are those pwn-broker reads; this file
reimplements the checks it makes (see validate) and never imports it.
"""

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

KINDS = ("kali", "upload", "both")
COMMAND_TYPES = ("binary", "python-entry", "python-script")
MANIFEST = "manifest.toml"
SUMS = "SHA256SUMS.txt"
INDEX = "index.toml"
MAX_MEMBERS = 10_000
MAX_UNPACKED_BYTES = 1024**3
MANIFEST_LIMIT = 1024 * 1024
FETCH_TIMEOUT = 60
FETCH_DEADLINE = 900.0
_CHUNK = 1024 * 1024
_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
_COMMAND_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
PYTHONS = ("3.12", "3.13")
PLATFORM = "manylinux_2_28_x86_64"
# The glibc on Kali satisfies every older glibc tag; each is given to pip as its own
# --platform, newest first
PLATFORMS = (
    *(f"manylinux_2_{n}_x86_64" for n in range(28, 16, -1)),
    "manylinux2014_x86_64", "manylinux_2_12_x86_64", "manylinux2010_x86_64",
    "manylinux_2_5_x86_64", "manylinux1_x86_64",
)  # fmt: skip
COMMAND_TIMEOUT = 900
REQUIREMENTS_IN = "requirements.in"
REQUIREMENTS_LOCK = "requirements.lock"
WHEELS_LOCK = "wheels.lock"
UV_PLATFORM = "x86_64-unknown-linux-gnu"
_TOP_FILES = (MANIFEST, "LICENSE", "wheels.lock")
_TOP_FOLDERS = ("files", "wheels")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_ZIP_ATTR = (stat.S_IFREG | 0o644) << 16
# toolinstall gives everything under files/ mode 0755 itself, so the zip carries no
# executable bits and the bytes do not depend on the OS that built them


class BuildError(Exception):
    pass


@dataclass
class Source:
    url: str
    size: int
    sha256: str
    to: str
    member: str | None = None


@dataclass
class Recipe:
    name: str
    version: str
    build: int
    sources: list[Source]
    manifest: dict
    folder: Path
    build_from_source: list[str] = field(default_factory=list)


Fetch = Callable[[str, Path, int], None]
Run = Callable[..., subprocess.CompletedProcess]


# ---- paths and files


def _relative(value: object, label: str, *, under: str = "") -> str:
    if not isinstance(value, str) or not value:
        raise BuildError(f"{label}: must be a relative path")
    parts = value.split("/")
    if (
        value.startswith("/")
        or "\\" in value
        or "\0" in value
        or any(p in ("", ".", "..") for p in parts)
        or parts[0].endswith(":")
    ):
        raise BuildError(f"{label}: {value!r} is not a relative path inside the package")
    if under and parts[0] != under:
        raise BuildError(f"{label}: {value!r} must start with {under}/")
    if under and len(parts) < 2:
        raise BuildError(f"{label}: {value!r} must name a file under {under}/")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _write_toml(path: Path, data: dict) -> None:
    _write_bytes(path, tomli_w.dumps(data).encode("utf-8"))


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_bytes().decode("utf-8"))
    except OSError as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise BuildError(f"{path} cannot be read ({exc})") from exc


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# ---- recipes


def _int(table: dict, key: str, label: str) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BuildError(f"{label}: must be a whole number of 1 or more")
    return value


def _source(row: object, index: int) -> Source:
    label = f"source[{index}]"
    if not isinstance(row, dict):
        raise BuildError(f"{label}: must be a table")
    url = row.get("url")
    if not isinstance(url, str) or not url:
        raise BuildError(f"{label}.url: must be a non-empty string")
    sha = row.get("sha256")
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha.lower()):
        raise BuildError(f"{label}.sha256: must be 64 hexadecimal characters")
    to = _relative(row.get("to"), f"{label}.to", under="files")
    member = row.get("member")
    if member is not None:
        member = _relative(member, f"{label}.member")
    return Source(url, _int(row, "size", f"{label}.size"), sha.lower(), to, member)


def load_recipe(folder: Path) -> Recipe:
    recipe = _read_toml(folder / "recipe.toml")
    manifest = _read_toml(folder / MANIFEST)
    name = recipe.get("name", folder.name)
    version = recipe.get("version")
    who = folder.name
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise BuildError(f"{who}: name {name!r} must be lower-case letters, digits and hyphens")
    if name != folder.name:
        raise BuildError(f"{who}: the folder name must equal the recipe's name {name!r}")
    # ':' and NUL are not manifest rules, but the index refuses a file name that holds them
    if not isinstance(version, str) or not version or re.search(r"[/\\\s:\0]", version):
        raise BuildError(
            f"{who}: version {version!r} must be text with no slash, backslash, colon or space"
        )
    build = _int(recipe, "build", f"{who}: build")
    for key, value in (("name", name), ("version", version), ("build", build)):
        if key in manifest and manifest[key] != value:
            raise BuildError(
                f"{folder.name}: manifest {key} {manifest[key]!r} differs from the recipe's {value!r}"
            )
    rows = recipe.get("source", [])
    if not isinstance(rows, list):
        raise BuildError("recipe: source must be a list of tables")
    sources = [_source(r, i) for i, r in enumerate(rows)]
    seen = set()
    for source in sources:
        # compared without case so a build on Windows and on Linux cannot differ
        if source.to.casefold() in seen:
            raise BuildError(
                f"{who}: {source.to} is the target of more than one source (compared without case)"
            )
        seen.add(source.to.casefold())
    if manifest.get("schema", 1) != 1:
        raise BuildError("manifest: schema must be 1")
    rest = {k: v for k, v in manifest.items() if k not in ("schema", "name", "version", "build")}
    filled = {"schema": 1, "name": name, "version": version, "build": build, **rest}
    listed = recipe.get("build_from_source", [])
    if not isinstance(listed, list) or not all(isinstance(n, str) and n for n in listed):
        raise BuildError(f"{who}: build_from_source must be a list of distribution names")
    return Recipe(name, version, build, sources, filled, folder, [_norm_name(n) for n in listed])


def tag(recipe: Recipe) -> str:
    return _tag(recipe.name, recipe.version, recipe.build)


def _tag(name: str, version: str, build: int) -> str:
    return f"{name}-v{version}-{build}"


def zip_name(name: str, version: str, build: int) -> str:
    return f"{name}-{version}-{build}.zip"


# ---- downloading


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise BuildError(f"redirect to {newurl!r} refused: only https is accepted")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, path: Path, limit: int) -> None:
    if not url.lower().startswith("https://"):
        raise BuildError(f"{url!r}: only https addresses are accepted")
    opener = urllib.request.build_opener(_HttpsOnly)
    request = urllib.request.Request(url, headers={"User-Agent": "pwn-shop-build"})
    deadline = time.monotonic() + FETCH_DEADLINE
    total = 0
    with opener.open(request, timeout=FETCH_TIMEOUT) as response, open(path, "wb") as out:
        while chunk := response.read(_CHUNK):
            total += len(chunk)
            if total > limit:
                raise BuildError(f"{url}: more than the {limit} bytes the recipe gives")
            if time.monotonic() > deadline:
                raise BuildError(f"{url}: the download took too long")
            out.write(chunk)


def fetch_source(source: Source, cache: Path, *, fetch: Fetch = fetch) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / source.sha256
    try:
        if target.is_file() and target.stat().st_size == source.size:
            if sha256_file(target) == source.sha256:
                return target
    except OSError:
        pass
    part = cache / (source.sha256 + ".part")
    try:
        try:
            fetch(source.url, part, source.size)
        except BuildError:
            raise
        except Exception as exc:
            raise BuildError(f"cannot download {source.url}: {str(exc) or type(exc).__name__}") from exc
        actual = part.stat().st_size
        if actual != source.size:
            raise BuildError(f"{source.url}: size is {actual} bytes, the recipe says {source.size}")
        if sha256_file(part) != source.sha256:
            raise BuildError(f"{source.url}: SHA-256 differs from the recipe")
        os.replace(part, target)
    except BaseException:
        _unlink(part)
        raise
    return target


# ---- staging


def _archive_kind(url: str) -> str:
    path = urllib.parse.urlsplit(url).path.lower()
    if path.endswith((".tar.gz", ".tgz")):
        return "tar"
    if path.endswith(".zip"):
        return "zip"
    raise BuildError(f"{url}: a source with a member must be a tar.gz, tgz or zip")


def _copy_limited(src, dest: Path, limit: int, label: str) -> None:
    total = 0
    with open(dest, "wb") as out:
        while chunk := src.read(_CHUNK):
            total += len(chunk)
            if total > limit:
                raise BuildError(f"{label}: larger than {limit} bytes")
            out.write(chunk)


def _plain_member(name: str) -> str:
    # archives made with "tar czf x ." store ./name
    while name.startswith("./"):
        name = name[2:]
    return name


def _only(matches: list, member: str, label: str):
    if not matches:
        raise BuildError(f"{label}: no member {member!r} in the archive")
    if len(matches) > 1:
        raise BuildError(f"{label}: more than one member is named {member!r} in the archive")
    return matches[0]


def _extract_member(archive: Path, kind: str, member: str, dest: Path, label: str) -> None:
    try:
        if kind == "tar":
            with tarfile.open(archive, "r:gz") as tf:
                info = _only(
                    [m for m in tf.getmembers() if _plain_member(m.name) == member], member, label
                )
                if not info.isfile():
                    raise BuildError(f"{label}: member {member!r} is not a regular file")
                src = tf.extractfile(info)
                if src is None:
                    raise BuildError(f"{label}: member {member!r} cannot be read")
                with src:
                    _copy_limited(src, dest, MAX_UNPACKED_BYTES, label)
        else:
            with zipfile.ZipFile(archive) as zf:
                zinfo = _only(
                    [i for i in zf.infolist() if _plain_member(i.filename) == member], member, label
                )
                if zinfo.is_dir() or stat.S_IFMT(zinfo.external_attr >> 16) not in (0, stat.S_IFREG):
                    raise BuildError(f"{label}: member {member!r} is not a regular file")
                with zf.open(zinfo) as src:
                    _copy_limited(src, dest, MAX_UNPACKED_BYTES, label)
    except (
        tarfile.TarError, zipfile.BadZipFile, EOFError, OSError, RuntimeError, NotImplementedError,
    ) as exc:
        raise BuildError(f"{label}: cannot read the archive ({exc})") from exc


def _python_manifest(recipe: Recipe) -> tuple[dict, bool]:
    manifest = dict(recipe.manifest)
    table = manifest.get("python")
    if table is None:
        return manifest, False
    if not isinstance(table, dict):
        raise BuildError(f"{recipe.name}: python must be a table")
    ours = {"versions": list(PYTHONS), "lock": WHEELS_LOCK, "wheels": "wheels"}
    for key, value in ours.items():
        if key in table and table[key] != value:
            raise BuildError(
                f"{recipe.name}: python.{key} is {table[key]!r}, but the builder sets it to "
                f"{value!r}; leave it out of the recipe's manifest"
            )
    manifest["python"] = ours
    return manifest, True


def stage(
    recipe: Recipe,
    workdir: Path,
    cache: Path,
    *,
    fetch: Fetch = fetch,
    run: Run = subprocess.run,
    find_links: Path | None = None,
) -> Path:
    licence = recipe.folder / "LICENSE"
    if not licence.is_file():
        raise BuildError(f"{recipe.name}: the recipe folder has no LICENSE file")
    manifest, is_python = _python_manifest(recipe)
    root = workdir / f"{recipe.name}-{recipe.version}-{recipe.build}"
    if root.exists():
        raise BuildError(f"{root} already exists")
    try:
        root.mkdir(parents=True)
        _write_toml(root / MANIFEST, manifest)
        _write_bytes(root / "LICENSE", licence.read_bytes())
        for source in recipe.sources:
            downloaded = fetch_source(source, cache, fetch=fetch)
            target = root.joinpath(*source.to.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.member is None:
                shutil.copyfile(downloaded, target)
            else:
                _extract_member(downloaded, _archive_kind(source.url), source.member, target, source.url)
        if is_python:
            collect_wheels(recipe, root, run=run, find_links=find_links)
            write_wheels_lock(root)
    except OSError as exc:
        raise BuildError(f"{recipe.name}: cannot lay out the package ({exc})") from exc
    return root


# ---- Python tools
#
# The wheels bundled must not depend on the machine running the build. pip evaluates
# environment markers against the interpreter it runs under, even with --platform and
# --python-version, so the lock holds none (it is resolved for x86_64 Linux alone), every
# pip download is given one pin with --no-deps, and the tags of each file are checked here.


@dataclass
class Pin:
    name: str
    version: str
    hashes: list[str]


_PIN = re.compile(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)")
_HASH_OPTION = re.compile(r"--hash=sha256:([0-9a-f]{64})")
_WHEEL = re.compile(
    r"(?P<name>[^-]+)-(?P<version>[^-]+)(?:-[0-9][^-]*)?-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl"
)
_PURE_SUFFIX = "-none-any.whl"


def _norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _same_version(a: str, b: str) -> bool:
    def canon(v: str) -> str:
        v = v.lower()
        while re.fullmatch(r"\d+(\.\d+)+", v) and v.endswith(".0"):
            v = v[:-2]
        return v

    return canon(a) == canon(b)


def _tail(text: str | None) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " / ".join(lines[-2:]) or "no output"


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        lines.append((current + line).strip())
        current = ""
    if current.strip():
        lines.append(current.strip())
    return lines


def read_requirements_lock(path: Path, tool: str) -> list[Pin]:
    again = f"resolve it again with: python build.py lock {tool}"
    if not path.is_file():
        raise BuildError(f"{tool}: {path.name} is missing; {again}")
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BuildError(f"{path} cannot be read ({exc})") from exc
    pins: list[Pin] = []
    seen = set()
    for line in _logical_lines(text):
        if ";" in line:
            raise BuildError(
                f"{path.name}: {line.split()[0]} carries an environment marker, which would make the "
                f"bundled wheels depend on the machine building them; {again}"
            )
        first, *options = line.split()
        found = _PIN.fullmatch(first)
        hashes = [_HASH_OPTION.fullmatch(o) for o in options]
        if not found or not hashes or not all(hashes):
            raise BuildError(
                f"{path.name}: {line!r} must be name==version followed by --hash=sha256: options; {again}"
            )
        name = _norm_name(found.group(1))
        if name in seen:
            raise BuildError(f"{path.name}: {name} is pinned more than once")
        seen.add(name)
        pins.append(Pin(found.group(1), found.group(2), [h.group(1) for h in hashes]))
    if not pins:
        raise BuildError(f"{tool}: {path.name} pins nothing; {again}")
    return pins


def _wheel_fits(filename: str, python: str) -> bool:
    """Whether the wheel installs on CPython `python` on x86_64 Linux (glibc 2.17 or 2.28 era)."""
    m = _WHEEL.fullmatch(filename)
    if m is None:
        return False
    minor = int(python.split(".")[1])
    if not any(p == "any" or p in PLATFORMS for p in m["plat"].split(".")):
        return False
    for py in m["py"].split("."):
        for abi in m["abi"].split("."):
            if abi == "none":
                if py == "py3" or py == f"cp3{minor}":
                    return True
                if py.startswith("py3") and py[3:].isdigit() and int(py[3:]) <= minor:
                    return True
            elif abi == f"cp3{minor}":
                if py == abi:
                    return True
            elif abi == "abi3":
                if py.startswith("cp3") and py[3:].isdigit() and int(py[3:]) <= minor:
                    return True
    return False


_NATIVE_SUFFIXES = (".so", ".pyd", ".dll", ".dylib")


def _check_pure(path: Path, label: str) -> None:
    """A wheel built here must be pure Python by content, or it would only fit this machine."""
    if not path.name.endswith(_PURE_SUFFIX):
        raise BuildError(
            f"{label}: the wheel built from source is {path.name}, which is not pure Python; "
            "it would only fit the build machine"
        )
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            marks = [n for n in names if n.count("/") == 1 and n.endswith(".dist-info/WHEEL")]
            if len(marks) != 1:
                raise BuildError(f"{label}: {path.name} has no single *.dist-info/WHEEL file")
            fields = zf.read(marks[0]).decode("utf-8", "replace").splitlines()
    except (zipfile.BadZipFile, OSError) as exc:
        raise BuildError(f"{label}: {path.name} cannot be read as a wheel ({exc})") from exc
    purelib = [
        ln.split(":", 1)[1].strip().lower()
        for ln in fields
        if ln.lower().startswith("root-is-purelib:")
    ]
    if purelib != ["true"]:
        raise BuildError(
            f"{label}: the wheel built from source does not say Root-Is-Purelib: true, "
            "so it is not pure Python"
        )
    native = [n for n in names if n.lower().endswith(_NATIVE_SUFFIXES)]
    if native:
        raise BuildError(
            f"{label}: the wheel built from source holds compiled code ({native[0]}), "
            "so it is not pure Python"
        )


def _check_wheel(path: Path, pin: Pin, python: str | None) -> None:
    """python None means a wheel built here from source, which must suit every Python."""
    label = f"{pin.name} {pin.version}"
    m = _WHEEL.fullmatch(path.name)
    if m is None or _norm_name(m["name"]) != _norm_name(pin.name) or not _same_version(
        m["version"], pin.version
    ):
        raise BuildError(f"{label}: got {path.name}, which is not that distribution and version")
    if python is None:
        _check_pure(path, label)
        return
    if not _wheel_fits(path.name, python):
        raise BuildError(
            f"{label}: {path.name} does not fit CPython {python} on x86_64 Linux ({PLATFORM})"
        )
    if sha256_file(path) not in pin.hashes:
        raise BuildError(f"{label}: {path.name} has a SHA-256 that requirements.lock does not list")


def _tool_env(tool: str) -> dict[str, str]:
    # the caller's pip and uv settings (index, config, require-virtualenv) must not change
    # what a build collects
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PIP_", "UV_"))}
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    if tool == "uv":
        env["UV_NO_CONFIG"] = "1"
    return env


def _sh(run: Run, cmd: list[str], cwd: Path | None = None):
    """The one place pip and uv are run."""
    uv = cmd[0] == "uv"
    label = " ".join(cmd[:3]) if uv else f"pip {cmd[3]}"
    try:
        return run(
            cmd, capture_output=True, text=True, cwd=cwd,
            env=_tool_env("uv" if uv else "pip"), timeout=COMMAND_TIMEOUT,
        )  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"{label} timed out after {COMMAND_TIMEOUT} seconds") from exc
    except FileNotFoundError as exc:
        if uv:
            raise BuildError(
                "uv is not installed; install uv (https://docs.astral.sh/uv/) and run lock again"
            ) from exc
        raise BuildError(f"cannot run {cmd[0]} ({exc})") from exc
    except OSError as exc:
        raise BuildError(f"cannot run {cmd[0]} ({exc})") from exc


def _pip(sub: str, find_links: Path | None, *args: str) -> list[str]:
    offline = ["--no-index", "--find-links", str(find_links)] if find_links else []
    return [sys.executable, "-m", "pip", sub, "--disable-pip-version-check", "--no-input",
            *offline, *args]  # fmt: skip


def _only_file(folder: Path, label: str, what: str) -> Path:
    files = sorted(p for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
    if len(files) != 1:
        raise BuildError(f"{label}: expected one {what}, found {len(files)}")
    return files[0]


# What is pinned: the source archive is fetched with --require-hashes. The build's own
# requirements (setuptools and the like) are fetched by pip's build isolation without hashes,
# and the build runs the dependency's setup code here; that is why a recipe must opt in by name.
def _wheel_from_source(pin: Pin, index: int, tmp: Path, run: Run, find_links: Path | None) -> Path:
    label = f"{pin.name} {pin.version}"
    req = tmp / f"req-{index}.txt"
    src = tmp / f"src-{index}"
    src.mkdir()
    # no --platform here: a source archive is the same file whatever the target
    done = _sh(
        run,
        _pip("download", find_links, "--no-binary", pin.name, "--no-deps", "--require-hashes",
             "-r", str(req), "--dest", str(src)),
    )  # fmt: skip
    if done.returncode != 0:
        raise BuildError(f"{label}: the pinned source archive cannot be fetched ({_tail(done.stderr)})")
    archive = _only_file(src, label, "source archive")
    if archive.name.endswith(".whl"):
        raise BuildError(f"{label}: got a wheel where a source archive was expected")
    if sha256_file(archive) not in pin.hashes:
        raise BuildError(f"{label}: {archive.name} has a SHA-256 that requirements.lock does not list")
    out = tmp / f"built-{index}"
    out.mkdir()
    done = _sh(run, _pip("wheel", find_links, "--no-deps", "--wheel-dir", str(out), str(archive)))
    if done.returncode != 0:
        raise BuildError(f"{label}: building a wheel from {archive.name} failed ({_tail(done.stderr)})")
    wheel = _only_file(out, label, "built wheel")
    _check_wheel(wheel, pin, None)
    return wheel


def _wheel_for(
    pin: Pin, python: str, index: int, tmp: Path, run: Run, find_links: Path | None
) -> Path:
    label = f"{pin.name} {pin.version}"
    dest = tmp / f"{python}-{index}"
    dest.mkdir()
    platforms = [a for tag in PLATFORMS for a in ("--platform", tag)]
    done = _sh(
        run,
        _pip("download", find_links, "--no-deps", "--require-hashes", "--only-binary", ":all:",
             *platforms, "--python-version", python, "--implementation", "cp",
             "--dest", str(dest), "-r", str(tmp / f"req-{index}.txt")),
    )  # fmt: skip
    if done.returncode != 0:
        raise BuildError(
            f"{label}: no wheel could be downloaded for Python {python} ({_tail(done.stderr)}). "
            "If the distribution publishes no wheel, add it to build_from_source in the recipe."
        )
    wheel = _only_file(dest, label, "downloaded file")
    _check_wheel(wheel, pin, python)
    return wheel


def collect_wheels(
    recipe: Recipe, staged: Path, *, run: Run = subprocess.run, find_links: Path | None = None
) -> list[Path]:
    pins = read_requirements_lock(recipe.folder / REQUIREMENTS_LOCK, recipe.name)
    locked = {_norm_name(p.name) for p in pins}
    for name in recipe.build_from_source:
        if name not in locked:
            raise BuildError(
                f"{recipe.name}: build_from_source names {name}, which {REQUIREMENTS_LOCK} does not pin"
            )
    wheels = staged / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pwn-shop-wheels-") as tmpdir:
        tmp = Path(tmpdir)
        for i, pin in enumerate(pins):
            # one pin per file, so pip fetches that distribution and nothing it would add itself
            options = " ".join(f"--hash=sha256:{h}" for h in pin.hashes)
            _write_bytes(tmp / f"req-{i}.txt", f"{pin.name}=={pin.version} {options}\n".encode())
        # a listed distribution is built once and the one wheel serves every Python
        built = {
            i: _wheel_from_source(pin, i, tmp, run, find_links)
            for i, pin in enumerate(pins)
            if _norm_name(pin.name) in recipe.build_from_source
        }
        for python in PYTHONS:
            for i, pin in enumerate(pins):
                wheel = built[i] if i in built else _wheel_for(pin, python, i, tmp, run, find_links)
                target = wheels / wheel.name
                if target.exists():
                    if sha256_file(target) != sha256_file(wheel):
                        raise BuildError(f"{pin.name} {pin.version}: two different files are named {wheel.name}")
                else:
                    shutil.copyfile(wheel, target)
    return sorted(wheels.glob("*.whl"))


def write_wheels_lock(staged: Path) -> Path:
    grouped: dict[str, tuple[str, list[str]]] = {}
    for wheel in sorted((staged / "wheels").glob("*.whl")):
        m = _WHEEL.fullmatch(wheel.name)
        if m is None:
            raise BuildError(f"{wheel.name}: not a wheel file name")
        name = _norm_name(m["name"])
        version = m["version"]
        known = grouped.setdefault(name, (version, []))
        if known[0] != version:
            raise BuildError(f"{name}: wheels of two versions ({known[0]} and {version}) are bundled")
        known[1].append(sha256_file(wheel))
    if not grouped:
        raise BuildError("wheels: no wheels were bundled")
    lines = []
    for name in sorted(grouped):
        version, hashes = grouped[name]
        lines.append(f"{name}=={version} " + " ".join(f"--hash=sha256:{h}" for h in sorted(hashes)))
    path = staged / WHEELS_LOCK
    _write_bytes(path, ("\n".join(lines) + "\n").encode("utf-8"))
    return path


def lock(recipe: Recipe, *, run: Run = subprocess.run) -> Path:
    source = recipe.folder / REQUIREMENTS_IN
    if "python" not in recipe.manifest:
        raise BuildError(f"{recipe.name}: not a Python tool (its manifest has no [python] table)")
    if not source.is_file():
        raise BuildError(f"{recipe.name}: the recipe folder has no {REQUIREMENTS_IN}")
    texts: dict[str, bytes] = {}
    resolved: dict[str, dict[str, tuple[str, list[str]]]] = {}
    with tempfile.TemporaryDirectory(prefix="pwn-shop-lock-") as tmpdir:
        for python in PYTHONS:
            out = Path(tmpdir) / f"lock-{python}.txt"
            # not --universal: it cannot be combined with --python-platform, and it writes
            # markers such as sys_platform == 'win32' that pip would judge by the build machine
            cmd = ["uv", "pip", "compile", REQUIREMENTS_IN, "--generate-hashes",
                   "--python-version", python, "--python-platform", UV_PLATFORM,
                   "--no-header", "--no-annotate", "-o", str(out)]  # fmt: skip
            done = _sh(run, cmd, recipe.folder)
            if done.returncode != 0:
                raise BuildError(f"{recipe.name}: uv failed for Python {python} ({_tail(done.stderr)})")
            try:
                texts[python] = out.read_bytes().replace(b"\r\n", b"\n")
            except OSError as exc:
                raise BuildError(f"{recipe.name}: uv wrote no lock for Python {python} ({exc})") from exc
            out.write_bytes(texts[python])
            resolved[python] = {
                _norm_name(p.name): (p.version, sorted(p.hashes))
                for p in read_requirements_lock(out, recipe.name)
            }
    first, second = (resolved[p] for p in PYTHONS)
    differing = sorted(n for n in first.keys() | second.keys() if first.get(n) != second.get(n))
    if differing:
        raise BuildError(
            f"{recipe.name}: the dependencies differ between Python {PYTHONS[0]} and {PYTHONS[1]} "
            f"({', '.join(differing)}); pin them in {REQUIREMENTS_IN} so both agree"
        )
    path = recipe.folder / REQUIREMENTS_LOCK
    _write_bytes(path, texts[PYTHONS[0]])
    return path


# ---- zip, card, sums, index


def write_zip(staged: Path, out: Path) -> Path:
    files = sorted(
        (p for p in staged.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(staged).as_posix(),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(staged).as_posix(), _ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = _ZIP_ATTR
            info.file_size = path.stat().st_size
            with open(path, "rb") as src, zf.open(info, "w", force_zip64=info.file_size >= 2**31) as dst:
                shutil.copyfileobj(src, dst, _CHUNK)
    return out


def card(recipe: Recipe, zip_path: Path) -> dict:
    m = recipe.manifest
    return {
        "name": recipe.name,
        "version": recipe.version,
        "build": recipe.build,
        "kind": m.get("kind", ""),
        "description": m.get("description", ""),
        "tags": list(m.get("tags", [])),
        "upstream": m.get("upstream", ""),
        "licence": m.get("licence", ""),
        "arch": m.get("arch", ""),
        "needs_root": bool(m.get("needs_root", False)),
        "commands": [c["name"] for c in m.get("command", []) if isinstance(c, dict) and "name" in c],
        "file": zip_path.name,
        "size": zip_path.stat().st_size,
        "sha256": sha256_file(zip_path),
    }


def write_card(recipe: Recipe, zip_path: Path) -> Path:
    path = zip_path.with_suffix(".toml")
    _write_toml(path, card(recipe, zip_path))
    return path


def write_sums(zip_path: Path, dest: Path | None = None) -> Path:
    path = dest or zip_path.with_name(SUMS)
    _write_bytes(path, f"{sha256_file(zip_path)}  {zip_path.name}\n".encode("utf-8"))
    return path


_LEADING = re.compile(r"(\d+(?:\.\d+)*)(.*)", re.DOTALL)


def _version_key(version: str) -> tuple:
    # the leading dotted numbers decide, with trailing zeros ignored; with equal numbers a
    # version with anything after them (-rc1, a, .dev1) is older than one with nothing after
    m = _LEADING.fullmatch(version)
    if m is None:
        return (0, (), 0, "", version)
    nums = [int(n) for n in m.group(1).split(".")]
    while nums and nums[-1] == 0:
        nums.pop()
    rest = m.group(2)
    return (1, tuple(nums), 0 if rest else 1, rest, version)


def card_order(cards: list[dict]) -> list[dict]:
    return sorted(cards, key=lambda c: (c["name"], _version_key(c["version"]), c["build"]))


def _url(base_url: str | None, name: str, version: str, build: int, file: str) -> str:
    if base_url is None:
        return file
    return base_url.replace("{tag}", _tag(name, version, build)).replace("{file}", file)


def build_index(cards: list[dict], *, base_url: str | None) -> dict:
    grouped: dict[str, list[dict]] = {}
    for c in cards:
        grouped.setdefault(c["name"], []).append(c)
    tools = []
    for name, rows in grouped.items():
        seen = set()
        builds = []
        for c in rows:
            key = (c["version"], c["build"])
            if key in seen:
                raise BuildError(f"{name}: build {c['version']}-{c['build']} appears more than once")
            seen.add(key)
            builds.append(
                {
                    "version": c["version"],
                    "build": c["build"],
                    "kind": c["kind"],
                    "needs_root": c["needs_root"],
                    "commands": list(c["commands"]),
                    "file": c["file"],
                    "url": _url(base_url, name, c["version"], c["build"], c["file"]),
                    "size": c["size"],
                    "sha256": c["sha256"],
                }
            )
        last = rows[-1]
        tools.append(
            {
                "name": name,
                "description": last["description"],
                "tags": list(last["tags"]),
                "licence": last["licence"],
                "upstream": last["upstream"],
                "latest": f"{last['version']}-{last['build']}",
                "build": builds,
            }
        )
    return {"schema": 1, "tool": tools}


_CARD_FILE = re.compile(r"[a-z0-9][a-z0-9-]*-[^/\\\s:]+-[1-9][0-9]*\.toml")
_CARD_FIELDS = {
    "name": str, "version": str, "build": int, "kind": str, "description": str, "tags": list,
    "upstream": str, "licence": str, "needs_root": bool, "commands": list, "file": str,
    "size": int, "sha256": str,
}


def _read_card(path: Path) -> dict:
    data = _read_toml(path)
    for key, kind in _CARD_FIELDS.items():
        if key not in data:
            raise BuildError(f"{path.name}: not a card (no {key})")
        value = data[key]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise BuildError(f"{path.name}: {key} must be a {kind.__name__}")
    return data


def write_index_from_cards(folder: Path, out: Path, *, base_url: str | None) -> Path:
    cards = [_read_card(p) for p in sorted(folder.glob("*.toml")) if _CARD_FILE.fullmatch(p.name)]
    if not cards:
        raise BuildError(f"{folder}: no cards found")
    _write_toml(out, build_index(card_order(cards), base_url=base_url))
    return out


# ---- validation (the checks of pwn-broker's toolpkg.parse_manifest and unpack)


def _text(table: dict, key: str, label: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise BuildError(f"{label}: must be a non-empty string")
    return value


def _strings(table: dict, key: str, label: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise BuildError(f"{label}: must be a list of strings")
    return value


def _rows(data: dict, key: str) -> list[dict]:
    rows = data.get(key, [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise BuildError(f"{key}: must be a list of tables")
    return rows


def _plain(value: str, label: str) -> None:
    if "/" in value or "\\" in value or "\0" in value or value in (".", ".."):
        raise BuildError(f"{label}: {value!r} must be a plain file name")


def _check_manifest(data: dict) -> tuple[list[str], str | None, list[str]]:
    """Returns the files the manifest names, its wheels folder (or None) and its lock path."""
    if data.get("schema") != 1 or isinstance(data.get("schema"), bool):
        raise BuildError("schema: must be 1")
    name = _text(data, "name", "name")
    if not _NAME.fullmatch(name):
        raise BuildError(f"name: {name!r} must be lower-case letters, digits and hyphens")
    version = _text(data, "version", "version")
    if re.search(r"[/\\\s]", version):
        raise BuildError(f"version: {version!r} must not contain a slash, backslash or space")
    build = data.get("build")
    if not isinstance(build, int) or isinstance(build, bool) or build < 1:
        raise BuildError("build: must be a whole number of 1 or more")
    kind = data.get("kind")
    if kind not in KINDS:
        raise BuildError(f"kind: must be one of {', '.join(KINDS)}")
    if not isinstance(data.get("needs_root", False), bool):
        raise BuildError("needs_root: must be true or false")
    for key in ("description", "upstream", "licence", "arch"):
        _text(data, key, key)
    _strings(data, "tags", "tags")
    _strings(data, "examples", "examples")

    named: list[str] = []
    seen = set()
    commands = _rows(data, "command")
    for i, row in enumerate(commands):
        label = f"command[{i}]"
        cname = _text(row, "name", f"{label}.name")
        if not _COMMAND_NAME.fullmatch(cname):
            raise BuildError(f"{label}.name: {cname!r} is not a valid command name")
        if cname in seen:
            raise BuildError(f"command.name: {cname!r} appears more than once")
        seen.add(cname)
        ctype = row.get("type")
        if ctype not in COMMAND_TYPES:
            raise BuildError(f"{label}.type: must be one of {', '.join(COMMAND_TYPES)}")
        env = row.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise BuildError(f"{label}.env: must be a table of strings")
        _strings(row, "args", f"{label}.args")
        if ctype == "binary":
            named.append(_relative(_text(row, "path", f"{label}.path"), f"{label}.path", under="files"))
        elif ctype == "python-script":
            named.append(
                _relative(_text(row, "script", f"{label}.script"), f"{label}.script", under="files")
            )
        else:
            _plain(_text(row, "entry", f"{label}.entry"), f"{label}.entry")
    uploads = _rows(data, "upload")
    for i, row in enumerate(uploads):
        label = f"upload[{i}]"
        named.append(_relative(_text(row, "path", f"{label}.path"), f"{label}.path", under="files"))
        _plain(_text(row, "as", f"{label}.as"), f"{label}.as")
        if not isinstance(row.get("note", ""), str):
            raise BuildError(f"{label}.note: must be a string")
    if kind in ("kali", "both") and not commands:
        raise BuildError(f"command: a {kind} package needs at least one command")
    if kind == "upload" and commands:
        raise BuildError("command: an upload package has no commands")
    if kind in ("upload", "both") and not uploads:
        raise BuildError(f"upload: a {kind} package needs at least one upload")
    if kind == "kali" and uploads:
        raise BuildError("upload: a kali package has no uploads")

    needed = any(c.get("type") != "binary" for c in commands)
    table = data.get("python")
    lock: list[str] = []
    wheels = None
    if table is None:
        if needed:
            raise BuildError("python: a python-entry or python-script command needs a [python] table")
    else:
        if not needed:
            raise BuildError("python: a [python] table is only for packages with a Python command")
        if not isinstance(table, dict):
            raise BuildError("python: must be a table")
        versions = _strings(table, "versions", "python.versions")
        if not versions or not all(versions):
            raise BuildError("python.versions: needs at least one version")
        for key in ("lock", "wheels"):
            value = _relative(_text(table, key, f"python.{key}"), f"python.{key}")
            if value.split("/")[0] == "files":
                raise BuildError(f"python.{key}: {value!r} must not be under files/")
            if key == "lock":
                lock.append(value)
            else:
                wheels = value
    home = data.get("data", {})
    if not isinstance(home, dict):
        raise BuildError("data: must be a table")
    for entry in _strings(home, "home", "data.home"):
        _relative(entry, "data.home")
    return named, wheels, lock


def _check_entries(infos: list[zipfile.ZipInfo]) -> dict[str, zipfile.ZipInfo]:
    if len(infos) > MAX_MEMBERS:
        raise BuildError(f"package has {len(infos)} entries; the limit is {MAX_MEMBERS}")
    if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
        raise BuildError(f"package unpacks to more than {MAX_UNPACKED_BYTES} bytes")
    seen: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.filename
        if info.compress_type != zipfile.ZIP_STORED:
            raise BuildError(f"package entry {name!r} is compressed")
        bare = name[:-1] if name.endswith("/") else name
        parts = bare.split("/")
        if (
            not bare
            or name.startswith("/")
            or "\\" in name
            or "\0" in name
            or any(p in ("", ".", "..") for p in parts)
            or parts[0].endswith(":")
        ):
            raise BuildError(f"package entry {name!r} would write outside the tool's folder")
        if bare in seen:
            raise BuildError(f"package entry {name!r} appears twice")
        if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise BuildError(f"package entry {name!r} is a link or special file")
        if info.flag_bits & 0x1:
            raise BuildError(f"package entry {name!r} is encrypted")
        top = parts[0]
        if info.is_dir() or "/" in bare:
            allowed = top in _TOP_FOLDERS
        else:
            allowed = bare in _TOP_FILES
        if not allowed:
            raise BuildError(f"package entry {name!r} is not part of the package layout")
        seen[bare] = info
    for bare in seen:
        parent = bare.rpartition("/")[0]
        while parent:
            if parent in seen and not seen[parent].is_dir():
                raise BuildError(f"package entry {bare!r} sits inside the file {parent!r}")
            parent = parent.rpartition("/")[0]
    return seen


def validate(zip_path: Path, *, file_name: str | None = None) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            seen = _check_entries(zf.infolist())
            info = seen.get(MANIFEST)
            if info is None or info.is_dir():
                raise BuildError(f"{MANIFEST} is missing from the package")
            if info.file_size > MANIFEST_LIMIT:
                raise BuildError(f"{MANIFEST} is too large")
            try:
                data = tomllib.loads(zf.read(info).decode("utf-8"))
            except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise BuildError(f"{MANIFEST} cannot be read ({exc})") from exc
            named, wheels, lock = _check_manifest(data)
            for name in named + lock:
                if name not in seen or seen[name].is_dir():
                    raise BuildError(f"{name}: named in the manifest but missing from the package")
            if wheels is not None:
                prefix = wheels + "/"
                if not any(not i.is_dir() and b.startswith(prefix) for b, i in seen.items()):
                    raise BuildError(f"{wheels}: the package holds no wheels")
            # not a pwn-broker rule, but the index names the file this way
            expected = zip_name(data["name"], data["version"], data["build"])
            actual = file_name or zip_path.name
            if actual != expected:
                raise BuildError(f"the file is named {actual}, the manifest says {expected}")
            for bare, i in seen.items():
                if not i.is_dir():
                    _check_size(zf, i)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BuildError(f"{zip_path.name}: cannot be read as a zip ({exc})") from exc


def _check_size(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> None:
    # unpack refuses an entry that is not the size it declares
    total = 0
    with zf.open(info) as src:
        while chunk := src.read(_CHUNK):
            total += len(chunk)
    if total != info.file_size:
        raise BuildError(f"package entry {info.filename!r} is not the size it declares")


# ---- command line


def _recipe_folders(recipes: Path, names: list[str]) -> list[Path]:
    if names:
        for n in names:
            if not n or n in (".", "..") or any(c in n for c in "/\\:\0"):
                raise BuildError(f"{n!r} is not the plain name of a folder under {recipes}")
        folders = [recipes / n for n in names]
        for f in folders:
            if not (f / "recipe.toml").is_file():
                raise BuildError(f"no recipe named {f.name!r} in {recipes}")
        return folders
    return sorted(p for p in recipes.iterdir() if (p / "recipe.toml").is_file()) if recipes.is_dir() else []


def build_one(
    recipe: Recipe,
    out: Path,
    cache: Path,
    *,
    fetch_fn: Fetch | None = None,
    run: Run = subprocess.run,
    find_links: Path | None = None,
) -> Path:
    final = out / zip_name(recipe.name, recipe.version, recipe.build)
    with tempfile.TemporaryDirectory(prefix="pwn-shop-") as tmp:
        staged = stage(
            recipe, Path(tmp), cache, fetch=fetch_fn or fetch, run=run, find_links=find_links
        )
        out.mkdir(parents=True, exist_ok=True)
        fd, part_name = tempfile.mkstemp(dir=out, prefix=".build-", suffix=".part")
        os.close(fd)
        part = Path(part_name)
        try:
            write_zip(staged, part)
            validate(part, file_name=final.name)
            os.replace(part, final)
        finally:
            _unlink(part)
    write_card(recipe, final)
    write_sums(final, out / "sums" / tag(recipe) / SUMS)
    return final


def main(argv: list[str] | None = None, *, run: Run = subprocess.run) -> int:
    parser = argparse.ArgumentParser(prog="build.py")
    sub = parser.add_subparsers(dest="cmd")
    for name in ("build", "lock", "check"):
        p = sub.add_parser(name)
        p.add_argument("tool", nargs="*")
        p.add_argument("--out", default="dist")
        p.add_argument("--recipes", default="recipes")
        p.add_argument("--cache", default=".cache")
    p = sub.add_parser("index")
    p.add_argument("cards")
    p.add_argument("--base-url", default=None)
    p.add_argument("--out", default=INDEX)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "index":
            path = write_index_from_cards(Path(args.cards), Path(args.out), base_url=args.base_url)
            print(f"ok wrote {path}")
        elif args.cmd == "build":
            out, cache = Path(args.out), Path(args.cache)
            for folder in _recipe_folders(Path(args.recipes), args.tool):
                zip_path = build_one(load_recipe(folder), out, cache, run=run)
                print(f"ok built {zip_path}")
            write_index_from_cards(out, out / INDEX, base_url=None)
            print(f"ok wrote {out / INDEX}")
        elif args.cmd == "check":
            for folder in _recipe_folders(Path(args.recipes), args.tool):
                recipe = load_recipe(folder)
                if not (folder / "LICENSE").is_file():
                    raise BuildError(f"{recipe.name}: the recipe folder has no LICENSE file")
                print(f"ok {recipe.name} {recipe.version}-{recipe.build}")
            for zip_path in sorted(Path(args.out).glob("*.zip")):
                validate(zip_path)
                print(f"ok {zip_path.name}")
        elif args.cmd == "lock":
            for folder in _recipe_folders(Path(args.recipes), args.tool):
                recipe = load_recipe(folder)
                if not args.tool and "python" not in recipe.manifest:
                    continue
                print(f"ok wrote {lock(recipe, run=run)}")
        else:
            parser.print_help()
            return 2
    except (BuildError, OSError) as exc:
        print(f"fail {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
