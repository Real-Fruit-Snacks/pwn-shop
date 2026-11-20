"""Builds pwn-broker tool packages, their cards and the index from recipes.

Standard library and tomli-w only. The formats are those pwn-broker reads; this file
reimplements the checks it makes (see validate) and never imports it.
"""

import argparse
import gzip
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
import unicodedata
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

KINDS = ("kali", "upload", "both", "runtime", "notes")
SCHEMAS = (1, 2, 3)
COMMAND_TYPES = ("binary", "python-entry", "python-script")
MANIFEST = "manifest.toml"
SUMS = "SHA256SUMS.txt"
INDEX = "index.toml"
MAX_MEMBERS = 10_000
MAX_UNPACKED_BYTES = 1024**3
# pwn-broker's limits on a name in a package (toolpkg.MAX_PATH_BYTES, MAX_PART_BYTES, MAX_DEPTH)
MAX_PATH_BYTES = 4096
MAX_PART_BYTES = 255
MAX_DEPTH = 64
MANIFEST_LIMIT = 1024 * 1024
FETCH_TIMEOUT = 60
FETCH_DEADLINE = 900.0
_CHUNK = 1024 * 1024
# a dot only between other characters, so a name is never ".", "..", hidden or ends with a dot
_NAME = re.compile(r"[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*")
_NAME_RULE = (
    "must be lower-case letters, digits, hyphens and dots, starting with a letter or digit, "
    "with no two dots together and none at the end"
)
_SERIES = re.compile(r"[0-9]+\.[0-9]+")
_NEEDS_RUNTIME = (
    "a Python tool must require a runtime: add requires = [\"python-3.13\"] (or another "
    "runtime's name) to its manifest.toml"
)
_COMMAND_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
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
# the package's own lock: pwn-broker reads exactly this shape and nothing else
LOCK_LIMIT = 1024 * 1024
_LOCK_LINE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9][A-Za-z0-9.!+_-]*( --hash=sha256:[0-9a-f]{64})+"
)
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
RESERVED_ENV = ("data", "box", "run", "pid")
# Names a launcher must never stand in for on PATH: pwn-broker's toolpkg.RESERVED_COMMANDS, in
# its groups. It is not a list of everything Kali has; a tool may take the name of Kali's own
# copy of it.
RESERVED_COMMANDS = tuple(
    # shells, and what starts a program for one
    "sh bash dash zsh fish env "
    # what asks for a password or changes who you are
    "sudo sudoedit su doas pkexec run0 passwd login newgrp visudo "
    "ssh scp sftp ssh-agent ssh-add ssh-keygen gpg "
    # Python and pip; their versioned names are refused by _VERSIONED_PYTHON
    "python python3 pip pip3 "
    # what pwn-broker runs by name
    "chown cp rm kill ip ping tar vim openvpn x-terminal-emulator pwn-broker record asciinema "
    # what a shell's start-up files, its prompt and everyday lines run
    "sed cut mkdir cat ls mv ln vi grep awk find head tail chmod id uname test "
    "whoami hostname sleep touch tee which ps tput dircolors xargs timeout nohup "
    # what other programs start by name to show or edit something
    "less more pager man nano editor "
    # what installs and manages the system
    "apt apt-get dpkg systemctl mount umount "
    # what fetches something to run
    "curl wget git".split()
)
_VERSIONED_PYTHON = re.compile(r"(?:python|pip)[0-9][A-Za-z0-9._-]*")
# a version is part of a folder's name and is printed: pwn-broker's rule
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,99}")
_VERSION_RULE = (
    "must start with a letter or digit and hold only letters, digits and . _ + ~ -, "
    "100 characters at most"
)
# Places in the home folder a tool may not claim as its own data, as pwn-broker refuses them:
# they hold the user's keys or settings, or run something at login. HOME_TREES is refused with
# everything under it, HOME_SHARED by its exact name.
HOME_TREES = (
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store", ".mozilla", ".pki",
    ".vim", ".bashrc.d", ".config/gcloud", ".config/gh", ".config/git", ".config/fish",
    ".config/chromium", ".config/autostart", ".config/systemd", ".config/environment.d",
    ".local/bin", ".local/share/keyrings", ".local/share/applications",
)  # fmt: skip
HOME_SHARED = tuple(
    ".config .local .local/share .local/state .cache .bashrc .zshrc .profile .bash_profile "
    ".bash_login .bash_logout .bash_aliases .zshenv .zprofile .zlogin .zlogout .bash_history "
    ".zsh_history .inputrc .xprofile .xinitrc .xsession .Xauthority .vimrc .tmux.conf "
    ".gitconfig .git-credentials .netrc .pgpass "
    "Desktop Documents Downloads Music Pictures Public Templates Videos".split()
)
# where programs keep a folder of their own, by their own name
HOME_PARENTS = (".config", ".local/share", ".local/state", ".cache")
# the first part of every place named above, in lower case
HOME_NOT_OWN = frozenset(
    place.split("/")[0].lower() for place in (*HOME_TREES, *HOME_SHARED, *HOME_PARENTS)
)
HOME_RULE = (
    "has to be .<name>, or <name> under .config, .local/share, .local/state or .cache, "
    "where <name> is the tool's name or one of its commands'"
)


def own_home_folder(entry: str, names: set[str]) -> bool:
    """Whether a path under the home folder is a folder named after the tool: pwn-broker's rule.

    No list of places to refuse can name everything that is someone else's, so a tool may claim
    only what is plainly its own, and anything inside it. `names` is the tool's name and its
    commands', in lower case.
    """
    parts = entry.split("/")
    # .config is not the folder of a command named config: a place the lists name, or one that
    # leads to one, is nobody's own whatever the tool is called
    if parts[0][:1] == "." and parts[0][1:].lower() in names and parts[0].lower() not in HOME_NOT_OWN:
        return True
    for parent in HOME_PARENTS:
        above = parent.split("/")
        if parts[: len(above)] == above and len(parts) > len(above):
            if parts[len(above)].lower() in names:
                return True
    return False
UV_PLATFORM = "x86_64-unknown-linux-gnu"
_TOP_FILES = (MANIFEST, "LICENSE", "wheels.lock")
_TOP_FOLDERS = ("files", "wheels")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_ZIP_ATTR = (stat.S_IFREG | 0o644) << 16
# toolinstall gives everything under files/ mode 0755 itself, so the zip carries no
# executable bits and the bytes do not depend on the OS that built them


class BuildError(Exception):
    pass


class MissingRuntime(BuildError):
    """A build requires a runtime that is not among the cards the index is made from."""

    def __init__(self, message: str, wanted: str):
        super().__init__(message)
        self.wanted = wanted


class FetchError(BuildError):
    """An upstream file could not be fetched, or is not the file the recipe pins.

    main exits with FETCH_FAILED for it, so CI can tell a file that went missing upstream
    from every other failure.
    """


FETCH_FAILED = 3


@dataclass
class Source:
    url: str
    size: int
    sha256: str
    to: str
    member: str | None = None
    # the whole zip, unpacked into the folder `to`
    unpack: bool = False


@dataclass
class Recipe:
    name: str
    version: str
    build: int
    sources: list[Source]
    manifest: dict
    folder: Path
    build_from_source: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    # a runtime's own series, or the series of the runtime a Python tool requires
    series: str | None = None


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
        or "\n" in value
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
    unpack = row.get("unpack", False)
    if not isinstance(unpack, bool):
        raise BuildError(f"{label}.unpack: must be true or false")
    member = row.get("member")
    if unpack:
        if member is not None:
            raise BuildError(f"{label}: unpack takes no member; the whole zip is unpacked")
        if not urllib.parse.urlsplit(url).path.lower().endswith(".zip"):
            raise BuildError(f"{label}: a source with unpack must be a zip")
        # a zip may be unpacked into files/ itself, a file only somewhere under it
        to = "files" if row.get("to") == "files" else _relative(row.get("to"), f"{label}.to", under="files")
    else:
        to = _relative(row.get("to"), f"{label}.to", under="files")
    if member is not None:
        member = _relative(member, f"{label}.member")
    return Source(url, _int(row, "size", f"{label}.size"), sha.lower(), to, member, unpack)


def load_recipe(folder: Path) -> Recipe:
    recipe = _read_toml(folder / "recipe.toml")
    manifest = _read_toml(folder / MANIFEST)
    name = recipe.get("name", folder.name)
    version = recipe.get("version")
    who = folder.name
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise BuildError(f"{who}: name {name!r} {_NAME_RULE}")
    if name != folder.name:
        raise BuildError(f"{who}: the folder name must equal the recipe's name {name!r}")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise BuildError(f"{who}: version {version!r} {_VERSION_RULE}")
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
    schema = _recipe_schema(manifest, who)
    rest = {k: v for k, v in manifest.items() if k not in ("schema", "name", "version", "build")}
    filled = {"schema": schema, "name": name, "version": version, "build": build, **rest}
    listed = recipe.get("build_from_source", [])
    if not isinstance(listed, list) or not all(isinstance(n, str) and n for n in listed):
        raise BuildError(f"{who}: build_from_source must be a list of distribution names")
    requires = []
    if "requires" in manifest:
        requires = _check_requires(manifest["requires"], name, f"{who}: requires")
    series = None
    if manifest.get("kind") == "runtime":
        if requires:
            raise BuildError(f"{who}: requires: a runtime requires nothing")
        try:
            archive, _python, series = _runtime_table(manifest)
        except BuildError as exc:
            raise BuildError(f"{who}: {exc}") from exc
        # the upstream archive is carried byte for byte, so it is the one source and is not unpacked
        if len(sources) != 1:
            raise BuildError(
                f"{who}: a runtime has exactly one [[source]], its archive; this one has {len(sources)}"
            )
        if sources[0].member is not None:
            raise BuildError(f"{who}: a runtime's source is carried whole; it takes no member")
        if sources[0].to != archive:
            raise BuildError(
                f"{who}: the source goes to {sources[0].to}, but runtime.archive is {archive}; "
                "they must be the same path"
            )
    elif "python" in manifest:
        if "requires" not in manifest:
            raise BuildError(f"{who}: {_NEEDS_RUNTIME}")
        if len(requires) != 1:
            raise BuildError(
                f"{who}: requires: a Python tool requires exactly one runtime; "
                f"this one names {len(requires)}"
            )
        series = _required_series(folder.parent, requires[0], who)
    elif "requires" in manifest:
        if _has_python_command(manifest):
            raise BuildError(
                f"{who}: python: a python-entry or python-script command needs a [python] table"
            )
        raise BuildError(f"{who}: requires: a tool that is not Python requires nothing")
    return Recipe(
        name, version, build, sources, filled, folder, [_norm_name(n) for n in listed],
        requires, series,
    )  # fmt: skip


def _recipe_schema(manifest: dict, who: str) -> int:
    """3 for notes, 2 for a runtime and for a tool that requires one, else 1; a stated schema
    must agree."""
    runtime = manifest.get("kind") == "runtime"
    notes = manifest.get("kind") == "notes"
    schema = 3 if notes else 2 if runtime or "requires" in manifest else 1
    stated = manifest.get("schema", schema)
    if isinstance(stated, bool) or stated not in SCHEMAS:
        raise BuildError(f"{who}: manifest schema must be 1, 2 or 3")
    if notes and stated != 3:
        raise BuildError(f"{who}: kind: notes needs schema 3")
    if stated == 3 and not notes:
        raise BuildError(f"{who}: schema: only a notes package is schema 3")
    if stated == 1 and runtime:
        raise BuildError(f"{who}: kind: a runtime needs schema 2")
    if stated == 1 and schema == 2:
        raise BuildError(
            f"{who}: requires: a schema 1 package cannot require anything; it needs schema 2"
        )
    if stated == 2 and schema == 1:
        raise BuildError(
            f"{who}: schema: a package that is not a runtime and requires nothing must be schema 1"
        )
    return schema


def _has_python_command(manifest: dict) -> bool:
    """Whether the manifest has a command that is not a binary: what makes a package Python to
    pwn-broker, with or without a [python] table."""
    rows = manifest.get("command", [])
    return isinstance(rows, list) and any(
        isinstance(row, dict) and row.get("type") != "binary" for row in rows
    )


def _required_series(recipes: Path, wanted: str, who: str) -> str:
    """The series of the runtime a tool requires, from that runtime's manifest alone.

    The required recipe is never loaded as a recipe, so one that requires something in turn
    (a tool, or the tool asking) is refused here and cannot lead round in a loop.
    """
    folder = recipes / wanted
    if not (folder / "recipe.toml").is_file():
        raise BuildError(f"{who}: requires {wanted}, but {recipes} has no recipe for it")
    try:
        manifest = _read_toml(folder / MANIFEST)
    except BuildError as exc:
        raise BuildError(f"{who}: requires {wanted}: {exc}") from exc
    if manifest.get("kind") != "runtime":
        raise BuildError(f"{who}: requires {wanted}, which is not a runtime")
    try:
        return _runtime_table(manifest)[2]
    except BuildError as exc:
        raise BuildError(f"{who}: requires {wanted}: {exc}") from exc


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
                raise FetchError(f"{url}: more than the {limit} bytes the recipe gives")
            if time.monotonic() > deadline:
                raise FetchError(f"{url}: the download took too long")
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
            raise FetchError(f"cannot download {source.url}: {str(exc) or type(exc).__name__}") from exc
        actual = part.stat().st_size
        if actual != source.size:
            raise FetchError(f"{source.url}: size is {actual} bytes, the recipe says {source.size}")
        if sha256_file(part) != source.sha256:
            raise FetchError(f"{source.url}: SHA-256 differs from the recipe")
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
    if path.endswith(".gz"):
        return "gz"
    raise BuildError(f"{url}: a source with a member must be a tar.gz, tgz, zip or gz")


def _gz_member(url: str) -> str:
    # one file gzipped on its own has no name inside it that can be trusted, so the member is
    # the name the URL gives it, less the .gz
    name = urllib.parse.unquote(urllib.parse.urlsplit(url).path).rsplit("/", 1)[-1]
    return name[: -len(".gz")]


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
        elif kind == "gz":
            inside = _gz_member(label)
            if member != inside:
                raise BuildError(f"{label}: a gz holds one file, {inside!r}; member must name it")
            with gzip.open(archive, "rb") as src:
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
        tarfile.TarError, zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError,
        NotImplementedError,
    ) as exc:
        raise BuildError(f"{label}: cannot read the archive ({exc})") from exc


def _too_long(name: str) -> str | None:
    """pwn-broker's toolpkg.too_long: what is wrong with the length of a name in a package."""
    if len(name.encode("utf-8", "surrogateescape")) > MAX_PATH_BYTES:
        return f"longer than {MAX_PATH_BYTES} bytes"
    parts = (name[:-1] if name.endswith("/") else name).split("/")
    if any(len(part.encode("utf-8", "surrogateescape")) > MAX_PART_BYTES for part in parts):
        return f"with a part longer than {MAX_PART_BYTES} bytes"
    if len(parts) > MAX_DEPTH:
        return f"with more than {MAX_DEPTH} parts"
    return None


def _unpack_zip(archive: Path, root: Path, to: str, claim: Callable[..., None], label: str) -> None:
    """Every file in the zip, at to/<its name> under root. The entries are checked as
    pwn-broker checks a package's: nothing outside the folder, no links or special files, nothing
    encrypted, and limits on the number of entries and on the bytes actually written."""
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_MEMBERS:
                raise BuildError(f"{label}: the zip has {len(infos)} entries; the limit is {MAX_MEMBERS}")
            total = files = 0
            for info in infos:
                name = info.filename
                bare = name[:-1] if name.endswith("/") else name
                parts = bare.split("/")
                rel = f"{to}/{bare}"
                if fault := _too_long(rel + ("/" if info.is_dir() else "")):
                    raise BuildError(f"{label}: entry {name[:100]!r} has a name {fault}")
                # zipfile on Windows turns a backslash in a name into a slash, so the name as
                # stored is checked too, or the two systems would build different packages
                if (
                    not bare
                    or name.startswith("/")
                    or "\\" in info.orig_filename
                    or ":" in name
                    or any(p in ("", ".", "..") for p in parts)
                ):
                    raise BuildError(f"{label}: entry {name!r} would write outside {to}")
                # zipfile cuts a name at a NUL, so the name as stored is the one to look at
                if any(unicodedata.category(c) == "Cc" for c in info.orig_filename):
                    raise BuildError(f"{label}: entry {info.orig_filename!r} has a control character in its name")
                # Windows drops a dot or space at the end of a name, so two names would be one
                if any(p.endswith((".", " ")) for p in parts):
                    raise BuildError(f"{label}: entry {name!r} has a part that ends in a dot or a space")
                if info.flag_bits & 0x1:
                    raise BuildError(f"{label}: entry {name!r} is encrypted")
                mode = stat.S_IFMT(info.external_attr >> 16)
                wanted = stat.S_IFDIR if info.is_dir() else stat.S_IFREG
                if mode not in (0, wanted):
                    raise BuildError(f"{label}: entry {name!r} is a link or special file")
                target = root.joinpath(*rel.split("/"))
                if info.is_dir():
                    claim(rel, folder=True)
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                claim(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                # counted as it is written, since a header's size is only a claim
                with zf.open(info) as src, open(target, "wb") as out:
                    while chunk := src.read(_CHUNK):
                        total += len(chunk)
                        if total > MAX_UNPACKED_BYTES:
                            raise BuildError(
                                f"{label}: the zip unpacks to more than {MAX_UNPACKED_BYTES} bytes"
                            )
                        out.write(chunk)
                files += 1
            if not files:
                raise BuildError(f"{label}: the zip holds no files")
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError, NotImplementedError) as exc:
        raise BuildError(f"{label}: cannot read the archive ({exc})") from exc


def _python_manifest(recipe: Recipe) -> tuple[dict, bool]:
    manifest = dict(recipe.manifest)
    table = manifest.get("python")
    if table is None:
        return manifest, False
    if not isinstance(table, dict):
        raise BuildError(f"{recipe.name}: python must be a table")
    ours = {"lock": WHEELS_LOCK, "wheels": "wheels"}
    if "versions" in table:
        raise BuildError(
            f"{recipe.name}: python.versions: schema 2 has no versions; the wheels are for "
            f"the required runtime's Python {recipe.series}. Leave it out of the recipe's manifest"
        )
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
        # every file and folder the sources write, compared without case, so a build on Windows
        # and one on Linux cannot differ: one path, one spelling, and never both a file and a folder
        files: set[str] = set()
        folders: dict[str, str] = {}

        def claim(rel: str, folder: bool = False) -> None:
            parts = rel.split("/")
            for end in range(1, len(parts) + folder):
                spelt = "/".join(parts[:end])
                if spelt.casefold() in files:
                    raise BuildError(
                        f"{recipe.name}: {spelt} is both a file and a folder (compared without case)"
                    )
                other = folders.setdefault(spelt.casefold(), spelt)
                if other != spelt:
                    raise BuildError(
                        f"{recipe.name}: {spelt} and {other} name one folder in different cases"
                    )
            if folder:
                return
            if rel.casefold() in folders:
                raise BuildError(
                    f"{recipe.name}: {rel} is both a file and a folder (compared without case)"
                )
            if rel.casefold() in files:
                raise BuildError(
                    f"{recipe.name}: {rel} is written by more than one source (compared without case)"
                )
            files.add(rel.casefold())

        for source in recipe.sources:
            downloaded = fetch_source(source, cache, fetch=fetch)
            if source.unpack:
                _unpack_zip(downloaded, root, source.to, claim, source.url)
                continue
            claim(source.to)
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


def _check_wheel(path: Path, pin: Pin, python: str, *, built: bool = False) -> None:
    """A wheel must fit CPython `python`. One built here from source must also be pure Python,
    and its hash is not checked: a build's bytes differ from one machine to the next."""
    label = f"{pin.name} {pin.version}"
    m = _WHEEL.fullmatch(path.name)
    if m is None or _norm_name(m["name"]) != _norm_name(pin.name) or not _same_version(
        m["version"], pin.version
    ):
        raise BuildError(f"{label}: got {path.name}, which is not that distribution and version")
    if built:
        _check_pure(path, label)
    if not _wheel_fits(path.name, python):
        raise BuildError(
            f"{label}: {path.name} does not fit CPython {python} on x86_64 Linux ({PLATFORM})"
        )
    if built:
        return
    if sha256_file(path) not in pin.hashes:
        raise FetchError(f"{label}: {path.name} has a SHA-256 that requirements.lock does not list")


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
        done = run(
            cmd, capture_output=True, text=True, cwd=cwd,
            env=_tool_env("uv" if uv else "pip"), timeout=COMMAND_TIMEOUT,
        )  # fmt: skip
        if not uv and done.returncode != 0 and "No module named pip" in (done.stderr or ""):
            raise BuildError(
                f"pip is not installed for {sys.executable}; install it (python -m ensurepip) "
                "and run the build again"
            )
        return done
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
def _wheel_from_source(
    pin: Pin, python: str, index: int, tmp: Path, run: Run, find_links: Path | None
) -> Path:
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
        raise FetchError(f"{label}: the pinned source archive cannot be fetched ({_tail(done.stderr)})")
    archive = _only_file(src, label, "source archive")
    if archive.name.endswith(".whl"):
        raise BuildError(f"{label}: got a wheel where a source archive was expected")
    if sha256_file(archive) not in pin.hashes:
        raise FetchError(f"{label}: {archive.name} has a SHA-256 that requirements.lock does not list")
    out = tmp / f"built-{index}"
    out.mkdir()
    done = _sh(run, _pip("wheel", find_links, "--no-deps", "--wheel-dir", str(out), str(archive)))
    if done.returncode != 0:
        raise BuildError(f"{label}: building a wheel from {archive.name} failed ({_tail(done.stderr)})")
    wheel = _only_file(out, label, "built wheel")
    _check_wheel(wheel, pin, python, built=True)
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
        raise FetchError(
            f"{label}: no wheel could be downloaded for Python {python} ({_tail(done.stderr)}). "
            "If the distribution publishes no wheel, add it to build_from_source in the recipe."
        )
    wheel = _only_file(dest, label, "downloaded file")
    _check_wheel(wheel, pin, python)
    return wheel


def _series(recipe: Recipe) -> str:
    """The one Python a tool's wheels are for: the series of the runtime it requires."""
    if recipe.series is None:
        raise BuildError(f"{recipe.name}: {_NEEDS_RUNTIME}")
    return recipe.series


def collect_wheels(
    recipe: Recipe, staged: Path, *, run: Run = subprocess.run, find_links: Path | None = None
) -> list[Path]:
    python = _series(recipe)
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
        for i, pin in enumerate(pins):
            if _norm_name(pin.name) in recipe.build_from_source:
                wheel = _wheel_from_source(pin, python, i, tmp, run, find_links)
            else:
                wheel = _wheel_for(pin, python, i, tmp, run, find_links)
            shutil.copyfile(wheel, wheels / wheel.name)
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
    python = _series(recipe)
    path = recipe.folder / REQUIREMENTS_LOCK
    with tempfile.TemporaryDirectory(prefix="pwn-shop-lock-") as tmpdir:
        out = Path(tmpdir) / "requirements.lock"
        # not --universal: it cannot be combined with --python-platform, and it writes
        # markers such as sys_platform == 'win32' that pip would judge by the build machine
        cmd = ["uv", "pip", "compile", REQUIREMENTS_IN, "--generate-hashes",
               "--python-version", python, "--python-platform", UV_PLATFORM,
               "--no-header", "--no-annotate", "-o", str(out)]  # fmt: skip
        done = _sh(run, cmd, recipe.folder)
        if done.returncode != 0:
            raise BuildError(f"{recipe.name}: uv failed for Python {python} ({_tail(done.stderr)})")
        try:
            text = out.read_bytes().replace(b"\r\n", b"\n")
        except OSError as exc:
            raise BuildError(f"{recipe.name}: uv wrote no lock for Python {python} ({exc})") from exc
        out.write_bytes(text)
        # the same checks a build makes, before the recipe's lock is replaced
        read_requirements_lock(out, recipe.name)
    _write_bytes(path, text)
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
    # requires is left out when empty, so the cards of schema 1 builds stay as published
    required = {"requires": list(recipe.requires)} if recipe.requires else {}
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
        **required,
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


def _check_shared_commands(commands: dict[str, list[str]]) -> None:
    """commands maps a tool to the commands of its latest build."""
    owner: dict[str, str] = {}
    for tool, names in commands.items():
        for command in names:
            if owner.setdefault(command, tool) != tool:
                raise BuildError(f"the command {command} is defined by both {owner[command]} and {tool}")


def _check_index_build(name: str, c: dict) -> list[str]:
    """The rules pwn-broker's index parser applies to one build; returns its requires."""
    label = f"{name} {c['version']}-{c['build']}"
    if c["kind"] not in KINDS:
        raise BuildError(f"{label}: kind must be one of {', '.join(KINDS)}")
    requires = _check_requires(c.get("requires", []), name, f"{label}: requires")
    if c["kind"] == "runtime":
        if c["commands"]:
            raise BuildError(f"{label}: a runtime has no commands")
        if requires:
            raise BuildError(f"{label}: requires: a runtime requires nothing")
    if c["kind"] == "notes":
        if c["commands"]:
            raise BuildError(f"{label}: a notes package has no commands")
        if requires:
            raise BuildError(f"{label}: requires: a notes package requires nothing")
        if c["needs_root"]:
            raise BuildError(f"{label}: a notes package never needs root")
    return requires


def build_index(cards: list[dict], *, base_url: str | None) -> dict:
    grouped: dict[str, list[dict]] = {}
    for c in cards:
        grouped.setdefault(c["name"], []).append(c)
    _check_shared_commands({name: rows[-1]["commands"] for name, rows in grouped.items()})
    tools = []
    needs: list[tuple[str, str, str]] = []
    # schema 2 or 3 only when a build needs it, so an index of older builds stays as it was: 3
    # for a notes build, which a pwn-broker older than 0.1.7 cannot read and tells to upgrade
    schema = 1
    notes = False
    for name, rows in grouped.items():
        if not _NAME.fullmatch(name):
            raise BuildError(f"name {name!r} {_NAME_RULE}")
        if len({c["kind"] == "runtime" for c in rows}) > 1:
            raise BuildError(f"{name}: its builds mix kind runtime with other kinds")
        if len({c["kind"] == "notes" for c in rows}) > 1:
            raise BuildError(f"{name}: its builds mix kind notes with other kinds")
        seen = set()
        builds = []
        for c in rows:
            key = (c["version"], c["build"])
            if key in seen:
                raise BuildError(f"{name}: build {c['version']}-{c['build']} appears more than once")
            seen.add(key)
            requires = _check_index_build(name, c)
            needs.extend((name, f"{c['version']}-{c['build']}", wanted) for wanted in requires)
            if requires or c["kind"] == "runtime":
                schema = 2
            notes = notes or c["kind"] == "notes"
            builds.append(
                {
                    "version": c["version"],
                    "build": c["build"],
                    "kind": c["kind"],
                    "needs_root": c["needs_root"],
                    "commands": list(c["commands"]),
                    **({"requires": requires} if requires else {}),
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
    runtimes = {name for name, rows in grouped.items() if rows[0]["kind"] == "runtime"}
    for name, build_id, wanted in needs:
        if wanted not in grouped:
            raise MissingRuntime(
                f"{name} {build_id}: requires {wanted}, which is not in the index", wanted
            )
        if wanted not in runtimes:
            raise BuildError(f"{name} {build_id}: requires {wanted}, which is not a runtime")
    return {"schema": 3 if notes else schema, "tool": tools}


_CARD_FILE = re.compile(_NAME.pattern + r"-[^/\\\s:]+-[1-9][0-9]*\.toml")
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
    # a card written before runtimes has no requires, which means none
    requires = data.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
        raise BuildError(f"{path.name}: requires must be a list of strings")
    return data


def write_index_from_cards(folder: Path, out: Path, *, base_url: str | None) -> Path:
    cards = [_read_card(p) for p in sorted(folder.glob("*.toml")) if _CARD_FILE.fullmatch(p.name)]
    if not cards:
        raise BuildError(f"{folder}: no cards found")
    _write_toml(out, build_index(card_order(cards), base_url=base_url))
    return out


# ---- publishing helpers (CI)


def has_bundled_wheels(recipe: Recipe) -> bool:
    return "python" in recipe.manifest


def card_changes(built: dict, published: dict, *, ignore: tuple[str, ...] = ()) -> list[str]:
    keys = sorted((set(built) | set(published)) - set(ignore))
    return [k for k in keys if built.get(k) != published.get(k)]


WHEELS_LOCK_LIMIT = 8 * 1024 * 1024


def _zip_member(zip_path: Path, name: str, limit: int) -> bytes | None:
    """The bytes of one named member, or None when the zip has none."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            found = [i for i in zf.infolist() if i.filename == name]
            if len(found) > 1:
                raise BuildError(f"{zip_path.name}: {name} appears more than once")
            if not found:
                return None
            info = found[0]
            if info.is_dir():
                raise BuildError(f"{zip_path.name}: {name} is a folder")
            if info.file_size > limit:
                raise BuildError(f"{zip_path.name}: {name} is too large")
            with zf.open(info) as src:
                data = src.read(limit + 1)
            if len(data) > limit:
                raise BuildError(f"{zip_path.name}: {name} is too large")
            return data
    except (zipfile.BadZipFile, OSError) as exc:
        raise BuildError(f"{zip_path.name}: cannot be read as a zip ({exc})") from exc


def _zip_digests(zip_path: Path, left_out: list[str]) -> dict[str, tuple[int, str]]:
    """Name -> (size, SHA-256) of every file in a zip, except the wheels of left_out."""
    found: dict[str, tuple[int, str]] = {}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
                raise BuildError(f"{zip_path.name}: unpacks to more than {MAX_UNPACKED_BYTES} bytes")
            for info in infos:
                name = info.filename
                limits = {MANIFEST: MANIFEST_LIMIT, WHEELS_LOCK: WHEELS_LOCK_LIMIT}
                if name in limits and info.file_size > limits[name]:
                    raise BuildError(f"{zip_path.name}: {name} is too large")
                if name in found:
                    raise BuildError(f"{zip_path.name}: {name} appears more than once")
                if _left_out_wheel(name, left_out):
                    continue
                digest = hashlib.sha256()
                with zf.open(info) as src:
                    while chunk := src.read(_CHUNK):
                        digest.update(chunk)
                found[name] = (info.file_size, digest.hexdigest())
    except (zipfile.BadZipFile, OSError) as exc:
        raise BuildError(f"{zip_path.name}: cannot be read as a zip ({exc})") from exc
    return found


def _left_out_wheel(name: str, left_out: list[str]) -> bool:
    folder, _, base = name.rpartition("/")
    found = _WHEEL.fullmatch(base)
    return folder == "wheels" and found is not None and _norm_name(found["name"]) in left_out


def _lock_lines(data: bytes, left_out: list[str]) -> list[str]:
    kept = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        if line and _norm_name(line.split("==", 1)[0]) in left_out:
            continue
        kept.append(line)
    return kept


def zip_changes(built_zip: Path, published_zip: Path, recipe: Recipe) -> list[str]:
    """The members that differ between two zips of one build.

    Every member counts (name, size, SHA-256) except the wheels of the distributions in
    build_from_source, whose bytes differ between builds by nature, and those distributions'
    lines in wheels.lock.
    """
    left_out = recipe.build_from_source
    new = _zip_digests(built_zip, left_out)
    old = _zip_digests(published_zip, left_out)
    changed = sorted(n for n in new.keys() | old.keys() if new.get(n) != old.get(n) and n != WHEELS_LOCK)
    if (WHEELS_LOCK in new) != (WHEELS_LOCK in old):
        changed.append(WHEELS_LOCK)
    elif WHEELS_LOCK in new and new[WHEELS_LOCK] != old[WHEELS_LOCK]:
        newer = _zip_member(built_zip, WHEELS_LOCK, WHEELS_LOCK_LIMIT) or b""
        older = _zip_member(published_zip, WHEELS_LOCK, WHEELS_LOCK_LIMIT) or b""
        if _lock_lines(newer, left_out) != _lock_lines(older, left_out):
            changed.append(WHEELS_LOCK)
    return changed


def compare_cards(
    built: Path,
    published: Path,
    recipe: Recipe,
    built_zip: Path | None = None,
    published_zip: Path | None = None,
) -> None:
    # The card's size and SHA-256 cover the whole zip, and a zip is reproducible: the same
    # recipe gives the same bytes on any machine. Only a distribution built from source
    # differs between builds, so only then are the two zips compared member by member.
    listed = bool(recipe.build_from_source)
    ignore = ("size", "sha256") if listed else ()
    changed = card_changes(_read_card(built), _read_card(published), ignore=ignore)
    if listed:
        if built_zip is None or published_zip is None:
            raise BuildError(f"{recipe.name}: comparing a package with build_from_source needs both zips")
        changed += zip_changes(built_zip, published_zip, recipe)
    if changed:
        raise BuildError(
            f"{recipe.name} {recipe.version}-{recipe.build} differs from its published build "
            f"in {', '.join(changed)}; raise build in recipes/{recipe.folder.name}/recipe.toml, "
            "because a published build is never replaced"
        )


def _fence(text: str) -> str:
    # a fenced block shows the text literally; the fence is longer than any run of backticks in it
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def _notes_requires(card: dict) -> str:
    if card.get("kind") == "runtime":
        return "A runtime: pwn-broker installs it with the tools that require it.\n"
    if card.get("requires"):
        return f"Requires: {', '.join(card['requires'])}\n"
    return ""


def release_notes(card: dict) -> str:
    return (
        f"{card['name']} {card['version']}, build {card['build']}\n"
        "\n"
        f"{_fence(card['description'])}\n"
        "\n"
        f"Upstream: {card['upstream']}\n"
        f"Licence: {card['licence']}\n"
        f"{_notes_requires(card)}"
        f"Package: {card['file']} ({card['size']} bytes)\n"
        f"SHA-256: {card['sha256']}\n"
        "\n"
        "Built from the upstream files pinned in the recipe. pwn-broker checks the SHA-256 "
        "above before it installs the package.\n"
    )


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
    if "/" in value or "\\" in value or "\0" in value or "\n" in value or value in (".", ".."):
        raise BuildError(f"{label}: {value!r} must be a plain file name")


def _check_requires(value: object, name: str, label: str) -> list[str]:
    """requires names packages; each name later becomes a folder name in pwn-broker."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise BuildError(f"{label}: must be a list of strings")
    seen = set()
    for wanted in value:
        if not _NAME.fullmatch(wanted):
            raise BuildError(f"{label}: {wanted!r} is not a valid package name")
        if wanted == name:
            raise BuildError(f"{label}: {name} cannot require itself")
        if wanted in seen:
            raise BuildError(f"{label}: {wanted!r} appears more than once")
        seen.add(wanted)
    return list(value)


def _runtime_table(data: dict) -> tuple[str, str, str]:
    """A runtime is nothing but its archive. Returns its archive, interpreter and series."""
    for key, table in (
        ("command", "[[command]]"),
        ("upload", "[[upload]]"),
        ("data", "[data]"),
        ("python", "[python]"),
    ):
        if key in data:
            raise BuildError(f"{key}: a runtime has no {table}")
    table = data.get("runtime")
    if table is None:
        raise BuildError("runtime: a runtime package needs a [runtime] table")
    if not isinstance(table, dict):
        raise BuildError("runtime: must be a table")
    archive = _relative(_text(table, "archive", "runtime.archive"), "runtime.archive", under="files")
    python = _relative(_text(table, "python", "runtime.python"), "runtime.python")
    series = _text(table, "series", "runtime.series")
    if not _SERIES.fullmatch(series):
        raise BuildError(f"runtime.series: {series!r} must be a Python series such as 3.13")
    return archive, python, series


# what a notes package may not carry, each with why it is refused (pwn-broker's toolpkg)
_NOT_IN_NOTES = (
    ("command", "command: a notes package has no commands"),
    ("upload", "upload: a notes package has no uploads"),
    ("python", "python: a notes package has no Python"),
    ("requires", "requires: a notes package requires nothing"),
    ("data", "data: a notes package keeps no tool data"),
    ("runtime", "runtime: a notes package is not a runtime"),
)


def _check_notes(data: dict) -> None:
    """A notes package: files to read, nothing to run, upload or keep, for any machine."""
    for key, message in _NOT_IN_NOTES:
        if key in data:
            raise BuildError(message)
    if data.get("needs_root", False):
        raise BuildError("needs_root: a notes package never needs root")
    for key in ("description", "upstream", "licence", "arch"):
        _text(data, key, key)
    if data["arch"] != "any":
        raise BuildError('arch: a notes package is for any machine (arch = "any")')
    _strings(data, "tags", "tags")
    _strings(data, "examples", "examples")
    table = data.get("notes")
    if table is None:
        raise BuildError("notes: a notes package needs a [notes] table")
    if not isinstance(table, dict):
        raise BuildError("notes: must be a table")
    _relative(_text(table, "root", "notes.root"), "notes.root", under="files")


def _check_manifest(data: dict) -> tuple[list[str], str | None, list[str]]:
    """Returns the files the manifest names, its wheels folder (or None) and its lock path."""
    schema = data.get("schema")
    if isinstance(schema, bool) or schema not in SCHEMAS:
        raise BuildError("schema: must be 1, 2 or 3")
    name = _text(data, "name", "name")
    if not _NAME.fullmatch(name):
        raise BuildError(f"name: {name!r} {_NAME_RULE}")
    version = _text(data, "version", "version")
    if not _VERSION.fullmatch(version):
        raise BuildError(f"version: {version!r} {_VERSION_RULE}")
    build = data.get("build")
    if not isinstance(build, int) or isinstance(build, bool) or build < 1:
        raise BuildError("build: must be a whole number of 1 or more")
    kind = data.get("kind")
    if kind not in KINDS:
        raise BuildError(f"kind: must be one of {', '.join(KINDS)}")
    if not isinstance(data.get("needs_root", False), bool):
        raise BuildError("needs_root: must be true or false")
    if kind == "runtime" and schema == 1:
        raise BuildError("kind: a runtime needs schema 2")
    if kind == "notes" and schema != 3:
        raise BuildError("kind: notes needs schema 3")
    if schema == 3 and kind != "notes":
        raise BuildError("schema: only a notes package is schema 3")
    if kind == "notes":
        _check_notes(data)
        return [], None, []
    requires: list[str] = []
    if "requires" in data:
        if schema == 1:
            raise BuildError("requires: a schema 1 package cannot require anything; it needs schema 2")
        requires = _check_requires(data["requires"], name, "requires")
    named: list[str] = []
    if kind == "runtime":
        if requires:
            raise BuildError("requires: a runtime requires nothing")
        named.append(_runtime_table(data)[0])
    for key in ("description", "upstream", "licence", "arch"):
        _text(data, key, key)
    if data["arch"] != "x86_64":
        raise BuildError(f"arch: {data['arch']!r} must be x86_64")
    _strings(data, "tags", "tags")
    _strings(data, "examples", "examples")

    seen = set()
    commands = _rows(data, "command")
    for i, row in enumerate(commands):
        label = f"command[{i}]"
        cname = _text(row, "name", f"{label}.name")
        if not _COMMAND_NAME.fullmatch(cname):
            raise BuildError(f"{label}.name: {cname!r} is not a valid command name")
        if cname in seen:
            raise BuildError(f"command.name: {cname!r} appears more than once")
        if cname in RESERVED_COMMANDS or _VERSIONED_PYTHON.fullmatch(cname):
            raise BuildError(f"{label}.name: {cname!r} is reserved; it would shadow a system command")
        seen.add(cname)
        ctype = row.get("type")
        if ctype not in COMMAND_TYPES:
            raise BuildError(f"{label}.type: must be one of {', '.join(COMMAND_TYPES)}")
        env = row.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise BuildError(f"{label}.env: must be a table of strings")
        for key, value in env.items():
            if not _ENV_NAME.fullmatch(key):
                raise BuildError(f"{label}.env: {key!r} is not a valid variable name")
            if key in RESERVED_ENV:
                raise BuildError(f"{label}.env: {key!r} is reserved for the launcher")
            if "\n" in value or "\0" in value:
                raise BuildError(f"{label}.env: the value of {key} holds a newline or NUL")
        for value in _strings(row, "args", f"{label}.args"):
            if "\n" in value or "\0" in value:
                raise BuildError(f"{label}.args: {value!r} holds a newline or NUL")
        if ctype == "binary":
            named.append(_relative(_text(row, "path", f"{label}.path"), f"{label}.path", under="files"))
        elif ctype == "python-script":
            named.append(
                _relative(_text(row, "script", f"{label}.script"), f"{label}.script", under="files")
            )
        else:
            _plain(_text(row, "entry", f"{label}.entry"), f"{label}.entry")
    uploads = _rows(data, "upload")
    shown_as = set()
    for i, row in enumerate(uploads):
        label = f"upload[{i}]"
        named.append(_relative(_text(row, "path", f"{label}.path"), f"{label}.path", under="files"))
        _plain(_text(row, "as", f"{label}.as"), f"{label}.as")
        if row["as"] in shown_as:
            raise BuildError(f"upload.as: {row['as']!r} appears more than once")
        shown_as.add(row["as"])
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
        if schema == 1:
            versions = _strings(table, "versions", "python.versions")
            if not versions or not all(versions):
                raise BuildError("python.versions: needs at least one version")
        elif "versions" in table:
            raise BuildError(
                "python.versions: schema 2 has no versions; the wheels are for the required runtime"
            )
        for key in ("lock", "wheels"):
            value = _relative(_text(table, key, f"python.{key}"), f"python.{key}")
            if value.split("/")[0] == "files":
                raise BuildError(f"python.{key}: {value!r} must not be under files/")
            if key == "lock":
                lock.append(value)
            else:
                wheels = value
    if schema == 2 and kind != "runtime":
        # schema 2 is only for what schema 1 cannot say, so published builds stay as they are
        if table is not None and len(requires) != 1:
            raise BuildError(
                f"requires: a Python tool requires exactly one runtime; this one names {len(requires)}"
            )
        if table is None and requires:
            raise BuildError("requires: a tool that is not Python requires nothing")
        if not requires:
            raise BuildError(
                "schema: a package that is not a runtime and requires nothing must be schema 1"
            )
    home = data.get("data", {})
    if not isinstance(home, dict):
        raise BuildError("data: must be a table")
    for entry in _strings(home, "home", "data.home"):
        _relative(entry, "data.home")
        if entry in HOME_SHARED or any(entry == t or entry.startswith(t + "/") for t in HOME_TREES):
            raise BuildError(f"data.home: {entry!r} is a home folder other programs use")
        own = {name.lower(), *(row["name"].lower() for row in commands)}
        if not own_home_folder(entry, own):
            raise BuildError(f"data.home: {entry!r} is not this tool's own folder; it {HOME_RULE}")
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
            for name in lock:
                _check_lock(zf, seen[name])
            if data["kind"] == "notes":
                root = data["notes"]["root"]
                if not any(
                    not i.is_dir() and b.startswith(root + "/") and b.endswith(".md")
                    for b, i in seen.items()
                ):
                    raise BuildError(f"{root}: the package holds no .md files")
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


def _check_lock(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> None:
    """One pin per line, each with hashes, and nothing else: the shape pwn-broker reads."""
    name = info.filename
    if info.file_size > LOCK_LIMIT:
        raise BuildError(f"{name} is too large")
    try:
        text = zf.read(info).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"{name} is not UTF-8 text ({exc})") from exc
    if not text:
        raise BuildError(f"{name} is empty")
    if not text.endswith("\n"):
        raise BuildError(f"{name} does not end with a line break")
    lines = text.split("\n")
    lines.pop()
    for number, line in enumerate(lines, 1):
        if not _LOCK_LINE.fullmatch(line):
            raise BuildError(
                f"{name}: line {number} must be name==version followed by --hash=sha256: "
                "options, with no blank lines, comments or carriage returns"
            )


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
            # mkstemp makes the file owner-only; a zip is published and shared
            os.chmod(part, 0o644)
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
        if name == "build":
            # for a tool built alone, as CI rebuilds a published one: its runtime is not in --out
            p.add_argument("--skip-index", action="store_true")
    p = sub.add_parser("plan")
    p.add_argument("--recipes", default="recipes")
    p = sub.add_parser("compare")
    p.add_argument("built")
    p.add_argument("published")
    p.add_argument("built_zip")
    p.add_argument("published_zip")
    p.add_argument("--recipes", default="recipes")
    p = sub.add_parser("notes")
    p.add_argument("card")
    p = sub.add_parser("index")
    p.add_argument("cards")
    p.add_argument("--base-url", default=None)
    p.add_argument("--out", default=INDEX)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "index":
            path = write_index_from_cards(Path(args.cards), Path(args.out), base_url=args.base_url)
            print(f"ok wrote {path}")
        elif args.cmd == "plan":
            folders = _recipe_folders(Path(args.recipes), [])
            if not folders:
                raise BuildError(f"no recipes in {args.recipes}")
            recipes = [load_recipe(folder) for folder in folders]
            _check_shared_commands(
                {r.name: [c["name"] for c in r.manifest.get("command", [])] for r in recipes}
            )
            for recipe in recipes:
                print(recipe.name, tag(recipe), zip_name(recipe.name, recipe.version, recipe.build))
        elif args.cmd == "compare":
            name = _read_card(Path(args.built))["name"]
            recipe = load_recipe(_recipe_folders(Path(args.recipes), [name])[0])
            compare_cards(
                Path(args.built), Path(args.published), recipe,
                Path(args.built_zip), Path(args.published_zip),
            )  # fmt: skip
            print(f"ok {recipe.name} {recipe.version}-{recipe.build} matches its published build")
        elif args.cmd == "notes":
            sys.stdout.write(release_notes(_read_card(Path(args.card))))
        elif args.cmd == "build":
            out, cache = Path(args.out), Path(args.cache)
            for folder in _recipe_folders(Path(args.recipes), args.tool):
                zip_path = build_one(load_recipe(folder), out, cache, run=run)
                print(f"ok built {zip_path}")
            if not args.skip_index:
                try:
                    write_index_from_cards(out, out / INDEX, base_url=None)
                except MissingRuntime as exc:
                    raise BuildError(
                        f"{exc}. {out / INDEX} was not written: build {exc.wanted} into {out} "
                        "as well, or pass --skip-index"
                    ) from exc
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
    except FetchError as exc:
        print(f"fail {exc}", file=sys.stderr)
        return FETCH_FAILED
    except (BuildError, OSError) as exc:
        print(f"fail {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
