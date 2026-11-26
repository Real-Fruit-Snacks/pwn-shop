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
- `playbooks` 2026.11.22 build 1: pwn-playbooks' sixth release, still 84 playbooks; the potatoes playbook now says that JuicyPotato is in pwn-shop.
- `playbooks` 2026.11.22.1 build 1: pwn-playbooks' seventh release, still 84 playbooks; the potatoes playbook adds RoguePotato, LocalPotato and CoercedPotato, which are in pwn-shop.
- `playbooks` 2026.11.22.2 build 1: pwn-playbooks' eighth release, still 84 playbooks; every tool in pwn-shop now has a `tool install` line in the playbooks that use it.
- Two new upload packages: `privesccheck` 2026.10.28-1, itm4n's PrivescCheck.ps1, and `plink` 0.85, PuTTY's plink.exe for 64-bit and 32-bit Windows, pinned to the SHA-256 values in PuTTY's own sha256sums.
- `runascs` 1.5 build 2 adds `Invoke-RunasCs.ps1`, the PowerShell version, from the commit the v1.5 tag names.
- `playbooks` 2026.11.22.3 build 1: pwn-playbooks' ninth release, still 84 playbooks; it says what Kali needs, uses the `plink`, `privesccheck` and `runascs` packages, and names a source for each tool pwn-shop may not carry.
- A new upload package, `mimikatz` 2.2.0-20220919: `mimikatz.exe` for 64-bit Windows and `mimikatz32.exe` for 32-bit, from upstream's release zip, under CC BY 4.0.
- `playbooks` 2026.11.22.4 build 1: pwn-playbooks' tenth release, still 84 playbooks; the golden and silver ticket playbook now gets mimikatz from pwn-shop.
- `playbooks` 2026.11.22.5 build 1: pwn-playbooks' eleventh release, 88 playbooks; it adds PostgreSQL, mail, message queue and IKE playbooks, and labels the steps OffSec bars on the OSCP exam.
- `playbooks` 2026.11.23 build 1: pwn-playbooks' twelfth release, still 88 playbooks; it corrects the mail playbook's IMAP listing and hMailServer path, and the PostgreSQL playbook's file-read prerequisites.
- Documented the limits of `autorecon`'s system-tool dependencies, `evil-winrm-py` compatibility and Kerberos support, and Penelope's always-on MCP socket. PrintSpoofer, SharpGPOAbuse and AccessChk remain unredistributed for licensing reasons.

Changed on 2026-11-23:

- The card of a build that builds a dependency from source names what it was built from: each such distribution's `requirements.lock` line with its hashes, and each `[[wheel_source]]` archive's SHA-256 with the build environment it sets, each named once. `compare` checks them, so a recipe cannot move to another commit or archive at the same version without a new build; a card published before this has none, and is compared as before.
- CI keeps the packages for `publish`, and records one SHA-256 of them, right after they are built and checked, before pwn-broker's install takes its dependencies from PyPI and the install check starts every packaged tool. `publish`, and the `kali` job before it installs them, refuse packages that do not give that SHA-256. Before, a step that ran later could have changed a zip and its card together, and `publish`, which checks each zip against its card, would have published them.
- `nmap` is a reserved command name, as pwn-broker's `scan` runs it by name, and `start` a reserved name in a command's `env`, as pwn-broker sets it itself: `check` refuses both, as pwn-broker already did at install.
- `compare` now notices a new version of a distribution a recipe builds from source, such as pwn-report in `report`: it still leaves out such a wheel's bytes and the hash on its `wheels.lock` line, which differ between builds by nature, but no longer its distribution and version, read from the wheel's name, or the line's version; the name's spelling and tags, which the unpinned build tools decide, do not count. Before, the recipe could move to a new version without a new build, and the published package would quietly stay on the old one.
- `check` and every build now refuse a package entry the way pwn-broker's own unpacking does, rule for rule: a name longer than 4,096 bytes, with a part longer than 255 bytes or more than 64 parts, or holding a colon or a control character; a stored entry whose compressed size differs from its size; and more than 10,000 files and folders once the folders its names imply are counted. Every package CI installs already met them, as pwn-broker refuses such an entry at install.
- A new Python package, `report` 0.1.0: the `pwn-report` command, which turns a pwn-broker finished or exported archive into a private, evidence-linked report folder, built from pwn-report v0.1.0's sdist, pinned to the SHA-256 in that release's `SHA256SUMS.txt`, on `python-3.13`. It has no dependencies.

Changed on 2026-11-25:

- A new package, `hash-bait` 1.0.0: the `hash-bait` command, which drops poisoned shortcuts on SMB shares to coerce NTLM hashes, from hash-bait v1.0.0's Linux x86-64 musl archive, pinned to the SHA-256 in that release's `SHA256SUMS.txt`.
- `playbooks` moves to 2026.11.25: forty-four new playbooks across web, foothold, privesc and AD, and the responder playbook's share baiting now uses `hash-bait`.
- `hash-bait` moves to 1.0.1: colour output also honours `TERM=dumb`, terminals without truecolour get the brand's 16-colour row, and `--version` prints the `v` prefix.

Changed on 2026-11-26:

- A new package, `undertow` 1.3.0 (kind `both`): the `undertow` static SSH server for bind and reverse shells with PTY, SFTP and port forwarding, plus its Linux and Windows binaries to upload, from Undertow v1.3.0's release archives, pinned to the SHA-256 values in that release's `SHA256SUMS.txt`.
- `undertow` becomes `shell-tunnel` 1.4.0 as the tool is renamed to the RFS naming rule upstream; the recipe, commands and uploads take the new name from shell-tunnel v1.4.0's release archives, and the `undertow` package is withdrawn.
- `playbooks` moves to 2026.11.26: the shells playbook gains the shell-tunnel section (handler, dial-home, bind mode and the ssh/SFTP/forwarding workflow).
- `shell-tunnel` moves to 1.4.2: the connect commands print without a `$` prefix, with numbered titles and a blank line between the two steps for clean copy-paste.
- A new package, `ad-sweep` 0.1.0: four isolated Active Directory enumeration modules with NetExec, from ad-sweep v0.1.0's Linux x86-64 glibc executable, pinned to the SHA-256 in that release's `SHA256SUMS.txt`. NetExec remains a separate prerequisite: `tool install ad-sweep` does not install it, so run `tool install netexec` separately.
- A new package, `cred-spray` 1.0.0 build 1: bounded, protocol-aware credential spraying for authorized testing, built from cred-spray v1.0.0's SHA-256-pinned sdist on the `python-3.13` runtime, with 51 locked distributions. External service backends remain separate prerequisites.
