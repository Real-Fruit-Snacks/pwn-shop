# Changelog

## Unreleased

- `build.py`: builds a package from a recipe, with a card and a `SHA256SUMS.txt`, and writes the index. Every upstream file is refused unless its size and SHA-256 match the recipe. `check` applies pwn-broker's package rules to the recipes and the zips. `lock` resolves a Python tool's `requirements.in` into a hash-pinned `requirements.lock`. `plan`, `compare` and `notes` are for CI.
- A Python tool's dependencies are bundled as wheels for Python 3.12 and 3.13 on x86_64 Linux. A dependency that publishes no wheel is built from its hash-checked source archive only if the recipe lists it in `build_from_source`; none of the first six does.
- Recipes for `certipy` 5.1.0, `kerbrute` 1.0.3, `ligolo-ng` 0.9.2, `pspy` 1.2.1, `peass-ng` 2026.11.14 and `targetedkerberoast` 2024.12.16.
- `fetch.sh`: downloads the published index and zips into a folder, checks each zip against the index, and prints the `tools_index` line for `config.toml`.
- CI: unit tests, then each package is built or downloaded and installed with pwn-broker's own code under Python 3.12 and 3.13, and every command is started once. On `main` it publishes a release for each new build and replaces the index. A recipe that changes without its `build` going up fails, unless an upstream file can no longer be fetched or matched, which is a warning.
- `record`, `asciinema`, `openvpn`, `ping` and `ip` are now reserved command names, because pwn-broker runs them itself.
- Published: build 1 of each of the six and the index, from the first CI run on 2026-11-15. The repository is private, and the packages and the index are not signed.
- Python 3.14 added: a Python tool's wheels now cover Python 3.12, 3.13 and 3.14, because Kali now ships Python 3.14 and pwn-broker refuses a Python tool on a Python its package does not list. `lock` resolves for every listed Python and, when they differ, names each dependency and what each Python resolved it to.
- Build 2 of `certipy` 5.1.0 and `targetedkerberoast` 2024.12.16, with wheels for Python 3.12, 3.13 and 3.14. `targetedkerberoast` now pins `pycryptodome` 4.5.0 (build 1: 4.4.0); the other dependencies of both are unchanged. Build 1 of each stays published as it is.
- CI: the unit tests also run under Python 3.14, and every package is installed under 3.14 as well as 3.12 and 3.13.
