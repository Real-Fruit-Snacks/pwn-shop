## Summary

- What changed and why, in plain words.

## Checked

- [ ] `python -m pytest -q -W error` passes
- [ ] `python build.py check <name>` passes for each recipe touched, and `python build.py build <name>` on Linux for a new or changed one
- [ ] A change to anything that goes into a package (a source, the manifest, the licence, a lock) raises `build` in `recipe.toml`
- [ ] The tables in `docs/PACKAGES.md` and `docs/index.html`, the package counts and `CHANGELOG.md` say what this changes
- [ ] `fetch.sh` and `.github/scripts/kali-packages.sh` still pass `sh -n`, `dash -n` and `shellcheck`, if either changed

## Publishes

Merging to `main` publishes a release for every recipe build that has none. Name each one here as `<name>-v<version>-<build>`, or write "nothing".
