# pwn-shop

Tool packages for pwn-broker, built from upstream releases

```sh
gh repo clone Real-Fruit-Snacks/pwn-shop
sh pwn-shop/fetch.sh ~/pwn-packages
```

`fetch.sh` downloads the published index and every package it names into the folder, checks each zip's SHA-256 against the index, and prints the line to put in `/opt/pwn-broker/config.toml`:

```
tools_index = "/home/kali/pwn-packages"
```

Then, inside a box, `tool install kerbrute`. `fetch.sh` is POSIX `sh`. It needs `gh` signed in with access to this repository, and `sha256sum`. It changes nothing on your machine except that folder, and it takes the repository from `PWN_SHOP_REPO` if you set it.

The repository is private, so a signed-in `gh` is the only way to download a package. The first CI run published all six packages and the index on 2026-11-15. To build the packages yourself instead, on Linux:

```sh
python3 -m venv .venv
.venv/bin/pip install tomli-w
.venv/bin/python build.py build
```

That writes `dist/` with the zips and an `index.toml`.

## Context

[pwn-broker](https://github.com/Real-Fruit-Snacks/pwn-broker) keeps a Hack The Box machine in order on Kali Linux, and its `tool install` puts a tool on the machine for one box and takes it off again at `finish`. This repository is where those tools come from. I wanted my own copy of each one, pinned to a version, so that a box I come back to in six months installs the same files, and so that nothing is installed from a package name that can change under it.

A package is one zip for one build of one tool: a manifest, the upstream licence, the tool's files exactly as upstream published them, and for a Python tool every dependency as a wheel with its SHA-256 recorded. No tool binary is committed here. A recipe names the exact upstream file with its size and SHA-256, and `build.py` downloads it, refuses it if either differs, and writes the zip. The package format is described in pwn-broker's `docs/design.md`, section 8.

A Python tool built now does not run on Kali's own Python. It names the runtime it requires, a package of kind `runtime` that carries one Python build from [python-build-standalone](https://github.com/astral-sh/python-build-standalone) byte for byte, and its wheels are for that Python alone. pwn-broker installs the runtime with the first tool that requires it, makes the tool's virtual environment with the runtime's interpreter, and removes the runtime once no installed tool requires it. A runtime puts nothing in `/opt/pwn-broker/bin`, so Kali's `python3` is never shadowed. When Kali moves to a new Python, a tool on a runtime is not affected.

`version` is upstream's. `build` is mine: it starts at 1 and goes up each time I repackage the same upstream version. A published build is never changed or replaced.

## What's in it

| Package | Version | Kind | You get | Licence |
|---|---|---|---|---|
| `certipy` | 5.1.0 | kali | `certipy`, on `python-3.13` | MIT |
| `kerbrute` | 1.0.3 | kali | `kerbrute` | Apache-2.0 |
| `ligolo-ng` | 0.9.2 | both | `ligolo-proxy` (needs root); to upload: `agent`, `agent.exe` | GPL-3.0 |
| `pspy` | 1.2.1 | upload | to upload: `pspy64`, `pspy32`, `pspy64s`, `pspy32s` | GPL-3.0 |
| `peass-ng` | 2026.11.14 | upload | to upload: `linpeas.sh`, `winPEASx64.exe`, `winPEASany.exe`, `winPEAS.bat` | GPL-2.0-or-later with additional terms |
| `python-3.13` | 3.13.18 | runtime | nothing on PATH; the Python that `certipy` and `targetedkerberoast` run on | PSF-2.0 and others (see its `LICENSE`) |
| `targetedkerberoast` | 2024.12.16 | kali | `targetedKerberoast`, on `python-3.13` | GPL-3.0 |

Kind `kali` puts a command on the machine, `upload` puts files in a folder to carry to the target, and `both` does both. A `runtime` is never installed on its own: `tool install` and `tool remove` refuse its name, and it comes and goes with the tools that require it.

Build 3 of `certipy` and `targetedkerberoast` requires `python-3.13` (`requires = ["python-3.13"]` in its manifest). Builds 1 and 2 stay published as they were: they carry no runtime and run on Kali's own Python, and only when it is one they list (build 1: 3.12 and 3.13; build 2: 3.12, 3.13 and 3.14). A box that recorded one of those builds is still offered it.

The runtime's `LICENSE` carries CPython's licence and then, each under a heading naming it, the licence of every library the build bundles (OpenSSL, SQLite, zlib, expat, libffi, Tcl/Tk and the others) and those of pip and the libraries pip vendors. They are copied byte for byte from upstream's full distribution of the same build and from the archive itself; `recipes/python-3.13/recipe.toml` names the full distribution and its SHA-256, and `tools/runtime-licence.py` writes the file from the two archives.

The `targetedkerberoast` version is the date of the pinned commit, because upstream has no releases. The `peass-ng` version is the date of the pinned upstream tag `20261114-5ac9329e`.

`build.py` uses the standard library and `tomli-w`, and runs `pip` (and `uv`, for `lock` only):

| Command | Does |
|---|---|
| `python build.py build [tool ...]` | Download, check and package each recipe into `dist/`, with a card and a `SHA256SUMS.txt` for each, and write `dist/index.toml` |
| `python build.py check [tool ...]` | Check the recipes and every zip in `dist/` against the package rules |
| `python build.py lock [tool ...]` | Resolve a Python tool's `requirements.in` into `requirements.lock` for its runtime's Python (needs `uv`) |
| `python build.py index <cards> --base-url <pattern>` | Write an index from a folder of cards |
| `python build.py plan` | List each recipe's name, release tag and zip name |
| `python build.py compare <built card> <published card> <built zip> <published zip>` | Fail if a rebuilt package differs from the published one for the same build. The whole card is compared, so any changed byte shows; only if the recipe has `build_from_source` are the zips compared member by member instead, leaving out those distributions' wheels and `wheels.lock` lines |
| `python build.py notes <card>` | Print a release's notes |

`build`, `check` and `lock` take `--recipes`, `--out` and `--cache`. See [CONTRIBUTING.md](CONTRIBUTING.md) to add a tool.

## Tested on

This section is updated by hand after each CI run. As of 2026-11-15:

- The unit tests: passed in CI on Ubuntu 24.04 under Python 3.11, 3.12 and 3.13, and on Windows 11 with Python 3.13 (516 passed and 3 skipped; the skips need a POSIX file system), warnings treated as errors. They also passed in CI under Python 3.14.
- All six packages, build 1: built by CI on Ubuntu 24.04, passed `build.py check`, and were installed with pwn-broker's own code under Python 3.12 and 3.13, then removed with nothing left behind. `certipy`, `kerbrute`, `ligolo-proxy` and `targetedKerberoast` each answered `--help`. The upload files of `ligolo-ng`, `pspy` and `peass-ng` were present at the right sizes and were not run.
- Build 1 of `certipy` and `targetedkerberoast` built on Windows 11 with Python 3.13 is byte-identical to the CI builds.
- Python 3.14 has been added. Build 2 of `certipy` and `targetedkerberoast` built on Windows 11 with Python 3.13 and passed `build.py check`; each lists Python 3.12, 3.13 and 3.14 and carries a wheel that fits each of them for every dependency. CI built build 2 of each on Ubuntu 24.04, ran `build.py check`, and installed it with pwn-broker's own code under Python 3.12, 3.13 and 3.14; `certipy` and `targetedKerberoast` each answered `--help`. The other four packages were installed under 3.14 in the same run.
- Published: build 1 of each package from the first CI run, build 2 of `certipy` and `targetedkerberoast` from the run that added Python 3.14, and the `index` release, which lists both builds of those two.
- `fetch.sh`: `sh -n` and `dash -n` are clean and its tests run with a stand-in `gh`. `shellcheck` was clean in CI before the change that lets a package name hold a dot, and has not been run on it since. It has not been run against the real releases.
- Runtimes, not yet run in CI: on Windows 11 with Python 3.13 the unit tests pass (695 passed, 3 skipped, warnings treated as errors), and `python-3.13` build 1 and build 3 of `certipy` and `targetedkerberoast` are built and pass `build.py check`; `kerbrute` and `pspy` rebuild to their published hashes. None of the three new builds has been installed or run. CI will install each tool after its runtime with pwn-broker's own code on Ubuntu 24.04, with pwn-broker itself under Python 3.12, 3.13 and 3.14, and the `kali` job will install every current build inside the Kali image with the network off. Neither has run yet.
- Not tested: any package installed or run on Kali.

## Known issues

- The repository is private. The index is not public either, so `fetch.sh` and a signed-in `gh` are the way to get packages, and pwn-broker's default `tools_index` (the published index URL) cannot be fetched without signing in. Set `tools_index` to the folder `fetch.sh` fills.
- The index and the packages are not signed. The SHA-256 values in the index are the only check, and they catch a corrupted or swapped zip. They do not prove who published the index.
- `kerbrute` and `pspy` publish no checksums upstream. Their pins are the SHA-256 of each file as first downloaded.
- PEASS-ng's licence has terms beyond the GPL. Read `recipes/peass-ng/LICENSE` before redistributing the package or making this repository public.
- Kali's own `ligolo-ng` package also installs a command named `ligolo-proxy`. While this package is installed, a bare `ligolo-proxy` runs this one, because pwn-broker's folder comes first on PATH. `sudo` does not use PATH, so a bare `sudo ligolo-proxy` runs Kali's copy. Use `sudo /opt/pwn-broker/bin/ligolo-proxy`, the path `tool install` prints.
- A folder filled by `fetch.sh` does not update itself. Run `fetch.sh` again after a new build is published; `tool refresh` does not reach a folder.
- Nothing has been installed on Kali yet. The `kali` job is the first thing that will.
- Antivirus on a Windows build machine removes several upstream files during a build. Build on Linux, or let CI do it.
- If an upstream file is removed or replaced, CI warns and keeps testing the published package; the recipe can no longer be rebuilt until it is repinned with a raised `build`.
- Packages are x86_64 with glibc only. Build 3 of `certipy` and `targetedkerberoast` carries wheels for its runtime's Python 3.13 only. Builds 1 and 2 run on Kali's own Python and are refused on one they do not list.
- The first Python tool on a machine costs the runtime: a 36 MB download and about 100 MB unpacked.
- The runtime carries its own OpenSSL and other libraries. They are fixed when I publish a new build of the runtime, not when Kali updates.
- The workflow checks out pwn-broker with a deploy key, so it cannot run on a pull request from a fork.
- When the index changes, `index.toml` is briefly missing from the `index` release while the new one is uploaded. CI skips the upload when nothing changed. `fetch.sh` fails cleanly if it runs in that gap; run it again.

## Configuration

| Setting | Where | Meaning |
|---|---|---|
| `tools_index` | pwn-broker's `/opt/pwn-broker/config.toml` | A URL, or a folder that holds `index.toml` and the zips it names. A folder is read by file name; the `url` in the index is not used |
| `PWN_SHOP_REPO` | environment, for `fetch.sh` | The repository to download from. Default `Real-Fruit-Snacks/pwn-shop` |
| `PWN_BROKER_DEPLOY_KEY` | repository secret | A read-only deploy key for pwn-broker. Only the checkout steps of the `packages` and `kali` jobs use it |
| `PWN_BROKER_REF` | repository variable | The pwn-broker branch CI tests against. Default `main` |

CI runs on pushes to `main` and on pull requests. Before anything is published, the `kali` job starts the official Kali image, installs pwn-broker in it, switches the container's network off, checks that nothing resolves or answers, then installs each recipe's current build from the built folder index with pwn-broker's own code and starts each command with `--help`. Publishing waits for it, so a build that fails on the Kali of the day is not published. Older builds are not tested there. On `main` it publishes one GitHub release per new build, tagged `<tool>-v<version>-<build>` and holding the zip, `SHA256SUMS.txt` and a card, then rebuilds the index from every release's card and replaces `index.toml` on the release tagged `index`. Do not turn on immutable releases for this repository: the index file could not be replaced.

A build is withdrawn by deleting its release and its git tag. Then either raise `build` in the recipe, which publishes a replacement, or remove the recipe. If neither is done, the next push to `main` builds and publishes that same build again. The index is rebuilt from the releases that exist, so a deleted release leaves the index at the next publish.

## License

MIT. Each package carries the licence of the tool in it; the runtime carries CPython's and those of the libraries it bundles.
