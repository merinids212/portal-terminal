#!/usr/bin/env python3
"""Detect whether the terminal has a light or dark background. Prints "light"/"dark".

  1. $COLORFGBG (iTerm2, Konsole, rxvt): "fg;bg", bg 7/15 or high = light.
  2. OSC 11 query to the tty: read back the background color, compute luminance.
  3. Fall back to "dark".
"""
import os
import re
import sys

LIGHT_BG_INDEXES = {7, 15}


def from_colorfgbg():
    val = os.environ.get("COLORFGBG", "")
    if not val or ";" not in val:
        return None
    bg = val.split(";")[-1].strip()
    if not bg.isdigit():
        return None
    n = int(bg)
    return "light" if (n in LIGHT_BG_INDEXES or n >= 10) else "dark"


def from_osc11(timeout=0.15):
    try:
        import select
        import termios
        import tty
    except Exception:
        return None
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    old = None
    try:
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        os.write(fd, b"\033]11;?\033\\")
        buf = b""
        while len(buf) < 128:
            r, _, _ = select.select([fd], [], [], timeout)
            if not r:
                break
            chunk = os.read(fd, 64)
            if not chunk:
                break
            buf += chunk
            if b"\a" in buf or b"\033\\" in buf:
                break
    except Exception:
        return None
    finally:
        if old is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass
        os.close(fd)

    m = re.search(rb"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", buf)
    if not m:
        return None

    def comp(h):
        h = h.decode()
        scale = (1 << (4 * len(h))) - 1
        return int(h, 16) / scale if scale else 0.0

    r, g, b = comp(m.group(1)), comp(m.group(2)), comp(m.group(3))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "light" if lum > 0.5 else "dark"


def detect():
    return from_colorfgbg() or from_osc11() or "dark"


if __name__ == "__main__":
    sys.stdout.write(detect())
