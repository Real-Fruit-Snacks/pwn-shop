"""fetch.sh, run with a stand-in curl that serves release files from a folder."""

import hashlib
import os
import shutil
import subprocess
import sys
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

# Stands in for curl -fsSL -o <out> <url>: a release file's address is
# https://github.com/<owner>/<name>/releases/download/<tag>/<file>, served from
# $FAKE_RELEASES/<tag>/<file>. It writes "<tag> <file>" to $FAKE_CURL_LOG for each request.
FAKE_CURL = """#!/bin/sh
out=""
url=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        --proto | --proto-redir) shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
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


def run_fetch(tmp_path, shop, folder, *, repo=None, served_repo=REPO, args=None):
    releases, bindir, _ = shop
    log = tmp_path / "curl.log"
    env = dict(os.environ)
    env["PATH"] = bindir.as_posix() + os.pathsep + env["PATH"]
    env["FAKE_RELEASES"] = releases.as_posix()
    env["FAKE_CURL_LOG"] = log.as_posix()
    env["FAKE_REPO"] = served_repo
    env.pop("PWN_SHOP_REPO", None)
    if repo is not None:
        env["PWN_SHOP_REPO"] = repo
    argv = ["sh", SCRIPT.as_posix(), *(args if args is not None else [folder.as_posix()])]
    result = subprocess.run(argv, env=env, capture_output=True, text=True)
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
    assert "beta-1.0-1.zip does not match" in result.stderr
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


@pytest.mark.parametrize("repo", ["noslash", "a/b c", "a/b;id", "https://github.com/a/b?x"])
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


def test_it_needs_exactly_one_argument(tmp_path, shop):
    result, _ = run_fetch(tmp_path, shop, tmp_path, args=[])
    assert result.returncode == 2
    assert "usage: fetch.sh <folder>" in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="a name with a quote or backslash needs POSIX")
@pytest.mark.parametrize("name", ['quo"te', "back\\slash"])
def test_a_folder_path_that_config_toml_would_need_escaped_is_refused(tmp_path, shop, name):
    folder = tmp_path / name
    result, calls = run_fetch(tmp_path, shop, folder)
    assert result.returncode != 0
    assert "quote or a backslash" in result.stderr
    # only the look at the index that comes before the folder; no zip is asked for
    assert calls == ["index index.toml"]
    assert list(folder.iterdir()) == []
