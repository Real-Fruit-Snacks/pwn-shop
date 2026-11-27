# pwn-shop Packages, Offline Mirror, Configuration & Known Issues

Complete list of all pinned tool packages, offline etch.sh instructions, build integrity guarantees, tested summary, known issues, and configuration for pwn-shop. See [guide.md](guide.md) for package internals and [tested.md](tested.md) for full test logs.

## Getting the Packages

If you use [pwn-broker](https://github.com/Real-Fruit-Snacks/pwn-broker), there is nothing to set up. Inside a box, `tool install` reads this repository's published index, downloads the zip from its release, checks its size and SHA-256 against the index, and installs it for the box:

```text
forest/dc01 > tool install certipy

  ok    python-3.13 3.13.18 installed (runtime, 34 MB)
  ok    certipy 5.1.0 installed
  run   certipy   from any terminal
```

The first Python tool brings the runtime it runs on, as here. `finish` takes the tools off again.

No network on the Kali machine when you want a tool, or you want your own copy: fill a folder while you have one.

```sh
git clone https://github.com/Real-Fruit-Snacks/pwn-shop
sh pwn-shop/fetch.sh ~/pwn-packages
```

It downloads the published index and every zip it names, checks each zip's SHA-256 against the index, and ends with the line to put in `/opt/pwn-broker/config.toml`:

```text
put this line in /opt/pwn-broker/config.toml:
tools_index = "/home/kali/pwn-packages"
```

`fetch.sh` is POSIX `sh`. It needs `curl` and `sha256sum` and no GitHub account, and it changes nothing on the machine except that folder. The folder does not update itself: run `fetch.sh` again after a new build is published.

The [guide](guide.md#getting-the-packages) has the rest, and how to [build the packages yourself](guide.md#building-the-packages-yourself).

pwn-broker keeps a Hack The Box machine in order on Kali Linux, and its `tool install` puts a tool on the machine for one box and takes it off again at `finish`. This repository is where those tools come from. I wanted my own copy of each one, pinned to a version, so that a box I come back to in six months installs the same files, and so that nothing is installed from a package name that can change under it.

## What's in it

| Package | Version | Kind | You get | Licence |
|---|---|---|---|---|
| `ad-sweep` | 0.1.0 | kali | `ad-sweep`; build 2 installs NetExec and its runtime automatically | MIT |
| `autorecon` | 2026.11.03 | kali | `autorecon`, on `python-3.13` | GPL-3.0 |
| `bloodhound-ce` | 1.9.1 | kali | `bloodhound-ce-python`, on `python-3.13` | MIT |
| `bloodyad` | 2.5.7 | kali | `bloodyAD`, `bloodyad`, on `python-3.13` | MIT |
| `cdk` | 1.5.6 | upload | to upload: `cdk`, `cdk_thin` | Apache-2.0 |
| `certipy` | 5.1.0 | kali | `certipy`, on `python-3.13` | MIT |
| `chisel` | 1.12.0 | both | `chisel`; to upload: `chisel`, `chisel.exe` | MIT |
| `coercedpotato` | 2026.07.30 | upload | to upload: `CoercedPotato_x64.exe`, `CoercedPotato_x86.exe` | MIT |
| `cred-spray` | 1.0.0 | kali | `cred-spray`, on `python-3.13`; minimal by default, `--full` for declared backends | MIT |
| `evil-winrm-py` | 1.7.0 | kali | `evil-winrm-py`, `ewp`, on `python-3.13` | MIT |
| `git-dumper` | 1.0.9 | kali | `git-dumper`, on `python-3.13` | MIT |
| `godpotato` | 1.20 | upload | to upload: `GodPotato-NET4.exe`, `GodPotato-NET35.exe` | Apache-2.0 |
| `hash-bait` | 1.0.1 | kali | `hash-bait` | MIT |
| `impacket` | 0.13.1 | kali | the `impacket-*` commands and `ldapdomaindump`, on `python-3.13` | Apache-2.0 with additional notices |
| `juicypotato` | 0.1 | upload | to upload: `JuicyPotato.exe` | GPL-3.0 |
| `jwt-tool` | 2.3.0 | kali | `jwt_tool`, on `python-3.13` | GPL-3.0 |
| `kerbrute` | 1.0.3 | kali | `kerbrute` | Apache-2.0 |
| `lazagne` | 2.4.7 | upload | to upload: `LaZagne.exe` | LGPL-3.0 |
| `ligolo-ng` | 0.9.2 | both | `ligolo-proxy` (needs root); to upload: `agent`, `agent.exe` | GPL-3.0 |
| `localpotato` | 1.1 | upload | to upload: `LocalPotato.exe` | MIT |
| `mimikatz` | 2.2.0-20220919 | upload | to upload: `mimikatz.exe`, `mimikatz32.exe` | CC-BY-4.0 |
| `netexec` | 1.5.1 | kali | `nxc`, `netexec`, `nxcdb`, on `python-3.13` | BSD-2-Clause |
| `peass-ng` | 2026.11.22 | upload | to upload: `linpeas.sh`, `winPEASx64.exe`, `winPEASany.exe`, `winPEAS.bat` | GPL-2.0-or-later with additional terms |
| `penelope` | 0.21.0 | kali | `penelope`, on `python-3.13` | GPL-3.0-or-later |
| `playbooks` | 2026.11.26 | notes | playbooks to read into the note window with `:Pb <name>` | CC-BY-4.0 |
| `plink` | 0.85 | upload | to upload: `plink.exe`, `plink32.exe` | MIT |
| `powerview` | 2020.08.17 | upload | to upload: `PowerView.ps1` | BSD-3-Clause |
| `privesccheck` | 2026.10.28-1 | upload | to upload: `PrivescCheck.ps1` | BSD-3-Clause |
| `pspy` | 1.2.1 | upload | to upload: `pspy64`, `pspy32`, `pspy64s`, `pspy32s` | GPL-3.0 |
| `pypykatz` | 0.6.13 | kali | `pypykatz`, on `python-3.13` | MIT |
| `python-3.13` | 3.13.18 | runtime | nothing on PATH; the Python that the Python tools run on | PSF-2.0 and others (see its `LICENSE`) |
| `report` | 0.1.0 | kali | `pwn-report`, on `python-3.13` | MIT |
| `roguepotato` | 1.0 | upload | to upload: `RoguePotato.exe`, `RogueOxidResolver.exe` | GPL-3.0 |
| `rubeus` | 2025.11.14 | upload | to upload: `Rubeus.exe` | BSD-3-Clause |
| `runascs` | 1.5 | upload | to upload: `RunasCs.exe`, `RunasCs_net2.exe`, `Invoke-RunasCs.ps1` | GPL-3.0 |
| `rusthound-ce` | 2.5.52 | kali | `rusthound-ce` | MIT |
| `sharphound` | 2.18.0 | upload | to upload: `SharpHound.exe` | GPL-3.0 |
| `snaffler` | 1.0.244 | upload | to upload: `Snaffler.exe` | GPL-3.0 |
| `sshuttle` | 2.0.0 | kali | `sshuttle`, on `python-3.13` | LGPL-2.1-or-later |
| `targetedkerberoast` | 2024.12.16 | kali | `targetedKerberoast`, on `python-3.13` | GPL-3.0 |
| `shell-tunnel` | 1.4.2 | both | `shell-tunnel`; to upload: `shell-tunnel`, `shell-tunnel.exe` | GPL-3.0 |

Kind `kali` puts a command on the machine, `upload` puts files in a folder to carry to the target, and `both` does both. A `runtime` is never installed on its own: it comes and goes with the tools that require it. A `notes` package puts nothing on PATH and nothing to upload: it is files to read in pwn-broker's note window, and needs pwn-broker 0.1.7 or later. `playbooks` packages each release of [pwn-playbooks](https://github.com/Real-Fruit-Snacks/pwn-playbooks), and `report` each release of [pwn-report](https://github.com/Real-Fruit-Snacks/pwn-report), which turns a box's finished or exported archive into a private report folder.

`version` is upstream's; `build` is mine, starts at 1, and goes up each time I repackage the same upstream version. A package is named for both: `kerbrute-1.0.3-1.zip`, released under the tag `kerbrute-v1.0.3-1`.

The [guide](guide.md) has the package format, the runtime, `build.py` and CI. [CONTRIBUTING.md](../CONTRIBUTING.md) has how to add a tool.

## How a package is made

- A recipe pins each upstream file by its size and SHA-256. No tool binary is committed here.
- The build downloads the file and refuses it if either differs. Most files are upstream's own; exceptions are source-built Python wheels and Rubeus, which is compiled deterministically from its pinned official source with a fixed .NET SDK and reference assemblies.
- A Python tool's dependencies are bundled as wheels pinned by hash, and it runs on the `python-3.13` runtime package, not on Kali's own Python, which it never shadows. Git dependencies are pinned to full commits and their archives' hashes; direct references in a built wheel's metadata are rewritten to the exact bundled versions.
- Before anything is published, CI installs every package with pwn-broker's own code, then again inside the Kali image with the network off.
- A published build is never changed. A fix is a new build.

## Tested on

- The unit tests on Windows 11 with Python 3.13, and on Ubuntu 24.04 in CI under Python 3.11, 3.12, 3.13 and 3.14, with warnings treated as errors
- Every package built or downloaded by CI on Ubuntu 24.04, passed `build.py check`, and installed with pwn-broker's own code running on Python 3.12, 3.13 and 3.14, each command started once
- Every package installed inside the official Kali image (Kali 2026.3) with the container's network off, before it was published
- `kerbrute`, `ligolo-ng`, `peass-ng` and `pspy` rebuilt by CI to their published bytes, and `python-3.13` built on Windows and on Ubuntu to the same SHA-256
- `fetch.sh` against the real releases in Kali 2026.2, as a normal user with no GitHub account: every zip matched the index, and pwn-broker then installed three tools from the folder with the network off
- All six tools installed by `tool install` from the published index, by hand in Kali 2026.2 under WSL 2 as root, each command started once, and removed again by `finish`
- Three of them again there as a normal user whose `sudo` asks for a password, `sudo /opt/pwn-broker/bin/ligolo-proxy` included
- `chisel`, `godpotato` and `runascs` built in Kali 2026.2 and installed there from the built folder by a normal user: `chisel` started, the six upload files were in place, and `finish` removed all three
- `cdk`, `git-dumper`, `lazagne`, `rusthound-ce` and `snaffler` built in Kali 2026.2 and installed there from the built folder by a normal user: `rusthound-ce` and `git-dumper` started, the four upload files matched their pinned hashes, and `finish` removed all five
- `playbooks` installed from the published index in Kali 2026.2 by a normal user with pwn-broker 0.1.7: `:Pb` read a playbook into the note, and `finish` removed it
- `report` installed from a folder index of its build in Kali under WSL 2 by a normal user with pwn-broker 0.1.11: `pwn-report` built a report from the box's exported archive, and `finish` removed it with its runtime; then from the published index with pwn-broker 0.1.12, whose `finish` built the report beside the archive itself
- `ad-sweep` 0.1.0 build 1 built and checked on Windows 11, then installed and help-tested through pwn-broker 0.1.13 in Kali under WSL 2; the release asset's version and four-module listing were checked, and no domain was contacted
- `cred-spray` 1.0.0 build 1 built and checked on Windows 11, then installed and help-tested through pwn-broker on Python 3.12–3.14 and in offline Kali in run 45909175019 before publication; no credential spray or target service was tested
- The 33 recipes of 2026-11-22 built and checked in Kali under WSL 2, then installed and help-tested through pwn-broker, including a complete run in a network namespace with no network; the unit suite also passed there and on Windows 11

[docs/tested.md](tested.md) is the full record: counts, runs, commits and dates, and what has not been run.

## Known issues

- Packages are x86_64 with glibc only. Build 3 of `certipy` and `targetedkerberoast` carries wheels for its runtime's Python 3.13 only.
- The index and the packages are not signed. The SHA-256 values in the index are the only check: they catch a corrupted or swapped zip, not who published the index.
- `kerbrute` and `pspy` publish no checksums upstream. Their pins are the SHA-256 of each file as first downloaded.
- Kali's own `ligolo-ng` package also installs a command named `ligolo-proxy`. While this package is installed a bare `ligolo-proxy` runs this one, but `sudo` does not use PATH, so `sudo ligolo-proxy` runs Kali's. Use `sudo /opt/pwn-broker/bin/ligolo-proxy`, the path `tool install` prints.
- `mimikatz` 2.2.0-20220919, from 2022, is upstream's latest release, and every antivirus product detects it. Upstream has no LICENSE file: its README gives CC BY 4.0, so `recipes/mimikatz/LICENSE` quotes that line before the licence's legal code.
- PEASS-ng's licence has terms beyond the GPL. Read `recipes/peass-ng/LICENSE` before redistributing the package.
- The first Python tool on a machine costs the runtime: a 34 MB download (mebibytes, the unit pwn-broker prints) and about 100 MB unpacked. Its OpenSSL and other libraries are as new as its build: they are fixed when I publish a new build of the runtime, not when Kali updates.
- `ad-sweep` 0.1.0 build 2 declares NetExec as an ordinary dependency: `tool install ad-sweep` installs NetExec and its runtime automatically with pwn-broker 0.1.14 or later. Its package check starts `ad-sweep --help`, which proves installation and startup, not NetExec command-line compatibility or a domain scan.
- `cred-spray` 1.0.0 build 2 bundles its Python code and dependencies and declares Nmap, NetExec, FreeRDP, ipmitool and Ruby evil-winrm as optional backends. A minimal install selects none; `tool install cred-spray --full` selects all five. Missing apt-backed providers are shown in a plan and installed only after confirmation; NetExec comes from pwn-shop. NetExec enables `--nxc`, WMI and automatic domain discovery. The package check only starts `cred-spray --help`; it does not install apt backends, scan or authenticate.
- `autorecon` bundles its Python code and dependencies, but its scans call nmap and other Kali system tools. Install those base tools separately; the package check starts the CLI but does not run a scan.
- `evil-winrm-py` is not a drop-in replacement for Ruby Evil-WinRM: it uses `--ssl` rather than `-S` and has no `-s` or `-e` directory options. Its Kerberos extra is not bundled because `gssapi` has no compatible Python 3.13 Linux wheel and needs a native build. Kali's Ruby `evil-winrm` remains separate.
- `penelope` 0.21.0 fixes the session-archive path traversal, and `--oscp-safe` disables Meterpreter, Traitor and ngrok, but this release still starts its local MCP socket unconditionally. It is therefore a general lab/CTF package, not an OSCP-approved profile; use Kali's nc, ncat or socat for the basic OSCP shell workflow.
- PrintSpoofer, SharpEfsPotato, SweetPotato, Hot Potato, SharpGPOAbuse, SeRestoreAbuse, SeManageVolumeExploit and calebstewart's CVE-2021-1675 script have no upstream redistribution licence, and Microsoft's Sysinternals terms prohibit publishing AccessChk for others to copy. They are not redistributed here. `godpotato`, `juicypotato`, `roguepotato`, `localpotato` and `coercedpotato` are packaged; obtain AccessChk directly from Microsoft.

The rest of the list is in [docs/tested.md](tested.md#known-limits).

## Configuration

| Setting | Where | Meaning |
|---|---|---|
| `tools_index` | pwn-broker's `/opt/pwn-broker/config.toml` | Not set: this repository's published index. Or a URL, or a folder that holds `index.toml` and the zips it names |
| `PWN_SHOP_REPO` | environment, for `fetch.sh` | The repository to download from. Default `Real-Fruit-Snacks/pwn-shop` |
