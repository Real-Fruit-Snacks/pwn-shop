"""The pure parts of .github/scripts/install-check.py. It is the one file that imports
pwn_broker, and only inside load_broker, so these tests run without pwn-broker installed."""

import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomli_w

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "install-check.py"


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("install_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result(code, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class Runner:
    """Stands in for subprocess.run: one answer per call, in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


# ---- arguments


def test_the_folder_is_required(check):
    with pytest.raises(SystemExit):
        check.parse_args([])


def test_folder_and_tool_names(check):
    args = check.parse_args(["dist", "--tool", "a", "--tool", "b"])
    assert args.folder == Path("dist")
    assert args.tool == ["a", "b"]


def test_no_tool_names_means_every_tool(check):
    assert check.parse_args(["dist"]).tool == []


# ---- the exit-status rule

TRACE = "Traceback (most recent call last):\n  File \"x\", line 1\nModuleNotFoundError: No module named 'y'\n"


@pytest.mark.parametrize("code", [0, 1, 2])
def test_exit_0_to_2_with_output_is_started_for_a_binary(check, code):
    assert check.started(code, "usage: tool\n")


@pytest.mark.parametrize("code", [3, 126, 127, 139, -9])
def test_other_exit_codes_are_not_started(check, code):
    assert not check.started(code, "usage: tool\n")


@pytest.mark.parametrize("out", ["", "  \n\t"])
def test_no_output_is_not_started(check, out):
    assert not check.started(0, out)


@pytest.mark.parametrize("code", [0, 1, 2])
@pytest.mark.parametrize("python", [False, True])
def test_a_traceback_is_never_started(check, code, python):
    assert not check.started(code, TRACE, python=python)
    assert not check.started(code, "usage: tool\n" + TRACE, python=python)


@pytest.mark.parametrize("code", [1, 2])
def test_a_python_command_must_exit_0(check, code):
    assert not check.started(code, "usage: tool\n", python=True)
    assert check.started(code, "usage: tool\n", python=False)
    assert check.started(0, "usage: tool\n", python=True)


# ---- the line the log shows


def test_the_first_non_empty_line_is_shown(check):
    assert check.first_line("\n  \nCertipy v5.1.0 - ly4k\nusage: x\n") == "Certipy v5.1.0 - ly4k"


def test_a_long_line_is_cut_to_80_characters(check):
    assert check.first_line("x" * 200) == "x" * 80


def test_control_characters_are_removed(check):
    assert check.first_line("\x1b[31mred\x07 text\x00\r\n") == "[31mred text"


def test_a_line_of_only_control_characters_is_skipped(check):
    assert check.first_line("\x1b\x07\nreal line\n") == "real line"


def test_no_text_gives_an_empty_line(check):
    assert check.first_line(" \n\t\n") == ""


# ---- running a launcher


def test_help_is_tried_first(check):
    run = Runner(result(0, "usage\n"))
    assert check.probe(Path("/x/tool"), run=run) == (True, "--help: usage")
    assert [c[0] for c in run.calls] == [[str(Path("/x/tool")), "--help"]]


def test_the_note_carries_the_first_line_of_output(check):
    ok, note = check.probe(Path("t"), run=Runner(result(0, "\nTool 1.2 - does things\nmore\n")))
    assert ok and note == "--help: Tool 1.2 - does things"


def test_short_help_is_the_fallback(check):
    run = Runner(result(1, ""), result(2, err="usage\n"))
    assert check.probe(Path("/x/tool"), run=run) == (True, "-h: usage")
    assert [c[0][1] for c in run.calls] == ["--help", "-h"]


def test_output_on_stderr_counts(check):
    assert check.probe(Path("t"), run=Runner(result(2, err="usage\n")))[0]


def test_both_failing_says_how(check):
    ok, note = check.probe(Path("t"), run=Runner(result(127, ""), result(0, "")))
    assert not ok
    assert "--help: exit 127, printed nothing" in note
    assert "-h: exit 0, printed nothing" in note


def test_a_crash_with_output_is_not_started(check):
    ok, note = check.probe(Path("t"), run=Runner(result(139, "Segmentation"), result(139, "x")))
    assert not ok
    assert "exit 139, printed something" in note


def test_a_hang_is_not_started(check):
    timeout = subprocess.TimeoutExpired("t", 60)
    ok, note = check.probe(Path("t"), run=Runner(timeout, timeout))
    assert not ok
    assert "no answer" in note


def test_a_missing_launcher_stops_at_once(check):
    run = Runner(FileNotFoundError("no such file"))
    ok, note = check.probe(Path("t"), run=run)
    assert not ok
    assert "cannot run it" in note
    assert len(run.calls) == 1


def test_the_launcher_gets_no_input_and_a_time_limit(check):
    run = Runner(result(0, "x"))
    check.probe(Path("t"), run=run, env={"A": "b"})
    kwargs = run.calls[0][1]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] == check.TIMEOUT
    assert kwargs["env"] == {"A": "b"}


def test_a_traceback_on_help_is_not_started_and_short_help_is_not_tried(check):
    run = Runner(result(1, err=TRACE), result(0, "usage\n"))
    ok, note = check.probe(Path("t"), python=True, run=run)
    assert not ok
    assert "traceback" in note
    assert len(run.calls) == 1


def test_a_traceback_that_exits_0_is_not_started(check):
    ok, _ = check.probe(Path("t"), python=True, run=Runner(result(0, TRACE), result(0, TRACE)))
    assert not ok


def test_a_traceback_is_not_started_for_a_binary_either(check):
    ok, note = check.probe(Path("t"), run=Runner(result(1, err=TRACE), result(1, err=TRACE)))
    assert not ok
    assert "traceback" in note


def test_a_python_command_that_exits_1_on_help_falls_back_to_short_help(check):
    run = Runner(result(1, "usage: tool\n"), result(0, "usage: tool\n"))
    assert check.probe(Path("t"), python=True, run=run) == (True, "-h: usage: tool")
    assert [c[0][1] for c in run.calls] == ["--help", "-h"]


def test_a_python_command_that_exits_1_on_both_is_not_started(check):
    ok, note = check.probe(
        Path("t"), python=True, run=Runner(result(1, "usage\n"), result(2, "usage\n"))
    )
    assert not ok
    assert "--help: exit 1" in note and "-h: exit 2" in note


def test_a_python_command_that_exits_0_on_help_needs_no_fallback(check):
    run = Runner(result(0, "usage\n"))
    assert check.probe(Path("t"), python=True, run=run)[0]
    assert len(run.calls) == 1


def test_a_binary_may_exit_2_on_help(check):
    assert check.probe(Path("t"), python=False, run=Runner(result(2, err="usage\n")))[0]


@pytest.mark.skipif(os.name == "nt", reason="a pseudo-terminal is POSIX-only")
def test_a_python_help_command_that_needs_a_terminal_gets_one(check, tmp_path):
    launcher = tmp_path / "terminal-tool"
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import sys, termios\n"
        "termios.tcgetattr(sys.stdin)\n"
        "print('usage: terminal-tool')\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    assert check.probe(launcher, python=True) == (
        True, "--help with a terminal: usage: terminal-tool"
    )


# ---- which commands are Python


def test_command_types_come_from_the_manifest(check, tmp_path):
    path = tmp_path / "p.zip"
    manifest = {
        "schema": 1,
        "command": [
            {"name": "a", "type": "binary", "path": "files/a"},
            {"name": "b", "type": "python-entry", "entry": "b"},
            {"name": "c", "type": "python-script", "script": "files/c.py"},
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.toml", tomli_w.dumps(manifest))
    assert check.command_types(path) == {"a": "binary", "b": "python-entry", "c": "python-script"}
    assert [check.is_python(t) for t in ("binary", "python-entry", "python-script")] == [
        False, True, True,
    ]


# ---- upload files


def make_zip(path: Path, uploads: dict[str, bytes]) -> Path:
    manifest = {
        "schema": 1,
        "upload": [{"path": f"files/{k}", "as": k} for k in uploads],
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.toml", tomli_w.dumps(manifest))
        for name, data in uploads.items():
            zf.writestr(f"files/{name}", data)
    return path


def test_uploads_present_with_the_right_size_pass(check, tmp_path):
    zip_path = make_zip(tmp_path / "p.zip", {"a": b"123", "b.exe": b"12345"})
    target = tmp_path / "upload"
    target.mkdir()
    (target / "a").write_bytes(b"xyz")
    (target / "b.exe").write_bytes(b"xxxxx")
    assert check.upload_problems(zip_path, target) == []


def test_a_missing_upload_is_named(check, tmp_path):
    zip_path = make_zip(tmp_path / "p.zip", {"a": b"123", "b": b"1"})
    target = tmp_path / "upload"
    target.mkdir()
    (target / "a").write_bytes(b"123")
    problems = check.upload_problems(zip_path, target)
    assert len(problems) == 1
    assert problems[0].startswith("b is missing")


def test_a_wrong_size_is_named(check, tmp_path):
    zip_path = make_zip(tmp_path / "p.zip", {"a": b"123"})
    target = tmp_path / "upload"
    target.mkdir()
    (target / "a").write_bytes(b"12")
    assert check.upload_problems(zip_path, target) == ["a is 2 bytes, the package holds 3"]


def test_a_folder_where_a_file_should_be_is_missing(check, tmp_path):
    zip_path = make_zip(tmp_path / "p.zip", {"a": b"123"})
    target = tmp_path / "upload"
    (target / "a").mkdir(parents=True)
    assert check.upload_problems(zip_path, target)[0].startswith("a is missing")


# ---- what is left after release


def test_nothing_left_is_an_empty_list(check, tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "tools").mkdir()
    assert check.leftovers(tmp_path, "tool", ["tool"]) == []


def test_each_leftover_is_listed(check, tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "cmd").write_text("x")
    (tmp_path / "tools" / "tool" / "1.0-1").mkdir(parents=True)
    (tmp_path / "upload" / "tool").mkdir(parents=True)
    found = check.leftovers(tmp_path, "tool", ["cmd", "other"])
    assert len(found) == 3
    assert any(f.endswith("cmd") for f in found)


def test_other_tools_files_are_not_leftovers(check, tmp_path):
    (tmp_path / "tools" / "other").mkdir(parents=True)
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "other-cmd").write_text("x")
    assert check.leftovers(tmp_path, "tool", ["cmd"]) == []


# ---- importing pwn_broker


def test_the_script_does_not_import_pwn_broker_until_it_is_asked_to(check):
    text = SCRIPT.read_text(encoding="utf-8")
    top = text.split("def load_broker")[0]
    assert "import pwn_broker" not in top
    assert "from pwn_broker" not in top


def test_without_pwn_broker_the_script_says_so(check, monkeypatch):
    monkeypatch.setitem(sys.modules, "pwn_broker", None)
    with pytest.raises(SystemExit) as err:
        check.load_broker()
    assert str(err.value).startswith("fail  pwn_broker cannot be imported")
    assert "interpreter" in str(err.value)


def test_main_stops_at_the_import_before_reading_the_folder(check, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pwn_broker", None)
    with pytest.raises(SystemExit):
        check.main([str(tmp_path / "nowhere")])


# ---- the environment a launcher gets


def test_a_launcher_gets_a_small_environment(check, tmp_path):
    caller = {
        "PATH": "/usr/bin", "HOME": "/runner", "LANG": "C.UTF-8", "GH_TOKEN": "secret",
        "GITHUB_TOKEN": "secret", "ACTIONS_RUNTIME_TOKEN": "x", "PWN_BROKER_OPT": "/elsewhere",
    }
    env = check.launcher_env(caller, tmp_path / "opt", tmp_path / "boxes", tmp_path / "home")
    assert env == {
        "PATH": "/usr/bin", "HOME": str(tmp_path / "home"), "LANG": "C.UTF-8",
        "PWN_BROKER_OPT": str(tmp_path / "opt"), "PWN_BROKER_HOME": str(tmp_path / "boxes"),
    }


def test_lc_all_is_passed_only_when_set(check, tmp_path):
    base = {"PATH": "/bin"}
    args = (tmp_path, tmp_path, tmp_path)
    assert "LC_ALL" not in check.launcher_env(base, *args)
    assert check.launcher_env({**base, "LC_ALL": "C"}, *args)["LC_ALL"] == "C"


def test_network_off_is_a_flag(check):
    assert check.parse_args(["dist"]).network_off is False
    assert check.parse_args(["dist", "--network-off"]).network_off is True


# ---- what is left after removal, runtimes included


def test_a_runtime_left_behind_is_listed(check, tmp_path):
    (tmp_path / "tools" / "python-3.13" / "3.13.18-1").mkdir(parents=True)
    (tmp_path / "tools" / "python-3.14").mkdir(parents=True)
    found = check.leftovers(tmp_path, "tool", [], ["python-3.13"])
    assert found == [str(tmp_path / "tools" / "python-3.13")]


# ---- the Python that made a tool's environment


def test_the_home_line_of_pyvenv_cfg_is_read(check):
    text = "include-system-site-packages = false\nHome = /opt/x/python/bin \nversion = 3.13.18\n"
    assert check.venv_home(text) == "/opt/x/python/bin"
    assert check.venv_home("version = 3.13\n") is None


def write_venv(folder: Path, home: str) -> Path:
    venv = folder / "venv"
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(f"home = {home}\nversion = 3.13.18\n", encoding="utf-8")
    return venv


def test_an_environment_made_by_the_runtime_passes(check, tmp_path):
    runtime = tmp_path / "tools" / "python-3.13" / "3.13.18-1"
    (runtime / "python" / "python" / "bin").mkdir(parents=True)
    venv = write_venv(tmp_path / "tool", str(runtime / "python" / "python" / "bin"))
    assert check.made_by(venv, runtime) is None


def test_an_environment_made_by_another_python_is_named(check, tmp_path):
    runtime = tmp_path / "tools" / "python-3.13" / "3.13.18-1"
    runtime.mkdir(parents=True)
    venv = write_venv(tmp_path / "tool", str(tmp_path / "usr" / "bin"))
    problem = check.made_by(venv, runtime)
    assert "made by the Python in" in problem and "not the runtime's" in problem


def test_an_environment_without_pyvenv_cfg_or_home_is_named(check, tmp_path):
    assert "cannot be read" in check.made_by(tmp_path / "venv", tmp_path)
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "pyvenv.cfg").write_text("version = 3.13\n", encoding="utf-8")
    assert "names no home" in check.made_by(tmp_path / "venv", tmp_path)


# ---- the user's home the app is given


def test_the_apps_user_home_is_replaced_and_put_back(check, tmp_path):
    def real():
        return Path("/root")

    hooks = SimpleNamespace(user_home=real)
    with check.users_home(hooks, tmp_path):
        assert hooks.user_home() == tmp_path
    assert hooks.user_home is real
    with pytest.raises(RuntimeError), check.users_home(hooks, tmp_path):
        raise RuntimeError("install failed")
    assert hooks.user_home is real


# ---- a runtime on its own


class Console:
    def __init__(self):
        self.lines = []


class RuntimeBroker:
    """Stands in for the parts of pwn_broker check_runtime uses."""

    def __init__(self, tmp_path, *, refuse=None, keep_files=False, forgets=True):
        self.opt = tmp_path / "opt"
        self.rows = {}
        self.forgotten = []
        broker = self

        def install_runtime(paths, entry, zip_path, *, installed, console):
            if refuse:
                console.lines.append(("warn", "the archive was looked at"))
                raise ValueError(refuse)
            folder = paths.opt / "tools" / entry.name / f"{entry.version}-{entry.build}"
            folder.mkdir(parents=True)
            item = SimpleNamespace(name=entry.name, python=str(folder / "python/bin/python3.13"))
            installed[entry.name] = item
            broker.rows[entry.name] = item
            return item

        def forget_runtime(paths, installed, name):
            broker.forgotten.append(name)
            if not forgets:
                return False
            if not keep_files:
                shutil.rmtree(paths.opt / "tools" / name)
            installed.pop(name)
            broker.rows.pop(name)
            return True

        self.toolindex = SimpleNamespace(fetch_zip=lambda paths, settings, entry: tmp_path / "r.zip")
        self.toolstate = SimpleNamespace(load_installed=lambda paths: dict(broker.rows))
        self.toolinstall = SimpleNamespace(
            install_runtime=install_runtime, forget_runtime=forget_runtime
        )
        self.FakeConsole = Console


RUNTIME = SimpleNamespace(name="python-3.13", version="3.13.18", build=1, kind="runtime")


def runtime_check(check, tmp_path, answer, **kwargs):
    broker = RuntimeBroker(tmp_path, **kwargs)
    paths = SimpleNamespace(opt=broker.opt)
    run = Runner(answer)
    return broker, run, (lambda: check.check_runtime(broker, paths, None, RUNTIME, run=run))


def test_a_runtime_is_unpacked_asked_its_version_and_removed(check, tmp_path):
    broker, run, go = runtime_check(check, tmp_path, result(0, "Python 3.13.18\n"))
    assert go() == "python-3.13 3.13.18-1: python3.13 --version: Python 3.13.18"
    argv, kwargs = run.calls[0]
    assert argv[1:] == ["-I", "--version"] and argv[0].endswith("python3.13")
    # the interpreter gets nothing of this environment
    assert kwargs["env"] == {"PATH": os.defpath} and kwargs["stdin"] == subprocess.DEVNULL
    assert broker.forgotten == ["python-3.13"] and broker.rows == {}
    assert not (broker.opt / "tools" / "python-3.13").exists()


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (result(0, "Python 3.14.2\n"), "Python 3.14.2, not Python 3.13.18"),
        (result(127, "", "not found\n"), "exit 127, not found"),
        (FileNotFoundError("no such file"), "cannot run it"),
        (subprocess.TimeoutExpired("python", 60), "no answer"),
    ],
)
def test_a_runtime_that_does_not_report_its_version_fails(check, tmp_path, answer, said):
    _, _, go = runtime_check(check, tmp_path, answer)
    with pytest.raises(check.CheckFailed, match="python-3.13 3.13.18-1: python3.13 --version: ") as err:
        go()
    assert said in str(err.value)


def test_a_runtime_that_cannot_be_unpacked_fails_with_what_the_app_said(check, tmp_path):
    _, run, go = runtime_check(check, tmp_path, result(0, "x"), refuse="a link leads outside")
    with pytest.raises(check.CheckFailed) as err:
        go()
    assert str(err.value) == (
        "python-3.13 3.13.18-1: unpacking failed (a link leads outside); the archive was looked at"
    )
    assert run.calls == []


def test_a_runtime_whose_files_stay_fails(check, tmp_path):
    _, _, go = runtime_check(check, tmp_path, result(0, "Python 3.13.18\n"), keep_files=True)
    with pytest.raises(check.CheckFailed, match="left behind after removal: .*python-3.13"):
        go()


def test_a_runtime_the_app_keeps_fails(check, tmp_path):
    _, _, go = runtime_check(check, tmp_path, result(0, "Python 3.13.18\n"), forgets=False)
    with pytest.raises(check.CheckFailed, match="the runtime was not removed"):
        go()


# ---- the network, for the Kali job


def unreachable(*args, **kwargs):
    raise OSError("Network is unreachable")


class Socket:
    closed = False

    def close(self):
        self.closed = True


def test_the_network_is_off_when_nothing_resolves_or_answers(check):
    off, note = check.network_state(resolve=unreachable, connect=unreachable)
    assert off
    assert note == (
        "resolving github.com failed (Network is unreachable); "
        "connecting to 1.1.1.1:443 failed (Network is unreachable)"
    )


def test_a_name_that_resolves_means_the_network_is_on(check):
    off, note = check.network_state(resolve=lambda *a: [("addr",)], connect=unreachable)
    assert not off and note == "github.com resolves"


def test_an_address_that_answers_means_the_network_is_on(check):
    sock = Socket()
    seen = []

    def connect(address, timeout):
        seen.append((address, timeout))
        return sock

    off, note = check.network_state(resolve=unreachable, connect=connect)
    assert not off and note == "1.1.1.1:443 can be reached"
    assert sock.closed and seen == [(("1.1.1.1", 443), check.NETWORK_TIMEOUT)]


def test_with_the_network_on_nothing_is_installed(check, monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "pwn_broker", None)
    status = check.main(
        [str(tmp_path), "--network-off"], network=lambda: (False, "github.com resolves")
    )
    assert status == 1
    assert capsys.readouterr().err == "fail  the network is on: github.com resolves\n"


def test_with_the_network_off_the_check_says_so_and_goes_on(check, monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "pwn_broker", None)
    with pytest.raises(SystemExit):
        check.main([str(tmp_path), "--network-off"], network=lambda: (True, "nothing answers"))
    assert capsys.readouterr().out == "ok    the network is off: nothing answers\n"


def test_without_the_flag_the_network_is_not_looked_at(check, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pwn_broker", None)

    def network():
        raise AssertionError("looked at")

    with pytest.raises(SystemExit):
        check.main([str(tmp_path)], network=network)


# ---- a tool that requires a runtime, through a stand-in for the app


def make_tool_zip(path: Path) -> Path:
    manifest = {"command": [{"name": "tinytool", "type": "python-entry", "entry": "tinytool"}]}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.toml", tomli_w.dumps(manifest))
    return path


class ToolBroker:
    """Stands in for the parts of pwn_broker _check_installed_tool uses, recording each call.

    install_tool lays out what the app would: the runtime's folder, the tool's folder with a
    virtual environment whose home is `venv_home` (the runtime's bin by default), a launcher,
    the two rows and a box record. release_tool and remove_idle_runtimes undo it, unless told
    to leave the runtime behind.
    """

    def __init__(self, tmp_path, *, venv_home=None, keep_runtime=False):
        opt = tmp_path / "opt"
        self.paths = SimpleNamespace(opt=opt, bin=opt / "bin", upload=opt / "upload")
        self.zip = make_tool_zip(tmp_path / "tinytool-1.0-1.zip")
        self.calls = []
        self.rows = {}
        broker = self
        runtime_folder = opt / "tools" / "python-3.13" / "3.13.18-1"
        tool_folder = opt / "tools" / "tinytool" / "1.0-1"
        home = venv_home or str(runtime_folder / "python" / "python" / "bin")

        def create_box(paths, **kwargs):
            broker.calls.append(("create_box", kwargs))
            return SimpleNamespace(id="box-1", name="ci", tools=[])

        def load_installed(paths):
            return dict(broker.rows)

        def holding_boxes(paths, installed, *, excluding=None):
            broker.calls.append(("holding_boxes", excluding))
            return {}

        def install_tool(session, index, entry, *, installed, unfinished, **options):
            broker.calls.append(("install_tool", session, index, entry, dict(unfinished), options))
            (runtime_folder / "python" / "python" / "bin").mkdir(parents=True)
            write_venv(tool_folder, home)
            broker.paths.bin.mkdir(parents=True)
            (broker.paths.bin / "tinytool").write_text("#!/bin/sh\n", encoding="utf-8")
            runtime = SimpleNamespace(
                name="python-3.13", kind="runtime", version="3.13.18", folder=str(runtime_folder)
            )
            item = SimpleNamespace(
                name="tinytool", kind="tool", folder=str(tool_folder), commands=["tinytool"],
                uploads=[],
            )  # fmt: skip
            for row in (runtime, item):
                installed[row.name] = row
                broker.rows[row.name] = row
            session.box.tools.append(SimpleNamespace(name="tinytool", removed=None))
            return item

        def release_tool(session, record, event, *, installed, unfinished):
            broker.calls.append(("release_tool", record.name, event, dict(unfinished)))
            shutil.rmtree(tool_folder.parent)
            (broker.paths.bin / "tinytool").unlink()
            installed.pop("tinytool")
            broker.rows.pop("tinytool")
            record.removed = "now"
            return True

        def remove_idle_runtimes(paths, installed, console):
            broker.calls.append(("remove_idle_runtimes", paths, console))
            if keep_runtime:
                return []
            shutil.rmtree(runtime_folder.parent)
            row = installed.pop("python-3.13")
            broker.rows.pop("python-3.13")
            return [row]

        def log_tool(session, event, item, *, kind="tool"):
            broker.calls.append(("log_tool", event, item.name, kind))

        self.boxes = SimpleNamespace(create_box=create_box)
        self.Session = lambda **kwargs: SimpleNamespace(**kwargs)
        self.FakeConsole = Console
        self.toolstate = SimpleNamespace(load_installed=load_installed, holding_boxes=holding_boxes)
        self.toolhooks = SimpleNamespace(
            install_tool=install_tool, release_tool=release_tool,
            remove_idle_runtimes=remove_idle_runtimes, log_tool=log_tool,
        )  # fmt: skip
        self.toolindex = SimpleNamespace(fetch_zip=lambda paths, settings, entry: broker.zip)
        self.current_tools = lambda box: [t for t in box.tools if t.removed is None]

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


TOOL = SimpleNamespace(name="tinytool", version="1.0", build=1, kind="kali", requires=["python-3.13"])


def tool_check(check, tmp_path, monkeypatch, **kwargs):
    broker = ToolBroker(tmp_path, **kwargs)
    probes = []

    def probe(launcher, *, python=False, env=None):
        probes.append((launcher, python, env))
        return True, "--help: usage: tinytool"

    monkeypatch.setattr(check, "probe", probe)
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    index = object()

    def go():
        return check._check_installed_tool(
            broker, broker.paths, "settings", index, TOOL, {"PATH": "/bin"}, now
        )

    return broker, probes, index, go


def test_a_tool_is_installed_on_its_runtime_started_and_removed_with_it(check, tmp_path, monkeypatch):
    broker, probes, index, go = tool_check(check, tmp_path, monkeypatch)
    assert go() == "tinytool 1.0-1: on python-3.13 3.13.18, tinytool --help: usage: tinytool"

    ((_, session, given_index, entry, unfinished, options),) = broker.named("install_tool")
    assert given_index is index and entry is TOOL
    assert session.box.id == "box-1" and session.paths is broker.paths
    assert session.settings == "settings" and session.targets == [] and session.current is None
    assert unfinished == {"box-1": "ci"}
    assert options == {"python": sys.executable}
    assert probes == [(broker.paths.bin / "tinytool", True, {"PATH": "/bin"})]
    # removed as tool remove does: with no box but this one holding it, then the idle runtime
    assert broker.named("release_tool") == [("release_tool", "tinytool", "remove", {})]
    assert ("holding_boxes", "box-1") in broker.calls
    ((_, paths, console),) = broker.named("remove_idle_runtimes")
    assert paths is broker.paths and console is session.console
    assert broker.named("log_tool") == [("log_tool", "remove", "python-3.13", "runtime")]


def test_a_tool_whose_environment_another_python_made_fails(check, tmp_path, monkeypatch):
    other = str(tmp_path / "usr" / "bin")
    broker, probes, _, go = tool_check(check, tmp_path, monkeypatch, venv_home=other)
    with pytest.raises(check.CheckFailed, match="tinytool 1.0-1: its virtual environment was made"):
        go()
    assert probes == [] and broker.named("release_tool") == []


def test_a_runtime_left_after_the_tool_is_removed_fails(check, tmp_path, monkeypatch):
    _, _, _, go = tool_check(check, tmp_path, monkeypatch, keep_runtime=True)
    with pytest.raises(check.CheckFailed) as err:
        go()
    message = str(err.value)
    assert message.startswith("tinytool 1.0-1: left behind after removal: ")
    assert "python-3.13 in the list of installed tools" in message



# ---- a notes package's playbooks

GOOD = "##########@@@@@@@@@@\nPlaybook: SMB enumeration   02-enum/smb   2026.11.20\nmy\n"


def notes_root(tmp_path, files):
    root = tmp_path / "files" / "playbooks"
    root.mkdir(parents=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return root


def test_good_notes_have_no_problems(check, tmp_path):
    root = notes_root(tmp_path, {"02-enum/smb.md": GOOD, "01-checklists/web.md": GOOD})
    assert check.notes_problems(root, f"{root.as_posix()}\n") == []


def test_a_missing_root_is_named(check, tmp_path):
    root = tmp_path / "nowhere"
    assert check.notes_problems(root, f"{root.as_posix()}\n") == [
        f"the notes folder {root.as_posix()} is missing"
    ]


def test_a_root_with_no_md_is_named(check, tmp_path):
    root = notes_root(tmp_path, {"readme.txt": GOOD})
    assert check.notes_problems(root, f"{root.as_posix()}\n") == [
        f"the notes folder {root.as_posix()} holds no .md files"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Playbook: SMB   02-enum/smb   2026.11.20\n",
        "##########@@@@@@@@@@\n## SMB\n",
        "",
        "  ##########@@@@@@@@@@\nPlaybook: SMB   02-enum/smb   2026.11.20\n",
    ],
)
def test_an_md_without_the_fold_marker_and_playbook_line_is_named(check, tmp_path, text):
    root = notes_root(tmp_path, {"02-enum/smb.md": text, "02-enum/ldap.md": GOOD})
    assert check.notes_problems(root, f"{root.as_posix()}\n") == [
        "02-enum/smb.md does not start with the fold marker and a Playbook: line"
    ]


def test_run_notes_must_name_the_root(check, tmp_path):
    root = notes_root(tmp_path, {"02-enum/smb.md": GOOD})
    assert check.notes_problems(root, "/opt/other/files/playbooks\n") == [
        f"run/notes does not name {root.as_posix()}"
    ]
    assert check.notes_problems(root, "") == [f"run/notes does not name {root.as_posix()}"]
