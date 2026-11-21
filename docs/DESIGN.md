---
name: The pwn handbook
description: One quick reference handbook for pwn-broker, pwn-shop and pwn-playbooks
colors:
  ground: "#0E1012"
  card: "#1C1F23"
  card-2: "#24282D"
  display: "#08090A"
  rule: "#33383F"
  rule-strong: "#6B717A"
  ink: "#ECEBE6"
  mute: "#A0A6AE"
  amber: "#F2B705"
  on-amber: "#121314"
  red: "#F0565B"
  broker: "#ECEBE6"
  shop: "#3FA86B"
  books: "#4D93DB"
  ground-day: "#34373C"
  card-day: "#F7F7F3"
  card-2-day: "#E4E4DD"
  display-day: "#101214"
  rule-day: "#D3D3CC"
  rule-strong-day: "#85888C"
  ink-day: "#15171A"
  mute-day: "#555B62"
  red-day: "#C8102E"
  broker-day: "#15171A"
  shop-day: "#2E7D4F"
  books-day: "#2F6FB2"
  screen-text: "#E8E6E1"
  screen-dim: "#9AA0A8"
  screen-ok: "#6FD39A"
  screen-bright: "#FFFFFF"
  screen-rule: "#2A2E34"
  screen-scroll: "#3A3F46"
  vim-marker: "#8A8F99"
  vim-ip: "#FFC247"
  vim-placeholder: "#FF6A3D"
  vim-selection: "#2A2F36"
  foot: "#C3C7CC"
  print-paper: "#FFFFFF"
  print-ink: "#000000"
typography:
  display: { fontFamily: "Overpass, Segoe UI, system-ui, sans-serif", fontSize: "clamp(3rem, 1.2rem + 6vw, 6rem)", fontWeight: 900, lineHeight: 0.9, letterSpacing: "-0.035em" }
  headline: { fontFamily: "Overpass, Segoe UI, system-ui, sans-serif", fontSize: "clamp(1.9rem, 1.3rem + 2vw, 2.8rem)", fontWeight: 900, lineHeight: 1, letterSpacing: "-0.025em" }
  title: { fontFamily: "Overpass, Segoe UI, system-ui, sans-serif", fontSize: "clamp(1.2rem, 1rem + .7vw, 1.55rem)", fontWeight: 700, lineHeight: 1.25 }
  item: { fontFamily: "Overpass, Segoe UI, system-ui, sans-serif", fontSize: "0.98rem", fontWeight: 700, lineHeight: 1.3, letterSpacing: "0.03em" }
  body: { fontFamily: "Overpass, Segoe UI, system-ui, sans-serif", fontSize: "1.0625rem", fontWeight: 400, lineHeight: 1.6 }
  response: { fontFamily: "Overpass Mono, ui-monospace, Cascadia Mono, Consolas, monospace", fontSize: "0.9rem", fontWeight: 600, lineHeight: 1.3, letterSpacing: "0.03em" }
  label: { fontFamily: "Overpass Mono, ui-monospace, Cascadia Mono, Consolas, monospace", fontSize: "0.75rem", fontWeight: 600, lineHeight: 1.4, letterSpacing: "0.08em" }
  screen: { fontFamily: "Overpass Mono, ui-monospace, Cascadia Mono, Consolas, monospace", fontSize: "13px", fontWeight: 400, lineHeight: "21px" }
rounded:
  box: "3px"
  chip: "4px"
  control: "6px"
  command: "8px"
  screen: "10px"
  tab: "10px 10px 0 0"
  card: "14px"
spacing:
  gutter: "clamp(.75rem, 3vw, 2.5rem)"
  card-pad: "clamp(1.25rem, 3.4vw, 3rem)"
  wrap: "1240px"
  between: "clamp(1.75rem, 1rem + 2.6vw, 3.25rem)"
  item: ".78rem .2rem"
components:
  card: { backgroundColor: "{colors.card}", rounded: "{rounded.card}", padding: "{spacing.card-pad}" }
  tab: { backgroundColor: "{colors.card-2}", textColor: "{colors.mute}", rounded: "{rounded.tab}", height: "50px", padding: ".55rem 1.1rem .5rem" }
  tab-current: { backgroundColor: "{colors.card}", textColor: "{colors.ink}", rounded: "{rounded.tab}", height: "58px" }
  item-live: { backgroundColor: "{colors.amber}", textColor: "{colors.on-amber}", typography: "{typography.item}", padding: "{spacing.item}" }
  button-copy: { backgroundColor: "{colors.amber}", textColor: "{colors.on-amber}", typography: "{typography.label}", height: "46px", padding: "0 1rem" }
  command: { backgroundColor: "{colors.display}", textColor: "{colors.ink}", rounded: "{rounded.command}", padding: ".85rem 1rem" }
  button-outline: { textColor: "{colors.ink}", typography: "{typography.label}", rounded: "{rounded.control}", height: "36px", padding: "0 .9rem" }
  chip: { backgroundColor: "{colors.card-2}", textColor: "{colors.ink}", typography: "{typography.label}", rounded: "{rounded.chip}", padding: ".32rem .45rem" }
---

# Design: the pwn handbook (docs/ in pwn-broker, pwn-shop and pwn-playbooks)

## Concept

The three project pages are one quick reference handbook, the kind kept in a cockpit: tabbed checklist cards, each worked top to bottom and closed out, laid on a dark glareshield. Every page is a card behind its own tab. Its first card is a checklist of challenge, dotted leader and response, beside a dark display that plays a real session; as the session reaches the line that proves an item, the item ticks. pwn-broker and pwn-playbooks carry the same system: `styles.css` and `script.js` are the same in all three repos, word for word, up to the comment "this page", and each page adds its own part after it.

## Audience and primary action

HTB and OSCP players on Kali. They need to see what each project does, believe it from real output, and copy item 1: the install line (pwn-broker), `tool install <name>` (pwn-shop) or `tool install playbooks` (pwn-playbooks).

## Type

- Overpass 900 for card titles and tabs, 700 for checklist items and the one-liner, 400 for text. It descends from the US highway signage face, so the cards read as placards.
- Overpass Mono 400 for commands and the display, 600 for responses, running heads and button labels.
- Titles are set at full volume: `clamp(3rem, 1.2rem + 6vw, 6rem)`, line height 0.9, with a bar in the tab hue under them. Section titles carry the same bar above them. pwn-playbooks, the longest name, steps its title down to stay on one line beside the display.
- Items, responses and labels are capitals with open tracking (0.03em to 0.1em). Running text is sentence case.
- Fonts are self-hosted in `fonts/` with `OFL-overpass.txt`. The IBM Plex Mono files there, under `OFL.txt`, are for the README panel only.

## Color

Night is the cockpit at night; Day lights the card, and the glareshield stays dark in both. The OS setting decides the theme, the Night/Day switch overrides it, and the choice is saved as `rfs-theme` like the other RFS pages.

| Role | Night | Day | Use |
|---|---|---|---|
| ground | `#0E1012` | `#34373C` | The glareshield behind the cards, and the open ground between them |
| card / card-2 | `#1C1F23` / `#24282D` | `#F7F7F3` / `#E4E4DD` | The laminated sheet; the sheet beneath, tabs at rest, inline code |
| display | `#08090A` | `#101214` | Session windows and command lines: dark in both themes, as a cockpit screen is |
| rule / rule-strong | `#33383F` / `#6B717A` | `#D3D3CC` / `#85888C` | Item rules; dotted leaders, control borders, link underlines |
| ink / mute | `#ECEBE6` / `#A0A6AE` | `#15171A` / `#555B62` | Text, the 3px rule over each list; notes and open responses |
| Amber | `#F2B705` on `#121314` | the same | The live item, the copy control, focus and the display's cursor. Nothing else |
| Red | `#F0565B` | `#C8102E` | Known issues and checks not run. Nothing else |
| Broker / Shop / Playbooks | `#ECEBE6` / `#3FA86B` / `#4D93DB` | `#15171A` / `#2E7D4F` / `#2F6FB2` | Each project's tab hue: its tab's top edge, its title bars, link hovers |

Broker's hue is ink, so the parent project reads as the handbook's own. Inside the display, text is `#E8E6E1`, dim lines `#9AA0A8`, prompts white and `ok` lines `#6FD39A` in both themes, with `#2A2E34` under the window's bar and a `#3A3F46` scrollbar; the vim buffers on pwn-broker and pwn-playbooks use the note window's own colours, IPs `#FFC247`, placeholders `#FF6A3D`, fold markers `#8A8F99` and a `#2A2F36` selection. The foot's text is `#C3C7CC` on the ground in both themes, and print is black on white. Every state carries a word as well as a colour: "Passed", "Not run", "On PATH".

**The one amber line.** At most one item on a card is amber. While a live item exists, the copy control on that card turns to a grey outline, so amber never appears twice.

## Layout

A 1240px wrap with a gutter of `clamp(.75rem, 3vw, 2.5rem)`. Above the cards, the spine holds the RFS mark, GitHub and the theme switch, then the three tabs: the current one raised, flush with its card and joined to it. The first card's hero is two columns (6fr to 7fr): title, one-liner, text and checklist on the left, the display on the right. Reference cards follow, each a titled sheet: a spec list (leaders with no box), command blocks, a two-column "Tested on" checklist, and "Known issues" as a plain list with red marks. pwn-shop adds the package table; pwn-playbooks adds the finder, the folder index and the placeholder glossary. Open ground of `clamp(1.75rem, 1rem + 2.6vw, 3.25rem)` sets each card apart from the next, 12px more after the first so its second sheet shows, and all cards share one left edge.

Below 980px the hero is one column, so the checklist comes before the display. Below 640px the tabs share the width and drop their sub-lines, a command stacks above its copy control, and spec lists drop their leaders. Below 720px pwn-shop's table becomes one block per package.

## Signature

The checklist line. A 1.15rem box with a 2.5px border, the challenge in capitals, a 3px dotted leader, the response in mono, right-aligned. An item is open (empty box, muted response), live (the whole line amber), done (box filled in ink with a tick drawn from two strokes) or not run (red box with a drawn cross, red response). A list ends with a closeout line between two rules: "Box closed out", "Playbook filled".

**Depth by overlap only.** No drop shadows. The first card has a second sheet showing 12px beneath its corner, and its tab overlaps its top edge; the reference cards below it are single sheets on open ground, so a section ends where its sheet does. Tabs rest 6px down and rise to meet their card.

## Wow moment

The display plays the real session and the checklist beside it works itself. Items start open, the first one live; each ticks when the session prints the line that proves it, the next turns amber, and the closeout line ends it. On pwn-shop, four `tool install` lines (kerbrute, pspy, certipy with its runtime, playbooks) and a `finish` that removes them all. pwn-broker plays a box from its first target to `finish`. pwn-playbooks plays the note window instead: `:Pb smb`, three values typed over `<Replace>`, `dd`, `V} "qy`, then `@q`. Item 1, the reader's own step, is never ticked. Replay runs it again.

- **No JS:** every item done and every display at its finished frame, as the HTML ships.
- **Reduced motion:** no playback and no Replay button; the theme switches without its reveal.
- **Phone:** the same card in one column, the checklist first and the display under it.

## Motion

States switch at once, as a checklist line does. Commands type at 45ms a character; other lines print 120ms apart, with 250ms after a command. The cursor blinks twice and stays. Hover and colour changes take 150ms and a tab's lift 250ms, on `cubic-bezier(.2, .7, .2, 1)`. The theme switch reveals the new theme in a 600ms circle from the switch. If the script never runs, the display's lines appear after 6s.

## Voice

Plain and exact, in the first person where the README is: real commands, real output quoted as the tools print it, versions and dates, and the checks not run stated beside the ones that passed.

## Files

- `index.html`, `styles.css`, `script.js`, `fonts/`, and the icons and manifest. The shared part of `styles.css` and `script.js` must stay the same in all three repos; change it in one and copy it to the other two.
- `art/make-panel.py` draws the README panel as `assets/panel-{dark,light}.svg`, with text turned to paths from `fonts/`. In pwn-playbooks, `art/make-page.py` writes the page's generated lists from the tree.
- `assets/cover-*`, `assets/social-*.png` and `assets/cover.toml` are the RFS brand cover from rfs-brand's `make-cover.py`.

## Alternatives considered

- A case-file folder: each project a file with evidence stamped on it. It suits proof, but stamps and paper texture turn into costume, and it makes the projects look like a story rather than tools.
- An ANSI bulletin-board screen. True to the scene, but it reads as nostalgia, and colour blocks would crowd out the real output.
- The dark terminal landing the pages had before: name, one-liner, terminal screenshot, feature grid. It works, but it looks like every other tool page and leaves the proof as a picture.
