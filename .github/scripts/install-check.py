"""Installs every package in a folder with pwn-broker's own code and starts each command.

Run it with the interpreter of the virtual environment that has pwn-broker installed:

    install-check.py <folder> [--tool NAME ...] [--network-off]

The folder holds the zips and the index.toml that build.py writes. Each package the index
names is checked in a temporary install root, app home and user home of its own:

- a tool is installed for a new box the way tool install does it, through
  toolhooks.install_tool, so the runtime it requires is installed first from the same index.
  Each command is run with --help (then -h), a tool that requires a runtime must have its
  virtual environment made by that runtime's interpreter, and then the tool is removed the way
  tool remove does it, runtime and all;
- a runtime on its own is unpacked through toolinstall.install_runtime, its interpreter must
  report the version the index gives, and it is removed again.

With --network-off, the check first shows that no name resolves and no address can be
reached, and stops if either can: it is how the Kali job proves its installs reach nothing.

This is the only file in the repository that imports pwn_broker; build.py and the unit tests
never do.
"""

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import tomllib
import unicodedata
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# A binary that prints something and exits 0, 1 or 2 has started; a missing file, a crash
# and a hang all end differently. A Python command must exit 0, because a tool that dies
# importing a missing module exits 1 and prints a traceback. A traceback is never "started".
STARTED = (0, 1, 2)
PROBES = ("--help", "-h")
TIMEOUT = 60
TRACEBACK = "Traceback (most recent call last)"
SHOWN = 80
PYTHON_TYPES = ("python-entry", "python-script")
# what --network-off tries: a name to resolve, and an address to reach without one
PUBLIC_NAME = "github.com"
PUBLIC_ADDRESS = ("1.1.1.1", 443)
NETWORK_TIMEOUT = 5


class CheckFailed(Exception):
    pass


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="install-check.py")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--tool", action="append", default=[], metavar="NAME")
    parser.add_argument("--network-off", action="store_true")
    return parser.parse_args(argv)


def is_python(command_type: str) -> bool:
    return command_type in PYTHON_TYPES


def started(returncode: int, output: str, *, python: bool = False) -> bool:
    if TRACEBACK in output or not output.strip():
        return False
    return returncode == 0 if python else returncode in STARTED


def first_line(output: str) -> str:
    """The first line with something on it, without control characters, cut short."""
    for raw in output.splitlines():
        line = "".join(c for c in raw if unicodedata.category(c) != "Cc").strip()
        if line:
            return line[:SHOWN]
    return ""


def probe(launcher: Path, *, python: bool = False, run=subprocess.run, env=None) -> tuple[bool, str]:
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
            if python:
                break
            continue
        except OSError as exc:
            return False, f"cannot run it ({exc})"
        output = (result.stdout or "") + (result.stderr or "")
        if started(result.returncode, output, python=python):
            return True, f"{flag}: {first_line(output)}"
        if TRACEBACK in output:
            said = "printed a traceback"
        elif output.strip():
            said = "printed something"
        else:
            said = "printed nothing"
        notes.append(f"{flag}: exit {result.returncode}, {said}")
        # -h is for a command that rejects --help; a Python command that crashed, or that
        # exited 0 and said nothing, has nothing to retry
        if python and (result.returncode == 0 or TRACEBACK in output):
            break
    return False, "; ".join(notes)


def command_types(zip_path: Path) -> dict[str, str]:
    """Each command's name and type, from the package's manifest."""
    with zipfile.ZipFile(zip_path) as zf:
        manifest = tomllib.loads(zf.read("manifest.toml").decode("utf-8"))
    return {row["name"]: row["type"] for row in manifest.get("command", [])}


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


def leftovers(opt: Path, name: str, commands: list[str], runtimes: list[str] = ()) -> list[str]:
    """What is still under the install root after the tool, and its runtimes, were removed."""
    found = []
    places = [opt / "tools" / name, opt / "upload" / name, *(opt / "tools" / r for r in runtimes)]
    for path in places:
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


def venv_home(text: str) -> str | None:
    """The folder of the interpreter that made a virtual environment, from its pyvenv.cfg."""
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().lower() == "home":
            return value.strip()
    return None


def made_by(venv: Path, runtime_folder: Path) -> str | None:
    """Why the environment was not made by the runtime's interpreter, or None if it was."""
    try:
        text = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError as exc:
        return f"{venv / 'pyvenv.cfg'} cannot be read ({exc})"
    home = venv_home(text)
    if home is None:
        return f"{venv / 'pyvenv.cfg'} names no home"
    if not Path(home).resolve().is_relative_to(runtime_folder.resolve()):
        return f"its virtual environment was made by the Python in {home}, not the runtime's"
    return None


def interpreter_version(python: str, *, run=subprocess.run) -> str:
    """What a runtime's interpreter says its version is, isolated from this environment."""
    try:
        result = run(
            [python, "-I", "--version"],
            capture_output=True, text=True, errors="replace",
            stdin=subprocess.DEVNULL, timeout=TIMEOUT, env={"PATH": os.defpath},
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return f"no answer in {TIMEOUT} seconds"
    except OSError as exc:
        return f"cannot run it ({exc})"
    said = first_line((result.stdout or "") + (result.stderr or "")) or "nothing"
    return said if result.returncode == 0 else f"exit {result.returncode}, {said}"


def network_state(
    *, resolve=socket.getaddrinfo, connect=socket.create_connection
) -> tuple[bool, str]:
    """Whether the network is off: no public name resolves and no public address answers."""
    try:
        resolve(PUBLIC_NAME, 443)
    except OSError as exc:
        resolved = f"resolving {PUBLIC_NAME} failed ({exc})"
    else:
        return False, f"{PUBLIC_NAME} resolves"
    host, port = PUBLIC_ADDRESS
    try:
        sock = connect(PUBLIC_ADDRESS, timeout=NETWORK_TIMEOUT)
    except OSError as exc:
        reached = f"connecting to {host}:{port} failed ({exc})"
    else:
        sock.close()
        return False, f"{host}:{port} can be reached"
    return True, f"{resolved}; {reached}"


def load_broker():
    try:
        from pwn_broker import boxes, config, toolhooks, toolindex, toolinstall, toolstate
        from pwn_broker.console import FakeConsole
        from pwn_broker.model import current_tools
        from pwn_broker.session import Session
        from pwn_broker.store import write_toml
    except ImportError as exc:
        raise SystemExit(
            f"fail  pwn_broker cannot be imported ({exc}); run this with the interpreter "
            "of the environment that has pwn-broker installed"
        ) from exc
    return SimpleNamespace(
        boxes=boxes, config=config, toolhooks=toolhooks, toolindex=toolindex,
        toolinstall=toolinstall, toolstate=toolstate, FakeConsole=FakeConsole,
        current_tools=current_tools, Session=Session, write_toml=write_toml,
    )  # fmt: skip


@contextmanager
def users_home(toolhooks, home: Path):
    """The app takes the user's home from toolhooks.user_home; here it is a temporary one."""
    saved = toolhooks.user_home
    toolhooks.user_home = lambda: home
    try:
        yield
    finally:
        toolhooks.user_home = saved


def _said(console) -> str:
    said = "; ".join(text for _, text in console.lines)
    return f"; {said}" if said else ""


def check_runtime(broker, paths, settings, entry, *, run=subprocess.run) -> str:
    """Unpacks a runtime, asks its interpreter for its version and removes it again."""
    label = f"{entry.name} {entry.version}-{entry.build}"
    zip_path = broker.toolindex.fetch_zip(paths, settings, entry)
    installed = broker.toolstate.load_installed(paths)
    console = broker.FakeConsole()
    try:
        item = broker.toolinstall.install_runtime(
            paths, entry, zip_path, installed=installed, console=console
        )
    except Exception as exc:
        raise CheckFailed(f"{label}: unpacking failed ({exc}){_said(console)}") from exc
    said = interpreter_version(item.python, run=run)
    shown = f"{Path(item.python).name} --version: {said}"
    if said != f"Python {entry.version}":
        raise CheckFailed(f"{label}: {shown}, not Python {entry.version}")
    if not broker.toolinstall.forget_runtime(paths, installed, entry.name):
        raise CheckFailed(f"{label}: the runtime was not removed")
    left = leftovers(paths.opt, entry.name, [])
    if entry.name in broker.toolstate.load_installed(paths):
        left.append("an entry in the list of installed tools")
    if left:
        raise CheckFailed(f"{label}: left behind after removal: {', '.join(left)}")
    return f"{label}: {shown}"


def check_tool(broker, folder: Path, name: str, now: datetime) -> str:
    """Installs, starts and removes one package in a temporary root. Returns the ok line's note."""
    with tempfile.TemporaryDirectory(prefix="pwn-shop-check-") as tmp:
        root = Path(tmp)
        opt, box_home, user_home = root / "opt", root / "boxes", root / "home"
        for made in (opt, box_home, user_home):
            made.mkdir()
        broker.write_toml(opt / "config.toml", {"tools_index": str(folder.resolve())})
        env = launcher_env(os.environ, opt, box_home, user_home)
        paths, settings = broker.config.load(env, user_home)

        index = broker.toolindex.load_index(paths, settings, now=now)
        entry = broker.toolindex.pick_build(broker.toolindex.find_tool(index, name), None)
        if entry.kind == "runtime":
            return check_runtime(broker, paths, settings, entry)
        with users_home(broker.toolhooks, user_home):
            return _check_installed_tool(broker, paths, settings, index, entry, env, now)


def _check_installed_tool(broker, paths, settings, index, entry, env, now) -> str:
    label = f"{entry.name} {entry.version}-{entry.build}"
    box = broker.boxes.create_box(
        paths, name="ci", platform="htb", day=now.date(), os_name="linux",
        difficulty="easy", now=now,
    )  # fmt: skip
    console = broker.FakeConsole()
    session = broker.Session(
        paths=paths, settings=settings, console=console, box=box, targets=[], creds=[],
        current=None, clock=lambda: now,
    )  # fmt: skip
    # as tool install does: every box holding a tool counts, and this one
    installed = broker.toolstate.load_installed(paths)
    unfinished = broker.toolstate.holding_boxes(paths, installed)
    unfinished[box.id] = box.name
    try:
        # python is only used by a schema 1 tool, which runs on the Python running this check
        item = broker.toolhooks.install_tool(
            session, index, entry, installed=installed, unfinished=unfinished,
            python=sys.executable,
        )  # fmt: skip
    except Exception as exc:
        raise CheckFailed(f"{label}: install failed ({exc}){_said(console)}") from exc

    on = []
    for runtime in entry.requires:
        row = installed.get(runtime)
        if row is None or row.kind != "runtime":
            raise CheckFailed(f"{label}: the runtime {runtime} it requires was not installed")
        problem = made_by(Path(item.folder) / "venv", Path(row.folder))
        if problem:
            raise CheckFailed(f"{label}: {problem}")
        on.append(f"on {runtime} {row.version}")

    zip_path = broker.toolindex.fetch_zip(paths, settings, entry)
    types = command_types(zip_path)
    started_with = []
    for command in item.commands:
        launcher = paths.bin / command
        if not launcher.is_file():
            raise CheckFailed(f"{label}: command {command} has no launcher at {launcher}")
        worked, note = probe(launcher, python=is_python(types.get(command, "")), env=env)
        if not worked:
            raise CheckFailed(f"{label}: command {command} did not start ({note})")
        started_with.append(f"{command} {note}")
    problems = upload_problems(zip_path, paths.upload / entry.name) if item.uploads else []
    if problems:
        raise CheckFailed(f"{label}: " + "; ".join(problems))

    # as tool remove does: the tool is taken off the box, then any runtime no tool requires
    record = next(t for t in broker.current_tools(box) if t.name == entry.name)
    holding = broker.toolstate.holding_boxes(paths, installed, excluding=box.id)
    broker.toolhooks.release_tool(
        session, record, "remove", installed=installed, unfinished=holding
    )
    for runtime in broker.toolhooks.remove_idle_runtimes(paths, installed, console):
        broker.toolhooks.log_tool(session, "remove", runtime, kind="runtime")
    left = leftovers(paths.opt, entry.name, item.commands, list(entry.requires))
    still = broker.toolstate.load_installed(paths)
    for name in (entry.name, *entry.requires):
        if name in still:
            left.append(f"{name} in the list of installed tools")
    if any(t.name == entry.name for t in broker.current_tools(box)):
        left.append("a current record in the box")
    if left:
        raise CheckFailed(f"{label}: left behind after removal: {', '.join(left)}{_said(console)}")

    files = f"{len(item.uploads)} upload files" if item.uploads else ""
    return f"{label}: " + ", ".join([*on, *started_with, *([files] if files else [])])


def main(argv=None, *, network=network_state) -> int:
    args = parse_args(argv)
    if args.network_off:
        off, note = network()
        if not off:
            print(f"fail  the network is on: {note}", file=sys.stderr)
            return 1
        print(f"ok    the network is off: {note}")
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
