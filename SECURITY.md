# Security

## Supported versions

The latest published build of each tool. A published build is never changed or replaced; a fix ships as a new build, and a build that should not be used is withdrawn by deleting its release.

## Reporting

Use GitHub's private vulnerability reporting on this repository (Security → Report a vulnerability). Don't open a public issue for anything that could get a package past pwn-broker's checks or put a different file under a published tag.

Include the tool and build (`kerbrute 1.0.3-1`), what you ran, and what you saw. Use placeholder credentials and addresses.

This is a one-person project. I read reports within a week. If it's real, the fix and the changelog entry name the problem plainly.

## What a package is checked for

- Before a package is built, each upstream file is downloaded and refused unless its size and SHA-256 equal the recipe's. A recipe does not follow a "latest" link; it names one file.
- The finished zip is checked with the same rules pwn-broker applies when it unpacks one: the manifest's schema and fields, every file the manifest names present, nothing compressed, no link or special file, no entry that would land outside the tool's folder, and the zip's name matching the manifest.
- A Python tool's `requirements.lock` must pin every dependency to one version with the SHA-256 of each upstream file, with no markers, ranges or URLs. Every bundled wheel is checked against the lock, must fit Python 3.12 or 3.13 on x86_64 Linux, and its contents must agree with its name. The package records each bundled wheel in `wheels.lock`.
- In CI, every package is installed with pwn-broker's own code into a temporary folder under Python 3.12 and 3.13, with no network for the Python tools, and each command is started once. A published build is tested from its published bytes.
- A recipe that changes without its `build` going up fails CI, so a published build cannot quietly differ from its recipe.
- pwn-broker checks the zip against the size and SHA-256 in the index before it uses it, and installs a Python tool's wheels with `pip --no-index --only-binary :all: --require-hashes`.

## What a package is not checked for

- Upstream binaries are redistributed unmodified and pinned by hash. They are not audited, decompiled or scanned. A pinned hash says the file is the one I downloaded, not that the file is safe. Several of them are hack tools by design, and some antivirus products will flag them.
- `kerbrute` publishes no checksums, so its pin is the hash of the file as first downloaded. If upstream's file was already wrong then, the pin is wrong with it.
- The index and the packages are not signed. The hashes in the index are the only check, and they catch a corrupted or swapped zip, not a bad index. Whoever can replace `index.toml` on the `index` release can point pwn-broker at any zip whose hash they also write.
- A Python dependency listed in a recipe's `build_from_source` is built into a wheel from a hash-checked source archive, but the build requirements of that archive (setuptools and the like) are fetched without hashes, and its build code runs on the build machine. That is why it is opt-in for each recipe. None of the six recipes uses it: all their dependencies come as pinned wheels.
- A wheel's code is not read. The bundled wheels run no code while they are installed, but the tool will run them when you use it.
- Licences are copied from upstream and I do not assess them. PEASS-ng's carries terms beyond the GPL; read it before redistributing.

## What counts

- A package or index built here that gets past pwn-broker's checks: an entry that unpacks outside the tool's folder, a link, a package that runs code at install, or a wheel that is not in the lock.
- A published tag whose files differ from its recipe, or a build whose card does not match its zip.
- `fetch.sh` keeping a zip whose SHA-256 differs from the index, or writing outside the folder it was given.
- The workflow exposing `PWN_BROKER_DEPLOY_KEY` or a token with write access to a step that runs a pull request's code.

## Out of scope

The tools in the packages (report to their maintainers), pwn-broker itself (report it there), and Hack The Box.
