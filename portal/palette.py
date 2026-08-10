#!/usr/bin/env python3
"""Theme-aware palette + truecolor gradient engine for `portal` (warm monochrome).

Portal's branding is single-hue: warm neutrals around 35° at 5-10% chroma — never pure
white on pure black. Two exceptions carry real color, both functional: CLAUDE (the agent
mark, in Claude's own coral) and pastel() (a stable hue per project, so you can find a
project in a long list without reading it).

Reads $PORTAL_THEME (light|dark, default dark). Emits 24-bit truecolor SGR when the
terminal advertises it ($COLORTERM=truecolor/24bit), else falls back to 256-color.

Keys: G primary ink · A bright accent · C value · D muted dim · P soft mark
      CORE emblem core · RINGS emblem gradient · GRAD_TOP/GRAD_BOT wordmark+recency stops
      CLAUDE agent accent (the only chromatic value in portal)
"""
import os

RESET = "\033[0m"


def truecolor_enabled():
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def _hex(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def lerp(a, b, t):
    """Interpolate hex color a->b at t in [0,1]; return a 24-bit SGR (or 256 fallback)."""
    ar, ag, ab = _hex(a)
    br, bg, bb = _hex(b)
    r = round(ar + (br - ar) * t)
    g = round(ag + (bg - ag) * t)
    bl = round(ab + (bb - ab) * t)
    if truecolor_enabled():
        return "\033[38;2;%d;%d;%dm" % (r, g, bl)
    return "\033[38;5;%dm" % _rgb_to_256(r, g, bl)


def grad(top, bot, n):
    """n SGR strings fading top->bot (inclusive)."""
    if n <= 1:
        return [lerp(top, bot, 0.0)]
    return [lerp(top, bot, i / (n - 1)) for i in range(n)]


def _rgb_to_256(r, g, b):
    # map to the 6x6x6 color cube (indices 16-231)
    def q(v):
        if v < 48:
            return 0
        if v < 115:
            return 1
        return (v - 35) // 40
    return 16 + 36 * q(r) + 6 * q(g) + q(b)


# Warm neutrals, not greys: one hue (~35°) held at 5-10% chroma. Pure #fff on pure #000
# is a screen; a hair of amber in the ink reads like phosphor that has been on a while.
# Defined as hex so truecolor terminals get the tint and 256-color ones degrade to the
# nearest cube cell automatically (see rgb()).
_DARK_HEX = {
    "G": "#f1ebe0", "D": "#837a6e", "C": "#fff9f0", "A": "#d6cec2", "P": "#a79e92",
    "CORE": "#fff9f0",
    "RINGS": ["#4c463e", "#6b6259", "#a79e92", "#d6cec2", "#f1ebe0"],
    "WORDMARK": ["#fff9f0", "#eee7db", "#d3cabc", "#a89e90", "#7d7467", "#554e46"],
    # gradient stops: wordmark fade + recency fade (recent -> old)
    "GRAD_TOP": "#fff9f0", "GRAD_MID": "#958c7e", "GRAD_BOT": "#463f38",
}

_LIGHT_HEX = {
    "G": "#241f19", "D": "#7a7266", "C": "#14110e", "A": "#3d372f", "P": "#5c554b",
    "CORE": "#14110e",
    "RINGS": ["#c9c1b4", "#a89f92", "#7d7467", "#4f483f", "#241f19"],
    "WORDMARK": ["#14110e", "#2a251f", "#463f38", "#6b6259", "#8f8578", "#b3a99a"],
    "GRAD_TOP": "#14110e", "GRAD_MID": "#6b6259", "GRAD_BOT": "#bdb4a6",
}

# the one chromatic value in portal's *branding*: Claude's own coral, worn by the agent mark.
CLAUDE_HEX = "#d97757"

# Project color is not branding — it's UX. A stable hue per project is what lets you
# find "billing" in a 128-row list without reading it, so pastel() keeps its hue and
# only loses saturation: muted enough that the chrome still reads black and white.
_PROJ_SAT_DARK, _PROJ_LIGHT_DARK = 0.30, 0.74
_PROJ_SAT_LIGHT, _PROJ_LIGHT_LIGHT = 0.42, 0.40


def _hsl_to_rgb(h, s, l):
    import colorsys
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)
    return round(r * 255), round(g * 255), round(b * 255)


def rgb(hexstr):
    """A single hex color as an SGR string (truecolor, or the nearest 256-color cube cell)."""
    r, g, b = _hex(hexstr)
    if truecolor_enabled():
        return "\033[38;2;%d;%d;%dm" % (r, g, b)
    return "\033[38;5;%dm" % _rgb_to_256(r, g, b)


def pastel(name, theme=None):
    """Stable per-project accent. Prefer a vivid color drawn from the project's assigned
    native Ghostty theme (see themes.py) so the board color matches the agent's window; fall
    back to a muted hue-hash when Ghostty's themes aren't available. Theme accents are
    curated for the dark ground — on a light terminal use the light-tuned HSL instead."""
    if theme is None:
        theme = os.environ.get("PORTAL_THEME", "dark")
    if theme != "light":
        try:
            import themes
            return rgb(themes.accent_hex(name))
        except Exception:
            pass
    h = 0
    for ch in str(name):
        h = (h * 131 + ord(ch)) % 100000
    hue = h % 360
    if theme == "light":
        r, g, b = _hsl_to_rgb(hue, _PROJ_SAT_LIGHT, _PROJ_LIGHT_LIGHT)
    else:
        r, g, b = _hsl_to_rgb(hue, _PROJ_SAT_DARK, _PROJ_LIGHT_DARK)
    if truecolor_enabled():
        return "\033[38;2;%d;%d;%dm" % (r, g, b)
    return "\033[38;5;%dm" % _rgb_to_256(r, g, b)


def palette(theme=None):
    """Resolve the theme's hex ramp into SGR strings (GRAD_* stay hex — grad() lerps them)."""
    if theme is None:
        theme = os.environ.get("PORTAL_THEME", "dark")
    src = _LIGHT_HEX if theme == "light" else _DARK_HEX
    p = {}
    for k, v in src.items():
        if k.startswith("GRAD_"):
            p[k] = v
        elif isinstance(v, list):
            p[k] = [rgb(c) for c in v]
        else:
            p[k] = rgb(v)
    p["CLAUDE"] = rgb(CLAUDE_HEX)
    p["R"] = RESET
    p["theme"] = theme
    return p
