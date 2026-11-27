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

A package is one zip for one build of one tool: `manifest.toml`, the upstream licence as `LICENSE`, the tool's files under `files/`, and for a Python tool every dependency as a wheel under `wheels/`, each recorded with its SHA-256 in `wheels.lock`. Most tool files are exactly as upstream published them. A Python dependency with no suitable wheel can be built from a pinned source archive, and Rubeus is built from its pinned official source. The format is the one pwn-broker reads, described in its `docs/design.md`, section 8.

No tool binary is committed here. A recipe (`recipes/<tool>/recipe.toml`) names each upstream file by its address, size and SHA-256, and where it goes in the package; a source may be a bare file, one member of a `tar.gz` or `zip`, or a whole `zip` unpacked into a folder under `files/`. `build.py` downloads it, refuses it if the size or the SHA-256 differs, and writes the zip. The zip's entries are stored, not compressed, with a fixed date and no executable bits, so its bytes do not depend on the machine that built it. pwn-broker makes everything under `files/` executable when it installs.

Kind `kali` puts a command on the machine, `upload` puts files in a folder to carry to the target, and `both` does both. A `runtime` is never installed on its own: `tool install` and `tool remove` refuse its name, and it comes and goes with the tools that require it. A `notes` package puts nothing on PATH and nothing to upload: it holds files to read, such as the playbooks pwn-broker's note window reads with `:Pb`. Its manifest is schema 3, which pwn-broker 0.1.7 and later read. An index also containing schema-4 metadata is schema 4, so a current mixed catalog needs pwn-broker 0.1.14 or later; an older pwn-broker refuses an index whose schema it does not know and says to upgrade.

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

The releases are read from the API a page at a time, so their number has no limit. Every action is pinned to a full commit, with its version in a comment beside it.

Do not turn on immutable releases for this repository: the index file could not be replaced.

CI checks out pwn-broker, which is public, with no key. The repository variable `PWN_BROKER_REF` names the pwn-broker branch CI tests against while a change needs both repositories; the default is `main`.

## Withdrawing a build

A build is withdrawn by deleting its release and its git tag. Then either raise `build` in the recipe, which publishes a replacement, or remove the recipe. If neither is done, the next push to `main` builds and publishes that same build again. The index is rebuilt from the releases that exist, so a deleted release leaves the index at the next publish.

If an upstream file is removed or replaced, CI warns and keeps testing the published package; the recipe can no longer be rebuilt until it is repinned with a raised `build`.
