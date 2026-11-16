"""fetch.sh, run with a stand-in curl that serves release files from a folder."""

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import tomli_w

import build

SCRIPT = Path(__file__).resolve().parent.parent / "fetch.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None or shutil.which("sha256sum") is None,
    reason="needs sh and sha256sum",
)

REPO = "Real-Fruit-Snacks/pwn-shop"

# Stands in for curl. A release file's address is
# https://github.com/<owner>/<name>/releases/download/<tag>/<file>, served from
# $FAKE_RELEASES/<tag>/<file>. It writes "<tag> <file>" to $FAKE_CURL_LOG for each request.
#
# Real curl without -f saves an error page and exits 0, without -L saves GitHub's redirect,
# and without the two --proto options follows a redirect to plain HTTP. The stand-in cannot
# show any of that, so it refuses a call that lacks one of them, or the time limits.
# $FAKE_PARTIAL names a file it writes half of before it fails, as a dropped connection does,
# and $FAKE_SLOW a file it waits five seconds before serving.
FAKE_CURL = """#!/bin/sh
out=""
url=""
short=""
proto=""
redir=""
connect=""
limit=""
window=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        --proto) proto="$2"; shift 2 ;;
        --proto-redir) redir="$2"; shift 2 ;;
        --connect-timeout) connect="$2"; shift 2 ;;
        --speed-limit) limit="$2"; shift 2 ;;
        --speed-time) window="$2"; shift 2 ;;
        --*) echo "curl stand-in: an option it does not know: $1" >&2; exit 2 ;;
        -*) short="$short$1"; shift ;;
        *) url="$1"; shift ;;
    esac
done
for letter in f s S L; do
    case "$short" in
        *"$letter"*) ;;
        *) echo "curl stand-in: called without -$letter" >&2; exit 2 ;;
    esac
done
if [ "$proto" != "=https" ] || [ "$redir" != "=https" ]; then
    echo "curl stand-in: called without --proto =https --proto-redir =https" >&2
    exit 2
fi
if [ "$connect" != 30 ] || [ "$limit" != 1 ] || [ "$window" != 60 ]; then
    echo "curl stand-in: called without --connect-timeout 30 --speed-limit 1 --speed-time 60" >&2
    exit 2
fi
if [ -z "$out" ]; then
    echo "curl stand-in: called without -o" >&2
    exit 2
fi
rest="${url#https://github.com/}"
if [ "$rest" = "$url" ]; then
    echo "curl: not a github.com address: $url" >&2
    exit 3
fi
repo="${rest%%/releases/download/*}"
path="${rest#*/releases/download/}"
tag="${path%%/*}"
file="${path#*/}"
echo "$tag $file" >>"$FAKE_CURL_LOG"
if [ "$repo" != "$FAKE_REPO" ] || [ ! -f "$FAKE_RELEASES/$tag/$file" ]; then
    echo "curl: (22) The requested URL returned error: 404" >&2
    exit 22
fi
if [ "$file" = "${FAKE_SLOW:-}" ]; then
    sleep 5
fi
if [ "$file" = "${FAKE_PARTIAL:-}" ]; then
    head -c 4 "$FAKE_RELEASES/$tag/$file" >"$out"
    echo "curl: (18) transfer closed with outstanding read data remaining" >&2
    exit 18
fi
cat "$FAKE_RELEASES/$tag/$file" >"$out"
"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def card(name, version, data, **extra):
    file = f"{name}-{version}-1.zip"
    return {
        "name": name, "version": version, "build": 1, "kind": "kali",
        "description": extra.pop("description", f"{name} does a thing"),
        "tags": ["x"], "upstream": "https://example.invalid", "licence": "MIT",
        "needs_root": False, "commands": [name], "file": file, "size": len(data),
        "sha256": sha(data), **extra,
    }


def index_text(cards, base_url=None) -> str:
    return tomli_w.dumps(build.build_index(build.card_order(cards), base_url=base_url))


@pytest.fixture
def shop(tmp_path):
    """A fake set of releases: two tools and the index."""
    releases = tmp_path / "releases"
    data = {"alpha": b"alpha bytes", "beta": b"beta bytes"}
    cards = []
    for name, blob in data.items():
        c = card(name, "1.0", blob)
        cards.append(c)
        folder = releases / f"{name}-v1.0-1"
        folder.mkdir(parents=True)
        (folder / c["file"]).write_bytes(blob)
    (releases / "index").mkdir()
    base = "https://github.com/x/y/releases/download/{tag}/{file}"
    (releases / "index" / "index.toml").write_text(index_text(cards, base), encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "curl").write_bytes(FAKE_CURL.encode())
    (bindir / "curl").chmod(0o755)
    return releases, bindir, cards


def fetch_env(tmp_path, shop, *, repo=None, served_repo=REPO, extra=None):
    releases, bindir, _ = shop
    env = dict(os.environ)
    env["PATH"] = bindir.as_posix() + os.pathsep + env["PATH"]
    env["FAKE_RELEASES"] = releases.as_posix()
    env["FAKE_CURL_LOG"] = (tmp_path / "curl.log").as_posix()
    env["FAKE_REPO"] = served_repo
    for name in ("PWN_SHOP_REPO", "CDPATH", "FAKE_PARTIAL", "FAKE_SLOW"):
        env.pop(name, None)
    if repo is not None:
        env["PWN_SHOP_REPO"] = repo
    env.update(extra or {})
    return env


def run_fetch(tmp_path, shop, folder, *, repo=None, served_repo=REPO, args=None, cwd=None, extra=None):
    env = fetch_env(tmp_path, shop, repo=repo, served_repo=served_repo, extra=extra)
    argv = ["sh", SCRIPT.as_posix(), *(args if args is not None else [folder.as_posix()])]
    result = subprocess.run(argv, env=env, capture_output=True, text=True, cwd=cwd)
    log = tmp_path / "curl.log"
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


# ---- the parsing, called with a file


def parse(tmp_path, text):
    path = tmp_path / "index.toml"
    path.write_text(text, encoding="utf-8", newline="\n")
    result = subprocess.run(
        ["sh", SCRIPT.as_posix(), "--parse-index", path.as_posix()],
        capture_output=True, text=True,
    )
    return result


def test_parse_reads_tag_file_and_hash_for_every_build(tmp_path):
    a1, a2 = card("alpha", "1.0", b"a1"), card("alpha", "1.1", b"a2")
    b1 = card("beta", "2024.12.16", b"b")
    result = parse(tmp_path, index_text([a1, a2, b1], "https://h/{tag}/{file}"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"alpha-v1.0-1\talpha-1.0-1.zip\t{sha(b'a1')}",
        f"alpha-v1.1-1\talpha-1.1-1.zip\t{sha(b'a2')}",
        f"beta-v2024.12.16-1\tbeta-2024.12.16-1.zip\t{sha(b'b')}",
    ]


def test_parse_works_on_an_index_with_bare_file_names(tmp_path):
    result = parse(tmp_path, index_text([card("alpha", "1.0", b"a")]))
    assert result.stdout.splitlines() == [f"alpha-v1.0-1\talpha-1.0-1.zip\t{sha(b'a')}"]


NASTY = [
    'file = "evil.zip"',
    'x"\nfile = "evil.zip"\nsha256 = "' + "0" * 64,
    "[[tool.build]]",
    'a\n[[tool.build]]\nfile = "evil-1-1.zip"',
    "name = \"evil\" sha256 = \"" + "1" * 64 + "\"",
    "file = x\\\nfile = \"evil.zip\"",
]


@pytest.mark.parametrize("description", NASTY)
def test_a_description_with_the_same_words_changes_nothing(tmp_path, description):
    good = card("alpha", "1.0", b"a", description=description)
    other = card("beta", "1.0", b"b", description=description)
    result = parse(tmp_path, index_text([good, other]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"alpha-v1.0-1\talpha-1.0-1.zip\t{sha(b'a')}",
        f"beta-v1.0-1\tbeta-1.0-1.zip\t{sha(b'b')}",
    ]


def test_parse_lowercases_the_hash(tmp_path):
    text = index_text([card("alpha", "1.0", b"a")]).replace(sha(b"a"), sha(b"a").upper())
    assert parse(tmp_path, text).stdout.split("\t")[2].strip() == sha(b"a")


@pytest.mark.parametrize(
    "old, new",
    [
        ('file = "alpha-1.0-1.zip"', 'file = "../alpha-1.0-1.zip"'),
        ('file = "alpha-1.0-1.zip"', 'file = "other-1.0-1.zip"'),
        ('file = "alpha-1.0-1.zip"', 'file = "a/b.zip"'),
        ('name = "alpha"', 'name = "../alpha"'),
        ('version = "1.0"', 'version = "1 0"'),
        ("sha256 = ", "sha256 = 'x' #"),
    ],
)
def test_parse_refuses_what_could_leave_the_folder(tmp_path, old, new):
    text = index_text([card("alpha", "1.0", b"a")])
    assert old in text
    result = parse(tmp_path, text.replace(old, new, 1))
    assert result.returncode != 0
    assert result.stdout == ""


def runtime_card(data: bytes) -> dict:
    return {
        **card("python-3.13", "3.13.18", data), "kind": "runtime", "tags": [], "commands": [],
        "licence": "PSF-2.0 and others (see LICENSE)",
    }


def test_parse_reads_a_runtime_and_a_tool_that_requires_it(tmp_path):
    tool = {**card("certipy", "5.1.0", b"c"), "build": 3, "file": "certipy-5.1.0-3.zip",
            "requires": ["python-3.13"]}
    result = parse(tmp_path, index_text([tool, runtime_card(b"r")], "https://h/{tag}/{file}"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"certipy-v5.1.0-3\tcertipy-5.1.0-3.zip\t{sha(b'c')}",
        f"python-3.13-v3.13.18-1\tpython-3.13-3.13.18-1.zip\t{sha(b'r')}",
    ]


def test_the_name_rule_is_the_one_build_py_applies():
    text = SCRIPT.read_text(encoding="utf-8")
    assert f"name !~ /^{build._NAME.pattern.replace('(?:', '(')}$/" in text


@pytest.mark.parametrize("name", ["python-3.13.", ".python", "python..3", "Python-3.13", "a/b"])
def test_parse_refuses_a_name_the_rule_does_not_allow(tmp_path, name):
    text = index_text([runtime_card(b"r")]).replace('name = "python-3.13"', f'name = "{name}"', 1)
    text = text.replace('file = "python-3.13-', f'file = "{name}-', 1)
    result = parse(tmp_path, text)
    assert result.returncode != 0
    assert "no valid tool name" in result.stderr
    assert result.stdout == ""


def test_parse_refuses_a_short_hash(tmp_path):
    text = index_text([card("alpha", "1.0", b"a")]).replace(sha(b"a"), sha(b"a")[:63])
    assert parse(tmp_path, text).returncode != 0


def test_parse_refuses_a_hash_that_is_not_hexadecimal(tmp_path):
    text = index_text([card("alpha", "1.0", b"a")]).replace(sha(b"a"), "g" + sha(b"a")[1:])
    result = parse(tmp_path, text)
    assert result.returncode != 0
    assert "not 64 hexadecimal characters" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "old, new",
    [
        ("[[tool.build]]", "[[tool.build]] # the second build"),
        ("[[tool.build]]", "[[tool.build]] "),
        ("[[tool.build]]", "[tool.build]"),
        ("[[tool]]", "[[tool]] # beta"),
        ("[[tool]]", "[[tools]]"),
        ("[[tool.build]]", "  [[tool.build]]"),
        ("[[tool.build]]", "\t[[tool.build]]"),
        ("[[tool]]", " [[tool]]"),
    ],
)
def test_parse_refuses_a_table_header_build_py_does_not_write(tmp_path, old, new):
    # read as some other table, the build under it would be left out without a word
    text = index_text([card("alpha", "1.0", b"a"), card("beta", "1.0", b"b")])
    head, _, tail = text.rpartition(old)
    result = parse(tmp_path, head + new + tail)
    assert result.returncode != 0
    assert "a table header build.py does not write" in result.stderr
    # nothing is printed for the build that did parse: the index is refused whole
    assert result.stdout == ""


def test_parse_refuses_an_index_with_no_builds(tmp_path):
    result = parse(tmp_path, "schema = 1\n")
    assert result.returncode != 0
    assert "names no packages" in result.stderr


# ---- the whole script


def test_it_downloads_checks_and_prints_the_config_line(tmp_path, shop):
    folder = tmp_path / "packages"
    result, calls = run_fetch(tmp_path, shop, folder)
    assert result.returncode == 0, result.stderr
    assert (folder / "index.toml").is_file()
    assert (folder / "alpha-1.0-1.zip").read_bytes() == b"alpha bytes"
    assert (folder / "beta-1.0-1.zip").read_bytes() == b"beta bytes"
    # the index twice: once to see it is there before the folder is touched, once to keep
    assert calls == [
        "index index.toml",
        "index index.toml",
        "alpha-v1.0-1 alpha-1.0-1.zip",
        "beta-v1.0-1 beta-1.0-1.zip",
    ]
    lines = result.stdout.splitlines()
    assert lines[-1].startswith('tools_index = "')
    assert lines[-1].endswith('packages"')
    assert [p.name for p in folder.iterdir() if p.name.startswith(".")] == []
    assert all(line.split()[0] == "ok" for line in lines[:-2])


def test_a_runtime_is_downloaded_like_any_package(tmp_path, shop):
    releases, _, cards = shop
    blob = b"runtime bytes"
    runtime = runtime_card(blob)
    folder = releases / "python-3.13-v3.13.18-1"
    folder.mkdir()
    (folder / runtime["file"]).write_bytes(blob)
    base = "https://github.com/x/y/releases/download/{tag}/{file}"
    (releases / "index" / "index.toml").write_text(index_text([*cards, runtime], base), encoding="utf-8")
    packages = tmp_path / "packages"
    result, calls = run_fetch(tmp_path, shop, packages)
    assert result.returncode == 0, result.stderr
    assert "python-3.13-v3.13.18-1 python-3.13-3.13.18-1.zip" in calls
    assert (packages / "python-3.13-3.13.18-1.zip").read_bytes() == blob
    assert "ok    3 packages in" in result.stdout


def test_the_published_index_with_urls_needs_no_rewrite(tmp_path, shop):
    # the folder form of pwn-broker looks zips up by file name and never reads url
    folder = tmp_path / "packages"
    run_fetch(tmp_path, shop, folder)
    text = (folder / "index.toml").read_text()
    assert "https://github.com/x/y/releases/download/alpha-v1.0-1/alpha-1.0-1.zip" in text


def test_a_second_run_downloads_only_the_index(tmp_path, shop):
    folder = tmp_path / "packages"
    run_fetch(tmp_path, shop, folder)
    (tmp_path / "curl.log").unlink()
    result, calls = run_fetch(tmp_path, shop, folder)
    assert result.returncode == 0, result.stderr
    assert calls == ["index index.toml", "index index.toml"]
    assert "already here" in result.stdout


def test_a_wrong_hash_removes_the_file_and_stops(tmp_path, shop):
    releases, _, _ = shop
    (releases / "beta-v1.0-1" / "beta-1.0-1.zip").write_bytes(b"swapped")
    folder = tmp_path / "packages"
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert "the download of beta-1.0-1.zip does not match the SHA-256 in the index" in result.stderr
    assert "left as it was" not in result.stderr
    assert not (folder / "beta-1.0-1.zip").exists()
    assert (folder / "alpha-1.0-1.zip").is_file()
    assert not (folder / "index.toml").exists()
    assert [p.name for p in folder.iterdir() if p.name.startswith(".")] == []


def test_a_corrupt_copy_already_there_is_replaced(tmp_path, shop):
    folder = tmp_path / "packages"
    folder.mkdir()
    (folder / "alpha-1.0-1.zip").write_bytes(b"corrupt")
    result, calls = run_fetch(tmp_path, shop, folder)
    assert result.returncode == 0, result.stderr
    assert (folder / "alpha-1.0-1.zip").read_bytes() == b"alpha bytes"
    assert "alpha-v1.0-1 alpha-1.0-1.zip" in calls


def test_a_file_already_there_is_kept_when_the_download_is_wrong_too(tmp_path, shop):
    releases, _, _ = shop
    (releases / "alpha-v1.0-1" / "alpha-1.0-1.zip").write_bytes(b"swapped")
    folder = tmp_path / "packages"
    folder.mkdir()
    (folder / "alpha-1.0-1.zip").write_bytes(b"corrupt")
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert "the download of alpha-1.0-1.zip does not match" in result.stderr
    assert "the copy already in the folder was left as it was" in result.stderr
    assert (folder / "alpha-1.0-1.zip").read_bytes() == b"corrupt"
    assert [p.name for p in folder.iterdir() if p.name.startswith(".")] == []


def test_stale_work_folders_from_a_killed_run_are_removed(tmp_path, shop):
    folder = tmp_path / "packages"
    stale = folder / ".fetch.AbC123"
    stale.mkdir(parents=True)
    (stale / "half.zip").write_bytes(b"half")
    other = folder / ".keep-me"
    other.mkdir()
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    assert other.is_dir()


def test_a_missing_release_fails_without_an_index(tmp_path, shop):
    releases, _, _ = shop
    shutil.rmtree(releases / "beta-v1.0-1")
    folder = tmp_path / "packages"
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert "cannot download beta-1.0-1.zip" in result.stderr
    assert not (folder / "index.toml").exists()


def test_an_index_that_cannot_be_downloaded_stops_before_touching_the_folder(tmp_path, shop):
    releases, _, _ = shop
    shutil.rmtree(releases / "index")
    folder = tmp_path / "packages"
    result, calls = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert f"cannot download index.toml from the index release of {REPO}" in result.stderr
    assert "the repository is public" in result.stderr
    assert calls == ["index index.toml"]
    assert not folder.exists()


def test_it_needs_no_gh_and_no_sign_in(tmp_path, shop):
    # a gh on PATH is never run: the release files of a public repository need no account
    _, bindir, _ = shop
    marker = tmp_path / "gh-ran"
    (bindir / "gh").write_bytes(f'#!/bin/sh\necho ran >"{marker.as_posix()}"\nexit 1\n'.encode())
    (bindir / "gh").chmod(0o755)
    result, _ = run_fetch(tmp_path, shop, tmp_path / "packages")
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_pwn_shop_repo_sets_the_repository_it_downloads_from(tmp_path, shop):
    folder = tmp_path / "packages"
    result, _ = run_fetch(tmp_path, shop, folder, repo="someone/fork", served_repo="someone/fork")
    assert result.returncode == 0, result.stderr
    assert (folder / "alpha-1.0-1.zip").read_bytes() == b"alpha bytes"
    # and the default repository is not asked when another is named
    result, _ = run_fetch(tmp_path, shop, tmp_path / "other", repo="someone/fork")
    assert result.returncode != 0
    assert "index release of someone/fork" in result.stderr


@pytest.mark.parametrize(
    "repo",
    ["noslash", "a/b c", "a/b;id", "https://github.com/a/b?x", "a/b/c", "a//b", "../..", "/a", "a/", "a/.."],
)
def test_a_pwn_shop_repo_that_is_not_owner_and_name_is_refused(tmp_path, shop, repo):
    folder = tmp_path / "packages"
    result, calls = run_fetch(tmp_path, shop, folder, repo=repo)
    assert result.returncode != 0
    assert "PWN_SHOP_REPO must look like owner/name" in result.stderr
    assert calls == []
    assert not folder.exists()


def test_the_help_names_what_it_needs(tmp_path, shop):
    result, calls = run_fetch(tmp_path, shop, tmp_path, args=["--help"])
    assert result.returncode == 0
    assert "Needs curl and sha256sum" in result.stdout
    assert "gh" not in result.stdout.split()
    assert calls == []


def test_a_run_that_fails_keeps_the_index_of_the_run_before(tmp_path, shop):
    releases, _, _ = shop
    folder = tmp_path / "packages"
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode == 0, result.stderr
    before = (folder / "index.toml").read_bytes()
    (folder / "beta-1.0-1.zip").write_bytes(b"corrupt")
    (releases / "beta-v1.0-1" / "beta-1.0-1.zip").write_bytes(b"swapped")
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert (folder / "index.toml").read_bytes() == before


def test_a_download_that_stops_half_way_is_not_kept(tmp_path, shop):
    folder = tmp_path / "packages"
    result, _ = run_fetch(tmp_path, shop, folder, extra={"FAKE_PARTIAL": "beta-1.0-1.zip"})
    assert result.returncode != 0
    assert "cannot download beta-1.0-1.zip" in result.stderr
    assert sorted(p.name for p in folder.iterdir()) == ["alpha-1.0-1.zip"]


@pytest.mark.parametrize("name", ["alpha-1.0-1.zip", "index.toml"])
def test_a_folder_where_a_file_would_go_is_refused(tmp_path, shop, name):
    # mv would put the file inside it, where pwn-broker does not look
    folder = tmp_path / "packages"
    (folder / name).mkdir(parents=True)
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert f"{name} is a link or a folder" in result.stderr
    assert list((folder / name).iterdir()) == []
    assert not (folder / "index.toml").is_file()
    assert [p.name for p in folder.iterdir() if p.name.startswith(".")] == []


@pytest.mark.skipif(sys.platform == "win32", reason="needs symbolic links")
@pytest.mark.parametrize("name", ["alpha-1.0-1.zip", "index.toml"])
@pytest.mark.parametrize("target", ["folder", "file", "nothing"])
def test_a_link_where_a_file_would_go_is_refused(tmp_path, shop, name, target):
    # a link to a folder would take the file out of the folder fetch.sh was given
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    aim = elsewhere / "aim"
    if target == "folder":
        aim.mkdir()
    elif target == "file":
        aim.write_bytes(b"alpha bytes")
    folder = tmp_path / "packages"
    folder.mkdir()
    (folder / name).symlink_to(aim)
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert f"{name} is a link or a folder" in result.stderr
    assert (folder / name).is_symlink()
    assert [p.name for p in elsewhere.iterdir()] == (["aim"] if target != "nothing" else [])
    if target == "folder":
        assert list(aim.iterdir()) == []
    elif target == "file":
        assert aim.read_bytes() == b"alpha bytes"


def test_an_index_with_a_header_build_py_does_not_write_fetches_nothing(tmp_path, shop):
    releases, _, cards = shop
    text = index_text(cards, "https://github.com/x/y/releases/download/{tag}/{file}")
    head, _, tail = text.rpartition("[[tool.build]]")
    (releases / "index" / "index.toml").write_text(head + "[[tool.build]] # beta" + tail, encoding="utf-8")
    folder = tmp_path / "packages"
    result, calls = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert "nothing was fetched" in result.stderr
    assert calls == ["index index.toml", "index index.toml"]
    # the folder was made by this run and holds nothing, so it is not left behind
    assert not folder.exists()


def test_a_folder_that_was_already_there_is_kept_when_a_run_fails(tmp_path, shop):
    releases, _, _ = shop
    (releases / "index" / "index.toml").write_text("schema = 1\n", encoding="utf-8")
    folder = tmp_path / "packages"
    folder.mkdir()
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert folder.is_dir()
    assert list(folder.iterdir()) == []


def test_a_folder_with_a_space_and_an_accent_in_its_name_works(tmp_path, shop):
    folder = tmp_path / "my päckages"
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in folder.iterdir()) == ["alpha-1.0-1.zip", "beta-1.0-1.zip", "index.toml"]


def test_an_empty_index_is_named_as_missing(tmp_path, shop):
    releases, _, _ = shop
    (releases / "index" / "index.toml").write_bytes(b"")
    folder = tmp_path / "packages"
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert f"the index release of {REPO} has no index.toml" in result.stderr
    assert not folder.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="needs symbolic links")
def test_a_link_named_like_a_work_folder_is_left_alone(tmp_path, shop):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "mine.txt").write_bytes(b"mine")
    folder = tmp_path / "packages"
    folder.mkdir()
    (folder / ".fetch.AbC123").symlink_to(elsewhere)
    result, _ = run_fetch(tmp_path, shop, folder)
    assert result.returncode == 0, result.stderr
    assert (folder / ".fetch.AbC123").is_symlink()
    assert (elsewhere / "mine.txt").read_bytes() == b"mine"


@pytest.mark.skipif(sys.platform == "win32", reason="such a name needs POSIX")
def test_a_folder_whose_real_path_ends_in_a_line_break_is_not_taken_for_its_neighbour(tmp_path, shop):
    # $( ) drops line breaks at the end, so "out\n" resolved would read as "out"
    inside = tmp_path / "out\n"
    inside.mkdir()
    neighbour = tmp_path / "out"
    neighbour.mkdir()
    result, calls = run_fetch(tmp_path, shop, None, args=["."], cwd=inside)
    assert result.returncode != 0
    assert "a quote, a backslash or a control character" in result.stderr
    assert list(neighbour.iterdir()) == []
    assert list(inside.iterdir()) == []
    assert calls == ["index index.toml"]


def test_a_folder_named_with_a_dash_is_the_folder_here(tmp_path, shop):
    # cd - means the folder you were in before, not a folder called -
    here = tmp_path / "here"
    here.mkdir()
    result, _ = run_fetch(tmp_path, shop, None, args=["-"], cwd=here)
    assert result.returncode == 0, result.stderr
    assert (here / "-" / "index.toml").is_file()
    assert (here / "-" / "alpha-1.0-1.zip").read_bytes() == b"alpha bytes"


@pytest.mark.skipif(sys.platform == "win32", reason="CDPATH is a list of POSIX paths")
def test_cdpath_does_not_send_it_to_another_folder(tmp_path, shop):
    here = tmp_path / "here"
    here.mkdir()
    other = tmp_path / "other"
    (other / "pk").mkdir(parents=True)
    result, _ = run_fetch(tmp_path, shop, None, args=["pk"], cwd=here, extra={"CDPATH": other.as_posix()})
    assert result.returncode == 0, result.stderr
    assert (here / "pk" / "index.toml").is_file()
    assert list((other / "pk").iterdir()) == []
    assert result.stdout.splitlines()[-1] == f'tools_index = "{(here / "pk").resolve().as_posix()}"'


@pytest.mark.skipif(sys.platform == "win32", reason="signals to a POSIX process")
@pytest.mark.parametrize("sig", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_a_run_that_is_killed_leaves_no_work_folder(tmp_path, shop, sig):
    folder = tmp_path / "packages"
    env = fetch_env(tmp_path, shop, extra={"FAKE_SLOW": "beta-1.0-1.zip"})
    process = subprocess.Popen(
        ["sh", SCRIPT.as_posix(), folder.as_posix()], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    log = tmp_path / "curl.log"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if log.exists() and "beta-v1.0-1 beta-1.0-1.zip" in log.read_text():
            break
        time.sleep(0.05)
    else:
        process.kill()
        pytest.fail("the download of beta never started")
    process.send_signal(getattr(signal, sig))
    process.communicate(timeout=30)
    assert process.returncode == 1
    assert sorted(p.name for p in folder.iterdir()) == ["alpha-1.0-1.zip"]


def test_it_needs_exactly_one_argument(tmp_path, shop):
    result, _ = run_fetch(tmp_path, shop, tmp_path, args=[])
    assert result.returncode == 2
    assert "usage: fetch.sh <folder>" in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="such a name needs POSIX")
@pytest.mark.parametrize("name", ['quo"te', "back\\slash", "line\nbreak", "tab\there", "bell\a"])
def test_a_folder_path_that_config_toml_would_need_escaped_is_refused(tmp_path, shop, name):
    folder = tmp_path / name
    result, calls = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert "a quote, a backslash or a control character" in result.stderr
    assert calls == []
    assert not folder.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="such a name needs POSIX")
@pytest.mark.parametrize("name", ['quo"te', "line\nbreak"])
def test_a_folder_under_a_path_config_toml_cannot_hold_is_refused_and_not_left(tmp_path, shop, name):
    # the name given is plain; the folder above it is not, which only shows once it is resolved
    parent = tmp_path / name
    parent.mkdir()
    result, _ = run_fetch(tmp_path, shop, None, args=["packages"], cwd=parent)
    assert result.returncode != 0
    assert "a quote, a backslash or a control character" in result.stderr
    assert list(parent.iterdir()) == []
