# Security

## Supported versions

The latest published build of each tool. A published build is never changed or replaced; a fix ships as a new build, and a build that should not be used is withdrawn by deleting its release and its git tag, then raising `build` in the recipe or removing the recipe; otherwise the next push to `main` publishes the same build again.

## Reporting

Use GitHub's private vulnerability reporting on this repository (Security → Report a vulnerability). Don't open a public issue for anything that could get a package past pwn-broker's checks or put a different file under a published tag.

Include the tool and build (`kerbrute 1.0.3-1`), what you ran, and what you saw. Use placeholder credentials and addresses.

This is a one-person project. I read reports within a week. If it's real, the fix and the changelog entry name the problem plainly.

## What a package is checked for

- Before a package is built, each upstream file is downloaded and refused unless its size and SHA-256 equal the recipe's. A recipe does not follow a "latest" link; it names one file.
- A source unpacked whole (`unpack = true`, a zip) has every entry checked before it is written: nothing absolute or outside its folder, no link, special file or encrypted entry, no backslash, colon or control character, pwn-broker's limits on a name's length, no two names that one system would make one, and limits on the entries and on the bytes actually written.
- The finished zip is checked with the same rules pwn-broker applies when it unpacks one: the manifest's schema and fields, every file the manifest names present, nothing compressed, no link or special file, no entry that would land outside the tool's folder, and the zip's name matching the manifest.
- A Python tool's `requirements.lock` must pin every dependency to one version with the SHA-256 of each upstream file, with no markers, ranges or URLs. A downloaded wheel must match the lock's hash and its name, version and tags; a wheel built from source must also be pure Python by its contents. Every wheel, one built from source included, must fit the Python of the runtime the tool requires (3.13 for `python-3.13`) on x86_64 Linux. The package records each bundled wheel in `wheels.lock`, and the zip check refuses a `wheels.lock` that holds anything but pinned, hashed lines.
- A Python tool requires exactly one runtime, and only a package of kind `runtime` can be required. A tool that requires another tool, itself, or a name with no recipe is refused before anything is built. A runtime is the upstream archive byte for byte, pinned by size and SHA-256; the pin for `python-3.13` matches the `SHA256SUMS` file of upstream's release. `build.py` does not unpack it.
- In CI, every package is installed with pwn-broker's own code into a temporary folder, with pwn-broker itself running under Python 3.12, 3.13 and 3.14. A tool is installed the way `tool install` does it, after the runtime it requires, with its dependencies installed from the bundled wheels with `--no-index`; its virtual environment must have been made by the runtime's interpreter, and each command is run once with `--help`, or with `-h` if `--help` is refused. A Python command must exit 0 and none may print a Python traceback. A runtime on its own is unpacked by pwn-broker and its interpreter must report the version the index gives. A notes package runs nothing: once installed, its notes folder must hold `.md` files that each begin with the note's fold marker and a `Playbook:` line, and pwn-broker must have named the folder in `run/notes`. Everything is removed again and nothing may be left behind, runtime included. The latest published build of each tool is tested from its published bytes.
- Before anything is published, the `kali` job does the same install in the official Kali image for each recipe's current build, from the built folder index, after switching the container's network off. Docker must report the container on no network, and the install check fails unless a public name cannot be resolved and a public address cannot be reached, so an install there shows it needs nothing from the network. Older builds are not tested in Kali.
- The latest build of each tool, once published, is built again in CI and compared with the published one. An older build stays published as it is, but CI no longer rebuilds or tests it. The whole card is compared, size and SHA-256 included, so a changed byte anywhere in the zip fails the run. Only a recipe that lists `build_from_source` is compared member by member instead, leaving out the wheels of those distributions and their lines in `wheels.lock`. A recipe that changes without its `build` going up fails CI, so a published build cannot quietly differ from its recipe. The one exception: when an upstream file can no longer be fetched, or no longer matches its pin, the rebuild is skipped with a warning and the published build is still tested.
- pwn-broker checks the zip against the size and SHA-256 in the index before it uses it, a runtime's included, and installs a Python tool's wheels with `pip --no-index --only-binary :all: --require-hashes`. While a tool on a runtime is installed, the only things that run are the runtime's interpreter, making the tool's virtual environment, and that `pip`. pwn-broker unpacks a runtime's archive itself and refuses the whole runtime for a hard link, a device or pipe, a name that is absolute, leaves the folder or holds a backslash, colon or control character, a link whose target is absolute or leads outside the runtime's folder, more than 20,000 members or more than 1 GiB unpacked.

## What a package is not checked for

- Upstream binaries are redistributed unmodified and pinned by hash. They are not audited, decompiled or scanned. A pinned hash says the file is the one I downloaded, not that the file is safe. Several of them are hack tools by design, and some antivirus products will flag them.
- `Rubeus.exe` is not an upstream binary, because upstream publishes only source. It is compiled from a pinned commit's archive, checked by size and SHA-256, against .NET Framework reference assemblies from a NuGet package pinned the same way. The .NET SDK that compiles it is not pinned by hash: the build only checks that `dotnet --version` is the version the recipe names. Three builds gave the same bytes. The source was not audited.
- `kerbrute` and `pspy` publish no checksums, so their pins are the hash of each file as first downloaded. If upstream's file was already wrong then, the pin is wrong with it.
- The index and the packages are not signed. The hashes in the index are the only check, and they catch a corrupted or swapped zip, not a bad index. Whoever can replace `index.toml` on the `index` release can point pwn-broker at any zip whose hash they also write.
- A Python dependency listed in a recipe's `build_from_source` is built into a wheel from a hash-checked source archive, but the build requirements of that archive (setuptools and the like) are fetched without hashes, and its build code runs on the build machine. That is why it is opt-in for each recipe. Two recipes use it: `jwt-tool`, for `cprint` and `ratelimit`, and `netexec`, for NetExec itself, `python-libnmap` and four dependencies NetExec takes from Git (`certipy-ad`, `impacket`, `oscrypto` and `pynfsclient`). A Git dependency's archive is pinned to a full commit and its SHA-256 by a `[[wheel_source]]`, and the built wheel's metadata is rewritten so that its Git requirements name the exact versions bundled beside it. Every other dependency comes as a pinned wheel.
- The runtime is python-build-standalone's build, pinned by hash and not audited. It carries its own OpenSSL, SQLite, zlib and other libraries, so a fix to one of them reaches pwn-broker only when I publish a new build of the runtime, not when Kali updates.
- A wheel's code is not read. The bundled wheels run no code while they are installed, but the tool will run them when you use it.
- Licences are copied from upstream and I do not assess them. PEASS-ng's carries terms beyond the GPL; read it before redistributing.
- The runtime's `LICENSE` is assembled by `tools/runtime-licence.py` from upstream's own files, byte for byte. CPython's licence and pip's come from the archive. The licence files the full distribution's `PYTHON.json` names for CPython itself and for the libraries the build links come from that distribution. CPython's `Doc/license.rst` at `v3.13.18` covers the software incorporated into the interpreter, and the leading comment of `Modules/_hacl/Hacl_Hash_SHA2.c` at that tag is the HACL* notice. `PYTHON.json` names licences only for extension modules. Searching `libpython3.13.so` found mimalloc and HACL* compiled into it, and both now have notices. That search is all that was checked: the binaries have not been audited for other code that needs a notice.
- The SHA-256 of each published zip is recorded only in its release (card, `SHA256SUMS.txt`, notes) and in the index, all written by the same CI job. Nothing in git records it. Anyone who can write this repository's releases can replace a zip and its hash together. A build can be reproduced while its upstream files and wheels are still published: `python build.py build <tool>` at the tag's commit gives the same SHA-256. CI has shown this for build 1 of all six on Ubuntu 24.04 under Python 3.12 and 3.13.

## CI

The workflow checks out `pwn-broker`, which is public, with no key, and the repository holds no secrets. A pull request from a fork runs with a read-only token. `publish` runs only on a push to `main`, and only the owner has write access.

## What counts

- A package or index built here that gets past pwn-broker's checks: an entry that unpacks outside the tool's folder, a link, a package that runs code at install, or a wheel that is not in the lock.
- A published tag whose files differ from its recipe, or a build whose card does not match its zip.
- `fetch.sh` keeping a zip it downloaded whose SHA-256 differs from the index, or writing outside the folder it was given.
- A runtime package whose archive is not the upstream file its recipe pins.
- The `kali` job passing while its container could still reach the network.
- The workflow exposing a token with write access to a step that runs a pull request's code.

## Out of scope

The tools in the packages (report to their maintainers), pwn-broker itself (report it there), and Hack The Box.
