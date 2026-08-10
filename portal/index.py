#!/usr/bin/env python3
"""portal session indexer.

Scans ~/.claude/projects for real Claude Code sessions and emits tab-delimited
rows for the `portal` fzf picker, most-recently-worked-on first. Bounded head+tail
reads, cached by (mtime, size) in ~/.claude/portal/cache.json.

Row: display  cwd  session_id  kind  title  last_worked  branch  reltime  project
"""
import calendar
import json
import os
import re
import sys
import time

from palette import palette, lerp
from core import clean_user, classify_type, model_label

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
CACHE = os.path.join(HOME, ".claude", "portal", "cache.json")
CODE_ROOT = os.environ.get("PORTAL_CODE_ROOT", os.path.join(HOME, "Desktop", "code"))

CACHE_VERSION = 8  # bump to invalidate cached rows when parse output changes
HEAD_LINES = 150
TAIL_BYTES = 200_000
TITLE_W = 44  # fixed title column so the project column aligns

SAMPLES = 24        # windows sampled across a file for the activity sparkline
SAMPLE_WIN = 6144   # bytes per sampled window
_SPARK = "▁▂▃▄▅▆▇█"
_TS = re.compile(r'"timestamp":"([^"]+)"')
_RE_INTOK = re.compile(rb'"input_tokens":(\d+)')     # usage token sums (bytes; buf is raw)
_RE_OUTTOK = re.compile(rb'"output_tokens":(\d+)')
_RE_MODEL = re.compile(r'"model":"([^"]+)"')

_P = palette()
G, D, C, A, PK, R = _P["G"], _P["D"], _P["C"], _P["A"], _P["P"], _P["R"]
_TOP, _BOT = _P["GRAD_TOP"], _P["GRAD_BOT"]

SKIP_SUBSTR = ("/subagents/", "/private/tmp", "/bundled-skills", "-scratchpad")
SENTINEL = "[portal-status]"   # stamps narrate.py's own model sessions — machine noise


def is_machinery(info):
    """True for portal's own narration subprocesses (claude -p / codex exec transcripts).
    Same class as subagent transcripts in SKIP_SUBSTR: not a session the user had."""
    hay = (info.get("title", "") or "") + " " + (info.get("first", "") or "")
    return SENTINEL in hay or "label a coding agent" in hay.lower()


def recency_color(age_secs):
    """Grade by age: fresh -> bright white, old -> deep grey (over ~30 days)."""
    t = min(1.0, max(0.0, age_secs / (86400 * 30)))
    return lerp(_TOP, _BOT, t)


def _junq(v):
    r"""Regex-extracted JSON string values may carry \uXXXX escapes - decode them."""
    if "\\" not in v:
        return v
    try:
        return json.loads('"%s"' % v)
    except ValueError:
        return v


def load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, CACHE)


HEAD_LINE_CAP = 128 * 1024   # per-line byte cap: a pasted-blob line can be many MB
HEAD_BYTE_BUDGET = 4 << 20   # total head-read budget, keeps worst case bounded


def read_head(path, n):
    """First n lines, byte-bounded. An oversized line arrives truncated (its json parse
    then fails and callers already skip unparseable lines) instead of being fully
    materialized — 'bounded head read' must hold in bytes, not just line count."""
    out, spent = [], 0
    with open(path, "r", errors="replace") as f:
        for _ in range(n):
            line = f.readline(HEAD_LINE_CAP)
            if not line:
                break
            spent += len(line)
            out.append(line)
            if spent >= HEAD_BYTE_BUDGET:
                break
    return out


def read_tail(path, nbytes):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > nbytes:
            f.seek(-nbytes, os.SEEK_END)
            f.readline()
        data = f.read()
    return data.decode("utf-8", errors="replace").splitlines()


def user_text(o):
    m = o.get("message") or {}
    if m.get("role") != "user":
        return None
    c = m.get("content")
    if isinstance(c, str):
        t = c
    elif isinstance(c, list):
        t = "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    else:
        return None
    return clean_user(t)  # recover prose from command-wrapped prompts (core.py)


def _epoch(s):
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def sparkline(vals):
    if not vals:
        return ""
    hi = max(vals)
    if hi <= 0:
        return _SPARK[0] * len(vals)
    return "".join(_SPARK[min(7, round(v / hi * 7))] for v in vals)


def sample_activity(path, size):
    """Sample SAMPLES windows across the file for an activity sparkline + rough counts.
    Constant work (~SAMPLES*SAMPLE_WIN bytes) regardless of file size."""
    dens, marks_tot, tools, edits, bytes_tot = [], 0, 0, 0, 0
    in_tok = out_tok = 0
    try:
        with open(path, "rb") as f:
            for i in range(SAMPLES):
                pos = min(int(i * size / SAMPLES), max(0, size - 1))
                f.seek(pos)
                if pos:  # align to a line boundary (capped: lines can be huge) — offset 0 IS one
                    f.readline(64 * 1024)
                buf = f.read(SAMPLE_WIN)
                m = buf.count(b'"type":"assistant"') + buf.count(b'"type":"user"')
                dens.append(m)
                marks_tot += m
                bytes_tot += len(buf)
                tools += buf.count(b'"type":"tool_use"')
                edits += buf.count(b'"name":"Edit"') + buf.count(b'"name":"Write"') + buf.count(b'"name":"MultiEdit"')
                # sum usage tokens seen in this window; scaled below like msgs/tools/edits
                for t in _RE_INTOK.findall(buf):
                    in_tok += int(t)
                for t in _RE_OUTTOK.findall(buf):
                    out_tok += int(t)
    except OSError:
        return {"spark": "", "msgs": 0, "tools": 0, "edits": 0, "in_tok": 0, "out_tok": 0}
    scale = size / bytes_tot if bytes_tot else 0
    return {
        "spark": sparkline(dens),
        "msgs": int(marks_tot * scale),
        "tools": int(tools * scale),
        "edits": int(edits * scale),
        "in_tok": int(in_tok * scale),   # approx: sampled + scaled (files reach 200MB)
        "out_tok": int(out_tok * scale),
    }


def parse_session(path, size=0):
    head = read_head(path, HEAD_LINES)
    tail = read_tail(path, TAIL_BYTES)
    cwd = branch = first_prompt = title = None
    model = None
    first_ts = last_ts = None

    def take_model(line):
        nonlocal model
        m = _RE_MODEL.search(line)
        if m:
            v = _junq(m.group(1))
            if v and not v.startswith("<"):   # skip "<synthetic>"; last real value wins
                model = v
    prompts = []  # cleaned user prompts -> the searchable index text
    files = []    # distinct edited-file basenames (a few), for "touched"

    def add_prompt(u):
        if u and len(u) > 3 and u not in prompts:
            prompts.append(u)

    def add_files(line):
        for fp in re.findall(r'"file_path":"([^"]+)"', line):
            b = os.path.basename(_junq(fp))
            if b and b not in files:
                files.append(b)

    for line in head:
        if cwd is None:
            m = re.search(r'"cwd":"([^"]+)"', line)
            if m:
                cwd = _junq(m.group(1))
        if branch is None:
            m = re.search(r'"gitBranch":"([^"]*)"', line)
            if m:
                branch = _junq(m.group(1))
        m = re.search(r'"aiTitle":"([^"]+)"', line)
        if m:
            title = _junq(m.group(1))
        if first_ts is None:
            m = _TS.search(line)
            if m:
                first_ts = _epoch(m.group(1))
        add_files(line)
        take_model(line)
        try:
            u = user_text(json.loads(line))
        except Exception:
            u = None
        if u:
            if first_prompt is None:
                first_prompt = u
            add_prompt(u)

    last_prompt = last_file = None
    for line in tail:
        m = re.search(r'"aiTitle":"([^"]+)"', line)
        if m:
            title = _junq(m.group(1))
        m = re.findall(r'"file_path":"([^"]+)"', line)
        if m:
            last_file = _junq(m[-1])
        add_files(line)
        take_model(line)
        if branch is None:
            m = re.search(r'"gitBranch":"([^"]*)"', line)
            if m:
                branch = _junq(m.group(1))
        if cwd is None:
            m = re.search(r'"cwd":"([^"]+)"', line)
            if m:
                cwd = _junq(m.group(1))
        m = _TS.search(line)
        if m:
            e = _epoch(m.group(1))
            if e:
                last_ts = e
        try:
            u = user_text(json.loads(line))
        except Exception:
            u = None
        if u:
            last_prompt = u
            add_prompt(u)

    if last_file:
        last_worked = "edited " + last_file.replace(HOME, "~")
    elif last_prompt:
        last_worked = last_prompt
    else:
        last_worked = first_prompt or ""

    if not title:
        title = (first_prompt or "untitled")[:60]

    dur = (last_ts - first_ts) if (first_ts and last_ts and last_ts >= first_ts) else 0
    act = sample_activity(path, size) if size else {"spark": "", "msgs": 0, "tools": 0, "edits": 0}
    search = " ".join(prompts[:24])[:1600]  # searchable index text
    stype = classify_type(title, search, act.get("edits", 0))

    return {
        "cwd": cwd or "", "branch": branch or "", "title": title,
        "last_worked": last_worked[:200], "search": search,
        "first": (first_prompt or "")[:160], "files": files[:6], "type": stype,
        "model": model, "size": size, "dur": dur, **act,
    }


def reltime(ts):
    d = max(0, time.time() - ts)
    if d < 90:
        return "now"
    if d < 3600:
        return "%dm" % (d // 60)
    if d < 86400:
        return "%dh" % (d // 3600)
    if d < 86400 * 7:
        return "%dd" % (d // 86400)
    return "%dw" % (d // (86400 * 7))


def gather_sessions(cache):
    rows, new_cache = [], {}
    # no ~/.claude/projects is NOT "no sessions": codex-only machines still have
    # rollouts to index — only the Claude scan is skipped, never the codex union
    for proj in (os.listdir(PROJECTS) if os.path.isdir(PROJECTS) else []):
        pdir = os.path.join(PROJECTS, proj)
        if not os.path.isdir(pdir):
            continue
        try:
            entries = os.listdir(pdir)
        except OSError:
            continue
        for name in entries:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(pdir, name)
            if any(s in path for s in SKIP_SUBSTR):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            # nanosecond mtime: a same-size rewrite within one second must still invalidate
            sig = [CACHE_VERSION, st.st_mtime_ns, st.st_size]
            cached = cache.get(path)
            if cached and cached.get("sig") == sig:
                info = cached
            else:
                try:
                    info = {"sig": sig, **parse_session(path, st.st_size)}
                except Exception:
                    # a transcript we can't parse must not silently vanish — surface it
                    # (cwd recovered from the dir name below when possible)
                    info = {"sig": sig, "cwd": "", "title": "(unreadable transcript)",
                            "last_worked": "", "branch": "", "search": "", "type": "·"}
            if not info.get("cwd"):
                info = dict(info, cwd=_decode_projdir(proj))
            if not info.get("cwd") or any(s in info["cwd"] for s in SKIP_SUBSTR):
                # cwd unknown: keep it out of the cache too, so cache and index never
                # disagree (the no-loss invariant is cache ⊆ index) — retried next scan
                continue
            if is_machinery(info):
                continue           # narration subprocess transcripts are not sessions
            new_cache[path] = info
            rows.append({
                "mtime": st.st_mtime, "cwd": info["cwd"], "session_id": name[:-6],
                "kind": "SESSION", "title": info["title"], "last_worked": info["last_worked"],
                "branch": info["branch"], "project": os.path.basename(info["cwd"]) or info["cwd"],
                "search": info.get("search", ""), "type": info.get("type", "·"),
                "stats": {
                    "size": info.get("size", 0), "dur": info.get("dur", 0),
                    "msgs": info.get("msgs", 0), "tools": info.get("tools", 0),
                    "edits": info.get("edits", 0), "spark": info.get("spark", ""),
                    "first": info.get("first", ""), "files": info.get("files", []),
                    "model": info.get("model"), "model_label": model_label(info.get("model")),
                    "in_tok": info.get("in_tok", 0), "out_tok": info.get("out_tok", 0),
                },
            })
    try:                       # union Codex sessions (~/.codex/sessions) when present
        import codex
        crows, cupdates = codex.gather_codex(cache)
        rows.extend(crows)
        new_cache.update(cupdates)
    except Exception:
        pass
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows, new_cache


def _decode_projdir(name):
    """Best-effort inverse of the '-' encoding for a projects dir name. The encoding is
    lossy (a dash may be a path separator OR a literal '-'), so enumerate the on-disk
    interpretations — pruned by isdir, so this stays cheap — and trust the decode only
    when exactly ONE exists. Ambiguity returns "": a wrong-but-existing cwd would cd
    the user into the wrong project and poison the cache."""
    if not name.startswith("-"):
        return ""
    hits = []

    def walk(prefix, rest):
        if len(hits) > 1:
            return
        i = rest.find("-")
        if i < 0:
            full = os.path.normpath(prefix + rest)
            if os.path.isdir(full) and full not in hits:
                hits.append(full)
            return
        head, tail = rest[:i], rest[i + 1:]
        sep = prefix + head
        if os.path.isdir(sep):          # this dash as a path separator
            walk(sep + "/", tail)
        walk(prefix + head + "-", tail)  # this dash as a literal character

    walk("/", name[1:])
    return hits[0] if len(hits) == 1 else ""


def _looks_like_project(d):
    try:
        entries = os.listdir(d)
    except OSError:
        return False
    if ".git" in entries:
        return True
    return any(not e.startswith(".") for e in entries)


def gather_fresh(seen_cwds):
    out = []
    if not os.path.isdir(CODE_ROOT):
        return out
    candidates = []
    for top in os.listdir(CODE_ROOT):
        if top.startswith((".", "_")):
            continue
        tpath = os.path.join(CODE_ROOT, top)
        if not os.path.isdir(tpath):
            continue
        candidates.append(tpath)
        try:
            for sub in os.listdir(tpath):
                spath = os.path.join(tpath, sub)
                if not sub.startswith(".") and os.path.isdir(spath):
                    candidates.append(spath)
        except OSError:
            pass
    scored = []
    for d in candidates:
        if d in seen_cwds or not _looks_like_project(d):
            continue
        try:
            scored.append((os.stat(d).st_mtime, d))
        except OSError:
            continue
    scored.sort(reverse=True)
    return [d for _, d in scored[:10]]


def clip(s, w):
    s = str(s)
    return s if len(s) <= w else s[: w - 1] + "…"


def emit(rows, fresh):
    def field(s):
        return str(s).replace("\t", " ").replace("\n", " ").replace("\r", " ")

    now = time.time()
    for r in rows:
        rel = reltime(r["mtime"])
        title = clip(r["title"], TITLE_W)
        rc = recency_color(now - r["mtime"])  # glyph + time fade by age
        display = "{rc}▸{r} {rc}{rel:>3}{r}  {g}{title:<{tw}}{r} {d}{proj}{r}".format(
            rc=rc, g=G, r=R, rel=rel, title=title, tw=TITLE_W, d=D, proj=r["project"],
        )
        statsjson = json.dumps(r["stats"], separators=(",", ":"))
        print("\t".join(field(x) for x in [
            display, r["cwd"], r["session_id"], r["kind"],
            r["title"], r["last_worked"], r["branch"], rel, r["project"], statsjson,
        ]))

    for d in fresh:
        proj = os.path.basename(d)
        display = "{p}+{r} {p}new{r}  {c}{proj:<{tw}}{r} {d}{path}{r}".format(
            p=PK, r=R, c=C, proj=clip(proj, TITLE_W), tw=TITLE_W, d=D,
            path=os.path.dirname(d).replace(HOME, "~"),
        )
        print("\t".join(field(x) for x in [
            display, d, "", "NEW", proj, "start a fresh session here", "", "", proj, "{}",
        ]))


def main():
    cache = load_cache()
    rows, new_cache = gather_sessions(cache)
    save_cache(new_cache)

    if "--stats" in sys.argv:
        projects = len({r["cwd"] for r in rows})
        newest = reltime(rows[0]["mtime"]) if rows else "—"
        sys.stdout.write("%d\t%d\t%s" % (len(rows), projects, newest))
        return

    seen = {r["cwd"] for r in rows}
    fresh = gather_fresh(seen) if "--no-fresh" not in sys.argv else []
    emit(rows, fresh)


if __name__ == "__main__":
    main()