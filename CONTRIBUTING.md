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

The tests need no network and do not import pwn-broker. CI runs them on Python 3.11, 3.12, 3.13 and 3.14, checks `fetch.sh` and `.github/scripts/kali-packages.sh` with `sh -n`, `dash -n` and `shellcheck`, then builds every package and installs it with pwn-broker's own code: on Ubuntu, and then inside the Kali image with the container's network off. Nothing is published until both pass. Build on Linux. Antivirus on a Windows machine removes several upstream files while a build runs.

The README panel (`docs/assets/panel-{dark,light}.svg`) is drawn by `docs/art/make-panel.py` from the kerbrute recipe; change the script and re-run it rather than editing the SVGs. CI checks they match:

```sh
python -m pip install -r docs/art/requirements.txt
python docs/art/make-panel.py
```

## Adding a tool

1. Make `recipes/<name>/`. The name is lower-case letters, digits, hyphens and dots, starting with a letter or digit, with no two dots together and none at the end, and the folder name is the tool's name.
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
5. For a Python tool, name the runtime it runs on in `manifest.toml`, with `schema = 2`, `requires = ["python-3.13"]` and an empty `[python]` table. List the tool and its direct dependencies in `requirements.in`, then pin everything:

   ```sh
   python build.py lock <name>
   ```

   This needs `uv`. It resolves the dependencies once, for the runtime's Python series on x86_64 Linux, and writes `requirements.lock`, which you commit. A build downloads only what the lock names, checks each hash, and refuses a wheel that does not fit that series. Leave `lock` and `wheels` out of the `[python]` table: the builder fills them. There is no `versions` key any more; `build` refuses a recipe that has one.
6. Build and check it:

   ```sh
   python build.py build <name>
   python build.py check <name>
   ```

   `build` writes `dist/<name>-<version>-<build>.zip`, its card and its sums, then `dist/index.toml`. An index is refused if a tool in it requires a runtime that is not there, so for a Python tool name its runtime too, unless `dist/` already holds it: `python build.py build <name> python-3.13`. `check` reads the recipe and every zip in `dist/` against pwn-broker's package rules.
7. Add the tool to the table in `README.md` and a line to `CHANGELOG.md`, and open a pull request. CI builds it, installs it with pwn-broker, after its runtime, and starts every command, on Ubuntu and then in Kali with the network off.

## Adding a runtime

A runtime is a Python for the tools that require it, named for its series: `python-3.13`. Its recipe has one `[[source]]`: python-build-standalone's `install_only_stripped` archive for `x86_64-unknown-linux-gnu`, pinned by size and SHA-256 (compare the hash with the release's `SHA256SUMS`) and carried whole to `files/python.tar.gz`, with no `member`. Its manifest is `schema = 2` and `kind = "runtime"` with a `[runtime]` table: `archive`, the path of the archive in the package; `python`, the interpreter inside the unpacked archive; and `series`, the Python the requiring tools' wheels are built for. It has no `[[command]]`, `[[upload]]`, `[data]` or `[python]`, and requires nothing. See `recipes/python-3.13/`.

The stripped archive has none of the licences of the libraries it bundles. Download the `pgo+lto-full` distribution of the same CPython from the same release and check it against `SHA256SUMS`. `PYTHON.json` names licences only for extension modules, so also take, from the CPython source at the tag of the same version, `Doc/license.rst` and `Modules/_hacl/Hacl_Hash_SHA2.c`: they carry the notices for mimalloc and HACL*, which are compiled into the interpreter. Then write `LICENSE` from the four files:

```sh
python tools/runtime-licence.py <stripped archive> <full distribution> <license.rst> <Hacl_Hash_SHA2.c> > recipes/<name>/LICENSE
```

The full distribution is a `.tar.zst`, so this needs the `zstandard` package; give it the file under its upstream name, which the headings quote. It only reads its inputs, and on stderr it names each licence file in the full distribution that it left out because `PYTHON.json` names it for nothing in the build. Name the full distribution and the two source files, each with its size and SHA-256, in a comment in `recipe.toml`.

A tool moves to a new runtime by naming it in `requires` and raising its `build`.

## Changing a tool

A published build is never changed or replaced. If a recipe changes in any way that changes the package (a different upstream version, a re-pinned dependency, a new description), raise `build` in `recipe.toml`. CI builds the recipe again, compares its card with the published one, and fails with the field that differs if `build` did not go up. The whole card is compared, size and SHA-256 included, so any change to a file in the zip shows. The one exception is a recipe with `build_from_source`: its size and hash are ignored, and the two zips are compared member by member instead, except the wheels of the distributions listed there and their lines in `wheels.lock`. If an upstream file has gone or no longer matches its pin, CI warns instead and still tests the published package; repin the recipe and raise `build`.

A build is withdrawn by deleting its release and its git tag. Then either raise `build` in the recipe, which publishes a replacement, or remove the recipe. If neither is done, the next push to `main` builds and publishes that same build again. The index is rebuilt from the releases that exist, so a deleted release leaves the index at the next publish.

## Manifest rules

`python build.py check` enforces these, using the same rules as pwn-broker. The ones that catch people:

- `name`, `version` and `build` may be left out of `manifest.toml`; the recipe supplies them. If you write them they must equal the recipe's. A version starts with a letter or digit and holds only letters, digits and `. _ + ~ -`, 100 characters at most.
- `kind` is `kali`, `upload`, `both` or `runtime`. A `kali` package needs a `[[command]]` and no `[[upload]]`. An `upload` package needs an `[[upload]]` and no `[[command]]`. `both` needs one of each. A `runtime` has neither (see above).
- `schema` is 2 for a runtime and for a tool that requires one, and 1 for everything else; the builder works it out, and a `schema` you write must agree. A schema 1 package cannot have `requires` or be a runtime.
- `requires` is a list of runtime names. A Python tool names exactly one; a tool that is not Python names none. A name may not repeat, may not be the tool's own, and must be a runtime with a recipe beside this one.
- `description`, `upstream`, `licence` and `arch` are required and not empty. `arch` must be `x86_64`, because pwn-broker refuses a package for another processor, and `check` refuses anything else.
- Every path (`path`, `script`, the recipe's `to`) is relative, starts with `files/`, names a file and has no `..`, no backslash and no drive letter.
- A command's `name` starts with a letter or digit and holds only letters, digits, `.`, `_` and `-`. No two commands in one package share a name, and no two tools share one. A command may not take a name that must never be stood in for, since its launcher comes first on PATH: a shell, anything that asks for a password (`sudo`, `ssh`, `passwd`), Python or pip under any name such as `python3.13`, a program pwn-broker runs itself (`chown`, `tar`, `vim`, `openvpn`), an everyday command (`ls`, `grep`, `less`), or what manages the system or fetches something to run (`apt`, `curl`, `git`). `RESERVED_COMMANDS` in `build.py` is the whole list, and it is pwn-broker's. A tool may still take the name of Kali's own copy of it.
- A command's `type` is `binary` (with `path`), `python-script` (with `script`) or `python-entry` (with `entry`, a plain console-script name). `env` is a table of strings whose names match `[A-Za-z_][A-Za-z0-9_]*` and are not `data`, `box`, `run` or `pid`, and `args` is a list of strings. No `args` or `env` value, and no path, holds a newline or NUL. `{data}` in either stands for the box's `tooldata/<tool>/` folder.
- A `python-script` or `python-entry` command needs a `[python]` table, and a `[python]` table is only for a package that has one. A package with either kind of command is a Python tool.
- An `[[upload]]`'s `as` is a plain file name with no slash, and no two uploads share one.
- Every file the manifest names must be in the package, so it must be some source's `to`. Two sources cannot share a `to`, even in a different case.
- A `member` must match exactly one regular file in its archive. A link or a directory is refused.
- `[data] home` lists paths under the home folder that the tool writes to, relative and without `..`. Each has to be a folder named after the tool, or something inside one: `.<name>`, or `<name>` under `.config`, `.local/share`, `.local/state` or `.cache`, where `<name>` is the tool's name or one of its commands' (`ligolo-ng` lists `.ligolo-proxy`). pwn-broker moves what is listed into the box at `finish` and deletes it from the home folder, so a tool may claim only what is plainly its own. It may not name, or lie under, a place that holds keys or sign-ins or runs something at login (`.ssh`, `.gnupg`, `.aws`, `.config/autostart`, `.local/bin` and the rest of `HOME_TREES` in `build.py`), and may not be exactly a shared folder or file such as `.config`, `.local/share`, `.cache`, `.bashrc`, `.netrc` or `Documents` (`HOME_SHARED`): give the tool a folder of its own such as `.config/<tool>`.
- In a Python package, `wheels.lock` holds one `name==version --hash=sha256:...` line for each distribution (a hash for every wheel of it), with no blank lines, comments or carriage returns, and is under 1 MiB. `build.py` writes it that way.
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
