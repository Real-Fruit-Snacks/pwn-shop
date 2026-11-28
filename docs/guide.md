# Guide

How the packages reach a machine, what is in one, how `build.py` makes it, and how CI tests and publishes it. The [README](../README.md) has the short version; [tested.md](tested.md) has what has been run where.

## Getting the packages

pwn-broker reads the published index by itself when `tools_index` is not set in `/opt/pwn-broker/config.toml`: `index.toml` on this repository's release tagged `index`. Each build in it names its zip's address, size and SHA-256, and pwn-broker checks the zip against both before it uses it.

`tools_index` can instead be a URL, or a folder that holds `index.toml` and the zips it names. A folder is read by file name; the `url` in the index is not used. That is for a machine that will have no network when you want a tool, or for keeping your own copy.

`fetch.sh` fills such a folder:

```sh
git clone https://github.com/Real-Fruit-Snacks/pwn-shop
sh pwn-shop/fetch.sh ~/pwn-packages
```

It downloads `index.toml` from the `index` release, then every zip the index names from that zip's own release, into the folder (created if it is missing). Each zip is checked against the SHA-256 in the index before it is kept; one that fails is removed and the script stops. A zip that is already there with the right hash is not downloaded again. The index is moved into the folder last, so the folder never holds an index that names a zip which was not checked. A link or a folder that sits where a zip or the index would go is refused, and so is a folder path that holds a quote, a backslash or a control character. A download gives up when no connection is made in 30 seconds or nothing arrives for a minute; run it again and the zips already there are kept. A folder the script made is removed again when a run fails before anything is in it; folders above it that it had to make are left. At the end it prints:

```text
put this line in /opt/pwn-broker/config.toml:
tools_index = "/home/kali/pwn-packages"
```

It is POSIX `sh` and needs `curl` and `sha256sum`. It needs no GitHub account: every file comes from this repository's public releases, over HTTPS only. It changes nothing on the machine except that folder. `PWN_SHOP_REPO` sets the repository to download from (default `Real-Fruit-Snacks/pwn-shop`), which has to be public, and `sh fetch.sh --help` describes it.

A folder filled by `fetch.sh` does not update itself, and `tool refresh` does not reach a folder: run `fetch.sh` again after a new build is published. When the index changes, `index.toml` is briefly missing from the `index` release while the new one is uploaded; `fetch.sh` fails cleanly if it runs in that gap, so run it again.

## Building the packages yourself

On Linux:

```sh
python3 -m venv .venv
.venv/bin/pip install tomli-w
.venv/bin/python build.py build
```

That writes `dist/` with the zips, a card for each, `dist/sums/<tag>/SHA256SUMS.txt` and an `index.toml`. Building every recipe also needs .NET SDK 9.0.320 on PATH for Rubeus. Build on Linux, or let CI do it: antivirus on a Windows machine removes several upstream files while a build runs. [CONTRIBUTING.md](../CONTRIBUTING.md) has how to add a tool or a runtime.

## The package format

A package is one zip for one build of one tool: `manifest.toml`, the upstream licence as `LICENSE`, the tool's files under `files/`, and for a Python tool every dependency as a wheel under `wheels/`, each recorded with its SHA-256 in `wheels.lock`. Most tool files are exactly as upstream published them. A Python dependency with no suitable wheel can be built from a pinned source archive, and Rubeus is built from its pinned official source. The format is the one pwn-broker reads. It is written down here, with [the manifest](#the-manifest) and [the index](#the-index) below and each rule in [CONTRIBUTING.md](../CONTRIBUTING.md#manifest-rules); pwn-broker's [guide](https://github.com/Real-Fruit-Snacks/pwn-broker/blob/main/docs/guide.md#what-a-package-is) says what it does with a package.

No tool binary is committed here. A recipe (`recipes/<tool>/recipe.toml`) names each upstream file by its address, size and SHA-256, and where it goes in the package; a source may be a bare file, one member of a `tar.gz` or `zip`, or a whole `zip` unpacked into a folder under `files/`. `build.py` downloads it, refuses it if the size or the SHA-256 differs, and writes the zip. The zip's entries are stored, not compressed, with a fixed date and no executable bits, so its bytes do not depend on the machine that built it. pwn-broker makes everything under `files/` executable when it installs.

Kind `kali` puts a command on the machine, `upload` puts files in a folder to carry to the target, and `both` does both. A `runtime` is never installed on its own: `tool install` and `tool remove` refuse its name, and it comes and goes with the tools that require it. A `notes` package puts nothing on PATH and nothing to upload: it holds files to read, such as the playbooks pwn-broker's note window reads with `:Pb`. Its manifest is schema 3, which pwn-broker 0.1.7 and later read. An index also containing schema-4 metadata is schema 4, so a current mixed catalog needs pwn-broker 0.1.14 or later; an older pwn-broker refuses an index whose schema it does not know and says to upgrade.

## The manifest

`manifest.toml` says what the package is and what pwn-broker is to put on the machine from it. This is `ligolo-ng` 0.9.2 build 1's, laid out as its recipe writes it, with the `name`, `version` and `build` that `build.py` takes from `recipe.toml`:

```toml
schema = 1
name = "ligolo-ng"
version = "0.9.2"
build = 1
kind = "both"
description = "Tunnelling and pivoting into internal networks"
tags = ["pivot", "tunnel", "linux", "windows"]
upstream = "https://github.com/nicocha30/ligolo-ng"
licence = "GPL-3.0"
arch = "x86_64"
needs_root = true
examples = [
  "ligolo-proxy -selfcert",
  "./agent -connect {lhost}:11601 -ignore-cert",
  "agent.exe -connect {lhost}:11601 -ignore-cert",
]

[[command]]
name = "ligolo-proxy"
type = "binary"
path = "files/proxy"

[[upload]]
path = "files/agent"
as = "agent"
note = "Linux x86_64"

[[upload]]
path = "files/agent.exe"
as = "agent.exe"
note = "Windows x86_64"

[data]
home = [".ligolo-proxy"]
```

| Key | Holds |
|---|---|
| `schema` | 1, 2, 3 or 4: the oldest format that can say what the package needs. See [the runtime](#the-runtime) for which is which |
| `name`, `version`, `build` | The tool, upstream's version and this repository's build of it. The zip is `<name>-<version>-<build>.zip`. A recipe's `manifest.toml` may leave all three out, with `schema`: `build.py` fills them from `recipe.toml` |
| `kind` | `kali`, `upload`, `both`, `runtime` or `notes` |
| `description`, `tags` | One line about the tool, and words for it. `tool search` looks in both and in the name |
| `upstream`, `licence` | The project's address and the name of its licence. The licence's text is the package's `LICENSE` |
| `arch` | `x86_64`, or `any` for a `notes` package, which runs nothing |
| `needs_root` | `true` when the command has to be run with `sudo`. Left out, it is `false` |
| `examples` | Command lines to show for the tool. `{ip}`, `{hostname}` and `{domain}` stand for the current target and `{lhost}` for the VPN address |
| `requires` | From schema 2: the one runtime a Python tool runs on, as `["python-3.13"]` |
| `depends`, `[[backend]]` | Schema 4: the packages a tool always needs, and its optional features. See [ordinary tool dependencies and optional backends](#ordinary-tool-dependencies-and-optional-backends) |
| `[[command]]` | A command for PATH, in kinds `kali` and `both`: its `name`, its `type`, and the file it runs. `binary` has a `path`, `python-script` a `script`, and `python-entry` an `entry`, the name of a console script. `args` are put before the user's own and `env` is a table of variables; in both, `{data}` stands for the box's folder for the tool's data |
| `[[upload]]` | A file to carry to the target, in kinds `upload` and `both`: its `path` in the package, the name it is given `as`, and a `note` about it, which may be left out |
| `[data]` | `home`: the paths under the home folder that the tool writes to, which pwn-broker moves into the box at `finish` |
| `[python]` | A Python tool only: `lock` and `wheels`, the package's `wheels.lock` and its folder of wheels. `build.py` fills both |
| `[runtime]` | A `runtime` only: `archive`, the Python archive in the package; `python`, the interpreter inside it once unpacked; and `series`, such as `3.13` |
| `[notes]` | A `notes` package only: `root`, the folder of `.md` files |

[CONTRIBUTING.md](../CONTRIBUTING.md#manifest-rules) has what each key may hold, and `build.py check` applies those rules to a recipe and to the manifest inside a zip. A key that is not in this table is refused. Inside the zip, `build.py` writes the same manifest with its `[[command]]` and `[[upload]]` tables in TOML's inline form. A package holds no install script.

pwn-broker refuses a manifest or an index whose `schema` it does not know and says to upgrade. So a change to either format is released in pwn-broker first and used in a package afterwards.

## The index

`index.toml` names every published build. pwn-broker and `fetch.sh` read it, and `build.py index` writes it from the cards of the releases. pwn-broker's [guide](https://github.com/Real-Fruit-Snacks/pwn-broker/blob/main/docs/guide.md#the-tools-index) says where it looks for the index and how long it keeps a copy. This is `kerbrute` in the published index:

```toml
schema = 4

[[tool]]
name = "kerbrute"
description = "Kerberos user enumeration and password spraying"
tags = [
    "ad",
    "kerberos",
    "enum",
    "bruteforce",
]
licence = "Apache-2.0"
upstream = "https://github.com/ropnop/kerbrute"
latest = "1.0.3-1"

[[tool.build]]
version = "1.0.3"
build = 1
kind = "kali"
needs_root = false
commands = [
    "kerbrute",
]
file = "kerbrute-1.0.3-1.zip"
url = "https://github.com/Real-Fruit-Snacks/pwn-shop/releases/download/kerbrute-v1.0.3-1/kerbrute-1.0.3-1.zip"
size = 8298909
sha256 = "fc399df537955ca34fe6191f04596af85baed797ec35dd94e2fcdd3bbf85b425"
```

- `schema` is the highest that any build in the index needs: 4 with a build that has `depends` or a backend, otherwise 3 with a `notes` build, otherwise 2 with a runtime or a build that requires one, otherwise 1.
- There is one `[[tool]]` for each package, in the order of their names, with one `[[tool.build]]` for each published build of it, oldest first, in the order [Versions and builds](#versions-and-builds) gives. `latest` is the last of them, written `<version>-<build>`, and `description`, `tags`, `licence` and `upstream` are that build's.
- A build has its `version`, `build`, `kind` and `needs_root`, and `commands`, the names it puts on PATH. No two tools' latest builds may have a command of one name.
- `requires`, `depends` and the build's `[[tool.build.backend]]` tables are as in the manifest, and each is left out when it is empty. A backend has a `name`, a `description`, its `commands`, and either a `package` or a list of `apt` names. Every package named has to be in the index: a runtime for `requires`, and an installable tool for `depends` and for a backend's `package`.
- `file` is the zip's name, and `size` and `sha256` are the zip's. pwn-broker checks a zip against both before it uses it.
- `url` is the zip's address on its own release. In the index that `build.py build` writes into a folder it is the file name alone, since a folder is read by file name.
- The file has only the three kinds of table shown and named here, `[[tool]]`, `[[tool.build]]` and `[[tool.build.backend]]`, and `fetch.sh` refuses any other.

A card is the small file published beside each zip, named as the zip with `.toml`. It holds the manifest's `name`, `version`, `build`, `kind`, `description`, `tags`, `upstream`, `licence`, `arch` and `needs_root`, the names of its `commands`, its `requires`, `depends` and `backend` when it has any, the pins of whatever it built from source as `sources`, the recipe's `source_code` when it has one, and the zip's `file`, `size` and `sha256`. `source_code` is on the card only: the index leaves it out. The index is written from the cards alone, so no zip is downloaded to write it, and CI compares a rebuilt package with its published card.

## The runtime

A Python tool built now does not run on Kali's own Python. It names the runtime it requires (`requires = ["python-3.13"]` in its manifest), a package of kind `runtime` that carries one Python build from [python-build-standalone](https://github.com/astral-sh/python-build-standalone) byte for byte, and its wheels are for that Python alone. pwn-broker installs the runtime with the first tool that requires it, makes the tool's virtual environment with the runtime's interpreter, and removes the runtime once no installed tool requires it. A runtime puts nothing in `/opt/pwn-broker/bin`, so Kali's `python3` is never shadowed. When Kali moves to a new Python, a tool on a runtime is not affected.

A runtime, or a tool that only requires one, is schema 2; a notes package is schema 3; ordinary dependencies or optional backends make a package schema 4; every other package is schema 1. An index takes the highest schema its builds need. Empty new fields are left out of cards and indexes, so packages published before schema 4 rebuild byte for byte.

The runtime's `LICENSE` holds these parts, each under a heading that names it and where it came from, each copied byte for byte:

- CPython's licence, from the archive.
- The licence files that the build's own `PYTHON.json` names for CPython itself and for the libraries it links (OpenSSL, SQLite, zlib, expat, libffi, Tcl and others), from upstream's full distribution of the same build.
- CPython's `Doc/license.rst` at the tag `v3.13.18`, its own statement of the software incorporated into the interpreter, mimalloc's notice among it.
- The HACL* notice, from the leading comment of `Modules/_hacl/Hacl_Hash_SHA2.c` at the same tag.
- pip's licences and those of the libraries pip vendors, from the archive.

`recipes/python-3.13/recipe.toml` names each source with its size and SHA-256, and `tools/runtime-licence.py` writes the file from them. Licence files in the full distribution that `PYTHON.json` names for nothing in the build (Tix and zstd) are left out, and the script says so each time it runs.

## Ordinary tool dependencies and optional backends

Schema 4 keeps ordinary tool dependencies separate from Python runtimes. `depends = ["netexec"]` names packages a tool always needs. Each must be an installable `kali`, `upload` or `both` package in the same catalog, not a runtime or notes package. Names may not repeat or name the tool itself, and the build refuses missing targets and hard-dependency cycles. pwn-broker resolves the graph dependencies first, reuses an installed build already represented and hash-pinned by the index, and removes only automatic dependencies that become orphaned.

An optional backend is a `[[backend]]` table with a display `name`, a `description` of the feature it enables, plain executable `commands` to detect, and exactly one provider: `package = "netexec"` for another pwn-shop package, or `apt = ["nmap"]` for system packages. Alternative executable names are allowed, such as `commands = ["xfreerdp3", "xfreerdp"]`. A package may not be both a hard dependency and an optional backend of the same tool, and backend providers may not repeat.

A minimal `tool install <name>` installs hard ordinary dependencies and runtimes but selects no optional backends. `tool install <name> --full` selects every declared optional backend: package-backed ones come from pwn-shop, while missing apt-backed ones are shown in a plan and installed only after confirmation. pwn-broker never runs a recipe-supplied installer, and apt packages remain system-owned during rollback and removal. `tool doctor <name>` reports backend commands, availability, providers and enabled features without installing or changing anything.

## Git source dependencies

A Python package that upstream does not publish to PyPI can use a `[[wheel_source]]` for the tool or one of its dependencies. Each entry pins the distribution name, the version its archive builds, and the archive's HTTPS URL, size and SHA-256. The name must also occur in `build_from_source`, because building it runs upstream's build code and fetches build requirements without hashes. Only pure-Python wheels are accepted. An optional `env` table supports a fixed build setting such as `POETRY_DYNAMIC_VERSIONING_BYPASS`.

When the tool's own metadata would otherwise resolve a Git dependency at a moving branch, `overrides.in` supplies the same pinned archive URLs to `uv`. The committed `requirements.lock` still contains only ordinary `name==version` lines with hashes. When a built wheel's `Requires-Dist` names one of those direct Git dependencies, the builder rewrites just that entry to the exact bundled version and updates `RECORD`; source code is not changed. NetExec uses this for its four custom Git dependencies.

## .NET source builds

A `[dotnet]` recipe builds one executable from a pinned source zip with a fixed SDK and pinned .NET Framework reference-assembly package. It names `sdk`, the source `url`, `size` and `sha256`, the project and output paths inside the archive, the final `to` path, and `framework_url`, `framework_size`, `framework_sha256` and `framework`. The builder checks `dotnet --version`, unpacks both archives with the usual path checks, and runs only `dotnet msbuild` with Release, `FrameworkPathOverride` and deterministic compilation. It does not restore packages. Only the output named by `to` enters the package. Rubeus uses SDK 9.0.320 and the .NET 4.0 reference assemblies; `global.json` stops a newer SDK already on the machine from being selected, and CI installs that SDK in the package jobs.

## Source code

The GPL, the AGPL and the LGPL have whoever passes a program on say where its source is, and the MPL asks the same for a file in executable form. Most packages here are upstream's own binaries, and pwn-shop hosts no source archives. So a package under one of those licences names the source of its build: a `[source_code]` table in `recipe.toml` with the full `commit` the pinned files were made from, the `tag` that names that commit when upstream has one, and the `url` of upstream's source archive for it.

```toml
[source_code]
tag = "v0.9.2"
commit = "e543732f4be6dc899096ae322ae620bf010bfbfc"
url = "https://github.com/nicocha30/ligolo-ng/archive/refs/tags/v0.9.2.tar.gz"
```

`build.py` refuses a recipe whose `licence` names one of those four families and has no such table, and any other recipe may have one. The commit is always given in full, because a tag can be moved and its name can mislead: PEASS-ng's tags end in eight characters that are not a commit of its repository. The tests hold each table to the recipe's own pins: the tag is the one whose release the files were downloaded from, or the commit is the one in their address.

The table goes into the build's card, and `build.py notes` prints it as a `Source code:` line under the licence, so the release that holds the zip says where its source is:

```text
Licence: GPL-3.0
Source code: tag v0.9.2, commit e543732f4be6dc899096ae322ae620bf010bfbfc, https://github.com/nicocha30/ligolo-ng/archive/refs/tags/v0.9.2.tar.gz
```

It is not in the zip and not in the index. pwn-broker refuses a package that holds a file outside the package layout, `LICENSE` is upstream's text unchanged, and the index holds only the fields pwn-broker reads. A recipe with no table gets a card with no such field, so a build published before its recipe named a source still rebuilds to its own card. Naming a source changes the card, and that takes a new build. [PACKAGES.md](PACKAGES.md#source-code) lists the source of each such package's current build.

## Versions and builds

`version` is upstream's. `build` is mine: it starts at 1 and goes up each time I repackage the same upstream version. A published build is never changed or replaced. A build is released under the tag `<tool>-v<version>-<build>` with the zip `<tool>-<version>-<build>.zip`, and its card is the zip's name with `.toml`.

The index calls the last of a tool's builds its `latest`, and that is the one `tool install` takes. Builds are ordered by the leading dotted numbers of the version, then by what follows them (`1.0-rc1` is older than `1.0`), then by build number. So a new build has to sort after every published one: a tool versioned by date, such as `2026.11.03`, cannot move to upstream's first numbered release, `2.1.0`, while the dated builds are published, because `latest` would stay on the date. `python build.py plan --index <index.toml> --releases <file of tags>` fails with that message, and CI runs it in the `packages` job with the published index and the tags that still have a release, so such a recipe stops before `publish`. Withdraw the builds that sort after the new one first, or give the recipe a version that sorts last.

Some versions are dates rather than upstream release numbers: `targetedkerberoast`, `powerview` and `rubeus` use the date of the pinned commit, `autorecon` does the same because it has no releases, and `coercedpotato` uses the publication date of its unnumbered release. The `peass-ng` version is the date of the pinned upstream tag `20261122-25957cb1`. Rubeus upstream deliberately publishes no binaries; its package is a deterministic build of the pinned source.

## build.py

`build.py` uses the standard library and `tomli-w`, and runs `pip` (and `uv`, for `lock` only):

| Command | Does |
|---|---|
| `python build.py build [tool ...]` | Download, check and package each recipe into `dist/`, with a card and a `SHA256SUMS.txt` for each, and write `dist/index.toml` |
| `python build.py check [tool ...]` | Check the recipes and every zip in `dist/` against the package rules |
| `python build.py lock [tool ...]` | Resolve a Python tool's `requirements.in` into `requirements.lock` for its runtime's Python (needs `uv`) |
| `python build.py index <cards> --base-url <pattern>` | Write an index from a folder of cards |
| `python build.py plan` | List each recipe's name, release tag and zip name |
| `python build.py compare <built card> <published card> <built zip> <published zip>` | Fail if a rebuilt package differs from the published one for the same build. The whole card is compared, so any changed byte shows; only if the recipe has `build_from_source` are the zips compared member by member instead. A wheel of one of those distributions is compared by its distribution, its version and every file in it except `WHEEL` and `RECORD`, which name the unpinned build tool, and its `wheels.lock` line by name and version without the hash |
| `python build.py notes <card>` | Print a release's notes |

`build`, `check` and `lock` take `--recipes`, `--out` and `--cache`; downloads are kept in `.cache/`, each under its SHA-256. `build` refuses to write an index whose runtime, ordinary dependency or package-backed backend target is not in the folder: build those packages into the same folder, or pass `--skip-index` to get the zip and its card with no index, as CI does when it rebuilds one published package to compare it. Apt-backed providers are system packages, not folder-index targets. A download that fails or does not match its pin exits with status 3, any other failure with 1.

## CI

CI runs on pushes to `main` and on pull requests. A push run is never cancelled, so a later push cannot stop an earlier one half way through publishing; a pull request run gives way to a newer one. The jobs:

- `lint`: `fetch.sh` and `.github/scripts/kali-packages.sh` under `sh -n`, `dash -n` and `shellcheck`, and the Python files compiled.
- `test`: the unit tests on Python 3.11, 3.12, 3.13 and 3.14, with warnings treated as errors. They need no network and no pwn-broker.
- `art` (README panel): `docs/art/make-panel.py` run again, a failure if the SVG files differ from the committed ones, and the panel's unit test, which skips in `test` because that job does not install `fonttools`.
- `packages`, on Python 3.12, 3.13 and 3.14: a build that already has a release is downloaded and tested from its published bytes, so CI and a user never see different files under one tag. A build with no release must build. The job installs .NET SDK 9.0.320 for Rubeus. After `build.py check`, the Python 3.13 leg records one SHA-256 of the packages and keeps them for `kali` and `publish`, and writes, and throws away, the index `publish` would write, from the card of every release and of each build about to be published: a history that the index refuses stops the run here, before anything is released. Only then is each published build built again, one tool at a time with `--skip-index`, and compared with the published one, so a recipe that changed without its `build` going up fails. If an upstream file can no longer be fetched or matched, that rebuild is a warning and the published zip is still tested. The rebuild of a recipe with `build_from_source` runs build tools that no hash pins, which is why it comes after the packages are kept, in a step with no token; `build.py` passes no variable whose name ends in `TOKEN` to pip or uv either. Then `.github/scripts/install-check.py` installs every package with pwn-broker's own code: a tool the way `tool install` does it, after its ordinary dependencies and runtime, each command started with `--help` or `-h`, and everything removed again. Minimal installs do not select optional backends, and the checks never run apt. A Python command that fails only because it reads terminal modes gets one retry with a pseudo-terminal; every other traceback still fails.
- `kali`: the official Kali image (not pinned, on purpose), with pwn-broker installed in it. The container's network is switched off, the check confirms that nothing resolves or answers, then each recipe's current build is installed from the built folder index with pwn-broker's own code and each command started with `--help` or `-h`. Older builds are not tested there. The evidence folder is uploaded whatever the result.
- `publish`, on `main` only, after every other job: so a build that fails on the Kali of the day is not published. It publishes one GitHub release per new build, tagged `<tool>-v<version>-<build>` and holding the zip, `SHA256SUMS.txt` and a card, with notes from `build.py notes`. Then it rebuilds the index from every release's card and replaces `index.toml` on the release tagged `index`, only when it changed. `gh release create` makes a draft, uploads the files and then publishes it, so a run that stops in between leaves a draft. A draft is not a release anywhere in CI: it is not counted as published and its card is not indexed, and `publish` deletes a draft of the tag it is about to make, so the next run publishes that build whole.

The releases are read from the API a page at a time, so their number has no limit. Every action is pinned to a full commit, with its version in a comment beside it. Nothing moves a pin forward on its own, so once a month Dependabot (`.github/dependabot.yml`) opens one pull request that raises every pin that has a newer release, the commit and its comment together. It watches the actions only: the Python pins and locks are raised by hand.

Do not turn on immutable releases for this repository: the index file could not be replaced.

CI checks out pwn-broker, which is public, with no key. The repository variable `PWN_BROKER_REF` names the pwn-broker branch CI tests against while a change needs both repositories; the default is `main`.

## Withdrawing a build

A build is withdrawn by deleting its release and its git tag. Then either raise `build` in the recipe, which publishes a replacement, or remove the recipe. If neither is done, the next push to `main` builds and publishes that same build again. The index is rebuilt from the releases that exist, so a deleted release leaves the index at the next publish.

If an upstream file is removed or replaced, CI warns and keeps testing the published package; the recipe can no longer be rebuilt until it is repinned with a raised `build`.
