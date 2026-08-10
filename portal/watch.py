#!/usr/bin/env python3
"""`portal watch` — the live agent view.

Every agent running right now: what each is doing, which needs you, how long it's been
going, tokens spent. Reads transcripts (agents.py) so it sees ALL live sessions, not
just ones portal launched.

`portal watch` (no args) opens the PICKER over the live fleet — the same fzf frame as
regular portal, auto-refreshing, ↵ jumps into the selected agent. This module feeds it:

    watch.py --rows         live agents as the picker's 10-field rows (one notify tick)
    watch.py --ticker       POST reload() to fzf's --listen port every 2s until it exits

and keeps the classic full-screen board for scripting and no-fzf machines:

    watch.py                the refreshing board; ^C exits
    watch.py --once         render one frame and exit (for tests / scripting)
    watch.py --interval N   refresh every N seconds (default 2)

When an agent flips to "waiting for you", a macOS notification fires (PORTAL_NOTIFY=1) so
you can walk away and get pulled back exactly when needed. If a model backend is configured
(narrate.py) the one-line action becomes plain English.
"""
import json
import os
import subprocess
import sys
import time

import agents
from palette import palette
from agents import STATES

_NOTIFY_STATES = {"waiting"}          # transitions worth interrupting you for


def _elapsed(age):
    if age < 60:
        return "%ds" % int(age)
    if age < 3600:
        return "%dm" % int(age // 60)
    return "%dh" % int(age // 3600)


def _human_tok(n):
    if n >= 1_000_000:
        return "%.1fM" % (n / 1e6)
    if n >= 1000:
        return "%dk" % round(n / 1000)
    return str(int(n)) if n else "—"


_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"   # braille spinner — a working agent visibly churns


def _clip(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _rule(label, p, width):
    """Section rule in the preview card's exact idiom: dim dashes, accent label."""
    D, A, R = p["D"], p["A"], p["R"]
    span = max(0, min(width - 4, 72) - len(label) - 4)
    return "  %s── %s%s %s%s%s" % (D, A, label, D, "─" * span, R)


def render(ags, p, width=88, narrator=None, frame=0):
    R, hi, ink, dim, faint = p["R"], p["G"], p["A"], p["D"], p["P"]
    CL, cream = p["CLAUDE"], p["C"]
    # urgency drives the ink weight; the project accent (pastel) carries the color
    weight = {"waiting": cream, "working": hi, "thinking": dim, "idle": faint}
    from palette import pastel
    import banner
    narrow = width < 78
    out = []
    n_wait = sum(1 for a in ags if a["state"] == "waiting")
    # the picker's compact wordmark, verbatim, with "watch" as the mode suffix —
    # one design language across every portal surface
    head = "%s %swatch%s" % (banner.compact_wordmark(p), hi, R)
    tail = "%s◇%s %s%d live%s" % (ink, R, ink, len(ags), R)
    if n_wait:
        tail += "%s · %s%d waiting on you%s" % (dim, cream, n_wait, R)
    out.append("  %s   %s" % (head, tail))
    out.append("")
    if not ags:
        out.append("  %s◇ no agents running — start one and it shows up here%s" % (dim, R))
        return "\n".join(out)
    pw = 12 if narrow else 14
    tw = max(12, (width - 40) if narrow else 34)   # task column; never negative on tiny panes
    # rows are sorted by urgency; chunk the fleet under preview-style rules so a big
    # board reads needs-you / active / idle at a glance
    _GROUPS = (("waiting", "needs you"), ("active", "active"), ("idle", "idle"))
    _LABEL = dict(_GROUPS)
    prev_g = None
    for a in ags:
        st = a["state"]
        g = "waiting" if st == "waiting" else ("idle" if st == "idle" else "active")
        if g != prev_g:
            out.append(_rule(_LABEL[g], p, width))
            prev_g = g
        w = weight.get(st, ink)
        # working agents get a live spinner; the rest their static state glyph
        glyph = _SPIN[frame % len(_SPIN)] if st == "working" else STATES.get(st, ("·", 9))[0]
        mark = ("%s✳%s" % (CL, R)) if a["agent"] == "claude" else ("%s❖%s" % (dim, R))
        proj = pastel(a["project"]) + _clip(a["project"], pw).ljust(pw) + R

        # WHAT it's working on (the task), in three tiers of fidelity:
        #   model narration (agentic prose) > the session's own title > the live tool
        # The live tool is the momentary "doing now"; the title/narration is the goal.
        narrated = narrator(a) if narrator else None
        task = narrated or a.get("title") or a["action"]
        tcol = faint if st == "idle" else ink   # idle rows recede
        cells = ["  %s%s%s" % (w, glyph, R), mark, proj,
                 "%s%-8s%s" % (w, st, R),
                 "%s%s%s" % (tcol, _clip(task, tw).ljust(tw), R)]
        if not narrow:
            # the momentary action as a dim detail, only when it adds something the task didn't
            tool = a["action"]
            hint = _clip(tool, 18) if (not narrated and tool not in ("thinking…", "waiting for you")) else ""
            cells.append("%s%-18s %4s %6s%s" % (
                faint, hint, _elapsed(a["age"]), _human_tok(a["tokens"]), R))
        out.append(" ".join(cells))
    out.append("")

    # key hints in the picker banner's exact idiom: accent key, dim label
    def k(key, label):
        return "%s%s%s %s%s%s" % (ink, key, R, dim, label, R)
    hints = [k("^C", "exit"), k("portal", "jump into one")]
    if sys.platform == "darwin":
        hints.append(k("⌘`", "cycle windows"))
    out.append("  " + "  ".join(hints))
    return "\n".join(out)


def _notify(title, message):
    """Desktop notification — OFF unless you opt in with PORTAL_NOTIFY=1, and always silent
    (no sound). The board is a terminal app; it shouldn't ping and chime by default."""
    if sys.platform != "darwin" or os.environ.get("PORTAL_NOTIFY", "") not in ("1", "true", "yes"):
        return
    # collapse whitespace: a newline in a project name would break the AppleScript
    # string literal and silently drop exactly that project's notification
    title = " ".join(str(title).split())
    message = " ".join(str(message).split())
    try:
        subprocess.run(
            ["osascript", "-e",
             "display notification %s with title %s" % (_asq(message), _asq(title))],
            capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def _asq(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def transitions(prev, cur):
    """Pure: given the previous and current {sid: agent} maps, return the notifications to
    fire this frame — an agent newly needing you, or one that was live and is now gone
    (finished). prev is None on the first frame (seed silently, no notifications)."""
    if prev is None:
        return []
    out = []
    for sid, a in cur.items():
        was = prev.get(sid, {}).get("state")
        if a["state"] in _NOTIFY_STATES and was not in _NOTIFY_STATES:
            out.append(("%s · waiting on you" % a["project"], a.get("action", "")))
    for sid, a in prev.items():
        # "done" only for an agent that vanished mid-activity. A waiting/idle session
        # leaving the board just aged past the live window — that's not news.
        if sid not in cur and a.get("state") in ("working", "thinking"):
            out.append(("%s · done" % a.get("project", sid[:8]), "session ended"))
    return out


def _statefile():
    return os.environ.get("PORTAL_WATCHSTATE",
                          os.path.join(os.path.expanduser("~"), ".claude", "portal", ".watchstate"))


def _tick_notify(ags):
    """One notification tick for the picker's live mode: diff current states against the
    sidecar from the previous tick, fire transitions, save. First tick seeds silently
    (same contract as the board loop's first frame)."""
    sf = _statefile()
    prev = None
    try:
        raw = json.load(open(sf))
        prev = {sid: {"state": v[0], "project": v[1]} for sid, v in raw.items()}
    except (OSError, ValueError):
        prev = None
    cur = {a["session_id"]: a for a in ags}
    for title, msg in transitions(prev, cur):
        _notify(title, msg)
    try:
        tmp = "%s.%d.tmp" % (sf, os.getpid())
        json.dump({sid: [a["state"], a["project"]] for sid, a in cur.items()}, open(tmp, "w"))
        os.replace(tmp, sf)
    except OSError:
        pass


def rows(ags, p):
    """Live agents as the picker's 10-field rows — same contract as rank.py emits, so the
    preview card, the resume dispatch (kind SESSION/CODEX), and ^O fan-out all just work."""
    from palette import pastel
    R, hi, ink, dim, faint = p["R"], p["G"], p["A"], p["D"], p["P"]
    CL, cream = p["CLAUDE"], p["C"]
    weight = {"waiting": cream, "working": hi, "thinking": dim, "idle": faint}
    out = []

    def field(s):
        return str(s).replace("\t", " ").replace("\n", " ").replace("\r", " ")

    for a in ags:
        st = a["state"]
        w = weight.get(st, ink)
        glyph = STATES.get(st, ("·", 9))[0]
        mark = ("%s✳%s" % (CL, R)) if a["agent"] == "claude" else ("%s❖%s" % (dim, R))
        proj = pastel(a["project"]) + _clip(a["project"], 14).ljust(14) + R
        task = a.get("title") or a["action"]
        tcol = faint if st == "idle" else ink
        display = "%s%s%s %s %s %s%-8s%s %s%s%s %s%4s %6s%s" % (
            w, glyph, R, mark, proj, w, st, R,
            tcol, _clip(task, 40).ljust(40), R,
            faint, _elapsed(a["age"]), _human_tok(a["tokens"]), R)
        kind = "CODEX" if a["agent"] == "codex" else "SESSION"
        sjson = json.dumps({"model_label": a.get("model_label", ""),
                            "in_tok": a.get("tokens", 0), "out_tok": 0})
        out.append("\t".join(field(x) for x in (
            display, a["cwd"], a["session_id"], kind, task, "",
            "", _elapsed(a["age"]) + " ago", a["project"], sjson)))
    return out


def rows_main():
    theme = os.environ.get("PORTAL_THEME", "dark")
    ags = agents.live_agents()
    _tick_notify(ags)
    sys.stdout.write("\n".join(rows(ags, palette(theme))) + ("\n" if ags else ""))
    return 0


def ticker():
    """Keep an open live picker fresh: POST reload(--rows) to fzf's --listen port every
    2s. fzf exports FZF_PORT to processes it spawns; when fzf exits the POST fails and
    we exit with it. stdlib-only — no curl, no shell quoting."""
    port = os.environ.get("FZF_PORT")
    if not port:
        return 0
    import shlex
    import urllib.request
    action = "reload(%s %s --rows)" % (shlex.quote(sys.executable),
                                       shlex.quote(os.path.abspath(__file__)))
    deadline = time.time() + 12 * 3600   # backstop: never outlive a workday-scale session
    while time.time() < deadline:
        time.sleep(2)
        try:
            req = urllib.request.Request("http://127.0.0.1:%s" % port,
                                         data=action.encode(), method="POST")
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            return 0
    return 0


def watch(interval=2.0, once=False, width=88):
    theme = os.environ.get("PORTAL_THEME", "dark")
    p = palette(theme)
    narrator = None
    try:
        import narrate
        narrator = narrate.narrator()   # None if no backend configured
    except Exception:
        narrator = None

    import shutil
    prev = None   # session_id -> state, for transition detection
    frame = 0
    while True:
        ags = agents.live_agents()
        # notify on transition into a "needs you" state / finish (seed silently first frame)
        cur = {a["session_id"]: a for a in ags}
        for title, msg in transitions(prev, cur):
            _notify(title, msg)
        prev = cur

        if not once:
            # re-measure every frame: the terminal can be resized mid-watch
            width = shutil.get_terminal_size((width, 24)).columns
            sys.stdout.write("\033[2J\033[H")   # clear + home
        sys.stdout.write(render(ags, p, width, narrator, frame) + "\n")
        sys.stdout.flush()
        if once:
            return 0
        frame += 1
        try:
            time.sleep(max(0.5, interval))
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            return 0


def main(argv):
    if "--rows" in argv:
        return rows_main()
    if "--ticker" in argv:
        return ticker()
    once = "--once" in argv
    interval = 2.0
    if "--interval" in argv:
        try:
            interval = float(argv[argv.index("--interval") + 1])
        except (IndexError, ValueError):
            pass
    import shutil
    width = shutil.get_terminal_size((88, 24)).columns  # zsh doesn't export COLUMNS
    try:
        return watch(interval=interval, once=once, width=width)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
