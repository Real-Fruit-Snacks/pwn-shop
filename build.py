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
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
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


Fetch = Callable[[str, Path, int], None]


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
    if not isinstance(name, str) or not name:
        raise BuildError("recipe: name must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise BuildError("recipe: version must be a non-empty string")
    build = _int(recipe, "build", "recipe: build")
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
        if source.to in seen:
            raise BuildError(f"recipe: {source.to} is the target of more than one source")
        seen.add(source.to)
    if manifest.get("schema", 1) != 1:
        raise BuildError("manifest: schema must be 1")
    rest = {k: v for k, v in manifest.items() if k not in ("schema", "name", "version", "build")}
    filled = {"schema": 1, "name": name, "version": version, "build": build, **rest}
    return Recipe(name, version, build, sources, filled, folder)


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


def _extract_member(archive: Path, kind: str, member: str, dest: Path, label: str) -> None:
    try:
        if kind == "tar":
            with tarfile.open(archive, "r:gz") as tf:
                try:
                    info = tf.getmember(member)
                except KeyError:
                    raise BuildError(f"{label}: no member {member!r} in the archive") from None
                if not info.isfile():
                    raise BuildError(f"{label}: member {member!r} is not a regular file")
                src = tf.extractfile(info)
                if src is None:
                    raise BuildError(f"{label}: member {member!r} cannot be read")
                with src:
                    _copy_limited(src, dest, MAX_UNPACKED_BYTES, label)
        else:
            with zipfile.ZipFile(archive) as zf:
                try:
                    zinfo = zf.getinfo(member)
                except KeyError:
                    raise BuildError(f"{label}: no member {member!r} in the archive") from None
                if zinfo.is_dir() or stat.S_IFMT(zinfo.external_attr >> 16) not in (0, stat.S_IFREG):
                    raise BuildError(f"{label}: member {member!r} is not a regular file")
                with zf.open(zinfo) as src:
                    _copy_limited(src, dest, MAX_UNPACKED_BYTES, label)
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as exc:
        raise BuildError(f"{label}: cannot read the archive ({exc})") from exc


def stage(recipe: Recipe, workdir: Path, cache: Path, *, fetch: Fetch = fetch) -> Path:
    licence = recipe.folder / "LICENSE"
    if not licence.is_file():
        raise BuildError(f"{recipe.name}: the recipe folder has no LICENSE file")
    root = workdir / f"{recipe.name}-{recipe.version}-{recipe.build}"
    if root.exists():
        raise BuildError(f"{root} already exists")
    try:
        root.mkdir(parents=True)
        _write_toml(root / MANIFEST, recipe.manifest)
        _write_bytes(root / "LICENSE", licence.read_bytes())
        for source in recipe.sources:
            downloaded = fetch_source(source, cache, fetch=fetch)
            target = root.joinpath(*source.to.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.member is None:
                shutil.copyfile(downloaded, target)
            else:
                _extract_member(downloaded, _archive_kind(source.url), source.member, target, source.url)
    except OSError as exc:
        raise BuildError(f"{recipe.name}: cannot lay out the package ({exc})") from exc
    return root


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


def _version_key(version: str) -> tuple:
    # numbers compare as numbers and sort before words; pwn-broker defines no order of its own
    parts = [p for p in re.split(r"[.\-+_]", version) if p]
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts)


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


def write_index_from_cards(folder: Path, out: Path, *, base_url: str | None) -> Path:
    cards = []
    for path in sorted(folder.glob("*.toml")):
        if path.name == INDEX:
            continue
        data = _read_toml(path)
        for key in ("name", "version", "build", "file", "size", "sha256", "kind"):
            if key not in data:
                raise BuildError(f"{path.name}: not a card (no {key})")
        cards.append(data)
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


def validate(zip_path: Path) -> None:
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
            if zip_path.name != expected:
                raise BuildError(f"the file is named {zip_path.name}, the manifest says {expected}")
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
        folders = [recipes / n for n in names]
        for f in folders:
            if not (f / "recipe.toml").is_file():
                raise BuildError(f"no recipe named {f.name!r} in {recipes}")
        return folders
    return sorted(p for p in recipes.iterdir() if (p / "recipe.toml").is_file()) if recipes.is_dir() else []


def build_one(recipe: Recipe, out: Path, cache: Path, *, fetch_fn: Fetch | None = None) -> Path:
    with tempfile.TemporaryDirectory(prefix="pwn-shop-") as tmp:
        staged = stage(recipe, Path(tmp), cache, fetch=fetch_fn or fetch)
        zip_path = write_zip(staged, out / zip_name(recipe.name, recipe.version, recipe.build))
    validate(zip_path)
    write_card(recipe, zip_path)
    write_sums(zip_path, out / "sums" / tag(recipe) / SUMS)
    return zip_path


def main(argv: list[str] | None = None) -> int:
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
                zip_path = build_one(load_recipe(folder), out, cache)
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
            raise BuildError("lock is not available yet")
        else:
            parser.print_help()
            return 2
    except BuildError as exc:
        print(f"fail {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
