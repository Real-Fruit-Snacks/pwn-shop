"""Draw the README's animated panel: docs/assets/panel-dark.svg and panel-light.svg.

The panel acts out one package in a 16 s loop. On the left, a terminal in a checkout of this
repository: python build.py build kerbrute, then python build.py check kerbrute, then, at a
pwn-broker prompt, tool install kerbrute. On the right, the checkout as the build leaves it: the
recipe's pin, the upstream file in .cache/ (written as <sha256>.part and renamed to its SHA-256
once its size and hash match the pin), then dist/ with the zip, its card and index.toml.

Every value comes from recipes/kerbrute/recipe.toml and manifest.toml, read when the script runs.
The build's lines are the ones build.py's main prints for that command, and the install's are the
ones pwn-broker prints (src/pwn_broker/toolinstall.py), two spaces in, as it prints them; its
blank lines are left out. Between the build and the install CI tests and publishes the package,
which is left out, as is dist/sums/. The terminal is 44 columns wide and the script stops if a
line would not fit. A long value is cut with an ellipsis.

The SVG's base styles are the moment after the install, with the cursor at the last prompt and
the folder complete, which is what a viewer that skips the animation shows.

Text is converted to paths from the page's own fonts (docs/fonts), because an SVG shown through
<img> can't load fonts. Run from the repo root:

    python -m pip install -r docs/art/requirements.txt
    python docs/art/make-panel.py
"""
import os
import sys
import tomllib

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "..", "fonts")
OUT = os.path.join(HERE, "..", "assets")
RECIPE = os.path.join(HERE, "..", "..", "recipes", "kerbrute")

# RFS Cyclorama Dawn v2.1.0 (LX 263 dark / LX 512 light)
THEMES = {
    "dark": dict(plate="#0D0E12", glass="#050507", line="#242834", ink="#F2F4F8", mute="#8F95A6",
                 ember="#FF9254", mint="#3DDC97", steel="#1F547A", beacon="#F06E2B"),
    "light": dict(plate="#FFFFFF", glass="#F5F7F9", line="#CBD3DD", ink="#11141B", mute="#535B69",
                  ember="#AD3D0A", mint="#127247", steel="#164669", beacon="#BD4811"),
}

W, H = 1000, 430
CYCLE = 16.0        # seconds
TYPE = 0.045        # per typed character (BRAND-RULES.md)
FADE = 0.25         # a row on the right appearing or changing
EASE = "cubic-bezier(.2,.7,.2,1)"
STILL = 12.5        # the moment the base styles show
OUT_AT = 15.2       # everything fades out, then the loop starts again

MONO = ("azeret-mono-latin.woff2", 400)
MONO_B = ("azeret-mono-latin.woff2", 600)

COLS = 44
CUT = "…"      # the ellipsis that ends or starts a cut value
SHOWN_HEX = 16      # characters of a SHA-256 shown


def _read(name):
    with open(os.path.join(RECIPE, name), "rb") as fh:
        return tomllib.load(fh)


_recipe, _manifest = _read("recipe.toml"), _read("manifest.toml")
(_source,) = _recipe["source"]
NAME = os.path.basename(os.path.normpath(RECIPE))
VERSION, BUILD = _recipe["version"], _recipe["build"]
URL, SIZE, SHA, TO = _source["url"], _source["size"], _source["sha256"], _source["to"]
(COMMAND,) = [c["name"] for c in _manifest["command"]]
# names as build.py makes them (zip_name, write_card, write_zip's sorted members)
ZIP = f"{NAME}-{VERSION}-{BUILD}.zip"
CARD = f"{NAME}-{VERSION}-{BUILD}.toml"
MEMBERS = sorted(["manifest.toml", "LICENSE", TO])
SHA_CUT = SHA[:SHOWN_HEX] + CUT
URL_CUT = CUT + "/" + "/".join(URL.split("/")[-2:])

BUILD_CMD = f"python build.py build {NAME}"
CHECK_CMD = f"python build.py check {NAME}"
SHELL = "$ "
PROMPT = "forest/dc01 > "    # the prompt of pwn-broker's README, box forest at target dc01


def built(s):
    """A line as build.py prints it: ok, one space, the text."""
    return [("ok", "mint"), (f" {s}", "ink")]


def status(s):
    """As pwn-broker prints it (console.py): two spaces in, the word padded to four, two spaces."""
    return [("  ", "ink"), ("ok  ", "mint"), (f"  {s}", "ink")]


def info(s):
    return [(f"  {s}", "mute")]


# The session. ("cmd", prompt, typed, prompt shown at, typing starts at), ("out", pieces, printed
# at), or ("gap",): a row left empty between the checkout and the box.
def _typed(start, s):
    return start + (len(s) - 1) * TYPE


T_CACHE = _typed(0.5, BUILD_CMD) + 0.25     # the build starts: .cache/ is made
T_PART = T_CACHE + 0.05                     # the download is written to <sha256>.part
T_CHECKED = T_PART + 1.2                    # size and SHA-256 match; renamed to <sha256>
T_DIST = T_CHECKED + 0.2
T_MEMBERS = [T_DIST + 0.2 + 0.15 * i for i in range(len(MEMBERS))]
T_ZIP = T_MEMBERS[-1] + 0.15                # the zip passes its check and gets its name
T_CARD = T_ZIP + 0.1
T_BUILT = T_CARD + 0.1
T_INDEX = T_BUILT + 0.25
T_WROTE = T_INDEX + 0.15

SESSION = [
    ("cmd", SHELL, BUILD_CMD, 0.0, 0.5),
    ("out", built(f"built dist/{ZIP}"), T_BUILT),
    ("out", built("wrote dist/index.toml"), T_WROTE),
    ("cmd", SHELL, CHECK_CMD, 5.1, 5.6),
    ("out", built(f"{NAME} {VERSION}-{BUILD}"), 7.25),
    ("out", built(ZIP), 7.4),
    ("gap",),
    ("cmd", PROMPT, f"tool install {NAME}", 8.0, 8.6),
    ("out", status(f"{NAME} {VERSION} installed"), 10.1),
    ("out", info(f"run   {COMMAND}   from any terminal"), 10.25),
    ("cmd", PROMPT, "", 10.7, None),
]

# The checkout. (row, depth, pieces, shown at); a piece is (text, face, column, state) where state
# is "ink", "mute", ("fill", t): muted until t, then ink, ("until", t, colour): gone at t, or
# ("from", t, colour): there from t.
TREE = [
    (0, 0, [(f"recipes/{NAME}/recipe.toml", MONO, 0, "ink")], 0.0),
    (1, 1, [(f'version = "{VERSION}"', MONO, 0, "ink"), (f"build = {BUILD}", MONO, 20, "ink")], 0.0),
    (2, 1, [(f'url = "{URL_CUT}"', MONO, 0, "ink")], 0.0),
    (3, 1, [(f"size = {SIZE}", MONO, 0, "ink")], 0.0),
    (4, 1, [(f'sha256 = "{SHA_CUT}"', MONO, 0, "ink")], 0.0),
    (5, 0, [(".cache/", MONO, 0, "ink")], T_CACHE),
    (6, 1, [(SHA_CUT, MONO, 0, ("fill", T_CHECKED)),
            (".part", MONO, len(SHA_CUT), ("until", T_CHECKED, "mute")),
            (str(SIZE), MONO, len(SHA_CUT) + 7, ("from", T_CHECKED, "mute"))], T_PART),
    (7, 0, [("dist/", MONO, 0, "ink")], T_DIST),
    (8, 1, [(ZIP, MONO_B, 0, ("fill", T_ZIP))], T_DIST + 0.05),
    *[(9 + i, 2, [(m, MONO, 0, "ink")], T_MEMBERS[i]) for i, m in enumerate(MEMBERS)],
    (9 + len(MEMBERS), 1, [(CARD, MONO, 0, "ink")], T_CARD),
    (10 + len(MEMBERS), 1, [("index.toml", MONO, 0, "ink"),
                            (f'latest = "{VERSION}-{BUILD}"', MONO, 13, ("from", T_INDEX + 0.05, "mute"))], T_INDEX),
]

# Layout
LX, RX, TOP, BOTTOM = 40, 544, 76, 404       # left window from LX, right window from RX to W - 40
L_SIZE, R_SIZE = 15.0, 14.2
L_PAD, L_FIRST, L_PITCH = 18, 108, 28
R_PAD, R_FIRST, R_PITCH, R_INDENT = 14, 106, 22, 14

_fonts = {}
_glyphs = {}   # (font, glyph name) -> (id, path in font units); drawn once in <defs>, reused by <use>


def font(spec):
    if spec not in _fonts:
        fname, wght = spec
        tt = TTFont(os.path.join(FONTS, fname))
        if "fvar" in tt:
            tt = instantiateVariableFont(tt, {"wght": wght}, inplace=True)
        _fonts[spec] = tt
    return _fonts[spec]


def char_w(size, fname=MONO):
    f = font(fname)
    return f.getGlyphSet()[f.getBestCmap()[ord("0")]].width * size / f["head"].unitsPerEm


def text(s, x, y, size, fname, fill, ls=0.0, anchor="start"):
    """Path for `s` with its baseline at y. ls is letter spacing in em."""
    f = font(fname)
    gs, cmap, upm = f.getGlyphSet(), f.getBestCmap(), f["head"].unitsPerEm
    k = size / upm
    names = []
    for ch in s:
        if ord(ch) not in cmap:
            sys.exit(f"{fname} has no glyph for {ch!r}")
        names.append(cmap[ord(ch)])
    width = sum(gs[n].width * k for n in names) + ls * size * (len(names) - 1)
    pen_x = {"start": x, "middle": x - width / 2, "end": x - width}[anchor]
    uses = []
    for n in names:
        key = (fname, n)
        if key not in _glyphs:
            pen = SVGPathPen(gs, ntos=lambda v: "%g" % round(v, 1))
            gs[n].draw(pen)
            _glyphs[key] = (f"g{len(_glyphs)}", pen.getCommands())
        gid, d = _glyphs[key]
        if d:
            uses.append(f'<use href="#{gid}" transform="matrix({k:.5f} 0 0 {-k:.5f} {pen_x:.2f} {y:.2f})"/>')
        pen_x += gs[n].width * k + ls * size
    return f'<g fill="{fill}">{"".join(uses)}</g>'


def pct(t):
    return f"{t / CYCLE * 100:.3f}%"


def check_layout():
    """Stop if a line is wider than the terminal or a piece leaves its window or meets another."""
    for line in SESSION:
        if line[0] == "cmd":
            s = line[1] + line[2]
        elif line[0] == "out":
            s = "".join(part for part, _ in line[1])
        else:
            continue
        if len(s) > COLS:
            sys.exit(f"{s!r} is {len(s)} columns; the terminal has {COLS}")
        if LX + L_PAD + len(s) * char_w(L_SIZE) > RX - 20 - L_PAD:
            sys.exit(f"{s!r} runs past the right edge of the terminal")
    rcw = char_w(R_SIZE)
    right = W - 40 - R_PAD
    for r, depth, pieces, _ in TREE:
        x0 = RX + R_PAD + depth * R_INDENT
        end = None
        for s, _, col, _ in sorted(pieces, key=lambda p: p[2]):
            if end is not None and col < end:
                sys.exit(f"row {r}: {s!r} starts at column {col}, inside the piece before it")
            end = col + len(s)
            if x0 + end * rcw > right:
                sys.exit(f"row {r}: {s!r} runs past the right edge of its window")
        if R_FIRST + r * R_PITCH + R_SIZE * 0.25 > BOTTOM:
            sys.exit(f"row {r} is below the window")
    if L_FIRST + (len(SESSION) - 1) * L_PITCH + L_SIZE * 0.25 > BOTTOM:
        sys.exit("the session is taller than its window")


class Anim:
    """Collects the keyframes and the still-frame styles, one class per animated element."""

    def __init__(self):
        self.css, self.rules, self.base = [], [], []

    def toggle(self, name, start):
        """Shown from `start` to the end of the loop, switched on at once (a terminal printing)."""
        self.base.append(f".{name}{{opacity:{int(start <= STILL)}}}")
        if start <= 0:
            return
        self.css.append(f"@keyframes {name}{{0%{{opacity:0}}{pct(start)}{{opacity:1}}100%{{opacity:1}}}}")
        self.rules.append(f".{name}{{animation:{name} {CYCLE}s step-end infinite}}")

    def fade(self, name, changes, init=0):
        """Opacity that eases to each (t, value) in turn, over FADE."""
        value, still = init, init
        frames = [f"0%{{opacity:{init}}}"]
        for t, v in changes:
            frames.append(f"{pct(t)}{{opacity:{value};animation-timing-function:{EASE}}}")
            frames.append(f"{pct(t + FADE)}{{opacity:{v}}}")
            value = v
            if t <= STILL:
                still = v
        frames.append(f"100%{{opacity:{value}}}")
        self.base.append(f".{name}{{opacity:{still}}}")
        if changes:
            self.css.append(f"@keyframes {name}{{{''.join(frames)}}}")
            self.rules.append(f".{name}{{animation:{name} {CYCLE}s linear infinite}}")

    def style(self):
        return f"<style>{''.join(self.base)}@media (prefers-reduced-motion:no-preference){{{''.join(self.css)}{''.join(self.rules)}}}</style>"


def build(theme):
    c = THEMES[theme]
    a = Anim()
    run = []                      # everything that plays, inside one group that fades out at the end
    cw = char_w(L_SIZE)
    lx0 = LX + L_PAD

    def left_y(row):
        return L_FIRST + row * L_PITCH

    # --- the session, and where the cursor is ---
    cursor = []                   # (t, row, col, shown)
    for row, line in enumerate(SESSION):
        y = left_y(row)
        if line[0] == "cmd":
            _, prompt, typed, shown, typing = line
            a.toggle(f"p{row}", shown)
            run.append(f'<g class="p{row}">{text(prompt, lx0, y, L_SIZE, MONO_B, c["ember"])}</g>')
            cursor.append((shown, row, len(prompt), 1))
            for i, ch in enumerate(typed):
                t = typing + i * TYPE
                a.toggle(f"p{row}c{i}", t)
                run.append(f'<g class="p{row}c{i}">{text(ch, lx0 + (len(prompt) + i) * cw, y, L_SIZE, MONO, c["ink"])}</g>')
                cursor.append((t, row, len(prompt) + i + 1, 1))
        elif line[0] == "out":
            _, pieces, shown = line
            # the cursor leaves the prompt line when the command's output starts
            if cursor and cursor[-1][3]:
                cursor.append((shown, cursor[-1][1], cursor[-1][2], 0))
            g, col = [], 0
            for part, colour in pieces:
                if part.strip():
                    g.append(text(part, lx0 + col * cw, y, L_SIZE, MONO, c[colour]))
                col += len(part)
            a.toggle(f"o{row}", shown)
            run.append(f'<g class="o{row}">{"".join(g)}</g>')

    # The cursor blinks twice when a prompt waits a while, then stays.
    waits = [line[3] for line in SESSION if line[0] == "cmd" and (line[4] is None or line[4] - line[3] > 1.5)]
    blinks = list(cursor)
    for start in waits:
        held = [p for p in blinks if p[0] <= start][-1]
        for dt, s in ((0.45, 0), (0.7, 1), (1.15, 0), (1.4, 1)):
            blinks.append((start + dt, held[1], held[2], s))
    blinks.sort()
    cur_w, cur_h = cw, L_SIZE * 1.05

    def at(col, r):
        return f"translate({lx0 + col * cw:.2f}px,{left_y(r) - L_SIZE * 0.8:.2f}px)"

    frames = []
    still = blinks[0]
    for t, r, col, s in blinks:
        frames.append(f"{pct(t)}{{transform:{at(col, r)};opacity:{s}}}")
        if t <= STILL:
            still = (t, r, col, s)
    a.base.append(f".cur{{transform:{at(still[2], still[1])};opacity:{still[3]}}}")
    first = blinks[0]
    a.css.append(f"@keyframes cur{{{''.join(frames)}100%{{transform:{at(first[2], first[1])};opacity:0}}}}")
    a.rules.append(f".cur{{animation:cur {CYCLE}s step-end infinite}}")
    run.append(f'<rect class="cur" x="0" y="0" width="{cur_w:.2f}" height="{cur_h:.2f}" fill="{c["ink"]}" fill-opacity=".7"/>')

    # --- the checkout ---
    rcw = char_w(R_SIZE)
    rx0 = RX + R_PAD

    def right_y(r):
        return R_FIRST + r * R_PITCH

    for r, depth, pieces, shown in TREE:
        y, x = right_y(r), rx0 + depth * R_INDENT
        for i, (s, face, col, state) in enumerate(pieces):
            px = x + col * rcw
            name = f"t{r}p{i}"
            if state in ("ink", "mute"):
                a.fade(name, [(shown, 1)] if shown > 0 else [], init=int(shown <= 0))
                run.append(f'<g class="{name}">{text(s, px, y, R_SIZE, face, c[state])}</g>')
            elif state[0] == "fill":
                filled = state[1]
                a.fade(name + "m", [(shown, 1), (filled, 0)])
                a.fade(name + "i", [(filled, 1)])
                run.append(f'<g class="{name}m">{text(s, px, y, R_SIZE, face, c["mute"])}</g>')
                run.append(f'<g class="{name}i">{text(s, px, y, R_SIZE, face, c["ink"])}</g>')
            elif state[0] == "until":
                a.fade(name, [(shown, 1), (state[1], 0)])
                run.append(f'<g class="{name}">{text(s, px, y, R_SIZE, face, c[state[2]])}</g>')
            else:
                a.fade(name, [(state[1], 1)])
                run.append(f'<g class="{name}">{text(s, px, y, R_SIZE, face, c[state[2]])}</g>')

    # --- the end of the loop: everything goes, then the shell prompt starts it again ---
    a.base.append(".run{opacity:1}")
    a.css.append(f"@keyframes run{{0%{{opacity:1}}{pct(OUT_AT)}{{opacity:1;animation-timing-function:{EASE}}}"
                 f"{pct(OUT_AT + 0.5)}{{opacity:0}}100%{{opacity:0}}}}")
    a.rules.append(f".run{{animation:run {CYCLE}s linear infinite}}")

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">']
    o.append(a.style())
    # --- the plate, the terminal and the checkout's pane ---
    o.append(f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="{c["plate"]}" stroke="{c["line"]}"/>')
    o.append(f'<rect x="1" y="1" width="{W - 2}" height="3" rx="1.5" fill="url(#rfs-bar)"/>')
    o.append(text("pwn-shop", LX, 52, 15, MONO_B, c["mute"]))
    o.append(text("pwn-shop/", RX, 52, 15, MONO, c["mute"]))
    o.append(f'<rect x="{LX}" y="{TOP}" width="{RX - 20 - LX}" height="{BOTTOM - TOP}" rx="8" fill="{c["glass"]}" stroke="{c["line"]}"/>')
    o.append(f'<rect x="{RX}" y="{TOP}" width="{W - 40 - RX}" height="{BOTTOM - TOP}" rx="8" fill="none" stroke="{c["line"]}"/>')
    o.append(f'<g class="run">{"".join(run)}</g>')

    bar_grad = (
        f'<linearGradient id="rfs-bar" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{c["steel"]}"/>'
        f'<stop offset="55%" stop-color="{c["steel"]}"/>'
        f'<stop offset="100%" stop-color="{c["beacon"]}"/>'
        f'</linearGradient>'
    )
    defs = bar_grad + "".join(f'<path id="{gid}" d="{d}"/>' for gid, d in _glyphs.values() if d)
    o.insert(1, f"<defs>{defs}</defs>")
    o.append("</svg>")
    return "".join(o)


check_layout()
for theme in THEMES:
    _glyphs.clear()
    path = os.path.join(OUT, f"panel-{theme}.svg")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(build(theme) + "\n")
    print(f"wrote {os.path.relpath(path)} ({os.path.getsize(path) // 1024} KB)")
