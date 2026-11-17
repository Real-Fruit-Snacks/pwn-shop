# What has been tested

Everything the packages, `build.py` and `fetch.sh` have been run on, with the counts, runs, commits and dates, and what has not been run. The [README](../README.md#tested-on) has the short list. This record is updated by hand after each CI run; it is as of 2026-11-16.

## Unit tests

- Passed in CI on Ubuntu 24.04 under Python 3.11, 3.12 and 3.13, and on Windows 11 with Python 3.13 (516 passed and 3 skipped; the skips need a POSIX file system), warnings treated as errors. They also passed in CI under Python 3.14.
- With runtimes, on Windows 11 with Python 3.13: 719 passed, 4 skipped, warnings treated as errors.
- With `fetch.sh` on `curl`, on Windows 11 with Python 3.13 on 2026-11-16: 866 passed, 23 skipped, warnings treated as errors. Nineteen of the skips are `fetch.sh` tests that need links, signals or file names Windows does not allow.

## Packages in CI

- All six packages, build 1: built by CI on Ubuntu 24.04, passed `build.py check`, and were installed with pwn-broker's own code under Python 3.12 and 3.13, then removed with nothing left behind. `certipy`, `kerbrute`, `ligolo-proxy` and `targetedKerberoast` each answered `--help`. The upload files of `ligolo-ng`, `pspy` and `peass-ng` were present at the right sizes and were not run.
- Python 3.14 added: CI built build 2 of `certipy` and `targetedkerberoast` on Ubuntu 24.04, ran `build.py check`, and installed it with pwn-broker's own code under Python 3.12, 3.13 and 3.14; `certipy` and `targetedKerberoast` each answered `--help`. The other four packages were installed under 3.14 in the same run.
- Runtimes, in GitHub Actions run 43768259174 at commit a33db87, with pwn-broker at its `runtimes` branch: every job passed (`lint`, `test` on Python 3.11, 3.12, 3.13 and 3.14, `packages` on 3.12, 3.13 and 3.14, and `kali`); `publish` was skipped because the run was not on `main`. `shellcheck` passed on `fetch.sh` in the `lint` job of that run, after the change that lets a package name hold a dot. In the `packages` job, `certipy` 5.1.0 build 3, `python-3.13` 3.13.18 build 1 and `targetedkerberoast` 2024.12.16 build 3 were built, and `kerbrute`, `ligolo-ng`, `peass-ng` and `pspy` were downloaded from their published zips and tested; each was installed with pwn-broker's own code. The `python-3.13` zip built on Ubuntu has the same SHA-256 as the one built on Windows (`ef33f7a02247e396ef8cc72e53a7f222d5637ce375b10eb598c00e5d234667b7`).
- The same jobs passed on `main` at commit 988be1c (run 43772170454): `packages` built the runtime and build 3 of the two tools, and rebuilt the four published packages to the same bytes; `kali` installed all seven with the network off; `publish` then published the three new builds and replaced the index.
- The next run (43773278524, a pull request) failed in `packages`: with build 3 published, CI rebuilt `certipy` alone and `build` refused the folder's index for want of the runtime. That is fixed with `build --skip-index`; see the [changelog](../CHANGELOG.md).

## In Kali

- The `kali` job of run 43768259174 ran inside `kalilinux/kali-rolling` (Kali 2026.3, Python 3.14.8) with Docker reporting the container on no network, and it confirmed that resolving `github.com` and connecting to `1.1.1.1:443` both failed. There, `python-3.13` 3.13.18 build 1 was installed and its interpreter reported `Python 3.13.18`; `certipy` 5.1.0 build 3 and `targetedkerberoast` 2024.12.16 build 3 were each installed on that runtime and answered `--help`; `kerbrute` and the proxy of `ligolo-ng` answered `--help`; `peass-ng` and `pspy` installed their upload files.
- In run 43772170454 on `main`, the `kali` job installed all seven with the network off, before `publish`.

- By hand on 2026-11-16, in Kali 2026.2 under WSL 2 on Windows 11, as root, with pwn-broker 0.1.0 installed from its release: `tool install` ran for `kerbrute`, `pspy`, `certipy`, `targetedkerberoast`, `ligolo-ng` and `peass-ng`, each from the published index, and `certipy` brought `python-3.13` 3.13.18 with it. `kerbrute version`, `certipy -h`, `targetedKerberoast -h` and `ligolo-proxy -h` each started and printed their own first lines; the ten upload files of `ligolo-ng`, `peass-ng` and `pspy` were in `/opt/pwn-broker/upload` and were not run. Kali's `python3` was still `/usr/bin/python3`, 3.14.7. `finish` then removed all six tools and the runtime, and left only pwn-broker's own two commands in `/opt/pwn-broker/bin`.
- By hand the same day and in the same Kali, as a normal user in the `sudo` group whose `sudo` asks for a password, with pwn-broker 0.1.0 installed by that user: `tool install` ran for `kerbrute`, `ligolo-ng` and `certipy` from the published index, and `certipy` brought the runtime. `kerbrute version`, `certipy -h` and `ligolo-proxy -h` each started, and so did `sudo /opt/pwn-broker/bin/ligolo-proxy -h`, the line `tool install` prints. Nothing under `/opt/pwn-broker` or the user's home belonged to root afterwards. `finish` removed the three tools and the runtime, with a `warn` line that root's home could not be checked for `ligolo-ng`'s data.

## Builds on Windows

- Build 1 of `certipy` and `targetedkerberoast` built on Windows 11 with Python 3.13 is byte-identical to the CI builds.
- Build 2 of `certipy` and `targetedkerberoast` built on Windows 11 with Python 3.13 and passed `build.py check`; each listed Python 3.12, 3.13 and 3.14 and carried a wheel that fits each of them for every dependency.
- With runtimes: `python-3.13` build 1 and build 3 of `certipy` and `targetedkerberoast` are built and pass `build.py check`; `kerbrute` and `pspy` rebuild to their published hashes.

## Published and withdrawn

- The first CI run published build 1 of each of the six tools and the index on 2026-11-15.
- Build 2 of `certipy` and `targetedkerberoast` was published from the run that added Python 3.14.
- `python-3.13` 3.13.18 build 1, with build 3 of `certipy` and `targetedkerberoast`, was published from run 43772170454 on 2026-11-15.
- Builds 1 and 2 of `certipy` and `targetedkerberoast` were withdrawn the same day, by deleting their releases and tags, so each package now has one published build. They had carried no runtime and ran on Kali's own Python, and only when it was one they listed (build 1: 3.12 and 3.13; build 2: 3.12, 3.13 and 3.14). No box had used them.
- The `index` release is schema 2 and lists the runtime, build 3 of those two tools and build 1 of the other four.

## fetch.sh

- `sh -n` and `dash -n` are clean, and its tests run with a stand-in `curl` that refuses a call without the options the script relies on. On 2026-11-16 in Kali 2026.2 under WSL 2, where `sh` is dash and `awk` is mawk 1.3.4, all 90 of them passed, the 19 that Windows skips included, and `shellcheck` 0.11.0 was clean.
- The first version downloaded with the GitHub CLI. Run against the real releases on 2026-11-15, from Git Bash on Windows 11, it downloaded the index and all 11 zips then published, and each matched the size and SHA-256 the index gives. On Kali it could not start: Kali packages no `gh`, in 2026.2 or in rolling, so `apt install gh`, which its own message gave, fails. With `gh` added from GitHub's package source it downloaded all seven zips there.
- It now downloads with `curl` and needs no account. On 2026-11-16, in Kali 2026.2 under WSL 2 on Windows 11, as a normal user, with curl 8.21.0, `dash` as `sh`, no `gh` installed and no GitHub sign-in on the machine: it downloaded the index and all seven zips, and each matched the size and SHA-256 in the index, checked a second time with Python. A second run found all seven in place and downloaded only the index. A zip overwritten with other bytes was downloaded again. A repository that does not exist failed with one `fail` line and left no folder.
- From that folder, with `tools_index` set to it and the shell in a network namespace whose only interface was `lo`, down, pwn-broker 0.1.0 installed `certipy` with the `python-3.13` runtime, `ligolo-ng` and `pspy`, and `finish` removed them. Resolving `github.com` failed in that shell.

## The published index from pwn-broker

- From the published address, pwn-broker's own download code fetched the index and two packages (`certipy` build 3 and the `python-3.13` runtime) and checked each against the index, on Windows on 2026-11-15.

## Not tested

- Any package run by hand on a Kali desktop or against a real target.
- Packages have been installed on Kali by the `kali` job, inside the Kali image with the network off, and by hand under WSL as root and as a normal user. Nothing has been installed on a full Kali desktop.
- `fetch.sh` from Git Bash on Windows since it changed to `curl`.

## Known limits

- The index and the packages are not signed. The SHA-256 values in the index are the only check, and they catch a corrupted or swapped zip. They do not prove who published the index.
- `kerbrute` and `pspy` publish no checksums upstream. Their pins are the SHA-256 of each file as first downloaded.
- PEASS-ng's licence has terms beyond the GPL. Read `recipes/peass-ng/LICENSE` before redistributing the package.
- Kali's own `ligolo-ng` package also installs a command named `ligolo-proxy`. While this package is installed, a bare `ligolo-proxy` runs this one, because pwn-broker's folder comes first on PATH. `sudo` does not use PATH, so a bare `sudo ligolo-proxy` runs Kali's copy. Use `sudo /opt/pwn-broker/bin/ligolo-proxy`, the path `tool install` prints.
- A folder filled by `fetch.sh` does not update itself. Run `fetch.sh` again after a new build is published; `tool refresh` does not reach a folder.
- Antivirus on a Windows build machine removes several upstream files during a build. Build on Linux, or let CI do it.
- If an upstream file is removed or replaced, CI warns and keeps testing the published package; the recipe can no longer be rebuilt until it is repinned with a raised `build`.
- Packages are x86_64 with glibc only. Build 3 of `certipy` and `targetedkerberoast` carries wheels for its runtime's Python 3.13 only.
- The first Python tool on a machine costs the runtime: a 34 MB download (mebibytes, the unit pwn-broker prints) and about 100 MB unpacked.
- The runtime carries its own OpenSSL and other libraries. They are fixed when I publish a new build of the runtime, not when Kali updates.
- When the index changes, `index.toml` is briefly missing from the `index` release while the new one is uploaded. CI skips the upload when nothing changed. `fetch.sh` fails cleanly if it runs in that gap; run it again.
