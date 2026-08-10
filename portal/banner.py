#!/usr/bin/env python3
"""PORTAL header for the fzf picker.

The wordmark is the real ANSI Shadow "PORTAL" — the exact art on the site and README, the
box-drawing drop-shadow and all. That specific style is inherently six rows; it can't be
shrunk without becoming a different logo. So instead of mangling it, the default just trims
the padding around it (no blank splash line), and PORTAL_BANNER=off drops the mark entirely
for anyone who wants every row for the session list. A one-line fallback keeps very narrow
terminals from wrapping the art.
"""
import os
import re
import sys

from palette import grad, palette

_ANSI = re.compile(r"\033\[[0-9;]*m")


def center(line, width):
    vis = len(_ANSI.sub("", line))
    pad = max(0, (width - vis) // 2)
    return " " * pad + line

# ANSI Shadow "PORTAL" — the wordmark, byte-identical to site/index.html and the README.
WORDMARK = [
    "██████╗  ██████╗ ██████╗ ████████╗ █████╗ ██╗     ",
    "██╔══██╗██╔═══██╗██╔══██╗╚══██╔══╝██╔══██╗██║     ",
    "██████╔╝██║   ██║██████╔╝   ██║   ███████║██║     ",
    "██╔═══╝ ██║   ██║██╔══██╗   ██║   ██╔══██║██║     ",
    "██║     ╚██████╔╝██║  ██║   ██║   ██║  ██║███████╗",
    "╚═╝      ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚══════╝",
]
WORD_W = max(len(r) for r in WORDMARK)


def wordmark_lines(p):
    reset = p["R"]
    stops = grad(p["GRAD_TOP"], p["GRAD_BOT"], len(WORDMARK))
    return [stops[i] + WORDMARK[i] + reset for i in range(len(WORDMARK))]


def compact_wordmark(p):
    """One clean line — reads like you're invoking portal at a prompt. A dim ❯, then the
    name in a soft left→right warm fade. No blocks, no shadow, nothing stylized."""
    reset = p["R"]
    name = "portal"
    stops = grad(p["GRAD_TOP"], p["GRAD_BOT"], len(name))
    lit = "".join(stops[i] + name[i] for i in range(len(name))) + reset
    return "%s❯%s %s" % (p["D"], reset, lit)


def stats_line(p, stats):
    A, D, reset = p["A"], p["D"], p["R"]
    sess, proj, newest = (stats or ["", "", ""])[:3]
    if not sess:
        return "  %s◇ portal · step into any session%s" % (D, reset)
    sep = "%s · " % D
    return "  %s◇%s %s%s%s sessions%s%s%s projects%snewest %s%s%s" % (
        A, reset, A, sess, D, sep, A + proj, D, sep, A, newest, reset,
    )


def hints_line(p):
    A, D, reset = p["A"], p["D"], p["R"]

    def k(key, label):
        return "%s%s%s %s%s%s" % (A, key, reset, D, label, reset)

    return "  " + "  ".join([
        k("↵", "jump"), k("⇥", "mark"), k("^O", "→windows"), k("^N", "new"),
        k("^Y", "copy"), k("^/", "preview"), k("esc", "exit"),
    ])


def build(theme=None, width=0, stats=None, mode=None):
    p = palette(theme)
    eff = width  # banner is printed above fzf, so center against the full terminal
    if mode is None:
        mode = os.environ.get("PORTAL_BANNER", "compact").lower()

    # off: no wordmark at all, just the two info rows
    if mode in ("off", "none", "0"):
        lines = [stats_line(p, stats)[2:], hints_line(p)[2:]]
    # full: the 6-row ANSI Shadow wordmark (the site/README hero), for anyone who wants it
    elif mode == "full" and not (width and width < WORD_W + 4):
        lines = wordmark_lines(p) + [stats_line(p, stats)[2:], hints_line(p)[2:]]
    # compact (default): one clean line — small, no splash
    else:
        lines = [compact_wordmark(p), stats_line(p, stats)[2:], hints_line(p)[2:]]

    if eff:
        return "\n".join(center(ln, eff) for ln in lines)
    return "\n".join("  " + ln for ln in lines)


def _arg(flag, default=None):
    if flag in sys.argv:
        try:
            return sys.argv[sys.argv.index(flag) + 1]
        except IndexError:
            return default
    return default


if __name__ == "__main__":
    theme = _arg("--theme")
    try:
        width = int(_arg("--width", "0"))
    except ValueError:
        width = 0
    stats = None
    s = _arg("--stats")
    if s:
        stats = s.split("\t")
    sys.stdout.write(build(theme, width, stats, _arg("--mode")))
