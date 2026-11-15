"""The pure parts of .github/scripts/install-check.py. It is the one file that imports
pwn_broker, and only inside load_broker, so these tests run without pwn-broker installed."""

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

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


@pytest.mark.parametrize("code", [0, 1, 2])
def test_exit_0_to_2_with_output_is_started(check, code):
    assert check.started(code, "usage: tool\n")


@pytest.mark.parametrize("code", [3, 126, 127, 139, -9])
def test_other_exit_codes_are_not_started(check, code):
    assert not check.started(code, "usage: tool\n")


@pytest.mark.parametrize("out", ["", "  \n\t"])
def test_no_output_is_not_started(check, out):
    assert not check.started(0, out)


# ---- running a launcher


def test_help_is_tried_first(check):
    run = Runner(result(0, "usage\n"))
    assert check.probe(Path("/x/tool"), run=run) == (True, "--help")
    assert [c[0] for c in run.calls] == [[str(Path("/x/tool")), "--help"]]


def test_short_help_is_the_fallback(check):
    run = Runner(result(1, ""), result(2, err="usage\n"))
    assert check.probe(Path("/x/tool"), run=run) == (True, "-h")
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
