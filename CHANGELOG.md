# Changelog

## Unreleased

This repository has no versions of its own: each package is its own release. What it holds today:

**Packages.** The current tools and versions are in the README table, with the `python-3.13` runtime and notes packages. A package name may hold a dot. `record`, `asciinema`, `openvpn`, `ping` and `ip` are reserved command names, with the shells and system commands, because pwn-broker runs them itself.

**The build.** `build.py` builds a package from a recipe, with a card and a `SHA256SUMS.txt`, and writes the index. Every upstream file is refused unless its size and SHA-256 match the recipe. `check` applies pwn-broker's package rules to the recipes and the zips. `lock` resolves a Python tool's `requirements.in` into a hash-pinned `requirements.lock` for its runtime's Python series. `plan`, `compare` and `notes` are for CI. `build` refuses to write an index in which a tool requires a runtime that is not in the folder; `--skip-index` writes no index, for a tool built alone. A dependency that publishes no wheel is built from its hash-checked source archive only if the recipe lists it in `build_from_source`. A `[[wheel_source]]` also pins a Git archive's URL and hash, and `[dotnet]` builds Rubeus from official source with a fixed SDK and reference assemblies.

**Runtimes.** A package of kind `runtime` carries one Python build from python-build-standalone, byte for byte, and a Python tool names the one it requires with `requires`; its wheels fit that Python alone, and the build refuses one that does not. A runtime or a tool that requires one is schema 2; every other package is schema 1, so the published builds of the other tools rebuild byte for byte. `python-3.13` comes from python-build-standalone release 20261110. Its `LICENSE`, written by `tools/runtime-licence.py`, holds CPython's and pip's licences, the licence files `PYTHON.json` names for CPython and the libraries it links, CPython's `Doc/license.rst` and the HACL* notice, each copied byte for byte. Two tools that require each other are refused with a message.

**The index and fetching.** One `index.toml` on the release tagged `index` names every published build. `fetch.sh` downloads it and every zip it names into a folder, checks each zip against it, and prints the `tools_index` line for `config.toml`. It needs `curl` and `sha256sum`, and no account.

**CI.** Lint, the unit tests on Python 3.11 to 3.14, and the README panel. Each package is built, or downloaded from its release and rebuilt to compare, then installed with pwn-broker's own code running on Python 3.12, 3.13 and 3.14, a tool after its runtime, and every command started once. The `kali` job installs each recipe's current build in the Kali image with the network off. On `main`, after both, it publishes a release for each new build and replaces the index. A recipe that changes without its `build` going up fails, unless an upstream file can no longer be fetched or matched, which is a warning. pwn-broker is checked out without a key; the repository holds no secrets.

Published and withdrawn, all on 2026-11-15:

- Build 1 of the six tools and the index, from the first CI run, while the repository was private.
- Build 2 of `certipy` and `targetedkerberoast`, with wheels for Python 3.12, 3.13 and 3.14. `targetedkerberoast` pinned `pycryptodome` 4.5.0 (build 1: 4.4.0).
- `python-3.13` 3.13.18 build 1, and build 3 of `certipy` and `targetedkerberoast` on it, with the dependencies of build 2. The index became schema 2.
- Fixed: CI's rebuild of a published tool failed for want of its runtime in the folder, until `build --skip-index`.
- The repository was made public, and so was pwn-broker. The deploy key and its secret were removed.
- Withdrawn: builds 1 and 2 of `certipy` and `targetedkerberoast`. No box had used them.

Changed on 2026-11-16:

- `fetch.sh` downloads with `curl`, over HTTPS only, and no longer needs the GitHub CLI or a sign-in. Kali packages no `gh`, so the script could not run there until a package source was added by hand. `PWN_SHOP_REPO` now has to name a public repository.
- `fetch.sh` refuses a link or a folder that sits where a zip or the index would go. `mv` would have put the file inside it, which for a link is outside the folder the script was given.
- It refuses an index with a table header `build.py` does not write, such as one with a comment after it, and one that gives a tool's builds as a value in place of `[[tool.build]]` tables. The build under such a header was left out without a word while the index still named its zip.
- A folder path with a control character is refused with the quote and the backslash, and before anything is asked for or made. A folder the script made is removed again when the run fails before anything is in it. `CDPATH` and a folder named `-` no longer send it elsewhere.
- A download gives up when no connection is made in 30 seconds or nothing arrives for a minute. `PWN_SHOP_REPO` has to be exactly `owner/name`.
- `build.py` follows pwn-broker's stricter package rules: the longer list of command names a package may not take, with Python and pip under any versioned name; a version of letters, digits and `. _ + ~ -`; and the longer list of places in the home folder a tool may not claim. No recipe is affected.
- A recipe's `[data] home` may list only a folder named after the tool or one of its commands (`.<name>`, or `<name>` under `.config`, `.local/share`, `.local/state` or `.cache`), as pwn-broker now requires. `ligolo-ng`, the one recipe that lists a folder, lists `.ligolo-proxy`.
- Three more packages, each at build 1: `chisel` 1.12.0 (a command on Kali, and its Linux and Windows binaries to upload), `godpotato` 1.20 and `runascs` 1.5 (Windows files to upload). `PrintSpoofer` was looked at and left out: its repository carries no licence, so it may not be redistributed.
- A recipe's source may be the one file in a plain `.gz`, which is how `chisel` publishes its Linux binary. `member` names the file as the URL does, less the `.gz`.
- Five more packages, each at build 1: `rusthound-ce` 2.5.51 and `git-dumper` 1.0.9 (commands on Kali, `git-dumper` on the `python-3.13` runtime), and `snaffler` 1.0.244, `lazagne` 2.4.7 and `cdk` 1.5.6 (files to upload).

Changed on 2026-11-20:

- A recipe's source may be a whole `zip`, unpacked with `unpack = true` into `files/` or a folder in it, with every entry checked: no links, special files or encrypted entries, nothing outside the folder, and limits on entries and on the bytes written.
- A new kind, `notes`: files to read in pwn-broker's note window, with nothing on PATH and nothing to upload. It is manifest and index schema 3, which pwn-broker 0.1.7 and later read, and `arch = "any"`. CI installs it and checks every playbook's first two lines in place of starting commands.
- A new package, `playbooks` 2026.11.20 build 1: the first release of pwn-playbooks, eight playbooks. The index is schema 3 from its first publish.
- `playbooks` 2026.11.20.1 build 1: pwn-playbooks' second release, with 79 playbooks in place of 8.

Changed on 2026-11-21:

- `playbooks` 2026.11.21 build 1: pwn-playbooks' third release, with 83 playbooks in place of 79. The four new ones are for Active Directory: certificate services, delegation, GPO abuse and lateral movement.
- `playbooks` 2026.11.21.1 build 1: pwn-playbooks' fourth release, with 84 playbooks in place of 83. The new one is for Active Directory: golden and silver Kerberos tickets.
- `playbooks` 2026.11.21.2 build 1: pwn-playbooks' fifth release, still 84 playbooks; the Kerberos ticket playbook now covers diamond and sapphire tickets as well.

Changed on 2026-11-22:

- Thirteen new tool packages: `autorecon` 2026.11.03, `bloodhound-ce` 1.9.1, `bloodyad` 2.5.7, `evil-winrm-py` 1.7.0, `impacket` 0.13.1, `jwt-tool` 2.3.0, `netexec` 1.5.1, `penelope` 0.21.0, `powerview` 2020.08.17, `pypykatz` 0.6.13, `rubeus` 2025.11.14, `sharphound` 2.18.0 and `sshuttle` 2.0.0.
- Four more Windows potato packages: `juicypotato` 0.1, `roguepotato` 1.0 (including `RogueOxidResolver.exe`), `localpotato` 1.1 and `coercedpotato` 2026.07.30 (x64 and x86). SharpEfsPotato, SweetPotato and Hot Potato remain unredistributed because their upstream repositories have no licence.
- `rusthound-ce` 2.5.52 build 1 fixes cleartext-password logging and adds collection timeout, concurrency and pacing controls. `peass-ng` 2026.11.22 build 1 updates the four Linux and Windows enumeration files.
- NetExec and its four custom Git dependencies are pinned to full commits and archive hashes. `wheel_source` builds those archives into pure-Python wheels, `overrides.in` stops transitive resolution at moving branches, and direct requirements in wheel metadata are pinned to the bundled versions. `jwt-tool` builds its two source-only dependencies under the existing opt-in rule.
- Rubeus is compiled deterministically from the official source with .NET SDK 9.0.320 and pinned .NET 4.0 reference assemblies; no unofficial binary mirror is used. The package jobs install the same SDK, and `global.json` prevents a newer installed SDK from being selected.
- The install checker retries a Python help command with a pseudo-terminal only after the specific termios/no-terminal traceback. Penelope uses that path; other tracebacks still fail.
- Documented the limits of `autorecon`'s system-tool dependencies, `evil-winrm-py` compatibility and Kerberos support, and Penelope's always-on MCP socket. PrintSpoofer, SharpGPOAbuse and AccessChk remain unredistributed for licensing reasons.
