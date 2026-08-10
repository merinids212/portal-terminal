#!/usr/bin/env python3
"""portal search brain — drives the live picker on every keystroke.

Usage:
  rank.py            -> browse: sessions by recency (superseded collapsed) + NEW rows
  rank.py "<query>"  -> search: sessions ranked by tuned BM25, with match badges
  rank.py --go "<q>" -> teleport: "<confidence>\t<cwd>\t<sid>\t<kind>" for the top hit
  rank.py --why "<q>"-> one-line match rationale (for the panel border label)

Rows carry type + topic-chips (project dropped from the list). Cross-session enrichment
(topics, related, supersession) is computed once per index build and cached in the pickle.
"""
import os
import sys
from collections import Counter, defaultdict

import index
from core import BM25, EXPAND, doc_tokens, toks, expand_query, topic_terms
from palette import palette, lerp, pastel

_P = palette()
G, D, C, A, PK, R = _P["G"], _P["D"], _P["C"], _P["A"], _P["P"], _P["R"]
CL = _P["CLAUDE"]   # agent mark only — portal is otherwise monochrome
_TOP, _BOT = _P["GRAD_TOP"], _P["GRAD_BOT"]
TITLE_W = 42
PORTAL = os.path.join(index.HOME, ".claude", "portal")
_IDX = os.path.join(PORTAL, "rank_index.pkl")


def load_rows():
    cache = index.load_cache()
    rows, new_cache = index.gather_sessions(cache)
    index.save_cache(new_cache)
    return rows


def _jac(a, b):
    return len(a & b) / len(a | b) if (a or b) else 0.0


def enrich(rows, docs):
    """Compute topics, related sessions, and supersession — mutates rows in place."""
    n = len(rows)
    df = Counter()
    for d in docs:
        for w in set(d):
            df[w] += 1
    for i, r in enumerate(rows):
        r["topics"] = topic_terms(docs[i], df, n, 4)
        r["superseded"] = False
        r["family"] = 1
    dsets = [set(d) for d in docs]
    tset = [set(toks(rows[i]["title"])) for i in range(n)]
    relset = [tset[i] | set(rows[i]["topics"]) for i in range(n)]

    # supersession: within one cwd, an older session is superseded by a newer one with a
    # near-identical title (the "clearly an old version" case) or near-identical content.
    bycwd = defaultdict(list)
    for i, r in enumerate(rows):
        bycwd[r["cwd"]].append(i)
    for idxs in bycwd.values():
        idxs.sort(key=lambda i: rows[i]["mtime"], reverse=True)  # newest first
        for pos, i in enumerate(idxs):
            if rows[i]["superseded"]:
                continue
            for j in idxs[pos + 1:]:
                if rows[j]["superseded"]:
                    continue
                if _jac(tset[i], tset[j]) >= 0.6 or _jac(dsets[i], dsets[j]) >= 0.6:
                    rows[j]["superseded"] = True
                    rows[i]["family"] += 1

    # related: sessions sharing >=2 distinctive topics (real threads, not stray word overlap),
    # preferring a different folder. Quality-gated: show none rather than a spurious link.
    topsets = [set(rows[i]["topics"]) for i in range(n)]
    for i in range(n):
        sims = []
        for j in range(n):
            if j == i:
                continue
            shared = len(topsets[i] & topsets[j])
            tj = _jac(tset[i], tset[j])
            if shared < 2 and tj < 0.4:      # need a real topical or title link
                continue
            bonus = 0.2 if rows[j]["cwd"] != rows[i]["cwd"] else 0.0
            score = shared + tj + bonus
            sims.append((score, rows[j]["session_id"][:8], rows[j]["title"]))
        sims.sort(reverse=True)
        rows[i]["related"] = [(sid, t) for _, sid, t in sims[:2]]


def get_index():
    """(rows, docs) with enrichment, cached to a pickle keyed by cache.json mtime."""
    import pickle
    try:
        mt = os.path.getmtime(index.CACHE)
    except OSError:
        mt = 0
    try:
        with open(_IDX, "rb") as f:
            blob = pickle.load(f)
        if blob.get("mt") == mt:
            return blob["rows"], blob["docs"]
    except Exception:
        pass
    rows = load_rows()
    docs = [doc_tokens(r) for r in rows]
    enrich(rows, docs)
    try:  # load_rows() rewrote cache.json — stamp its post-save mtime or we never hit
        mt = os.path.getmtime(index.CACHE)
    except OSError:
        pass
    try:  # atomic: concurrent keystroke reloads must never see a half-written pickle
        tmp = _IDX + ".%d.tmp" % os.getpid()
        with open(tmp, "wb") as f:
            pickle.dump({"mt": mt, "rows": rows, "docs": docs}, f)
        os.replace(tmp, _IDX)
    except Exception:
        pass
    return rows, docs


def _sidecar(name, default):
    return os.environ.get("PORTAL_%s_FILE" % name.upper(), os.path.join(PORTAL, default))


def _expanded():
    try:
        return open(_sidecar("expand", ".expand")).read().strip() == "1"
    except OSError:
        return False


# ------------------------------------------------------------------ rendering
PROJ_W = 14


def family_badge(row):
    """+N = N older near-duplicate versions collapsed under this row (^E reveals)."""
    n = row.get("family", 1)
    return " %s+%d%s" % (A, n - 1, R) if n > 1 else ""


def display_col(row, lead_glyph, lead_text, lead_color):
    """time · project (dim) · title — clean and scannable; badges only when they matter."""
    proj = index.clip(row.get("project", ""), PROJ_W)
    title = index.clip(row["title"], TITLE_W)
    cwd = row.get("cwd", "")
    mark = (PK + "⌁ " + R) if (cwd and not os.path.isdir(cwd)) else ""
    pc = pastel(row.get("project", ""))     # stable per-project shade
    is_codex = (row.get("stats") or {}).get("agent") == "codex" or row.get("kind") == "CODEX"
    # which agent wrote this session: Codex mono, Claude in its own coral — the only
    # color portal spends, one glyph per row.
    ag = ("%s❖%s " % (D, R)) if is_codex else ("%s✳%s " % (CL, R))
    atag = " %scodex%s" % (D, R) if is_codex else ""
    return "{lc}{lg}{r} {lc}{lt:>3}{r}  {ag}{pc}{proj:<{pw}}{r} {mk}{g}{title:<{tw}}{r}{at}{fam}".format(
        lc=lead_color, lg=lead_glyph, r=R, lt=lead_text, ag=ag, pc=pc, proj=proj, pw=PROJ_W,
        mk=mark, g=G, title=title, tw=TITLE_W, at=atag, fam=family_badge(row),
    )


def _statsjson(row):
    import json
    s = dict(row.get("stats", {}))
    s["type"] = row.get("type", "·")
    s["topics"] = row.get("topics", [])
    s["related"] = row.get("related", [])
    s["project"] = row.get("project", "")
    s["mtime"] = row.get("mtime", 0)
    return json.dumps(s, separators=(",", ":"))


def _field(s):
    return str(s).replace("\t", " ").replace("\n", " ").replace("\r", " ")


def emit_row(display, row, rel):
    print("\t".join(_field(x) for x in [
        display, row["cwd"], row["session_id"], row["kind"],
        row["title"], row["last_worked"], row["branch"], rel, row.get("project", ""),
        _statsjson(row),
    ]))


def emit_browse(rows):
    import time
    now = time.time()
    show_all = _expanded()
    # stateful live glyphs: for the handful of rows written in the last 5 min, read the real
    # state so the list shows ◇ waiting / ● working / • thinking, not a generic dot. Only the
    # live candidates are inspected (a bounded tail read each), so the hot path stays fast.
    _LIVE = {"waiting": "◇", "working": "●", "thinking": "•", "idle": "●"}
    try:
        import agents
    except Exception:
        agents = None
    for r in rows:
        if r.get("superseded") and not show_all:
            continue
        rc = index.recency_color(now - r["mtime"])
        recent = (now - r["mtime"]) < 300
        if r.get("kind") == "CODEX":
            glyph = "◇"                                    # cross-agent (Codex) session
        elif recent:
            glyph = "●"
            if agents is not None:
                try:
                    st = agents.live_state_of(r["session_id"])
                except Exception:
                    st = None
                if st:
                    glyph = _LIVE.get(st["state"], "●")
                    if st["state"] == "waiting":
                        rc = C                              # the one that needs you pops
        else:
            glyph = "▸"
        emit_row(display_col(r, glyph, index.reltime(r["mtime"]), rc), r, index.reltime(r["mtime"]))
    seen = {r["cwd"] for r in rows}
    fresh = index.gather_fresh(seen)
    if not rows and not fresh:
        # first run, nothing indexed yet — never show an empty screen
        cwd = os.getcwd()
        proj = os.path.basename(cwd) or cwd
        display = "{p}+ new  start your first session here{r} {d}{path}{r}".format(
            p=PK, r=R, d=D, path=cwd.replace(index.HOME, "~"))
        print("\t".join(_field(x) for x in [
            display, cwd, "", "NEW", proj, "no sessions yet — ↵ starts one", "", "", proj, "{}",
        ]))
        return
    for d in fresh:
        proj = os.path.basename(d)
        display = "{p}+{r} {p}new{r}  {dm}{proj:<{pw}}{r} {c}start fresh here{r} {dm}{path}{r}".format(
            p=PK, r=R, dm=D, proj=index.clip(proj, PROJ_W), pw=PROJ_W, c=C,
            path=os.path.dirname(d).replace(index.HOME, "~"),
        )
        print("\t".join(_field(x) for x in [
            display, d, "", "NEW", proj, "start a fresh session here", "", "", proj, "{}",
        ]))


def _build_query(query, bm):
    """Expand each typed token with vocab prefix matches (substring fallback) + synonyms,
    so search-as-you-type works: 'webh' scores everything containing 'webhook*'."""
    base = toks(query)
    qt = list(base)
    vocab = bm.df.keys()
    for t in base:
        if len(t) >= 2:
            pref = [w for w in vocab if w.startswith(t) and w != t]
            if not pref and len(t) >= 3:
                pref = [w for w in vocab if t in w and w != t]
            qt += pref[:25]
    for w in base:
        qt += EXPAND.get(w, [])
    return base, qt


# ---- ranking algorithm: relevance + recency, both first-class -------------
# score(session) = BM25(query, doc)
#                + title-fragment boosts        (typed a piece of the title)
#                + hi · RECENCY_W · 2^(-age/HALFLIFE)   (fresh work rises)
#                + hi · LIVE_BONUS  if written within LIVE_WINDOW_H (what you're
#                                                       doing RIGHT NOW wins near-ties)
# Recency only sways sessions that already match (sc > 0) — it reorders, never invents.
RECENCY_W = 0.12          # weight of the freshness curve, relative to the top score
RECENCY_HALFLIFE_D = 7.0  # freshness halves every week
LIVE_BONUS = 0.20         # extra for actively-written sessions
LIVE_WINDOW_H = 1.0       # "actively written" = modified within the last hour


def rank_rows(rows, docs, query):
    """Return ([(norm, raw, row), ...] high->low, qtset)."""
    import re as _re
    import time as _time
    bm = BM25(docs)
    base, qt = _build_query(query, bm)
    scored = bm.rank(qt)
    hi = scored[0][0] if scored else 0.0
    qnorm = " ".join(base)
    last = base[-1] if base else ""
    now = _time.time()
    out = []
    for sc, i in scored:
        tnorm = _re.sub(r"[-_]+", " ", rows[i]["title"].lower())
        boost = 0.0
        if qnorm:
            if qnorm in tnorm:
                boost = hi * 0.8 + 1.0     # typed a literal fragment of the title
            elif all(w in tnorm for w in base):
                boost = hi * 0.35          # all words appear somewhere in the title
            elif len(last) >= 2 and any(tw.startswith(last) for tw in tnorm.split()):
                boost = hi * 0.15          # last word is a prefix of a title word
        if sc > 0:
            age_days = max(0.0, now - rows[i]["mtime"]) / 86400.0
            fresh = 2.0 ** (-age_days / RECENCY_HALFLIFE_D)
            live = LIVE_BONUS if age_days * 24.0 < LIVE_WINDOW_H else 0.0
            boost += hi * (RECENCY_W * fresh + live)
        out.append((sc + boost, rows[i]["mtime"], i))
    out.sort(reverse=True)                  # score, then mtime breaks exact ties
    hi2 = out[0][0] if out else 0.0
    return [((s / hi2) if hi2 > 0 else 0.0, s, rows[i]) for s, m, i in out], set(qt)


def emit_search(ranked):
    shown = 0
    for norm, raw, r in ranked:
        if raw <= 0:
            continue
        bc = lerp(_TOP, _BOT, 1.0 - max(0.0, min(1.0, norm)))
        emit_row(display_col(r, "▉", "%d" % int(round(norm * 99)), bc), r, index.reltime(r["mtime"]))
        shown += 1
    if shown == 0:
        cwd = os.getcwd()
        proj = os.path.basename(cwd) or cwd
        display = "{p}+ new  start fresh here{r} {d}{path}{r}".format(
            p=PK, r=R, d=D, path=cwd.replace(index.HOME, "~"),
        )
        print("\t".join(_field(x) for x in [
            display, cwd, "", "NEW", proj, "no session matched — start fresh", "", "", proj, "{}",
        ]))


def why_line(ranked, query, qtset=None):
    if not ranked or ranked[0][1] <= 0:
        return "no match — ↵ start fresh · ^G ask"
    norm, raw, top = ranked[0]
    qset = qtset if qtset is not None else set(expand_query(toks(query)))
    hit = [w for w in dict.fromkeys(toks(top["title"] + " " + top.get("search", ""))) if w in qset]
    matched = " · ".join(hit[:4]) if hit else query.strip()
    return "matched \"%s\" → %s · %d%%" % (matched, top["title"][:34], int(round(norm * 99)))


def _write_why(text):
    try:
        with open(_sidecar("why", ".why"), "w") as f:
            f.write(text)
    except OSError:
        pass


def main():
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        get_index()          # imports, cache, enrichment — any breakage raises here
        print("ok")
        return
    mode = None
    if args and args[0] in ("--go", "--why"):
        mode, args = args[0], args[1:]
    query = (args[0] if args else "").strip()

    if mode == "--why":
        rows, docs = get_index()
        if query:
            ranked, qtset = rank_rows(rows, docs, query)
            print(why_line(ranked, query, qtset), end="")
        return

    if mode == "--go":
        if not query:
            print("0\t\t\t")
            return
        rows, docs = get_index()
        ranked, _ = rank_rows(rows, docs, query)
        if ranked and ranked[0][1] > 0:
            norm, raw, top = ranked[0]
            margin = norm - (ranked[1][0] if len(ranked) > 1 else 0)
            orig = set(toks(query))
            top_tokens = set(doc_tokens(top))
            overlap = (sum(1 for w in orig if w in top_tokens) / len(orig)) if orig else 0.0
            conf = round(overlap * (0.65 + 0.35 * min(1.0, margin * 3)), 3)
            print("%.3f\t%s\t%s\t%s" % (conf, top["cwd"], top["session_id"], top["kind"]))
        else:
            print("0\t\t\t")
        return

    rows, docs = get_index()
    if not query:
        emit_browse(rows)
        hidden = 0 if _expanded() else sum(1 for r in rows if r.get("superseded"))
        _write_why("%d sessions · %d collapsed (^E) · type to search · ↵ jump · ^N new"
                   % (len(rows), hidden))
        return
    ranked, qtset = rank_rows(rows, docs, query)
    _write_why(why_line(ranked, query, qtset))
    emit_search(ranked)


if __name__ == "__main__":
    main()
