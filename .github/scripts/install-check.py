"""Installs every package in a folder with pwn-broker's own code and starts each command.

Run it with the interpreter of the virtual environment that has pwn-broker installed:

    install-check.py <folder> [--tool NAME ...]

The folder holds the zips and the index.toml that build.py writes. For each tool the index
names, a temporary install root and home are made, a box is created, the tool is installed
through toolinstall.install, each command is run with --help (then -h), and the tool is
released again. This is the only file in the repository that imports pwn_broker; build.py
and the unit tests never do.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# a command that prints something and exits 0, 1 or 2 has started; a missing file, a crash
# on import and a hang all end differently
STARTED = (0, 1, 2)
PROBES = ("--help", "-h")
TIMEOUT = 60


class CheckFailed(Exception):
    pass


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="install-check.py")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--tool", action="append", default=[], metavar="NAME")
    return parser.parse_args(argv)


def started(returncode: int, output: str) -> bool:
    return returncode in STARTED and bool(output.strip())


def probe(launcher: Path, *, run=subprocess.run, env=None) -> tuple[bool, str]:
    """Runs the launcher with --help, then -h. Returns whether it started and a note."""
    notes = []
    for flag in PROBES:
        try:
            result = run(
                [str(launcher), flag],
                capture_output=True, text=True, errors="replace",
                stdin=subprocess.DEVNULL, timeout=TIMEOUT, env=env,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            notes.append(f"{flag}: no answer in {TIMEOUT} seconds")
            continue
        except OSError as exc:
            return False, f"cannot run it ({exc})"
        output = (result.stdout or "") + (result.stderr or "")
        if started(result.returncode, output):
            return True, flag
        said = "printed something" if output.strip() else "printed nothing"
        notes.append(f"{flag}: exit {result.returncode}, {said}")
    return False, "; ".join(notes)


def upload_problems(zip_path: Path, upload_dir: Path) -> list[str]:
    """Each upload file must be present with the size the package holds."""
    problems = []
    with zipfile.ZipFile(zip_path) as zf:
        manifest = tomllib.loads(zf.read("manifest.toml").decode("utf-8"))
        for row in manifest.get("upload", []):
            target = upload_dir / row["as"]
            expected = zf.getinfo(row["path"]).file_size
            if not target.is_file():
                problems.append(f"{row['as']} is missing from {upload_dir}")
            elif target.stat().st_size != expected:
                problems.append(
                    f"{row['as']} is {target.stat().st_size} bytes, the package holds {expected}"
                )
    return problems


def leftovers(opt: Path, name: str, commands: list[str]) -> list[str]:
    """What is still under the install root after the tool was released."""
    found = []
    for path in (opt / "tools" / name, opt / "upload" / name):
        if os.path.lexists(path):
            found.append(str(path))
    for command in commands:
        path = opt / "bin" / command
        if os.path.lexists(path):
            found.append(str(path))
    return found


def launcher_env(source, opt: Path, box_home: Path, user_home: Path) -> dict:
    """What a launcher gets: not the runner's whole environment, which holds tokens."""
    env = {"PATH": source.get("PATH", os.defpath)}
    for key in ("LANG", "LC_ALL"):
        if key in source:
            env[key] = source[key]
    env["HOME"] = str(user_home)
    env["PWN_BROKER_OPT"] = str(opt)
    env["PWN_BROKER_HOME"] = str(box_home)
    return env


def load_broker():
    try:
        from pwn_broker import boxes, config, toolindex, toolinstall, toolstate
        from pwn_broker.console import FakeConsole
        from pwn_broker.store import write_toml
    except ImportError as exc:
        raise SystemExit(
            f"fail  pwn_broker cannot be imported ({exc}); run this with the interpreter "
            "of the environment that has pwn-broker installed"
        ) from exc
    return SimpleNamespace(
        boxes=boxes, config=config, toolindex=toolindex, toolinstall=toolinstall,
        toolstate=toolstate, FakeConsole=FakeConsole, write_toml=write_toml,
    )  # fmt: skip


def check_tool(broker, folder: Path, name: str, now: datetime) -> str:
    """Installs, starts and releases one tool in a temporary root. Returns a note for the ok line."""
    with tempfile.TemporaryDirectory(prefix="pwn-shop-check-") as tmp:
        root = Path(tmp)
        opt, box_home, user_home = root / "opt", root / "boxes", root / "home"
        for made in (opt, box_home, user_home):
            made.mkdir()
        broker.write_toml(opt / "config.toml", {"tools_index": str(folder.resolve())})
        env = launcher_env(os.environ, opt, box_home, user_home)
        paths, settings = broker.config.load(env, user_home)

        index = broker.toolindex.load_index(paths, settings, now=now)
        tool = broker.toolindex.find_tool(index, name)
        entry = broker.toolindex.pick_build(tool, None)
        label = f"{entry.name} {entry.version}-{entry.build}"
        zip_path = broker.toolindex.fetch_zip(paths, settings, entry)

        box = broker.boxes.create_box(
            paths, name="ci", platform="htb", day=now.date(), os_name="linux",
            difficulty="easy", now=now,
        )  # fmt: skip
        installed = broker.toolstate.load_installed(paths)
        unfinished = broker.toolstate.unfinished_boxes(paths)
        console = broker.FakeConsole()
        try:
            item = broker.toolinstall.install(
                paths, box, entry, zip_path,
                installed=installed, unfinished=unfinished, home=user_home,
                console=console, now=now, python=sys.executable,
            )  # fmt: skip
        except Exception as exc:
            said = "; ".join(text for _, text in console.lines)
            raise CheckFailed(f"{label}: install failed ({exc}){'; ' + said if said else ''}") from exc

        started_with = []
        for command in item.commands:
            launcher = paths.bin / command
            if not launcher.is_file():
                raise CheckFailed(f"{label}: command {command} has no launcher at {launcher}")
            worked, note = probe(launcher, env=env)
            if not worked:
                raise CheckFailed(f"{label}: command {command} did not start ({note})")
            started_with.append(f"{command} {note}")
        problems = upload_problems(zip_path, paths.upload / entry.name) if item.uploads else []
        if problems:
            raise CheckFailed(f"{label}: " + "; ".join(problems))

        broker.toolinstall.release(paths, box, entry.name, installed=installed, unfinished=unfinished)
        left = leftovers(opt, entry.name, item.commands)
        if entry.name in broker.toolstate.load_installed(paths):
            left.append("an entry in the list of installed tools")
        if any(t.name == entry.name for t in box.tools):
            left.append("a record in the box")
        if left:
            raise CheckFailed(f"{label}: left behind after release: {', '.join(left)}")

        files = f"{len(item.uploads)} upload files" if item.uploads else ""
        return f"{label}: " + ", ".join([*started_with, *([files] if files else [])])


def main(argv=None) -> int:
    args = parse_args(argv)
    broker = load_broker()
    now = datetime.now(timezone.utc)
    names = args.tool
    if not names:
        index = broker.toolindex.parse_index(
            tomllib.loads((args.folder / "index.toml").read_text(encoding="utf-8"))
        )
        names = [t.name for t in index.tools]
    if not names:
        print(f"fail  {args.folder / 'index.toml'} names no tools", file=sys.stderr)
        return 1
    failed = 0
    for name in names:
        try:
            note = check_tool(broker, args.folder, name, now)
        except CheckFailed as exc:
            print(f"fail  {exc}", file=sys.stderr)
            failed += 1
        except Exception as exc:
            print(f"fail  {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1
        else:
            print(f"ok    {note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
