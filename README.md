# pwn-shop

Tool packages for pwn-broker, built from upstream releases

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/panel-dark.svg">
  <img alt="A terminal in a pwn-shop checkout beside the checkout's folder. python build.py build kerbrute downloads the upstream file into .cache/, where it is kept under its SHA-256 once its size and hash match the pin in recipes/kerbrute/recipe.toml; dist/ fills with kerbrute-1.0.3-1.zip, holding LICENSE, files/kerbrute_linux_amd64 and manifest.toml, then its card and index.toml. python build.py check kerbrute passes, and tool install kerbrute at a pwn-broker prompt installs kerbrute 1.0.3" src="docs/assets/panel-light.svg" width="100%">
</picture>

```text
forest/dc01 > tool install certipy
  ok    python-3.13 3.13.18 installed (runtime, 34 MB)
  ok    certipy 5.1.0 installed
  run   certipy   from any terminal
```

- **Pinned, reproducible packages:** 39 recipes covering Active Directory, enumeration, privesc, pivoting, playbooks, and reporting — every upstream binary, wheel, and git source pinned by size and SHA-256.
- **Zero Kali pollution:** Python tools run on an isolated `python-3.13` runtime package and never shadow system Python; `finish` in [pwn-broker](https://github.com/Real-Fruit-Snacks/pwn-broker) removes them cleanly.
- **Offline mirror ready:** Run `sh fetch.sh ~/pwn-packages` to verify and mirror the entire published index locally (`tools_index` in `/opt/pwn-broker/config.toml`).
- **CI verified in Kali:** Every package is tested with `pwn-broker`'s installer inside an offline Kali Linux container before publishing.

## Documentation

- [Project Page (pwn handbook)](https://real-fruit-snacks.github.io/pwn-shop/)
- [All 39 Packages, Offline Mirror, Configuration & Known Issues](docs/PACKAGES.md)
- [Package Format & Build Guide](docs/guide.md) · [Tested Record & Limits](docs/tested.md)
- [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## License

[MIT](LICENSE) for this repository's code; each package carries its upstream tool's licence. Not affiliated with upstream projects.
