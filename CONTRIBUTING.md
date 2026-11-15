# Contributing

Small project, light process. Most changes are a new tool or a new build of one.

## Reporting a problem

Use the bug form. Name the tool and build (`kerbrute 1.0.3-1`), your pwn-broker version, and what `tool install` or `fetch.sh` printed. Remove real credentials, flags and addresses first. To ask for a tool, use the tool form.

## Setting up and testing

You need Python 3.11 or later. `build.py` uses the standard library and `tomli-w`; building a Python tool also runs `pip`, and `lock` runs `uv`.

```sh
python3 -m venv .venv
.venv/bin/pip install pytest tomli-w
.venv/bin/python -m pytest -q -W error
```

The tests need no network and do not import pwn-broker. CI runs them on Python 3.11, 3.12 and 3.13, checks `fetch.sh` with `sh -n`, `dash -n` and `shellcheck`, then builds every package and installs it with pwn-broker's own code. Build on Linux. Antivirus on a Windows machine removes several upstream files while a build runs.

## Adding a tool

1. Make `recipes/<name>/`. The name is lower-case letters, digits and hyphens, and the folder name is the tool's name.
2. Write `recipe.toml`: the upstream version, your build number, and one `[[source]]` for each upstream file.

   ```toml
   version = "1.0.3"
   build = 1

   [[source]]
   url = "https://github.com/ropnop/kerbrute/releases/download/v1.0.3/kerbrute_linux_amd64"
   size = 8286607
   sha256 = "710a9d2653c8bd3689e451778dab9daec0de4c4c75f900788ccf23ef254b122a"
   to = "files/kerbrute_linux_amd64"
   ```

   Download the file once, and take its size and hash from the file: `wc -c < file` and `sha256sum file`. If upstream publishes checksums, compare them, and say in a comment when it does not. A source may be a bare file, or one member of a `tar.gz` or `zip` (`member = "proxy"`). A tool with no releases is pinned to a full commit hash in the URL, and its version is the commit's date.
3. Copy the upstream licence text, unchanged, to `LICENSE`.
4. Write `manifest.toml`: the manifest that goes into the package (see below).
5. For a Python tool, list the tool and its direct dependencies in `requirements.in`, then pin everything:

   ```sh
   python build.py lock <name>
   ```

   This needs `uv`. It writes `requirements.lock`, which you commit. A build downloads only what the lock names and checks each hash. Leave `versions`, `lock` and `wheels` out of the `[python]` table: the builder fills them.
6. Build and check it:

   ```sh
   python build.py build <name>
   python build.py check <name>
   ```

   `build` writes `dist/<name>-<version>-<build>.zip`, its card and its sums. `check` reads the recipe and every zip in `dist/` against pwn-broker's package rules.
7. Add the tool to the table in `README.md` and a line to `CHANGELOG.md`, and open a pull request. CI builds it, installs it with pwn-broker and starts every command.

## Changing a tool

A published build is never changed or replaced. If a recipe changes in any way that changes the package (a different upstream version, a re-pinned dependency, a new description), raise `build` in `recipe.toml`. CI builds the recipe again, compares its card with the published one, and fails with the field that differs if `build` did not go up. For a Python tool, a different size or hash alone is allowed, because the wheels come from the index at build time; the other fields are not.

To drop a build, delete its release. The next index leaves it out.

## Manifest rules

`python build.py check` enforces these, using the same rules as pwn-broker. The ones that catch people:

- `name`, `version` and `build` may be left out of `manifest.toml`; the recipe supplies them. If you write them they must equal the recipe's. A version has no slash, backslash, colon or space.
- `kind` is `kali`, `upload` or `both`. A `kali` package needs a `[[command]]` and no `[[upload]]`. An `upload` package needs an `[[upload]]` and no `[[command]]`. `both` needs one of each.
- `description`, `upstream`, `licence` and `arch` are required and not empty. `arch` must be `x86_64`, because pwn-broker refuses a package for another processor.
- Every path (`path`, `script`, the recipe's `to`) is relative, starts with `files/`, names a file and has no `..`, no backslash and no drive letter.
- A command's `name` starts with a letter or digit and holds only letters, digits, `.`, `_` and `-`. No two commands share a name.
- A command's `type` is `binary` (with `path`), `python-script` (with `script`) or `python-entry` (with `entry`, a plain console-script name). `env` is a table of strings and `args` a list of strings. `{data}` in either stands for the box's `tooldata/<tool>/` folder.
- A `python-script` or `python-entry` command needs a `[python]` table, and a `[python]` table is only for a package that has one.
- An `[[upload]]`'s `as` is a plain file name with no slash.
- Every file the manifest names must be in the package, so it must be some source's `to`. Two sources cannot share a `to`, even in a different case.
- A `member` must match exactly one regular file in its archive. A link or a directory is refused.
- `[data] home` lists paths under the home folder that the tool writes to, relative and without `..`.
- `examples` may use `{ip}`, `{hostname}`, `{domain}` and `{lhost}`, and no other placeholder. `build.py check` does not look, but the tests do.

## Dependencies

A Python dependency that publishes no wheel can be built from its pinned source archive if the recipe lists it in `build_from_source` (a list of distribution names in `recipe.toml`). The archive is hash-checked, but its build requirements are fetched without hashes and its build code runs on the build machine, so this is opt-in for each recipe and none of the current ones uses it. Prefer a tool whose dependencies are all wheels.

## Commits

One short, plain line in the imperative: `Add a recipe for chisel`. No prefixes, no trailing full stop. Say more in the pull request if it needs it.

## Style

- Python: 4 spaces (`.editorconfig`), standard library and `tomli-w` only in `build.py`.
- Shell: POSIX `sh`, `set -eu`, passes `shellcheck`.
- Comments say why, not what.
- Status lines: lowercase, plain, starting with `ok`, `warn` or `fail`.
