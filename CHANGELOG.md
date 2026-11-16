# Changelog

## Unreleased

This repository has no versions of its own: each package is its own release. What it holds today:

**Packages.** Recipes for `certipy` 5.1.0 (build 3), `kerbrute` 1.0.3, `ligolo-ng` 0.9.2, `pspy` 1.2.1, `peass-ng` 2026.11.14 and `targetedkerberoast` 2024.12.16 (build 3), each at build 1 unless named, and for the `python-3.13` runtime, 3.13.18 build 1. A package name may hold a dot. `record`, `asciinema`, `openvpn`, `ping` and `ip` are reserved command names, with the shells and system commands, because pwn-broker runs them itself.

**The build.** `build.py` builds a package from a recipe, with a card and a `SHA256SUMS.txt`, and writes the index. Every upstream file is refused unless its size and SHA-256 match the recipe. `check` applies pwn-broker's package rules to the recipes and the zips. `lock` resolves a Python tool's `requirements.in` into a hash-pinned `requirements.lock` for its runtime's Python series. `plan`, `compare` and `notes` are for CI. `build` refuses to write an index in which a tool requires a runtime that is not in the folder; `--skip-index` writes no index, for a tool built alone. A dependency that publishes no wheel is built from its hash-checked source archive only if the recipe lists it in `build_from_source`; no recipe does.

**Runtimes.** A package of kind `runtime` carries one Python build from python-build-standalone, byte for byte, and a Python tool names the one it requires with `requires`; its wheels fit that Python alone, and the build refuses one that does not. A runtime or a tool that requires one is schema 2; every other package is schema 1, so the published builds of the other tools rebuild byte for byte. `python-3.13` comes from python-build-standalone release 20261110. Its `LICENSE`, written by `tools/runtime-licence.py`, holds CPython's and pip's licences, the licence files `PYTHON.json` names for CPython and the libraries it links, CPython's `Doc/license.rst` and the HACL* notice, each copied byte for byte. Two tools that require each other are refused with a message.

**The index and fetching.** One `index.toml` on the release tagged `index` names every published build. `fetch.sh` downloads it and every zip it names into a folder, checks each zip against it, and prints the `tools_index` line for `config.toml`.

**CI.** Lint, the unit tests on Python 3.11 to 3.14, and the README panel. Each package is built, or downloaded from its release and rebuilt to compare, then installed with pwn-broker's own code running on Python 3.12, 3.13 and 3.14, a tool after its runtime, and every command started once. The `kali` job installs each recipe's current build in the Kali image with the network off. On `main`, after both, it publishes a release for each new build and replaces the index. A recipe that changes without its `build` going up fails, unless an upstream file can no longer be fetched or matched, which is a warning. pwn-broker is checked out without a key; the repository holds no secrets.

Published and withdrawn, all on 2026-11-15:

- Build 1 of the six tools and the index, from the first CI run, while the repository was private.
- Build 2 of `certipy` and `targetedkerberoast`, with wheels for Python 3.12, 3.13 and 3.14. `targetedkerberoast` pinned `pycryptodome` 4.5.0 (build 1: 4.4.0).
- `python-3.13` 3.13.18 build 1, and build 3 of `certipy` and `targetedkerberoast` on it, with the dependencies of build 2. The index became schema 2.
- Fixed: CI's rebuild of a published tool failed for want of its runtime in the folder, until `build --skip-index`.
- The repository was made public, and so was pwn-broker. The deploy key and its secret were removed.
- Withdrawn: builds 1 and 2 of `certipy` and `targetedkerberoast`. No box had used them.
