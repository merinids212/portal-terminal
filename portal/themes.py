#!/usr/bin/env python3
"""Deterministic project → native Ghostty theme mapping.

Portal used to tint each project with an arbitrary pastel HSL color. This does it smart
instead: every project maps to one of Ghostty's own bundled themes (stable — the same
project always gets the same theme), and its board/picker color is a vivid accent pulled
from that theme. When portal dispatches an agent into its own Ghostty window it applies the
*same* theme, so the row color on the board and the window on screen are the one thing.

Falls back to a muted HSL accent when Ghostty's themes aren't present (non-Ghostty machine,
CI), so nothing here is load-bearing for the picker on other terminals.

CLI (for portal.zsh):
    themes.py accent <project>   -> #rrggbb   (or empty if no themes + no fallback wanted)
    themes.py name   <project>   -> theme name (empty if themes unavailable)
    themes.py selftest           -> exercises the mapping against the real theme dir
"""
import json
import os
import sys

HOME = os.path.expanduser("~")
CACHE = os.path.join(HOME, ".claude", "portal", "themes.cache.json")

# Same discovery order the user's own ghostty-random-theme script uses.
_DIRS = [
    os.environ.get("GHOSTTY_RESOURCES_DIR", "") + "/themes",
    "/Applications/Ghostty.app/Contents/Resources/ghostty/themes",
    "/usr/share/ghostty/themes",
    os.path.join(os.environ.get("XDG_DATA_HOME", HOME + "/.local/share"), "ghostty/themes"),
]


def themes_dir():
    for d in _DIRS:
        if d and os.path.isdir(d):
            return d
    return None


def _lum(h):
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return 0.0
    f = lambda v: (v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


_BG_LUM = _lum("0f0e0e")   # portal's ground; accents are shown against it


def _contrast(h):
    la = _lum(h)
    hi, lo = max(la, _BG_LUM), min(la, _BG_LUM)
    return (hi + 0.05) / (lo + 0.05)


def _parse(path):
    bg = None
    pal = {}
    try:
        for ln in open(path, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if ln.startswith("background"):
                bg = ln.split("=", 1)[1].strip().lstrip("#")
            elif ln.startswith("palette"):
                try:
                    rhs = ln.split("=", 1)[1].strip()
                    idx, col = rhs.split("=", 1)
                    pal[int(idx)] = col.strip().lstrip("#")
                except (ValueError, IndexError):
                    pass
    except OSError:
        return None, {}
    return bg, pal


def _accent(bg, pal):
    """A theme's signature accent: the most saturated bright color that reads on our ground."""
    best, score = None, -1.0
    for i in (9, 10, 11, 12, 13, 14, 4, 2, 1, 5, 6, 3):   # bright ANSI first, then normal
        c = pal.get(i)
        if not c or len(c) != 6:
            continue
        try:
            r, g, b = (int(c[j:j + 2], 16) / 255 for j in (0, 2, 4))
        except ValueError:
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        sat = (mx - mn) / mx if mx else 0
        if _contrast(c) >= 3.0 and sat * mx > score:
            score, best = sat * mx, c
    return best


def _scan(d):
    """Curated list of (theme_name, accent_hex) for dark-background themes with a usable
    accent. Sorted for stable indexing across machines with the same Ghostty version."""
    out = []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        bg, pal = _parse(p)
        if not bg or _lum(bg) >= 0.15:   # dark backgrounds only
            continue
        acc = _accent(bg, pal)
        if acc:
            out.append([name, "#" + acc])
    return out


def curated():
    """The (name, accent) list, cached and rebuilt only when the theme dir changes."""
    d = themes_dir()
    if not d:
        return []
    try:
        sig = "%d-%d" % (int(os.path.getmtime(d)), len(os.listdir(d)))
    except OSError:
        return []
    try:
        c = json.load(open(CACHE))
        if c.get("sig") == sig and c.get("dir") == d and c.get("themes"):
            return c["themes"]
    except (OSError, ValueError):
        pass
    themes = _scan(d)
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump({"dir": d, "sig": sig, "themes": themes}, open(CACHE, "w"))
    except OSError:
        pass
    return themes


def _hash(name):
    h = 0
    for ch in str(name):
        h = (h * 131 + ord(ch)) % (1 << 32)
    return h


def _pick(project):
    c = curated()
    if not c:
        return None
    return c[_hash(project) % len(c)]


def theme_for(project):
    """The Ghostty theme name for a project (for `--theme=` on window dispatch), or ""."""
    p = _pick(project)
    return p[0] if p else ""


def accent_hex(project):
    """A stable vivid accent for a project, from its theme. Falls back to muted HSL so the
    picker still color-codes projects on machines without Ghostty's themes."""
    p = _pick(project)
    if p:
        return p[1]
    import colorsys
    h = _hash(project) % 360
    r, g, b = colorsys.hls_to_rgb(h / 360.0, 0.74, 0.30)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "accent" and len(args) > 1:
        sys.stdout.write(accent_hex(args[1]))
    elif cmd == "name" and len(args) > 1:
        sys.stdout.write(theme_for(args[1]))
    elif cmd == "selftest":
        d = themes_dir()
        c = curated()
        print("themes dir:", d or "(none — HSL fallback)")
        print("curated dark themes:", len(c))
        for proj in ("billing", "api", "sacred-frequencies", "portal-terminal"):
            print("  %-20s -> %-24s %s" % (proj, theme_for(proj) or "(hsl)", accent_hex(proj)))
        # determinism
        assert theme_for("billing") == theme_for("billing")
        assert accent_hex("api") == accent_hex("api")
        print("deterministic: ok")
    else:
        sys.stderr.write(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    _main()
