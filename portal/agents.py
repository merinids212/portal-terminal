#!/usr/bin/env python3
"""Live-agent data layer for `portal watch` and the launch nudge.

Two jobs:
One job: live_agents() — find sessions being written right now and, from a bounded tail
read of each transcript, classify what the agent is doing this second: working / thinking /
waiting (on you) / idle, plus its current action. Metadata (project, title, tokens)
is joined from portal's existing index; only the volatile state is read fresh.

Reading a live 200MB transcript means tail-only, always — never a full read.
"""
import json
import os
import re
import time

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
CODEX = os.path.join(HOME, ".codex", "sessions")

# state → (glyph, rank) — rank orders the board; lower = more urgent (needs you first)
STATES = {
    "waiting": ("◇", 0),   # turn ended, awaiting your input — the one that needs you
    "working": ("●", 1),   # a tool is running right now
    "thinking": ("•", 2),  # mid-turn, model composing / tool just returned
    "idle": ("·", 3),      # no writes for a while, but recent enough to still be open
}
# How far back a transcript can have last been written and still count as an open agent.
# Generous by default: an agent paused mid-task (waiting on you, or a slow tool) can be quiet
# for minutes and is still "live". Tune with PORTAL_WATCH_WINDOW (minutes). Active agents sort
# to the top; quiet-but-open ones show dimmed as idle rather than vanishing.
IDLE_AFTER = 75            # seconds of quiet → idle rather than working/thinking
_SKIP = ("/subagents/", "/private/tmp", "/bundled-skills", "-scratchpad",
         "/projects/-private-tmp-", "/projects/-tmp-")  # the dir-name spelling of tmp cwds


# transcript content reaches the terminal (board rows, notifications): strip control
# bytes so an embedded ANSI/OSC sequence in a tool result can't repaint the display
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def sane(s):
    return _CTRL_RE.sub(" ", str(s or ""))


def LIVE_WINDOW():
    try:
        return max(60, int(float(os.environ.get("PORTAL_WATCH_WINDOW", "45")) * 60))
    except ValueError:
        return 2700


def read_tail(path, want_lines=40, cap=4_000_000):
    """The last `want_lines` COMPLETE lines. Reads backward in chunks until it has enough
    newlines or hits `cap` — a single transcript line can be megabytes (a big tool result or
    an inlined image), and a fixed-size tail can land entirely inside one, yielding nothing."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        data = b""
        pos = size
        step = 65536
        while pos > 0 and data.count(b"\n") <= want_lines and (size - pos) < cap:
            back = min(step, pos)
            pos -= back
            f.seek(pos)
            data = f.read(back) + data
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-want_lines:]


def _content_types(obj):
    m = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    if not m:
        # codex rollout shape: {"type":"response_item","payload":{...}} — map its turns
        # onto the same (role, kinds) vocabulary, or codex agents can never be "waiting"
        pl = obj.get("payload")
        if isinstance(pl, dict):
            t = pl.get("type")
            if t == "message":
                m = pl
            elif t == "function_call":
                name = pl.get("name", "tool")
                arg = str(pl.get("arguments") or "").split("\n")[0][:46]
                return "assistant", ["tool_use"], [{"type": "tool_use", "name": name,
                                                    "input": {"description": arg}}]
            elif t == "function_call_output":
                return "user", ["tool_result"], []
    c = m.get("content")
    role = m.get("role") or obj.get("type")
    if isinstance(c, str) and c.strip():
        # real transcripts carry most typed prompts as plain strings, not lists —
        # they are turn content, not meta rows, or classify() misses your reply
        # and keeps a freshly-answered agent stuck on "waiting on you"
        return role, ["text"], []
    kinds = [x.get("type") for x in c if isinstance(x, dict)] if isinstance(c, list) else []
    return role, kinds, (c if isinstance(c, list) else [])


def _last_tool(content):
    for x in reversed(content):
        if isinstance(x, dict) and x.get("type") == "tool_use":
            name = x.get("name", "tool")
            inp = x.get("input") or {}
            hint = inp.get("file_path") or inp.get("command") or inp.get("path") or \
                inp.get("pattern") or inp.get("description") or ""
            hint = str(hint).replace(HOME, "~").split("\n")[0][:46]
            return ("%s %s" % (name, hint)).strip() if hint else name
    return None


def classify(lines, age):
    """(state, action) from a transcript tail — Claude Code or codex rollout shape."""
    events = []
    for ln in lines[-40:]:
        ln = ln.strip()
        if not ln or ln[0] != "{":
            continue
        try:
            events.append(json.loads(ln))
        except ValueError:
            continue
    # find the last real user/assistant turn (skip meta rows: summaries, titles, hooks)
    last = None
    for o in reversed(events):
        role, kinds, content = _content_types(o)
        if role in ("user", "assistant") and kinds:
            last = (role, kinds, content)
            break
    if last is None:
        return ("idle", "idle") if age >= IDLE_AFTER else ("working", "starting…")
    role, kinds, content = last
    if age >= IDLE_AFTER:
        # quiet a while: if it ended on assistant text it's genuinely waiting; else just idle
        if role == "assistant" and "tool_use" not in kinds:
            return ("waiting", "waiting for you")
        return ("idle", "idle")
    if role == "assistant" and "tool_use" in kinds:
        return ("working", _last_tool(content) or "working")
    if role == "user" and "tool_result" in kinds:
        return ("thinking", "thinking…")
    if role == "assistant":                     # text, no tool → turn handed back
        return ("waiting", "waiting for you")
    if role == "user":                          # a fresh prompt, no reply yet
        return ("thinking", "reading your message…")
    return ("working", "working")


def live_state(path, now=None):
    now = now or time.time()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    age = now - mtime
    try:
        state, action = classify(read_tail(path), age)
    except OSError:
        return None
    return {"state": state, "action": action, "mtime": mtime, "age": age}


def find_transcript(sid):
    """Path to a session's transcript by id (Claude), or None. Cheap — a stat per project
    dir, no directory walk, no index. Used by the picker preview on highlight."""
    if os.path.isdir(PROJECTS):
        for proj in os.scandir(PROJECTS):
            if proj.is_dir():
                p = os.path.join(proj.path, sid + ".jsonl")
                if os.path.isfile(p):
                    return p
    return None


def live_state_of(sid, now=None):
    """live_state for a session id, but only if it's actually live right now (else None).
    Lets the picker preview flag a running session without importing the index."""
    path = find_transcript(sid)
    if not path:
        return None
    st = live_state(path, now)
    if not st or st["age"] > LIVE_WINDOW():
        return None
    return st


def _recent_transcripts(max_age, now):
    """(path, session_id, agent) for transcripts written within max_age. Authoritative and
    fresh — a straight filesystem scan, so a just-started session shows up immediately (the
    index pickle can lag). Skips the real noise: subagent transcripts, scratch/tmp, skills."""
    out = []
    if os.path.isdir(PROJECTS):
        for proj in os.scandir(PROJECTS):
            if not proj.is_dir():
                continue
            try:
                for e in os.scandir(proj.path):
                    if not e.name.endswith(".jsonl"):
                        continue
                    if any(s in e.path for s in _SKIP):
                        continue
                    try:
                        if (now - e.stat().st_mtime) <= max_age:
                            out.append((e.path, e.name[:-6], "claude"))
                    except OSError:
                        continue
            except OSError:
                continue
    if os.path.isdir(CODEX):                       # codex rollouts, nested by date
        for root, _dirs, files in os.walk(CODEX):
            for fn in files:
                if not (fn.startswith("rollout-") and fn.endswith(".jsonl")):
                    continue
                p = os.path.join(root, fn)
                try:
                    if (now - os.path.getmtime(p)) <= max_age:
                        # codex filename: rollout-<ts>-<uuid>.jsonl → pull the trailing uuid
                        mm = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", fn)
                        out.append((p, mm.group(1) if mm else fn[:-6], "codex"))
                except OSError:
                    continue
    return out


_CODEX_HEAD_MEMO = {}


def _codex_head(path, max_lines=40, max_bytes=65536):
    """(cwd, first-user-text) from a codex rollout head. Bounded (per-line byte cap, so a
    giant pasted line can't blow memory). Needed for rollouts too new to be indexed —
    without it a narrator-spawned `codex exec` rollout has no title, slips past the
    sentinel filter, shows as a phantom agent, and gets narrated in turn (a self-loop)."""
    hit = _CODEX_HEAD_MEMO.get(path)
    if hit:
        return hit
    cwd, text = "", ""
    try:
        with open(path, "r", errors="replace") as f:
            for _ in range(max_lines):
                ln = f.readline(max_bytes)
                if not ln:
                    break
                if not ln.startswith("{"):
                    continue
                try:
                    o = json.loads(ln)
                except ValueError:
                    continue
                pl = o.get("payload")
                if not isinstance(pl, dict):
                    continue
                if not cwd and pl.get("cwd"):
                    cwd = pl["cwd"]
                if not text and pl.get("type") == "message" and pl.get("role") == "user":
                    for x in pl.get("content") or []:
                        if isinstance(x, dict) and (x.get("text") or "").strip():
                            text = " ".join(x["text"].split())[:120]
                            break
                if cwd and text:
                    break
    except OSError:
        pass
    if cwd:                      # head fields never change once written — memo them,
        _CODEX_HEAD_MEMO[path] = (cwd, text)   # but never a still-empty head
    return cwd, text


_META_MEMO = {}   # path -> (at, result): the fallback parse below is bounded but not
                  # free, and watch calls it every 2s frame until the session is indexed


def _meta(path, sid, agent, index_rows):
    """Project/title/tokens for a transcript. Prefer the index (topics, tokens); for a
    session too new to be indexed, fall back to a bounded parse of the file so it still
    shows (and so the sentinel filter can see narration sessions of either agent).
    The fallback is memoized for a minute — title/cwd don't churn mid-session."""
    r = index_rows.get(sid)
    if r:
        stats = r.get("stats") if isinstance(r.get("stats"), dict) else {}
        return (r.get("cwd", ""), r.get("title", ""), r.get("project"),
                stats.get("model_label", ""),
                (stats.get("in_tok", 0) or 0) + (stats.get("out_tok", 0) or 0))
    hit = _META_MEMO.get(path)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    res = ("", "", sid[:8], "", 0)
    if agent == "claude":
        try:
            import index
            info = index.parse_session(path, os.path.getsize(path))
            cwd = info.get("cwd", "")
            res = (cwd, info.get("title", ""), os.path.basename(cwd) if cwd else sid[:8],
                   "", (info.get("in_tok", 0) or 0) + (info.get("out_tok", 0) or 0))
        except Exception:
            pass
    elif agent == "codex":
        cwd, text = _codex_head(path)
        res = (cwd, text, os.path.basename(cwd) if cwd else sid[:8], "", 0)
    _META_MEMO[path] = (time.time(), res)
    return res


_PROC_CACHE = [0.0, frozenset()]   # [stamped_at, cwds] — ps+lsof is not free, cache ~15s


def _proc_cwds(now=None):
    """cwds of running claude/codex processes — the ground truth for "open". A tab can sit
    quiet for hours with its agent still alive; transcript mtime alone would age it off the
    board. ONE ps + ONE batched lsof (per-pid lsof calls stack up badly on a big fleet),
    cached; empty set when unavailable (then the window rules). PORTAL_NO_PROC=1 disables
    the scan entirely — the hermetic escape hatch for tests and constrained machines."""
    if os.environ.get("PORTAL_NO_PROC"):
        return frozenset()
    now = now or time.time()
    if now - _PROC_CACHE[0] < 15:
        return _PROC_CACHE[1]
    cwds = set()
    try:
        import subprocess
        ps = subprocess.run(["ps", "-axo", "pid=,comm="],
                            capture_output=True, text=True, timeout=5)
        pids = []
        for ln in ps.stdout.splitlines():
            parts = ln.strip().split(None, 1)
            if len(parts) != 2:
                continue
            base = os.path.basename(parts[1])
            if base in ("claude", "codex") or base.startswith(("claude-", "codex-")):
                pids.append(parts[0])
        if pids:
            lf = subprocess.run(["lsof", "-a", "-p", ",".join(pids[:64]), "-d", "cwd", "-Fn"],
                                capture_output=True, text=True, timeout=10)
            for ln in lf.stdout.splitlines():
                if ln.startswith("n/"):
                    cwds.add(ln[1:])
    except Exception:
        pass
    _PROC_CACHE[0] = now
    _PROC_CACHE[1] = frozenset(cwds)
    return _PROC_CACHE[1]


def _newest_claude_for(cwd):
    """(path, sid) of the newest transcript in cwd's project dir, any age."""
    d = os.path.join(PROJECTS, re.sub(r"[^A-Za-z0-9]", "-", str(cwd).rstrip("/")))
    best = None
    try:
        for e in os.scandir(d):
            if not e.name.endswith(".jsonl") or any(s in e.path for s in _SKIP):
                continue
            try:
                mt = e.stat().st_mtime
            except OSError:
                continue
            if best is None or mt > best[0]:
                best = (mt, e.path, e.name[:-6])
    except OSError:
        return None
    return (best[1], best[2]) if best else None


def _newest_codex_for(cwd, days=7):
    """(path, sid) of the newest rollout whose head names this cwd — last `days` only,
    head reads memoized (cwd is a head field; it never changes for a file)."""
    if not os.path.isdir(CODEX):
        return None
    cutoff = time.time() - days * 86400
    best = None
    for root, _dirs, files in os.walk(CODEX):
        for fn in files:
            if not (fn.startswith("rollout-") and fn.endswith(".jsonl")):
                continue
            p = os.path.join(root, fn)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt < cutoff or (best and mt <= best[0]):
                continue
            if _codex_head(p)[0] == cwd:
                mm = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", fn)
                best = (mt, p, mm.group(1) if mm else fn[:-6])
    return (best[1], best[2]) if best else None


def _row(path, sid, agent, now, idx, seen):
    """One board row, or None (dupe, unreadable, or portal's own narration session)."""
    if not sid or sid in seen:
        return None
    st = live_state(path, now)
    if not st:
        return None
    cwd, title, project, model_label, tokens = _meta(path, sid, agent, idx)
    # hide portal's own narration subprocesses — the sentinel, or (pre-sentinel ones) the
    # unmistakable signature of the narration system prompt
    tl = (title or "").lower()
    if "[portal-status]" in tl or "label a coding agent" in tl:
        return None
    seen.add(sid)
    return {
        "session_id": sid, "agent": agent, "path": path, "cwd": cwd,
        "project": sane(project or (os.path.basename(cwd) if cwd else sid[:8])),
        "title": sane(title), "model_label": model_label, "tokens": tokens,
        "state": st["state"], "action": sane(st["action"]), "age": st["age"], "mtime": st["mtime"],
    }


def live_agents(max_age=None, now=None):
    """Every open agent and its current state, most-urgent first. Recently-written
    transcripts are found by filesystem scan; on top of that, any cwd with a running
    claude/codex process keeps its newest session on the board no matter how long the
    transcript has been quiet — an open tab is an open agent. Deduped by session id;
    scratch/subagent/narration sessions excluded."""
    now = now or time.time()
    if max_age is None:
        max_age = LIVE_WINDOW()
    idx = {}
    try:
        import rank
        rows, _ = rank.get_index()
        idx = {r["session_id"]: r for r in rows}
    except Exception:
        pass
    agents, seen = [], set()
    for path, sid, agent in _recent_transcripts(max_age, now):
        r = _row(path, sid, agent, now, idx, seen)
        if r:
            agents.append(r)
    have_cwds = {a["cwd"] for a in agents if a["cwd"]}
    for cwd in _proc_cwds(now):
        if cwd in have_cwds:
            continue
        cands = [c + (kind,) for c, kind in
                 ((_newest_claude_for(cwd), "claude"), (_newest_codex_for(cwd), "codex")) if c]
        if not cands:
            continue

        def _mt(c):
            try:
                return os.path.getmtime(c[0])
            except OSError:
                return 0
        path, sid, agent = max(cands, key=_mt)
        r = _row(path, sid, agent, now, idx, seen)
        if r:
            agents.append(r)
    agents.sort(key=lambda a: (STATES.get(a["state"], ("", 9))[1], a["age"]))
    return agents


def nudge_line():
    """One-line summary for the picker header, or "" when nothing is live. Names the projects
    waiting on you — a count tells you to look, a name tells you where."""
    ags = live_agents()
    if not ags:
        return ""
    n = len(ags)
    live = "%d %s live" % (n, "agent" if n == 1 else "agents")
    waiting = list(dict.fromkeys(a["project"] for a in ags if a["state"] == "waiting"))
    if not waiting:
        return "◇ %s — portal watch" % live
    if len(waiting) == 1:
        return "◇ %s is waiting on you · %s — portal watch" % (waiting[0], live)
    shown = ", ".join(waiting[:3]) + (" +%d" % (len(waiting) - 3) if len(waiting) > 3 else "")
    return "◇ %d waiting on you: %s · %s — portal watch" % (len(waiting), shown, live)


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["--json"]:
        print(json.dumps(live_agents(), indent=2, default=str))
    elif sys.argv[1:2] == ["--nudge"]:
        sys.stdout.write(nudge_line())
    else:
        ags = live_agents()
        if not ags:
            print("no agents live")
        for a in ags:
            g = STATES.get(a["state"], ("·", 9))[0]
            print("%s %-16s %-9s %s" % (g, a["project"][:16], a["state"], a["action"]))
