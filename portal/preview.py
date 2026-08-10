#!/usr/bin/env python3
"""portal preview — a high-signal session card: type, topic chips, the session's arc
(started → landed), key files touched, related cross-folder threads, and one compact
activity line. Signal over clutter.

Invoked as: python3 preview.py "{}"  ({} = highlighted tab-delimited row)
Fields: display cwd sid kind title last_worked branch reltime project statsjson
"""
import json
import os
import sys

from palette import grad, palette


_P = palette()
G, D, C, A, PK, R = _P["G"], _P["D"], _P["C"], _P["A"], _P["P"], _P["R"]
CL = _P["CLAUDE"]   # agent mark only — portal is otherwise monochrome
GTOP, GBOT = _P["GRAD_TOP"], _P["GRAD_BOT"]
HOME = os.path.expanduser("~")
W = 46


def rule(label):
    return "%s── %s%s %s%s%s" % (D, A, label, D, "─" * max(0, W - len(label) - 4), R)


def human(n):
    if n >= 1_000_000:
        return "%.1fM" % (n / 1e6)
    if n >= 10000:
        return "%dk" % round(n / 1000)
    if n >= 1000:
        return "%.1fk" % (n / 1000)
    return str(n)


def human_bytes(b):
    for u, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if b >= step:
            return "%.0f%s" % (b / step, u)
    return "%dB" % b


def human_dur(s):
    if s <= 0:
        return "—"
    m, s = divmod(int(s), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return "%dd" % d
    if h:
        return "%dh" % h
    return "%dm" % m if m else "%ds" % s


def clip(s, w):
    s = " ".join(str(s).split())
    return s if len(s) <= w else s[: w - 1] + "…"


def wrap(text, width, indent="  "):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return [indent + ln for ln in lines] or [indent]


def spark_gradient(s):
    if not s:
        return ""
    cols = grad(GTOP, GBOT, len(s))
    return "".join(cols[i] + ch for i, ch in enumerate(s)) + R


def main():
    line = sys.argv[1] if len(sys.argv) > 1 else ""
    f = line.rstrip("\n").split("\t")
    f += [""] * (10 - len(f))
    cwd, sid, kind, title, last_worked, branch, reltime, project, sjson = f[1:10]
    try:
        s = json.loads(sjson) if sjson else {}
    except Exception:
        s = {}

    out = [""]
    if kind == "NEW":
        out += ["  " + PK + "+ start fresh" + R, "", rule("new mission"),
                "  %sdir%s  %s%s%s" % (A, R, C, cwd.replace(HOME, "~"), R), "",
                "  " + A + "launches a new session here" + R, ""]
        print("\n".join(out))
        return

    # type label dropped: evaluated at 67-71% accuracy on hand labels (sessions are
    # mixed-mode) — a wrong tag 1/3 of the time is worse than none. Chips carry meaning.
    ttl = clip(title, W - 6)
    # the agent that wrote it, in its own color — portal's one chromatic accent
    amark = ("%s❖%s" % (D, R)) if s.get("agent") == "codex" else ("%s✳%s" % (CL, R))
    out.append("  %s %s▸ %s%s" % (amark, G, ttl, R))
    for ln in wrap(cwd.replace(HOME, "~"), W - 2):
        out.append(A + ln + R)   # full folder, top of the panel

    # live banner: if this session is running right now, say so and what it's doing —
    # so you don't double-open a live agent, and can spot the one waiting on you
    try:
        import agents
        live = agents.live_state_of(sid)
    except Exception:
        live = None
    if live:
        lst = live.get("state", "idle")
        gly = {"working": "●", "waiting": "◇", "thinking": "•", "idle": "·"}.get(lst, "·")
        lab = {"working": "working now", "waiting": "waiting on you",
               "thinking": "thinking", "idle": "idle"}.get(lst, lst)
        col = C if lst == "waiting" else G       # waiting pops in the brightest warm
        out.append("  %s%s %s%s  %s%s%s" % (col, gly, lab, R, D, clip(live.get("action", ""), W - 18), R))
    topics = s.get("topics", [])[:3]
    if topics:
        out.append("  " + D + " ".join("#" + t for t in topics) + R)
    out.append("")

    # arc: started -> landed
    out.append(rule("arc"))
    first = s.get("first", "")
    if first:
        for ln in wrap("↗ " + first, W - 2):
            out.append(A + ln + R)
    for ln in wrap("landed  " + (last_worked or "—"), W - 2):
        out.append(C + ln + R)
    out.append("")

    # key files touched
    files = s.get("files", [])
    if files:
        out.append(rule("touched"))
        for ln in wrap(" · ".join(files[:6]), W - 2):
            out.append(C + ln + R)
        out.append("")

    # related cross-folder threads
    related = s.get("related", [])
    if related:
        out.append(rule("related"))
        names = " · ".join(clip(t, 22) for _, t in related)
        out.append("  " + A + "↔ " + R + C + names + R)
        out.append("")

    # commits created during this session (git log over the session's time-span)
    mt = s.get("mtime", 0)
    if mt and os.path.isdir(cwd):
        from mobility import commits_during
        commits = commits_during(cwd, mt - s.get("dur", 0) - 600, mt + 600)
        if commits:
            out.append(rule("commits"))
            for c in commits[:4]:
                out.append("  " + C + clip(c, W - 2) + R)
            if len(commits) > 4:
                out.append("  " + D + "… +%d more" % (len(commits) - 4) + R)
            out.append("")

    # one compact activity line + gradient sparkline
    out.append(rule("activity"))
    parts = [human_dur(s.get("dur", 0)), human_bytes(s.get("size", 0)),
             "~%s msgs" % human(s.get("msgs", 0)), "~%s tools" % human(s.get("tools", 0))]
    toks = (s.get("in_tok", 0) or 0) + (s.get("out_tok", 0) or 0)
    if toks:
        parts.append("~%s tok" % human(toks))
    out.append("  " + C + (" %s·%s " % (D, C)).join(parts) + R)
    if s.get("spark"):
        out.append("  " + spark_gradient(s["spark"]))
    out.append("")

    # dim metadata strip (folder now lives at the top) — [codex] · model · branch · sid
    _agent = ["codex"] if s.get("agent") == "codex" else []
    meta = " · ".join(_agent + [s.get("model_label") or "unknown", branch or "—", sid[:8]])
    out.append("  " + D + clip(meta, W) + R)
    out.append("")
    print("\n".join(out))


if __name__ == "__main__":
    main()
