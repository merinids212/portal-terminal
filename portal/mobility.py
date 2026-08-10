#!/usr/bin/env python3
"""portal session mobility — move folders with their Claude history, pull history in,
relink orphans. Claude Code keys transcripts to the launch folder (encoded under
~/.claude/projects), so a moved folder strands its sessions; these commands fix that.

  mobility.py doctor [--list] [--sid <sid8>]   find orphans; relink (interactive)
  mobility.py mv <src> <dst>                   move a folder AND migrate its sessions
  mobility.py pull <query|sid> [--move]        copy (or move) a session into $PWD; prints "sid\tcwd"
  mobility.py ls [--json]                      session listing for scripting
"""
import json
import os
import shutil
import sys

import index


def encode(cwd):
    """A cwd maps to its transcript dir name: every non-alphanumeric char -> '-'
    (matches Claude Code: /a/b.c -> -a-b-c)."""
    import re
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd).rstrip("/"))


def sessions_in(cwd):
    d = os.path.join(index.PROJECTS, encode(cwd))
    if not os.path.isdir(d):
        return []
    return [(f[:-6], os.path.join(d, f)) for f in os.listdir(d) if f.endswith(".jsonl")]


def _invalidate():
    for f in ("rank_index.pkl",):
        try:
            os.remove(os.path.join(index.HOME, ".claude", "portal", f))
        except OSError:
            pass


def locate(sid):
    """Find a session's transcript anywhere under the projects tree (the dir name
    usually equals encode(cwd), but not always — e.g. /tmp vs /private/tmp)."""
    try:
        dirs = os.listdir(index.PROJECTS)
    except OSError:
        return None            # codex-only machine: no ~/.claude/projects at all
    for d in dirs:
        p = os.path.join(index.PROJECTS, d, sid + ".jsonl")
        if os.path.isfile(p):
            return p
    return None


def migrate(sid, old_cwd, new_cwd, move=True):
    """Move/copy one session's transcript to new_cwd's dir, rewriting cwd references.
    Streams line-by-line (files can be hundreds of MB)."""
    src_dir = os.path.join(index.PROJECTS, encode(old_cwd))
    dst_dir = os.path.join(index.PROJECTS, encode(new_cwd))
    src = os.path.join(src_dir, sid + ".jsonl")
    if not os.path.isfile(src):
        src = locate(sid)
        if src is None:
            raise FileNotFoundError(sid + ".jsonl not found under " + index.PROJECTS)
        src_dir = os.path.dirname(src)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, sid + ".jsonl")
    # encode() is lossy (/x/a.b and /x/a-b share a dir), so src and dst can be the
    # same file: rewrite in place and never remove. A distinct existing dst is a
    # different session's transcript — refuse rather than clobber it.
    same = os.path.normpath(src) == os.path.normpath(dst)
    if not same and os.path.exists(dst):
        raise FileExistsError(dst + " already exists — refusing to overwrite")
    # Rewrite cwd references in ONE regex pass. Three traps this design avoids:
    #  - the old cwd may appear raw or JSON-escaped (non-ASCII → \uXXXX) — match both;
    #  - a bare prefix replace would also hit sibling paths (/x/app vs /x/app2) — the
    #    lookahead requires the value to end (") or continue into a subpath (/);
    #  - sequential replaces can re-match their own output — a single pass cannot,
    #    and emitting the escaped spelling of new_cwd keeps every line valid JSON.
    import re
    alts = {re.escape('"cwd":"%s' % old_cwd),
            re.escape('"cwd":' + json.dumps(old_cwd)[:-1])}
    rx = re.compile("(?:%s)" % "|".join(sorted(alts, key=len, reverse=True)) + r'(?=["/])')
    new_frag = '"cwd":' + json.dumps(new_cwd)[:-1]
    st = os.stat(src)
    tmp = dst + ".tmp"
    with open(src, "r", errors="replace") as fi, open(tmp, "w") as fo:
        for line in fi:
            fo.write(rx.sub(lambda m: new_frag, line))
    os.replace(tmp, dst)
    os.utime(dst, (st.st_atime, st.st_mtime))  # a relink must not fake recency
    side_src = os.path.join(src_dir, sid)
    if not same and os.path.isdir(side_src):
        side_dst = os.path.join(dst_dir, sid)
        if move and not os.path.isdir(side_dst):   # an existing dst dir would nest, not merge
            shutil.move(side_src, side_dst)
        elif not move and not os.path.isdir(side_dst):
            shutil.copytree(side_src, side_dst)
    if move and not same:
        os.remove(src)
    _invalidate()
    return dst


def find_orphans():
    """[(sid, cwd, title)] whose cwd no longer exists. Claude sessions only — codex
    rollouts live under ~/.codex keyed by date, not cwd, so there is nothing to relink
    (and migrate() would crash on them)."""
    cache = index.load_cache()
    out = []
    for path, v in cache.items():
        if not path.startswith(index.PROJECTS + os.sep):
            continue
        cwd = v.get("cwd")
        if not cwd or os.path.isdir(cwd):
            continue
        out.append((os.path.basename(path)[:-6], cwd, v.get("title", "")))
    return sorted(out, key=lambda x: x[1])


def candidates(gone_cwd):
    """Dirs under the code root sharing the orphan's basename — likely new homes."""
    base = os.path.basename(gone_cwd.rstrip("/"))
    hits = []
    root = index.CODE_ROOT
    if not os.path.isdir(root):
        return hits
    for top in os.listdir(root):
        tp = os.path.join(root, top)
        if not os.path.isdir(tp):
            continue
        if top == base:
            hits.append(tp)
        try:
            for sub in os.listdir(tp):
                sp = os.path.join(tp, sub)
                if sub == base and os.path.isdir(sp):
                    hits.append(sp)
        except OSError:
            continue
    return hits


def cmd_doctor(args):
    only_sid = None
    if "--sid" in args:
        i = args.index("--sid")
        if i + 1 >= len(args):
            print("usage: portal doctor --sid <sid8>", file=sys.stderr)
            return 2
        only_sid = args[i + 1]
    orphans = find_orphans()
    if only_sid:
        orphans = [o for o in orphans if o[0].startswith(only_sid)]
    if not orphans:
        print("◇ no orphaned sessions — all clean")
        return 0
    listing = "--list" in args
    rc = 0
    for sid, cwd, title in orphans:
        cands = candidates(cwd)
        print("⌁ %s  %s" % (sid[:8], title[:44]))
        print("   was: %s" % cwd)
        if not cands:
            print("   no candidate folder found (moved outside the code root, or deleted)")
            rc = 1
            continue
        for i, c in enumerate(cands, 1):
            print("   %d) %s" % (i, c))
        if listing:
            continue
        try:
            ans = input("   relink to [1-%d, enter=skip]: " % len(cands)).strip()
        except EOFError:
            ans = ""
        if ans.isdigit() and 1 <= int(ans) <= len(cands):
            new = cands[int(ans) - 1]
            migrate(sid, cwd, new, move=True)
            print("   ✓ relinked → %s" % new)
            if only_sid:
                print(new)  # machine-readable: final cwd on last line
    return rc


def cmd_mv(args):
    if len(args) < 2:
        print("usage: portal mv <src> <dst>", file=sys.stderr)
        return 2
    src, dst = os.path.abspath(args[0]), os.path.abspath(args[1])
    moved_folder = False
    if os.path.isdir(src):
        if os.path.exists(dst):
            print("mv: destination exists: %s" % dst, file=sys.stderr)
            return 1
        shutil.move(src, dst)
        moved_folder = True
        print("◇ moved folder → %s" % dst)
    elif not os.path.isdir(dst):
        print("mv: neither src folder nor dst folder exists", file=sys.stderr)
        return 1
    # union: transcripts in the encoded dir + cache entries whose cwd matches
    # (transcript dir can differ from encode(cwd), e.g. /tmp vs /private/tmp)
    sids = {sid for sid, _ in sessions_in(src)}
    for path, v in index.load_cache().items():
        # codex rollouts (outside ~/.claude/projects) are date-keyed, not cwd-keyed:
        # nothing to migrate, and migrate() can't locate them
        if v.get("cwd") == src and path.startswith(index.PROJECTS + os.sep):
            sids.add(os.path.basename(path)[:-6])
    n = 0
    for sid in sorted(sids):
        migrate(sid, src, dst, move=True)
        print("  ✓ session %s migrated" % sid[:8])
        n += 1
    if n == 0 and not moved_folder:
        print("  (no sessions found for %s)" % src)
    print("◇ %d session(s) now live at %s" % (n, dst))
    return 0


def cmd_pull(args):
    move = "--move" in args
    args = [a for a in args if a != "--move"]
    if not args:
        print("usage: portal pull <query|sid> [--move]", file=sys.stderr)
        return 2
    query = " ".join(args)
    here = os.getcwd()

    import rank
    rows, docs = rank.get_index()
    target = None
    for r in rows:  # exact sid / prefix first
        if r["session_id"].startswith(query):
            target = r
            break
    if target is None:
        ranked, _ = rank.rank_rows(rows, docs, query)
        vis = [r for _, raw, r in ranked if raw > 0]
        if not vis:
            print("pull: nothing matches %r" % query, file=sys.stderr)
            return 1
        target = vis[0]
    sid, src_cwd = target["session_id"], target["cwd"]
    if target.get("kind") == "CODEX":
        print("pull: %s is a codex session — codex keeps history in ~/.codex/sessions "
              "(not keyed by folder), so there is nothing to move; just resume it "
              "from any directory with: codex resume %s" % (sid[:8], sid), file=sys.stderr)
        return 1
    if os.path.abspath(src_cwd) == os.path.abspath(here):
        print("pull: session already lives here", file=sys.stderr)
        return 1
    migrate(sid, src_cwd, here, move=move)
    print("◇ %s %s → %s" % ("moved" if move else "pulled", target["title"][:40], here),
          file=sys.stderr)
    print("%s\t%s" % (sid, here))  # stdout contract for portal.zsh
    return 0


def commits_during(cwd, t0, t1):
    """Commits created in cwd's repo inside [t0, t1] (epoch secs) — retroactive
    commit-to-session association, no daemon: the session's time-span IS the filter."""
    import subprocess
    try:
        p = subprocess.run(
            ["git", "-C", cwd, "log", "--since=@%d" % int(t0), "--until=@%d" % int(t1),
             "--format=%h %s", "--no-merges", "-n", "12"],
            capture_output=True, text=True, timeout=10)
        if p.returncode != 0:
            return []
        return [ln for ln in p.stdout.strip().splitlines() if ln]
    except (OSError, subprocess.TimeoutExpired):
        return []


def find_overlaps(rows, window_days=7):
    """Sessions in the same repo that touched the same file recently — the
    'two terminals editing one file' warning. Returns [(cwd, fname, [rows])]."""
    import time
    from collections import defaultdict
    now = time.time()
    recent = [r for r in rows
              if now - r["mtime"] < window_days * 86400 and os.path.isdir(r["cwd"])]
    seen = defaultdict(list)          # (cwd, file) -> [row]
    for r in recent:
        for f in (r.get("stats") or {}).get("files", []):
            seen[(r["cwd"], f)].append(r)
    out = []
    for (cwd, f), rs in sorted(seen.items()):
        if len(rs) >= 2:
            out.append((cwd, f, sorted(rs, key=lambda r: -r["mtime"])))
    return out


def cmd_overlap(args):
    import time
    import rank
    from palette import pastel
    rows, _ = rank.get_index()
    overlaps = find_overlaps(rows)
    if not overlaps:
        print("◇ no overlapping edits across recent sessions")
        return 0
    now = time.time()
    reset = "\033[0m"
    for cwd, f, rs in overlaps:
        proj = os.path.basename(cwd)
        both_live = all(now - r["mtime"] < 3600 for r in rs[:2])
        sev = "critical" if both_live else "warn"
        print("%s %s%s%s  %s  (%s)" % ("⚠" if both_live else "·",
                                       pastel(proj), proj, reset, f, sev))
        for r in rs[:3]:
            age = index.reltime(r["mtime"]) if hasattr(index, "reltime") else "?"
            print("    %s  %s  %s" % (r["session_id"][:8], age, r["title"][:40]))
    print("◇ %d overlapping file(s)" % len(overlaps))
    return 0


def cmd_status(args):
    """Sweep git state across every session folder: dirty, unpushed, live, stale.
    Finds work you could lose before you close a terminal. No daemon — computed now."""
    import subprocess
    import time

    import rank
    rows, _ = rank.get_index()
    now = time.time()
    seen = {}
    for r in rows:  # newest session per folder
        cwd = r["cwd"]
        if os.path.isdir(cwd) and (cwd not in seen or r["mtime"] > seen[cwd]["mtime"]):
            seen[cwd] = r

    def git(cwd, *a):
        try:
            p = subprocess.run(["git", "-C", cwd] + list(a),
                               capture_output=True, text=True, timeout=10)
            return p.stdout.strip() if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    n_dirty = n_unpushed = n_live = 0
    out = []
    for cwd, r in sorted(seen.items(), key=lambda kv: -kv[1]["mtime"]):
        marks = []
        live = (now - r["mtime"]) < 3600
        if live:
            marks.append("live")
            n_live += 1
        porcelain = git(cwd, "status", "--porcelain")
        if porcelain:  # None = not a git repo; "" = clean
            marks.append("dirty:%d" % len(porcelain.splitlines()))
            n_dirty += 1
        ahead = git(cwd, "rev-list", "--count", "@{u}..HEAD")
        if ahead and ahead != "0":
            marks.append("unpushed:%s" % ahead)
            n_unpushed += 1
        if not marks:
            continue  # clean and idle — no news is good news
        proj = os.path.basename(cwd)
        from palette import pastel
        reset = "\033[0m"
        out.append("%s %s%-20s%s %-38s %s" % (
            "●" if live else " ", pastel(proj), proj[:20], reset,
            r["title"][:38], " · ".join(marks)))
    if out:
        print("\n".join(out))
    print("◇ %d folders · %d dirty · %d unpushed · %d live" % (
        len(seen), n_dirty, n_unpushed, n_live))
    return 0


def _human_tok(n):
    if n >= 1_000_000:
        return "%.1fM" % (n / 1e6)
    if n >= 1000:
        return "%dk" % round(n / 1000)
    return str(int(n))


def cmd_heavy(args):
    """Rank past sessions by (approx) tokens spent — what to compact when you reopen.
    Retrospective form of 'compact': you can't compact a live terminal, but you CAN
    see which sessions burned the most so you know where to reopen and /compact."""
    import rank
    from palette import pastel
    repo = None
    if "--repo" in args:
        i = args.index("--repo")
        repo = args[i + 1] if i + 1 < len(args) else None
    rows, _ = rank.get_index()
    scored = []
    for r in rows:
        st = r.get("stats") or {}
        tot = (st.get("in_tok", 0) or 0) + (st.get("out_tok", 0) or 0)
        if tot <= 0:
            continue
        if repo and r["project"] != repo and repo not in r["cwd"]:
            continue
        scored.append((tot, r))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        print("◇ no token data yet (older transcripts, or usage keys absent)")
        return 0
    reset = "\033[0m"
    for tot, r in scored[:20]:
        st = r["stats"]
        age = index.reltime(r["mtime"])
        model = st.get("model_label") or "unknown"
        print("%s%9s tok%s  %s%-14s%s %4s  %-11s %s" % (
            "\033[38;5;220m", _human_tok(tot), reset,
            pastel(r["project"]), r["project"][:14], reset, age, model[:11],
            r["title"][:38]))
    print("◇ %d session(s) with token data%s — approx (sampled)" % (
        len(scored), (" · repo %s" % repo) if repo else ""))
    return 0


def _grep_snippet(line, rx, ctx=46):
    """Readable window around the first match — prefer a JSON "text" value, else raw."""
    import re
    best = None
    for tm in re.finditer(r'"text":"((?:[^"\\]|\\.)*)"', line):
        seg = tm.group(1)
        if rx.search(index._junq(seg)):
            best = index._junq(seg)
            break
    s = best if best is not None else line
    m = rx.search(s) or rx.search(line)
    if m is None:
        return ""
    if best is None:
        s = line
    start = max(0, m.start() - ctx)
    end = min(len(s), m.end() + ctx)
    snip = " ".join(s[start:end].replace("\t", " ").split())
    return ("…" if start else "") + snip + ("…" if end < len(s) else "")


def cmd_grep(args):
    """Literal/regex search INSIDE session transcripts (not just titles).
    Answers 'the session where I discussed X'. Bounded per-file byte budget."""
    import re
    import rank
    from palette import pastel
    ignore = fixed = False
    repo = None
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-i", "--ignore-case"):
            ignore = True
        elif a in ("-F", "--fixed"):
            fixed = True
        elif a == "--repo" and i + 1 < len(args):
            repo = args[i + 1]
            i += 1
        else:
            rest.append(a)
        i += 1
    pattern = " ".join(rest)
    if not pattern:
        print("usage: portal grep [-i] [-F] [--repo NAME] <pattern>", file=sys.stderr)
        return 2
    fl = re.I if ignore else 0
    try:
        rx = re.compile(pattern if not fixed else re.escape(pattern), fl)
    except re.error:
        rx = re.compile(re.escape(pattern), fl)   # bad regex -> literal, never crash
    rows, _ = rank.get_index()
    BUDGET = 16 << 20   # cap bytes scanned per transcript (files reach 200MB)
    MAXHITS = 3
    reset = "\033[0m"
    n = 0
    for r in rows:
        if repo and r["project"] != repo and repo not in r["cwd"]:
            continue
        if r.get("kind") == "CODEX":
            import codex
            path = codex.locate(r["session_id"])   # rollouts live under ~/.codex, not projects
        else:
            path = locate(r["session_id"])
        if not path:
            continue
        snips, read = [], 0
        try:
            with open(path, "r", errors="replace") as f:
                # readline with a cap: `for line in f` would materialize a whole
                # multi-MB line before the budget check ever ran
                for line in iter(lambda: f.readline(1 << 20), ""):
                    read += len(line)
                    if read > BUDGET:
                        break
                    if rx.search(line):
                        sn = _grep_snippet(line, rx)
                        if sn:
                            snips.append(sn)
                        if len(snips) >= MAXHITS:
                            break
        except OSError:
            continue
        if snips:
            n += 1
            age = index.reltime(r["mtime"])
            print("%s%-14s%s %4s  %s%s%s" % (
                pastel(r["project"]), r["project"][:14], reset, age,
                "\033[38;5;250m", r["title"][:40], reset))
            for sn in snips:
                print("    \033[38;5;245m%s%s" % (sn[:150], reset))
    print("◇ %d session(s) match %r" % (n, pattern))
    return 0 if n else 1


def cmd_ls(args):
    import rank
    rows, _ = rank.get_index()
    if "--json" in args:
        print(json.dumps([{
            "sid": r["session_id"], "cwd": r["cwd"], "title": r["title"],
            "project": r["project"], "mtime": r["mtime"],
            "orphan": not os.path.isdir(r["cwd"]),
            "model": (r.get("stats") or {}).get("model"),
            "model_label": (r.get("stats") or {}).get("model_label"),
            "tokens": ((r.get("stats") or {}).get("in_tok", 0) or 0)
                      + ((r.get("stats") or {}).get("out_tok", 0) or 0),
        } for r in rows], indent=None))
        return 0
    for r in rows:
        mark = "⌁" if not os.path.isdir(r["cwd"]) else " "
        print("%s %s  %-16s %s" % (mark, r["session_id"][:8], r["project"][:16], r["title"][:48]))
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip())
        return 2
    cmd, rest = args[0], args[1:]
    if cmd == "doctor":
        return cmd_doctor(rest)
    if cmd == "mv":
        return cmd_mv(rest)
    if cmd == "pull":
        return cmd_pull(rest)
    if cmd == "ls":
        return cmd_ls(rest)
    if cmd == "status":
        return cmd_status(rest)
    if cmd == "overlap":
        return cmd_overlap(rest)
    if cmd == "heavy":
        return cmd_heavy(rest)
    if cmd == "grep":
        return cmd_grep(rest)
    print("unknown command: %s" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
