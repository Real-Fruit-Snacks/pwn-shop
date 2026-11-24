# Agent instructions — pwn-shop

Read before touching recipes, `build.py`, `docs/`, or release panels.

## What this repo is

`pwn-shop` builds reproducible, SHA-256-pinned Kali/HTB tool packages for `pwn-broker`. The `docs/` GitHub Pages site is part of the unified four-tab **pwn handbook** (`pwn-broker`, `pwn-shop`, `pwn-playbooks`, `pwn-report`).

## Brand contract (Cyclorama Dawn `v2.1.0`)

Source of truth: `Real-Fruit-Snacks/rfs-brand` (`v2.1.0`).

- **Dark-first (`LX 263` `#050507`)** default; **Light (`LX 512` `#F5F7F9`)** opt-in toggle with radial View Transition and cross-tab `rfs-theme` `localStorage` sync.
- **Palette:** Glacial Steel (`#1F547A` / `#74B8E8` / `#164669`), Maritime Beacon Orange (`#F06E2B` / `#FF9254` / `#AD3D0A`), Signal Mint (`#3DDC97` / `#127247`), Amber (`#FFC247` / `#885800`), Signal Crimson Fault (`#FF5C6C` / `#C92438` / `#B91C2E`).
- **Fonts:** Self-hosted variable WOFF2 only (`Big Shoulders Stencil`, `Albert Sans`, `Azeret Mono`, `OFL.txt`) — zero external CDNs.
- **Chrome:** Below-header `.rfs-shell` scroll container (`<header class="rfs-shell-header">` + `<div class="rfs-scroll-region">`), wrapped command cards, Cartesian `in oklab` gradients only, zero neon glows, zero capsule pills, zero native `alert()`/`confirm()`/`prompt()`, semantic `[ok]` `popover="manual"` toasts.
- **Shared handbook contract:** The shared base of `docs/styles.css` and `docs/script.js` stays identical across `pwn-broker`, `pwn-shop`, `pwn-playbooks`, and `pwn-report` up to the `this page` comment.

## Required checks before pushing

```sh
python -m py_compile build.py .github/scripts/install-check.py tools/runtime-licence.py
python docs/art/make-panel.py
python -m pytest -q -W error
```

- Never edit a published build in place; bump `build` in `recipe.toml` for package changes.
- `docs/assets/panel-{dark,light}.svg` must match `docs/art/make-panel.py` byte-for-byte.
- Never mention private repositories or unrelated internal tools in any public file.
