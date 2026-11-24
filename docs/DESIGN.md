---
name: The pwn handbook (Cyclorama Dawn v2.1.0)
description: Unified Cyclorama Dawn v2.1.0 quick-reference handbook for pwn-broker, pwn-shop, pwn-playbooks, and pwn-report
brand: Real-Fruit-Snacks/rfs-brand v2.1.0
---

# Design: the pwn handbook (Cyclorama Dawn `v2.1.0`)

## Concept

`pwn-broker`, `pwn-shop`, `pwn-playbooks`, and `pwn-report` share one tabbed quick-reference handbook laid inside the Cyclorama Dawn `v2.1.0` shell. The top bar stays pinned outside the scroll viewport (`<body class="rfs-shell">` + `<div class="rfs-scroll-region">`) so the scrollbar never enters the header; below it, the four-tab bar joins flush to laminated checklist cards alongside an animated matte-dark cockpit display.

## Tokens & Palette

Canonical tokens load from `rfs.css` and `rfs-components.css` (`rfs-brand` `v2.1.0`). Dark (`LX 263`, `#050507`) is the explicit default; Light (`LX 512`, `#F5F7F9`) is an opt-in toggle synchronized across RFS tabs via `localStorage['rfs-theme']` with a radial View Transition.

| Role | Dark (`LX 263`) | Light (`LX 512`) | Use |
|---|---|---|---|
| Ground (`--rfs-bg`) | `#050507` | `#F5F7F9` | Scroll canvas behind cards |
| Card / Under-sheet | `#0D0E12` / `#161820` | `#FFFFFF` / `#E9EEF3` | Laminated checklist sheets & inactive tabs |
| Display (`--display`) | `#07080B` | `#0D0E12` | Terminal & vim cockpit screen (dark in both themes) |
| Live step (`--rfs-accent`) | `#FFC247` on `#050507` | `#FFC247` on `#050507` | Active checklist row & display cursor |
| Fault / Limits (`--rfs-fault`) | `#FF5C6C` | `#B91C2E` | Signal Crimson unchecked/limits & failures |
| Done (`--rfs-signal`) | `#3DDC97` | `#127247` | Completed checklist ticks & `[ok]` lines |
| Tab: Broker | `#FF9254` | `#AD3D0A` | Maritime Beacon Orange |
| Tab: Shop | `#3DDC97` | `#127247` | Signal Mint |
| Tab: Playbooks | `#74B8E8` | `#164669` | Glacial Steel |
| Tab: Report | `#FFC247` | `#885800` | Amber Evidence |

## Typography & Assets

- Self-hosted variable WOFF2 only (`Big Shoulders Stencil`, `Albert Sans`, `Azeret Mono`, `OFL.txt`) — zero external CDNs.
- README panels (`docs/assets/panel-{dark,light}.svg`) are generated deterministically by `docs/art/make-panel.py` from `docs/fonts/azeret-mono-latin.woff2` using Cyclorama Dawn `v2.1.0` inks.
- Shared base of `docs/styles.css` and `docs/script.js` stays identical across all four `pwn-*` repos up to the `this page` marker.
