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

The repository is private for now, so a signed-in `gh` is the only way to download a package. Nothing has been published yet either: CI has not run, so there is no index release, and `fetch.sh` will stop with a `fail` line until the first run on `main` has finished. To use the packages before that, build them on Linux and point `tools_index` at the folder:

```sh
python3 -m pip install tomli-w
python3 build.py build
```

That writes `dist/` with the zips and an `index.toml`.

## Context

[pwn-broker](https://github.com/Real-Fruit-Snacks/pwn-broker) keeps a Hack The Box machine in order on Kali Linux, and its `tool install` puts a tool on the machine for one box and takes it off again at `finish`. This repository is where those tools come from. I wanted my own copy of each one, pinned to a version, so that a box I come back to in six months installs the same files, and so that nothing is installed from a package name that can change under it.

A package is one zip for one build of one tool: a manifest, the upstream licence, the tool's files exactly as upstream published them, and for a Python tool every dependency as a wheel with its SHA-256 recorded. No tool binary is committed here. A recipe names the exact upstream file with its size and SHA-256, and `build.py` downloads it, refuses it if either differs, and writes the zip. The package format is described in pwn-broker's `docs/design.md`, section 8.

`version` is upstream's. `build` is mine: it starts at 1 and goes up each time I repackage the same upstream version. A published build is never changed or replaced.

## What's in it

| Package | Version | Kind | You get | Licence |
|---|---|---|---|---|
| `certipy` | 5.1.0 | kali | `certipy` | MIT |
| `kerbrute` | 1.0.3 | kali | `kerbrute` | Apache-2.0 |
| `ligolo-ng` | 0.9.2 | both | `ligolo-proxy` (needs root); to upload: `agent`, `agent.exe` | GPL-3.0 |
| `pspy` | 1.2.1 | upload | to upload: `pspy64`, `pspy32`, `pspy64s`, `pspy32s` | GPL-3.0 |
| `peass-ng` | 2026.11.14 | upload | to upload: `linpeas.sh`, `winPEASx64.exe`, `winPEASany.exe`, `winPEAS.bat` | GPL-2.0-or-later with additional terms |
| `targetedkerberoast` | 2024.12.16 | kali | `targetedKerberoast` | GPL-3.0 |

Kind `kali` puts a command on the machine, `upload` puts files in a folder to carry to the target, and `both` does both. The `targetedkerberoast` version is the date of the pinned commit, because upstream has no releases. The `peass-ng` version is the date of the pinned upstream tag `20261114-5ac9329e`.

`build.py` uses the standard library and `tomli-w`, and runs `pip` (and `uv`, for `lock` only):

| Command | Does |
|---|---|
| `python build.py build [tool ...]` | Download, check and package each recipe into `dist/`, with a card and a `SHA256SUMS.txt` for each, and write `dist/index.toml` |
| `python build.py check [tool ...]` | Check the recipes and every zip in `dist/` against the package rules |
| `python build.py lock [tool ...]` | Resolve a Python tool's `requirements.in` into `requirements.lock` (needs `uv`) |
| `python build.py index <cards> --base-url <pattern>` | Write an index from a folder of cards |
| `python build.py plan` | List each recipe's name, release tag and zip name |
| `python build.py compare <built> <published>` | Fail if a card differs from the published one for the same build |
| `python build.py notes <card>` | Print a release's notes |

`build`, `check` and `lock` take `--recipes`, `--out` and `--cache`. See [CONTRIBUTING.md](CONTRIBUTING.md) to add a tool.

## Tested on

This section is updated by hand after each CI run. As of 2026-11-15:

- The unit tests, on Windows 11 with Python 3.13: 321 passed, 2 skipped, with warnings treated as errors. The two skips need a POSIX file system. They run no recipe and install nothing.
- `certipy` and `targetedkerberoast`: built on a Windows 11 development machine with Python 3.13. The zips pass `build.py check` and pwn-broker's package checks (the size and hash check, the unpack and the manifest reader). They have not been installed.
- `fetch.sh`: its index parsing and the whole script, with a stand-in `gh`, under `sh` on that machine. `dash -n` and `shellcheck` are clean.
- *Not* tested: `kerbrute`, `pspy`, `ligolo-ng` and `peass-ng` have recipes with pinned sizes and hashes, but have not been built anywhere. Antivirus on the development machine deletes their upstream files, so CI is the first place they are built and installed.
- *Not* tested: any package installed on Kali. CI installs them on Ubuntu 24.04 with pwn-broker's own code, and starts each command once, but CI has not run yet.
- *Not* tested: the workflow. It has been read and parsed, and nothing in it has run on GitHub.
- *Not* tested: `fetch.sh` against GitHub. Nothing is published to fetch.

## Known issues

- The repository is private. The index is not public either, so `fetch.sh` and a signed-in `gh` are the way to get packages, and pwn-broker's default `tools_index` (the published index URL) cannot be fetched without signing in. Set `tools_index` to the folder `fetch.sh` fills.
- The index and the packages are not signed. The SHA-256 values in the index are the only check, and they catch a corrupted or swapped zip. They do not prove who published the index.
- `kerbrute` publishes no checksums upstream. Its pin is the SHA-256 of the file as first downloaded.
- PEASS-ng's licence has terms beyond the GPL. Read `recipes/peass-ng/LICENSE` before redistributing the package or making this repository public.
- Antivirus on a Windows build machine removes several upstream files during a build (`kerbrute`, `pspy`, `ligolo-ng`, `peass-ng` among them). Build on Linux, or let CI do it.
- Packages are x86_64 only. Python tools carry wheels for Python 3.12 and 3.13 only, and are refused on any other Python.
- The workflow checks out pwn-broker with a deploy key, so it cannot run on a pull request from a fork.

## Configuration

| Setting | Where | Meaning |
|---|---|---|
| `tools_index` | pwn-broker's `/opt/pwn-broker/config.toml` | A URL, or a folder that holds `index.toml` and the zips it names. A folder is read by file name; the `url` in the index is not used |
| `PWN_SHOP_REPO` | environment, for `fetch.sh` | The repository to download from. Default `Real-Fruit-Snacks/pwn-shop` |
| `PWN_BROKER_DEPLOY_KEY` | repository secret | A read-only deploy key for pwn-broker. Only the `packages` job's checkout step uses it |
| `PWN_BROKER_REF` | repository variable | The pwn-broker branch CI tests against. Default `main` |

CI runs on pushes to `main` and on pull requests. On `main` it publishes one GitHub release per new build, tagged `<tool>-v<version>-<build>` and holding the zip, `SHA256SUMS.txt` and a card, then rebuilds the index from every release's card and replaces `index.toml` on the release tagged `index`. Do not turn on immutable releases for this repository: the index file could not be replaced.

Withdrawing a build is deleting its release. The next index leaves it out.

## License

MIT. Each package carries the licence of the tool in it.
