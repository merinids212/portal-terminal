#!/usr/bin/env python3
"""portal test suite — zero dependencies, runs in a throwaway HOME.

    python3 tests/run_tests.py

Builds a fake ~/.claude/projects with synthetic session transcripts, then exercises
the real modules end-to-end: cleaning, tokenization, indexing, search-as-you-type,
teleport confidence, orphan detection, and the no-session-lost invariant.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORTAL = REPO / "portal"

# ---- isolated HOME (must happen BEFORE importing portal modules) ----
TMP = tempfile.mkdtemp(prefix="portal-test-")
os.environ["HOME"] = TMP
sys.path.insert(0, str(PORTAL))

import core          # noqa: E402
import index         # noqa: E402
import rank          # noqa: E402
import mobility      # noqa: E402
import codex         # noqa: E402
import palette as palette_mod   # noqa: E402

PASS = 0
FAIL = []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL.append(name)
        print(f"  ✗ {name}  {detail}")


def jline(**kw):
    # compact separators — real Claude Code transcripts have no spaces after colons
    return json.dumps(kw, separators=(",", ":"))


def build_fixtures():
    alpha = Path(TMP) / "work" / "api"
    beta = Path(TMP) / "work" / "searchsvc"
    gone = Path(TMP) / "work" / "old-project"     # will NOT be created -> orphan
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    code_root = Path(TMP) / "Desktop" / "code" / "newproj"
    code_root.mkdir(parents=True)
    (code_root / "main.py").write_text("print('hi')\n")

    proj = Path(TMP) / ".claude" / "projects"
    (Path(TMP) / ".claude" / "portal").mkdir(parents=True)

    def write_session(dirname, sid, cwd, title, prompts, files, t0, t1,
                      model=None, in_tok=0, out_tok=0):
        d = proj / dirname
        d.mkdir(parents=True, exist_ok=True)
        lines = [
            jline(type="queue-operation", cwd=str(cwd), gitBranch="main", timestamp=t0),
            jline(type="ai-title", aiTitle=title),
        ]
        for p in prompts:
            lines.append(jline(type="user", timestamp=t0,
                               message={"role": "user", "content": p}))
        for f in files:
            lines.append(jline(type="assistant", timestamp=t1, message={"role": "assistant"},
                               toolu={"type": "tool_use", "name": "Edit", "file_path": str(cwd) + "/" + f}))
        final = {"role": "assistant"}
        if model:  # carry model + usage on the last assistant line (parsed from the tail)
            final["model"] = model
            final["usage"] = {"input_tokens": in_tok, "cache_read_input_tokens": 0,
                              "output_tokens": out_tok}
        lines.append(jline(type="assistant", timestamp=t1, message=final))
        (d / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")

    write_session("-fx-alpha", "aaaa1111-0000-0000-0000-000000000001", alpha,
                  "refactor-auth-middleware",
                  ["Refactor the auth middleware to rotate refresh tokens",
                   "<command-message>go</command-message>\nAlso add webhook retries for stripe"],
                  ["src/middleware.ts", "src/tokens.ts"],
                  "2026-07-16T09:00:00.000Z", "2026-07-16T12:30:00.000Z",
                  model="claude-opus-4-8", in_tok=1000, out_tok=400)   # Opus, heavy
    write_session("-fx-beta", "bbbb2222-0000-0000-0000-000000000002", beta,
                  "research-vector-databases",
                  ["Benchmark embeddings recall on pgvector and qdrant for the search service"],
                  ["bench.py"],
                  "2026-07-15T10:00:00.000Z", "2026-07-15T11:00:00.000Z",
                  model="claude-sonnet-4-5", in_tok=150, out_tok=50)   # Sonnet, light
    # 'gone' fixture intentionally has no model -> must degrade to 'unknown'
    write_session("-fx-gone", "cccc3333-0000-0000-0000-000000000003", gone,
                  "old-moved-project",
                  ["Prototype the exporter before the folder moved"],
                  [],
                  "2026-07-10T10:00:00.000Z", "2026-07-10T11:00:00.000Z")
    return alpha, beta, gone


def main():
    # Isolation: if the developer has portal sourced, PORTAL_WHY_FILE / PORTAL_EXPAND_FILE /
    # PORTAL_THEME leak into the environment and every subprocess would write its sidecars to
    # the real paths instead of the throwaway HOME. Scrub them so the suite is hermetic.
    for k in [k for k in os.environ if k.startswith("PORTAL_")]:
        del os.environ[k]
    # never scan the host's real claude/codex processes — the process-union tests patch
    # _proc_cwds directly, so this only removes nondeterminism (and an lsof per call)
    os.environ["PORTAL_NO_PROC"] = "1"
    print(f"fixture HOME: {TMP}\n")
    alpha, beta, gone = build_fixtures()

    print("== core ==")
    check("cleaner recovers prose from command wrapper",
          "webhook retries" in (core.clean_user("<command-message>x</command-message>\nAlso add webhook retries") or ""))
    check("cleaner drops image placeholders", core.clean_user("[Image: original 300x300]") is None)
    _cav = ("Caveat: The messages below were generated by the user while running local "
            "commands. DO NOT respond to these messages or otherwise consider them in your "
            "response unless the user explicitly asks you to.\n"
            "<command-name>/resume</command-name>\nmake the branding monochrome")
    check("cleaner strips the local-command caveat",
          core.clean_user(_cav) == "make the branding monochrome",
          core.clean_user(_cav))
    check("cleaner drops a caveat-only message",
          core.clean_user("Caveat: The messages below were generated by the user while "
                          "running local commands. DO NOT respond.") is None)
    if core._wordset():
        check("compound split works", core.split_compound("dealflow") == ["deal", "flow"])
        check("no junk split", core.split_compound("renders") == ["renders"])
    else:  # minimal system without a words file — degradation is the contract
        check("compound split degrades gracefully", core.split_compound("dealflow") == ["dealflow"])
    df = {"alpha": 1, "bc2c": 1, "tokens": 1}
    topics = core.topic_terms(["tokens", "tokens", "bc2c", "the"], df, 10)
    check("topic terms drop digit-fragments", "bc2c" not in topics and "tokens" in topics)
    _saved = core._WORDS
    core._WORDS = set()   # simulate Linux without /usr/share/dict/words
    check("search works without a system wordlist", core.toks("dealflow") == ["dealflow"])
    core._WORDS = _saved

    print("== index ==")
    rows, cache = index.gather_sessions(index.load_cache())
    index.save_cache(cache)
    check("all fixtures indexed (no loss)", len(rows) == 3, f"got {len(rows)}")
    byid = {r["session_id"][:4]: r for r in rows}
    check("cwd extracted", byid["aaaa"]["cwd"] == str(alpha))
    check("title extracted", byid["aaaa"]["title"] == "refactor-auth-middleware")
    check("search text includes cleaned prompt",
          "webhook" in byid["aaaa"].get("search", "").lower())
    check("files captured", "middleware.ts" in byid["aaaa"]["stats"].get("files", []))

    print("== model per session (add 01) ==")
    check("model extracted + labelled (opus)",
          byid["aaaa"]["stats"].get("model_label") == "Opus 4.x",
          byid["aaaa"]["stats"].get("model_label"))
    check("sonnet labelled", byid["bbbb"]["stats"].get("model_label") == "Sonnet 4.x")
    check("missing model -> unknown", byid["cccc"]["stats"].get("model_label") == "unknown")
    check("model_label maps opus id", core.model_label("claude-opus-4-8") == "Opus 4.x")
    check("model_label handles None", core.model_label(None) == "unknown")
    check("model_label ignores <synthetic>", core.model_label("<synthetic>") == "unknown")
    # head model differs from tail model -> final (tail) wins
    _sw = Path(TMP) / "switch.jsonl"
    _sw.write_text("\n".join([
        jline(type="q", cwd=str(alpha), gitBranch="main", timestamp="2026-07-14T10:00:00.000Z"),
        jline(type="assistant", message={"role": "assistant", "model": "claude-sonnet-4-5"}),
        jline(type="assistant", message={"role": "assistant", "model": "claude-opus-4-8"}),
    ]) + "\n")
    _swi = index.parse_session(str(_sw), _sw.stat().st_size)
    check("model switch: final (tail) model wins",
          core.model_label(_swi["model"]) == "Opus 4.x", _swi.get("model"))

    print("== token spend (add 02) ==")
    _ta = byid["aaaa"]["stats"].get("in_tok", 0) + byid["aaaa"]["stats"].get("out_tok", 0)
    # true total is 1400; the sampler is an approximation (windows overlap on tiny files,
    # are disjoint on real 200MB ones) — assert captured, positive, same order of magnitude
    check("usage tokens summed (approx, scaled)", 700 <= _ta <= 4000, "got %d" % _ta)
    check("no-usage session -> 0 tokens",
          byid["cccc"]["stats"].get("in_tok", 0) + byid["cccc"]["stats"].get("out_tok", 0) == 0)

    print("== rank / search-as-you-type ==")
    rows2, docs = rank.get_index()
    check("get_index row parity", len(rows2) == 3)

    def top(q):
        ranked, _ = rank.rank_rows(rows2, docs, q)
        vis = [r for _, raw, r in ranked if raw > 0]
        return vis[0]["session_id"][:4] if vis else None

    check("prefix 'auth' finds alpha", top("auth") == "aaaa")
    check("prefix 'vect' finds beta", top("vect") == "bbbb")
    check("3-char prefix 'ref' finds alpha", top("ref") == "aaaa")
    check("full title query wins", top("refactor the auth middleware") == "aaaa")
    check("gibberish matches nothing", top("zzqqxx") is None)

    print("== teleport confidence ==")
    def go_conf(q):
        ranked, _ = rank.rank_rows(rows2, docs, q)
        if not ranked or ranked[0][1] <= 0:
            return 0.0
        norm, raw, t = ranked[0]
        margin = norm - (ranked[1][0] if len(ranked) > 1 else 0)
        orig = set(core.toks(q))
        tt = set(core.doc_tokens(t))
        overlap = sum(1 for w in orig if w in tt) / len(orig) if orig else 0
        return overlap * (0.65 + 0.35 * min(1.0, margin * 3))
    check("confident on distinctive query", go_conf("vector databases benchmark") >= 0.60,
          f"{go_conf('vector databases benchmark'):.2f}")
    check("not confident on gibberish", go_conf("zzqqxx totally unrelated") < 0.60)

    print("== orphans ==")
    orphan_row = byid["cccc"]
    disp = rank.display_col(orphan_row, "▸", "1w", rank.G)
    check("orphan marked with ⌁", "⌁" in disp)
    alive = rank.display_col(byid["aaaa"], "▸", "2h", rank.G)
    check("live session unmarked", "⌁" not in alive)

    print("== mobility ==")
    check("encode maps / to -", mobility.encode("/a/b/c") == "-a-b-c")
    check("encode maps . to - (Claude Code rule)", mobility.encode("/a/b.c/d_e") == "-a-b-c-d-e")
    orphs = mobility.find_orphans()
    check("orphan detected by doctor", any(s.startswith("cccc") for s, _, _ in orphs),
          str(orphs))
    # candidate discovery: create the moved folder under the code root with same basename
    moved_home = Path(TMP) / "Desktop" / "code" / "grp" / "old-project"
    moved_home.mkdir(parents=True)
    (moved_home / "keep.txt").write_text("x")
    cands = mobility.candidates(str(gone))
    check("candidate folder discovered", str(moved_home) in cands, str(cands))
    # migrate the orphan to its new home (must preserve the original mtime)
    _src_mtime = os.path.getmtime(mobility.locate("cccc3333-0000-0000-0000-000000000003"))
    mobility.migrate("cccc3333-0000-0000-0000-000000000003", str(gone), str(moved_home))
    newp = Path(TMP) / ".claude" / "projects" / mobility.encode(str(moved_home)) / "cccc3333-0000-0000-0000-000000000003.jsonl"
    check("migrated transcript in new encoded dir", newp.exists())
    check("cwd rewritten inside transcript", str(moved_home) in newp.read_text()
          and str(gone) not in newp.read_text())
    check("migrate preserves mtime (no fake recency)",
          abs(os.path.getmtime(newp) - _src_mtime) < 1)
    rows3, _c3 = index.gather_sessions({})
    fixed = [r for r in rows3 if r["session_id"].startswith("cccc")]
    check("index sees relinked session", fixed and fixed[0]["cwd"] == str(moved_home),
          fixed[0]["cwd"] if fixed else "missing")
    check("no session lost after migrate", len(rows3) == 3, f"got {len(rows3)}")
    # pull (copy): bring alpha into beta's cwd, original must survive
    os.chdir(str(beta))
    original = Path(mobility.locate("aaaa1111-0000-0000-0000-000000000001"))
    mobility.migrate("aaaa1111-0000-0000-0000-000000000001", str(alpha), str(beta), move=False)
    pulled = Path(TMP) / ".claude" / "projects" / mobility.encode(str(beta)) / "aaaa1111-0000-0000-0000-000000000001.jsonl"
    check("pull copies transcript here", pulled.exists())
    check("pull keeps the original", original.exists())

    print("== audit fixes ==")
    # encode() is lossy: /x/col.lide and /x/col-lide share a transcript dir. mv between
    # them made src == dst and the old remove-after-copy DELETED the transcript.
    colsrc = Path(TMP) / "work" / "col.lide"
    coldst = Path(TMP) / "work" / "col-lide"
    colsrc.mkdir(parents=True, exist_ok=True)
    coldst.mkdir(parents=True, exist_ok=True)
    csid = "dddd4444-0000-0000-0000-000000000004"
    cdir = Path(TMP) / ".claude" / "projects" / mobility.encode(str(colsrc))
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / (csid + ".jsonl")).write_text(
        jline(type="q", cwd=str(colsrc), timestamp="2026-07-18T10:00:00.000Z") + "\n")
    mobility.migrate(csid, str(colsrc), str(coldst), move=True)
    surv = cdir / (csid + ".jsonl")
    check("mv survives encode() collision (no data loss)", surv.exists())
    check("collision migrate still rewrote cwd", surv.exists() and str(coldst) in surv.read_text())

    # a distinct transcript already at dst is another session's history — refuse, don't clobber
    esrc = Path(TMP) / "work" / "esrc"
    edst = Path(TMP) / "work" / "edst"
    esrc.mkdir(exist_ok=True)
    edst.mkdir(exist_ok=True)
    esid = "eeee5555-0000-0000-0000-000000000005"
    d1 = Path(TMP) / ".claude" / "projects" / mobility.encode(str(esrc))
    d2 = Path(TMP) / ".claude" / "projects" / mobility.encode(str(edst))
    d1.mkdir(parents=True, exist_ok=True)
    d2.mkdir(parents=True, exist_ok=True)
    (d1 / (esid + ".jsonl")).write_text(jline(type="q", cwd=str(esrc)) + "\n")
    (d2 / (esid + ".jsonl")).write_text("PRECIOUS\n")
    try:
        mobility.migrate(esid, str(esrc), str(edst), move=True)
        refused = False
    except FileExistsError:
        refused = True
    check("migrate refuses to clobber an existing dst", refused)
    check("existing dst transcript untouched", (d2 / (esid + ".jsonl")).read_text() == "PRECIOUS\n")

    check("doctor --sid without a value exits 2, no crash", mobility.cmd_doctor(["--sid"]) == 2)

    # bounded head reads: one giant line must arrive truncated, not fully materialized
    bigf = Path(TMP) / "bigline.jsonl"
    bigf.write_text(jline(type="q", cwd="/x") + "\n" + "x" * (3 << 20) + "\n" + jline(type="t") + "\n")
    head = index.read_head(str(bigf), 150)
    check("read_head caps per-line bytes", max(len(ln) for ln in head) <= index.HEAD_LINE_CAP + 1)
    check("read_head total stays within budget",
          sum(len(ln) for ln in head) <= index.HEAD_BYTE_BUDGET + index.HEAD_LINE_CAP)
    bigf.unlink()

    # rank pickle cache: the stamp must be the post-save cache.json mtime, or every
    # keystroke rebuilds (O(n²) enrichment) forever
    rank.get_index()
    _s1 = os.stat(os.path.join(TMP, ".claude", "portal", "rank_index.pkl"))
    rank.get_index()
    _s2 = os.stat(os.path.join(TMP, ".claude", "portal", "rank_index.pkl"))
    check("rank pickle cache hits on second call",
          (_s1.st_mtime_ns, _s1.st_ino) == (_s2.st_mtime_ns, _s2.st_ino))

    # codex sentinel: a narrator-spawned `codex exec` rollout (no index row, no title)
    # must be hidden by its first-prompt sentinel, or watch narrates it in turn — a loop
    import agents as agents_mod
    _now = time.time()
    croot = Path(TMP) / ".codex" / "sessions" / "2026" / "08" / "08"
    croot.mkdir(parents=True, exist_ok=True)
    ssid = "abcd1234-1111-2222-3333-444455556666"
    sfile = croot / ("rollout-2026-08-08T10-00-00-" + ssid + ".jsonl")
    sfile.write_text(
        jline(type="session_meta", payload={"cwd": str(alpha), "session_id": ssid}) + "\n" +
        jline(type="response_item", payload={"type": "message", "role": "user",
              "content": [{"type": "input_text",
                           "text": "[portal-status] You label a coding agent from its transcript tail."}]}) + "\n")
    live_cx = agents_mod.live_agents(max_age=600, now=_now)
    check("codex narration rollout hidden by sentinel",
          all(a["session_id"] != ssid for a in live_cx))
    # while a real codex rollout too fresh for the index still shows, titled from its head
    rsid = "abcd1234-aaaa-bbbb-cccc-ddddeeeeffff"
    rfile = croot / ("rollout-2026-08-08T10-05-00-" + rsid + ".jsonl")
    rfile.write_text(
        jline(type="session_meta", payload={"cwd": str(alpha), "session_id": rsid}) + "\n" +
        jline(type="response_item", payload={"type": "message", "role": "user",
              "content": [{"type": "input_text", "text": "fix the webhook retry logic"}]}) + "\n")
    live_cx = agents_mod.live_agents(max_age=600, now=_now)
    mine = [a for a in live_cx if a["session_id"] == rsid]
    check("fresh codex rollout appears with head-parsed title",
          bool(mine) and "webhook" in mine[0]["title"], str([a["session_id"] for a in live_cx]))

    # pull on a codex session: clear refusal (history isn't folder-keyed), not a traceback
    mobility._invalidate()
    rows_cx, _ = rank.get_index()
    check("codex rollout indexed for pull test", any(r["session_id"] == rsid for r in rows_cx))
    check("pull on a codex session refuses cleanly", mobility.cmd_pull([rsid[:8]]) == 1)

    # cleanup so later sections' counts and the no-loss invariant see the base fixtures
    for junk in (surv, d1 / (esid + ".jsonl"), d2 / (esid + ".jsonl"), sfile, rfile):
        junk.unlink()
    mobility._invalidate()

    # a cwd that json escapes (non-ASCII → \uXXXX on disk) must still be rewritten —
    # a raw-bytes replace misses it and the migrated session instantly re-orphans
    usrc = Path(TMP) / "work" / "café"
    udst = Path(TMP) / "work" / "cafe2"
    usrc.mkdir(exist_ok=True)
    udst.mkdir(exist_ok=True)
    usid = "abab7777-0000-0000-0000-000000000007"
    udir = Path(TMP) / ".claude" / "projects" / mobility.encode(str(usrc))
    udir.mkdir(parents=True, exist_ok=True)
    (udir / (usid + ".jsonl")).write_text(jline(type="q", cwd=str(usrc)) + "\n")
    assert "\\u" in (udir / (usid + ".jsonl")).read_text()  # premise: escaped on disk
    mobility.migrate(usid, str(usrc), str(udst), move=True)
    up = Path(TMP) / ".claude" / "projects" / mobility.encode(str(udst)) / (usid + ".jsonl")
    check("migrate rewrites a json-escaped cwd",
          up.exists() and json.loads(up.read_text())["cwd"] == str(udst))
    up.unlink()
    shutil.rmtree(usrc, ignore_errors=True)
    shutil.rmtree(udst, ignore_errors=True)

    # codex rollout shape drives the same state machine as the Claude shape
    cx_ln = [
        jline(type="response_item", payload={"type": "message", "role": "user",
              "content": [{"type": "input_text", "text": "do the thing"}]}),
        jline(type="response_item", payload={"type": "function_call", "name": "shell",
              "arguments": "{\"command\":[\"ls\"]}"}),
    ]
    st_cx, act_cx = agents_mod.classify(cx_ln, 10)
    check("codex function_call classifies as working", st_cx == "working", st_cx)
    check("codex tool name surfaces in the action", "shell" in act_cx, act_cx)
    cx_ln.append(jline(type="response_item", payload={"type": "function_call_output",
                                                      "output": "ok"}))
    check("codex tool output classifies as thinking",
          agents_mod.classify(cx_ln, 10)[0] == "thinking")
    cx_ln.append(jline(type="response_item", payload={"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "done — want the tests too?"}]}))
    check("codex assistant text classifies as waiting",
          agents_mod.classify(cx_ln, 10)[0] == "waiting")

    # escape bytes from transcript content never reach the terminal
    check("sane() strips ANSI escapes",
          "\x1b" not in agents_mod.sane("evil \x1b]0;pwned\x07 title"))

    # a waiting/idle session aging off the board is not "done"
    import watch as watch_mod2
    A2 = lambda sid, stt: {"session_id": sid, "project": "p", "state": stt, "action": ""}
    check("aged-out waiting session fires no 'done'",
          watch_mod2.transitions({"w1": A2("w1", "waiting")}, {}) == [])
    check("aged-out idle session fires no 'done'",
          watch_mod2.transitions({"w2": A2("w2", "idle")}, {}) == [])

    # ^Y on a codex row must copy a codex resume command
    stubbin = Path(TMP) / "stubbin"
    stubbin.mkdir(exist_ok=True)
    clip = Path(TMP) / "clip.txt"
    (stubbin / "pbcopy").write_text("#!/bin/sh\ncat > %s\n" % clip)
    (stubbin / "pbcopy").chmod(0o755)
    env2 = dict(os.environ, PATH="%s:%s" % (stubbin, os.environ["PATH"]))
    subprocess.run(["zsh", str(PORTAL / "copycmd.sh"), "/w", "abc123", "CODEX"], env=env2)
    check("^Y codex row copies codex resume", "codex resume abc123" in clip.read_text(),
          clip.read_text())

    # decodable project-dir names recover a cwd for sessions whose head lost theirs
    os.makedirs("/tmp/portaldeco/x", exist_ok=True)
    try:
        check("projdir decode recovers an existing path",
              index._decode_projdir("-tmp-portaldeco-x") in ("/tmp/portaldeco/x", "/private/tmp/portaldeco/x"))
    finally:
        shutil.rmtree("/tmp/portaldeco", ignore_errors=True)
    check("projdir decode refuses a non-existent path",
          index._decode_projdir("-no-such-dir-here-xyz") == "")

    # migrate boundary: a sibling path sharing the old cwd as a prefix must NOT be touched
    psrc = Path(TMP) / "work" / "app"
    pdst = Path(TMP) / "work" / "app-v2"
    psib = str(Path(TMP) / "work" / "app2")
    psrc.mkdir(exist_ok=True)
    pdst.mkdir(exist_ok=True)
    psid = "cdcd9999-0000-0000-0000-000000000009"
    pdir = Path(TMP) / ".claude" / "projects" / mobility.encode(str(psrc))
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / (psid + ".jsonl")).write_text(
        jline(type="q", cwd=str(psrc)) + "\n" +
        jline(type="note", text='saw {"cwd":"%s"} in a tool result' % psib) + "\n" +
        jline(type="q", cwd=str(psrc) + "/sub") + "\n")
    mobility.migrate(psid, str(psrc), str(pdst), move=True)
    pp = Path(TMP) / ".claude" / "projects" / mobility.encode(str(pdst)) / (psid + ".jsonl")
    ptxt = pp.read_text()
    check("migrate leaves sibling-prefix paths alone", psib in ptxt, ptxt)
    check("migrate rewrites subpath cwds", str(pdst) + "/sub" in ptxt, ptxt)
    check("migrate rewrote the exact cwd", json.loads(ptxt.splitlines()[0])["cwd"] == str(pdst))
    pp.unlink()

    # migrate to a non-ASCII destination: single-pass rewrite must not double-apply
    qsrc = Path(TMP) / "work" / "plainapp"
    qdst = Path(TMP) / "work" / "plainapp-café"
    qsrc.mkdir(exist_ok=True)
    qdst.mkdir(exist_ok=True)
    qsid = "dede1010-0000-0000-0000-000000000010"
    qdir = Path(TMP) / ".claude" / "projects" / mobility.encode(str(qsrc))
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / (qsid + ".jsonl")).write_text(jline(type="q", cwd=str(qsrc)) + "\n")
    mobility.migrate(qsid, str(qsrc), str(qdst), move=True)
    qp = Path(TMP) / ".claude" / "projects" / mobility.encode(str(qdst)) / (qsid + ".jsonl")
    check("non-ASCII destination rewritten exactly once",
          json.loads(qp.read_text())["cwd"] == str(qdst), qp.read_text())
    qp.unlink()
    for d in (psrc, pdst, qsrc, qdst):
        shutil.rmtree(d, ignore_errors=True)

    # decode ambiguity: two on-disk interpretations → refuse rather than guess
    os.makedirs("/tmp/portaldeco2/a-b", exist_ok=True)
    os.makedirs("/tmp/portaldeco2/a/b", exist_ok=True)
    try:
        check("ambiguous projdir decode returns nothing",
              index._decode_projdir("-tmp-portaldeco2-a-b") == "")
    finally:
        shutil.rmtree("/tmp/portaldeco2", ignore_errors=True)

    # string-content prompts are turn content, not meta rows
    st_s, act_s = agents_mod.classify(
        [jline(type="assistant", message={"role": "assistant",
               "content": [{"type": "text", "text": "done — anything else?"}]}),
         jline(type="user", message={"role": "user", "content": "yes, also fix the tests"})], 10)
    check("string-content reply flips waiting → thinking", st_s == "thinking", st_s)
    check("string-content reply reads as your message", "your message" in act_s, act_s)

    # codex-only machine: no ~/.claude/projects must still index codex rollouts
    xfile = croot / ("rollout-2026-08-08T11-00-00-" + rsid + ".jsonl")
    xfile.write_text(
        jline(type="session_meta", payload={"cwd": str(alpha), "session_id": rsid}) + "\n" +
        jline(type="response_item", payload={"type": "message", "role": "user",
              "content": [{"type": "input_text", "text": "codex only machine check"}]}) + "\n")
    _real_projects = index.PROJECTS
    index.PROJECTS = os.path.join(TMP, "no-such-projects-dir")
    try:
        rows_co, _ = index.gather_sessions({})
        check("codex-only machine still indexes rollouts",
              any(r["session_id"] == rsid for r in rows_co), str(len(rows_co)))
    finally:
        index.PROJECTS = _real_projects
    check("codex.locate finds a rollout by sid", codex.locate(rsid) == str(xfile))
    xfile.unlink()
    mobility._invalidate()

    # an open tab is an open agent: a session hours-quiet stays on the board when its
    # cwd has a running claude/codex process — and ages off when it doesn't
    prcwd = Path(TMP) / "work" / "procproj"
    prcwd.mkdir(exist_ok=True)
    prdir = Path(TMP) / ".claude" / "projects" / mobility.encode(str(prcwd))
    prdir.mkdir(parents=True, exist_ok=True)
    prsid = "fafa1212-0000-0000-0000-000000000012"
    prf = prdir / (prsid + ".jsonl")
    prf.write_text(
        jline(type="q", cwd=str(prcwd), timestamp="2026-07-18T10:00:00.000Z") + "\n" +
        jline(type="assistant", message={"role": "assistant",
              "content": [{"type": "text", "text": "shipped — want the docs too?"}]}) + "\n")
    _two_h_ago = time.time() - 2 * 3600
    os.utime(prf, (_two_h_ago, _two_h_ago))
    # on Linux the fixture HOME lives under /tmp, whose encoded dir name trips the
    # (intentional) scratch-session skip — neutralize _SKIP so both checks test the
    # window/process logic itself, on every platform
    _real_skip = agents_mod._SKIP
    _real_proc = agents_mod._proc_cwds
    agents_mod._SKIP = tuple(s for s in _real_skip if "tmp" not in s)
    try:
        live_np = agents_mod.live_agents(now=time.time())
        check("processless quiet session ages off the board",
              all(a["session_id"] != prsid for a in live_np))
        agents_mod._proc_cwds = lambda now=None: frozenset({str(prcwd)})
        live_p = agents_mod.live_agents(now=time.time())
        mine_p = [a for a in live_p if a["session_id"] == prsid]
        check("open process keeps a quiet session on the board", bool(mine_p),
              str([(a["session_id"], a["cwd"]) for a in live_p]))
        check("hours-quiet open agent reads as waiting",
              bool(mine_p) and mine_p[0]["state"] == "waiting",
              mine_p[0]["state"] if mine_p else "missing")
    finally:
        agents_mod._proc_cwds = _real_proc
        agents_mod._SKIP = _real_skip
    prf.unlink()

    # narration transcripts (claude -p / codex exec runs stamped with the sentinel)
    # never appear as sessions in the picker index — or in the cache
    ndir = Path(TMP) / ".claude" / "projects" / mobility.encode(str(alpha))
    ndir.mkdir(parents=True, exist_ok=True)
    nf = ndir / "cafe8888-0000-0000-0000-000000000008.jsonl"
    nf.write_text(
        jline(type="q", cwd=str(alpha), timestamp="2026-07-18T10:00:00.000Z") + "\n" +
        jline(type="user", message={"role": "user",
              "content": "[portal-status] You label a coding agent from its transcript tail."}) + "\n")
    rows_sn, cache_sn = index.gather_sessions({})
    check("narration transcript hidden from the picker index",
          all(r["session_id"] != "cafe8888-0000-0000-0000-000000000008" for r in rows_sn))
    check("narration transcript kept out of the cache", str(nf) not in cache_sn)
    nf.unlink()

    # cache ⊆ index: an unparseable, undecodable transcript stays out of BOTH
    junkdir = Path(TMP) / ".claude" / "projects" / "-zz-nowhere-qq"
    junkdir.mkdir(parents=True, exist_ok=True)
    junkf = junkdir / ("ffff6666-0000-0000-0000-000000000006.jsonl")
    junkf.write_text("this is not json and has no cwd\n")
    rows_nl, cache_nl = index.gather_sessions({})
    check("cwd-less transcript kept out of cache (cache ⊆ index)",
          str(junkf) not in cache_nl)
    check("cwd-less transcript not in index rows",
          all(r["session_id"] != "ffff6666-0000-0000-0000-000000000006" for r in rows_nl))
    junkf.unlink()
    junkdir.rmdir()
    mobility._invalidate()

    print("== recency ==")
    import time as _t
    twin = lambda m: {"title": "twin project cleanup", "search": "identical text",
                      "project": "twin", "cwd": str(alpha), "mtime": m,
                      "session_id": "x" * 8, "kind": "SESSION", "last_worked": "",
                      "branch": "", "stats": {}}
    trows = [twin(_t.time() - 30 * 86400), twin(_t.time())]
    tdocs = [["twin", "project", "cleanup"], ["twin", "project", "cleanup"]]
    tr, _ = rank.rank_rows(trows, tdocs, "twin cleanup")
    check("near-tie goes to the fresher session", tr[0][2]["mtime"] == trows[1]["mtime"])

    print("== pastel ==")
    import palette
    os.environ["COLORTERM"] = "truecolor"
    check("pastel stable per name", palette.pastel("api") == palette.pastel("api"))
    check("pastel distinct across names", palette.pastel("api") != palette.pastel("billing"))

    print("== mv finds non-canonical transcript dirs ==")
    odd = Path(TMP) / "work" / "oddloc"
    odd.mkdir(parents=True)
    d = Path(TMP) / ".claude" / "projects" / "-not-the-encoding"
    d.mkdir(parents=True)
    (d / "dddd4444-0000-0000-0000-000000000004.jsonl").write_text(
        jline(type="q", cwd=str(odd), timestamp="2026-07-17T10:00:00.000Z") + "\n"
        + jline(type="t", aiTitle="odd-location") + "\n")
    index.save_cache(index.gather_sessions(index.load_cache())[1])
    dst = Path(TMP) / "work" / "oddloc2"
    r = subprocess.run([sys.executable, str(PORTAL / "mobility.py"), "mv", str(odd), str(dst)],
                       capture_output=True, text=True, env=dict(os.environ, HOME=TMP),
                       cwd=str(PORTAL))
    check("mv migrates via cache when dir name is non-canonical",
          "dddd4444 migrated" in r.stdout, r.stdout[:120])

    print("== status ==")
    if shutil.which("git"):
        subprocess.run(["git", "init", "-q", str(alpha)], capture_output=True)
        (alpha / "wip.txt").write_text("uncommitted")
        out = subprocess.run([sys.executable, str(PORTAL / "mobility.py"), "status"],
                             capture_output=True, text=True, env=dict(os.environ, HOME=TMP),
                             cwd=str(PORTAL))
        check("status reports dirty folder", "dirty" in out.stdout, out.stdout[:120])
        check("status summary line", "folders ·" in out.stdout)
        check("status exit 0", out.returncode == 0)

    print("== commits + overlap ==")
    if shutil.which("git"):
        import time as _t2
        ge = dict(os.environ, HOME=TMP, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                  GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "-C", str(alpha), "add", "-A"], capture_output=True, env=ge)
        subprocess.run(["git", "-C", str(alpha), "commit", "-qm", "wip: auth middleware"],
                       capture_output=True, env=ge)
        got = mobility.commits_during(str(alpha), _t2.time() - 3600, _t2.time() + 60)
        check("commits_during finds in-window commit", any("auth middleware" in c for c in got), str(got))
        check("commits_during empty outside window",
              mobility.commits_during(str(alpha), 1000, 2000) == [])
    mkrow = lambda sid, cwd, mt, files: {"session_id": sid, "cwd": cwd, "mtime": mt,
                                         "title": "t", "stats": {"files": files}}
    import time as _t3
    _now = _t3.time()
    ovl = mobility.find_overlaps([
        mkrow("s1x", str(alpha), _now - 60, ["shared.ts", "a.ts"]),
        mkrow("s2x", str(alpha), _now - 120, ["shared.ts", "b.ts"]),
        mkrow("s3x", str(beta), _now - 60, ["shared.ts"]),          # other repo — no clash
        mkrow("s4x", str(alpha), _now - 30 * 86400, ["a.ts"]),      # too old
    ])
    check("overlap: same repo + same file flagged",
          len(ovl) == 1 and ovl[0][1] == "shared.ts", str([(o[0], o[1]) for o in ovl]))
    check("overlap: both sessions listed", len(ovl[0][2]) == 2)

    print("== CLI smoke (subprocess) ==")
    env = dict(os.environ, HOME=TMP)
    out = subprocess.run([sys.executable, str(PORTAL / "rank.py")],
                         capture_output=True, text=True, env=env, cwd=str(PORTAL))
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    sess = [l for l in lines if l.split("\t")[3] == "SESSION"]
    news = [l for l in lines if l.split("\t")[3] == "NEW"]
    # 3 originals + pulled copy + odd-location fixture (moved by the mv test)
    check("browse emits all sessions", len(sess) == 5, f"got {len(sess)}")
    check("browse offers fresh dirs", len(news) >= 1)
    check("rows carry 10 fields", all(len(l.split("\t")) == 10 for l in lines))
    # the browse list marks live rows with a stateful glyph (◇ waiting / ● working / • thinking),
    # not a generic dot — fixtures were just written, so they read as live
    _vis = [re.sub(r"\033\[[0-9;]*m", "", l) for l in lines]
    check("browse marks live sessions statefully", any(v and v[0] in "●◇•" for v in _vis))
    why = (Path(TMP) / ".claude" / "portal" / ".why").read_text()
    check("why sidecar written", "sessions ·" in why, why)
    out = subprocess.run([sys.executable, str(PORTAL / "mobility.py"), "ls", "--json"],
                         capture_output=True, text=True, env=env, cwd=str(PORTAL))
    try:
        parsed = json.loads(out.stdout)
        check("ls --json parses", isinstance(parsed, list) and len(parsed) == 5
              and all("sid" in r and "orphan" in r for r in parsed), out.stdout[:80])
        check("ls --json carries model + tokens",
              all("model_label" in r and "tokens" in r for r in parsed))
    except ValueError:
        check("ls --json parses", False, out.stdout[:80])
        check("ls --json carries model + tokens", False)

    # preview: the documented right-hand card must render every row kind without crashing
    for label, row in [("session", sess[0]), ("new-dir", news[0])]:
        pv = subprocess.run([sys.executable, str(PORTAL / "preview.py"), row],
                            capture_output=True, text=True, env=env, cwd=str(PORTAL))
        check("preview renders a %s row" % label, pv.returncode == 0 and pv.stdout.strip(),
              pv.stderr[-120:])
    pv = subprocess.run([sys.executable, str(PORTAL / "preview.py"), "garbage\twith\tfew\tfields"],
                        capture_output=True, text=True, env=env, cwd=str(PORTAL))
    check("preview survives a malformed row", pv.returncode == 0, pv.stderr[-120:])
    _pv_sess = subprocess.run([sys.executable, str(PORTAL / "preview.py"), sess[0]],
                              capture_output=True, text=True, env=env, cwd=str(PORTAL)).stdout
    _pv_clean = re.sub(r"\033\[[0-9;]*m", "", _pv_sess)
    check("preview shows the session folder", "~" in _pv_clean or TMP in _pv_clean)
    check("preview shows the arc rule", "arc" in _pv_clean, _pv_clean[:200])

    print("== portal heavy (add 02) ==")
    out = subprocess.run([sys.executable, str(PORTAL / "mobility.py"), "heavy"],
                         capture_output=True, text=True, env=env, cwd=str(PORTAL))
    hl = [l for l in out.stdout.splitlines() if "tok" in l and "◇" not in l]
    check("heavy lists token-bearing sessions", len(hl) >= 2, out.stdout[:120])
    check("heavy orders by tokens (heaviest first)",
          bool(hl) and "refactor-auth-middleware" in hl[0], hl[0] if hl else "none")

    print("== portal grep (add 11) ==")
    out = subprocess.run([sys.executable, str(PORTAL / "mobility.py"), "grep", "pgvector"],
                         capture_output=True, text=True, env=env, cwd=str(PORTAL))
    check("grep finds content inside transcripts",
          "research-vector-databases" in out.stdout and out.returncode == 0, out.stdout[:120])
    out = subprocess.run([sys.executable, str(PORTAL / "mobility.py"), "grep", "-i", "STRIPE"],
                         capture_output=True, text=True, env=env, cwd=str(PORTAL))
    check("grep -i is case-insensitive", out.returncode == 0 and "stripe" in out.stdout.lower())
    out = subprocess.run([sys.executable, str(PORTAL / "mobility.py"), "grep", "zzqqxxnope"],
                         capture_output=True, text=True, env=env, cwd=str(PORTAL))
    check("grep exit 1 on no match", out.returncode == 1)
    out = subprocess.run([sys.executable, str(PORTAL / "rank.py"), "--go", "vector databases benchmark"],
                         capture_output=True, text=True, env=env, cwd=str(PORTAL))
    parts = out.stdout.strip().split("\t")
    check("--go contract: conf\\tcwd\\tsid\\tkind", len(parts) == 4 and parts[3] == "SESSION"
          and float(parts[0]) >= 0.6, out.stdout.strip()[:80])

    print("== unicode / escaped paths ==")
    check("_junq decodes \\uXXXX", index._junq("a\\u00f1o del ni\\u00f1o") == "año del niño")
    check("_junq passthrough plain", index._junq("/plain/path") == "/plain/path")
    check("_junq survives malformed", index._junq("bad\\qesc") == "bad\\qesc")

    print("== first run ==")
    empty_home = tempfile.mkdtemp(prefix="portal-empty-")
    (Path(empty_home) / ".claude" / "projects").mkdir(parents=True)
    out = subprocess.run([sys.executable, str(PORTAL / "rank.py")],
                         capture_output=True, text=True,
                         env=dict(os.environ, HOME=empty_home), cwd=str(PORTAL))
    lines0 = [l for l in out.stdout.splitlines() if l.strip()]
    check("zero-session browse is never empty", len(lines0) == 1 and "NEW" in lines0[0],
          out.stdout[:80])
    shutil.rmtree(empty_home, ignore_errors=True)

    print("== selftest ==")
    out = subprocess.run([sys.executable, str(PORTAL / "rank.py"), "--selftest"],
                         capture_output=True, text=True, env=dict(os.environ, HOME=TMP),
                         cwd=str(PORTAL))
    check("selftest passes on healthy install", out.returncode == 0 and "ok" in out.stdout)

    print("== codex (multi-agent index) ==")
    # a real Codex rollout: ~/.codex/sessions/YYYY/MM/DD/rollout-<iso>-<uuid>.jsonl
    cx_dir = Path(TMP) / ".codex" / "sessions" / "2026" / "07" / "18"
    cx_dir.mkdir(parents=True)
    cx_sid = "019f7b54-9e9f-7e43-b6bc-5b2bc43e6801"
    cx_path = cx_dir / ("rollout-2026-07-18T09-00-00-" + cx_sid + ".jsonl")
    cx_path.write_text("\n".join([
        jline(type="session_meta", payload={"session_id": cx_sid, "cwd": str(alpha),
                                            "timestamp": "2026-07-18T09:00:00.000Z"}),
        jline(type="turn_context", payload={"cwd": str(alpha)}),
        jline(type="response_item", payload={"type": "message", "role": "user",
              "content": [{"type": "input_text", "text": "<environment_context><cwd>/x</cwd></environment_context>"}]}),
        jline(type="response_item", payload={"type": "message", "role": "user",
              "content": [{"type": "input_text", "text": "Optimize the ranking algorithm for codex search"}]}),
        jline(type="response_item", payload={"type": "message", "role": "assistant",
              "model": "gpt-5.6-sol", "content": [{"type": "output_text", "text": "done"}]}),
        jline(type="event_msg", payload={"type": "token_count",
              "info": {"total_token_usage": {"input_tokens": 8000, "output_tokens": 2000, "total_tokens": 10000}}}),
    ]) + "\n")
    rec = codex.parse_rollout(str(cx_path), cx_path.stat().st_size)
    check("codex: cwd extracted from session_meta", rec["cwd"] == str(alpha), rec.get("cwd"))
    check("codex: skips injected context, keeps real prompt",
          "ranking algorithm" in rec["search"].lower() and "environment_context" not in rec["search"])
    check("codex: model captured", core.model_label(rec["model"]) == "GPT-5", rec.get("model"))
    check("codex: tokens exact (cumulative, not sampled)",
          rec["in_tok"] + rec["out_tok"] == 10000, rec["in_tok"] + rec["out_tok"])
    check("codex: title from first real prompt", "ranking" in rec["title"].lower())
    cxrows, _cxc = codex.gather_codex({})
    check("codex: gather returns one row", len(cxrows) == 1, str(len(cxrows)))
    check("codex: row tagged kind=CODEX", cxrows and cxrows[0]["kind"] == "CODEX")
    check("codex: sid parsed from filename", cxrows and cxrows[0]["session_id"] == cx_sid,
          cxrows[0]["session_id"] if cxrows else "none")
    allrows, _ar = index.gather_sessions({})
    check("index unions codex sessions with claude", any(r["kind"] == "CODEX" for r in allrows))
    disp = rank.display_col(cxrows[0], "◇", "1d", rank.G)
    check("codex marked in display", "codex" in disp)

    print("== header (banner) ==")
    import banner
    st = ["128", "24", "2h"]

    def _rows(mode):
        return banner.build("dark", 88, st, mode).count("\n") + 1
    # compact is the default: a single-line 'portal' + stats + hints, no splash
    check("compact header is 3 rows (default)", _rows("compact") == 3, _rows("compact"))
    check("off header is 2 rows", _rows("off") == 2, _rows("off"))
    check("full header is the 8-row wordmark", _rows("full") == 8, _rows("full"))
    check("compact is much shorter than full", _rows("compact") < _rows("full"))
    check("default mode is compact",
          banner.build("dark", 88, st) == banner.build("dark", 88, st, "compact"))
    compact = banner.build("dark", 88, st)
    check("compact shows the portal name",
          "portal" in re.sub(r"\033\[[0-9;]*m", "", compact))
    check("compact carries the warm gradient", "38;2;255;249;240" in compact)
    # full mode still renders the real ANSI Shadow art, and it must stay byte-identical to
    # the one on the site — same logo everywhere (compare stripped, the app right-pads).
    full = banner.build("dark", 88, st, "full")
    check("full renders the ANSI Shadow wordmark", all(row in full for row in banner.WORDMARK))
    idx = (REPO / "site" / "index.html").read_text()
    art = "\n".join(r.rstrip() for r in banner.WORDMARK)
    check("app wordmark matches the website's", art in idx, "app + site art diverged")

    print("== live agents (watch) ==")
    import agents as ag
    import narrate
    import watch as watch_mod

    def _ev(**kw):
        return json.dumps(kw)

    def _asst_tool():
        return _ev(type="assistant", message={"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]})

    def _asst_text():
        return _ev(type="assistant", message={"role": "assistant", "content": [
            {"type": "text", "text": "All done — want me to push?"}]})

    def _user_result():
        return _ev(type="user", message={"role": "user", "content": [
            {"type": "tool_result", "content": "ok"}]})

    def _user_text():
        return _ev(type="user", message={"role": "user", "content": [
            {"type": "text", "text": "add retries please"}]})

    # the core state machine, tail -> (state, action)
    check("classify: tool in flight -> working",
          ag.classify([_user_text(), _asst_tool()], age=2)[0] == "working")
    check("classify: working action names the tool",
          "Bash" in ag.classify([_asst_tool()], age=2)[1])
    check("classify: assistant text -> waiting on you",
          ag.classify([_user_text(), _asst_text()], age=2)[0] == "waiting")
    check("classify: tool_result -> thinking",
          ag.classify([_asst_tool(), _user_result()], age=2)[0] == "thinking")
    check("classify: quiet a while -> idle",
          ag.classify([_asst_tool(), _user_result()], age=999)[0] == "idle")
    check("classify: quiet after a question -> waiting (still needs you)",
          ag.classify([_asst_text()], age=999)[0] == "waiting")
    check("classify: empty tail -> not a crash",
          ag.classify([], age=2)[0] in ("working", "idle"))

    # read_tail must return whole lines even when a single line is huge (200MB transcripts)
    big = Path(TMP) / "huge.jsonl"
    big.write_text(_user_text() + "\n" + ("x" * 300000) + "\n" + _asst_text() + "\n")
    tl = ag.read_tail(str(big), want_lines=2)
    check("read_tail returns complete lines past a huge line",
          any(l.startswith("{") and "done" in l for l in tl))

    # live_agents returns a well-formed list (fixtures were just written, so they're 'live')
    la = ag.live_agents()
    check("live_agents returns a list", isinstance(la, list))
    check("live_agents rows have state+project",
          all("state" in a and "project" in a for a in la))

    # the flood fix, driven by real transcripts in the fixture HOME: the board is filesystem-
    # scanned (so nothing live is missed) but skips subagent/scratch paths, portal's own
    # narration sessions, and anything outside the live window.
    proj_dir = Path(TMP) / ".claude" / "projects" / "-live-test"
    proj_dir.mkdir(parents=True, exist_ok=True)
    def _wln(role, text):
        return json.dumps({"type": role, "message": {"role": role,
                          "content": [{"type": "text", "text": text}]}})
    (proj_dir / "real0000-0000-0000-0000-000000000001.jsonl").write_text(
        jline(type="queue-operation", cwd=str(Path(TMP) / "liveproj"), timestamp="2026-07-28T00:00:00Z") + "\n"
        + _wln("assistant", "waiting for your call") + "\n")
    (Path(TMP) / "liveproj").mkdir(exist_ok=True)
    # a narration subprocess session — first prompt is the narration system prompt
    (proj_dir / "narr0000-0000-0000-0000-000000000002.jsonl").write_text(
        _wln("user", "[portal-status] You label a coding agent's status for a dashboard.") + "\n")
    # a scratchpad path — must be skipped like the picker does
    scratch = Path(TMP) / ".claude" / "projects" / "-my-scratchpad"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "scra0000-0000-0000-0000-000000000003.jsonl").write_text(_wln("assistant", "x") + "\n")
    # a stale one — older than the window
    stale = proj_dir / "stal0000-0000-0000-0000-000000000004.jsonl"
    stale.write_text(_wln("assistant", "ancient") + "\n")
    old = time.time() - 3 * 3600
    os.utime(stale, (old, old))

    ids = {a["session_id"] for a in ag.live_agents()}
    check("board includes a real live session", "real0000-0000-0000-0000-000000000001" in ids)
    check("board hides its own narration sessions", "narr0000-0000-0000-0000-000000000002" not in ids)
    check("board skips scratchpad/subagent paths", "scra0000-0000-0000-0000-000000000003" not in ids)
    check("board drops sessions outside the live window", "stal0000-0000-0000-0000-000000000004" not in ids)
    check("live window default is 45 minutes", ag.LIVE_WINDOW() == 45 * 60)
    os.environ["PORTAL_WATCH_WINDOW"] = "10"
    try:
        check("live window is configurable via env", ag.LIVE_WINDOW() == 600)
    finally:
        del os.environ["PORTAL_WATCH_WINDOW"]
    check("nudge line reflects count or is empty",
          ag.nudge_line() == "" or "live" in ag.nudge_line())
    # the nudge names who's waiting, not just a count — stub live_agents for a fixed answer
    _real_la = ag.live_agents
    ag.live_agents = lambda *a, **k: [
        {"project": "billing", "state": "waiting"}, {"project": "api", "state": "working"}]
    check("nudge names a single waiting project", "billing is waiting on you" in ag.nudge_line())
    ag.live_agents = lambda *a, **k: [
        {"project": "billing", "state": "waiting"}, {"project": "api", "state": "waiting"},
        {"project": "web", "state": "working"}]
    nl = ag.nudge_line()
    check("nudge lists multiple waiting projects", "billing" in nl and "api" in nl and "2 waiting" in nl)
    ag.live_agents = lambda *a, **k: []
    check("nudge silent when nothing live", ag.nudge_line() == "")
    ag.live_agents = _real_la

    # picker preview fleet-awareness: find a transcript by id, read its live state
    a_sid = byid["aaaa"]["session_id"]
    check("find_transcript locates a session by id", ag.find_transcript(a_sid) is not None)
    check("find_transcript returns None for a bogus id", ag.find_transcript("nope-nope") is None)
    ls = ag.live_state_of(a_sid)   # fixtures were just written, so they read as live
    check("live_state_of returns state for a live session",
          ls is not None and "state" in ls)

    # narration is OFF by default (opt-in); works with either CLI when asked
    for k in ("PORTAL_MODEL", "PORTAL_MODEL_URL"):
        os.environ.pop(k, None)
    check("narrate: off by default", narrate.backend()[0] is None)
    check("narrate: narrator() is None when off", narrate.narrator() is None)
    os.environ["PORTAL_MODEL"] = "claude"
    check("narrate: opt in to claude", narrate.backend()[0] == "claude")
    os.environ["PORTAL_MODEL"] = "codex"
    check("narrate: opt in to codex", narrate.backend()[0] == "codex")
    os.environ["PORTAL_MODEL_URL"] = "http://localhost:11434/v1"
    check("narrate: a local URL wins", narrate.backend()[0] == "url")
    for k in ("PORTAL_MODEL", "PORTAL_MODEL_URL"):
        os.environ.pop(k, None)
    # codex exec output is an agent transcript, not a bare reply — parse out the model's line
    codex_out = "--------\nuser\nsummarize\ncodex\ntuning the ranking weights\ntokens used\n16,509\n"
    check("narrate: parses the reply out of codex exec output",
          narrate._parse_codex(codex_out) == "tuning the ranking weights")
    # the narrator reads BOTH Claude and Codex transcript shapes
    claude_ln = json.dumps({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "text", "text": "refactoring the auth middleware"}]}})
    codex_ln = json.dumps({"type": "response_item", "payload": {"type": "message",
                "role": "assistant", "content": [{"type": "output_text", "text": "tuning bm25 weights"}]}})
    cfile = Path(TMP) / "narr.jsonl"
    cfile.write_text(claude_ln + "\n" + codex_ln + "\n")
    ctx = narrate._recent_text(str(cfile))
    check("narrate reads Claude transcript text", "refactoring the auth middleware" in ctx)
    check("narrate reads Codex transcript text", "tuning bm25 weights" in ctx)

    # the board renders a frame with a row per live agent
    p = palette.palette("dark")
    board = watch_mod.render([{
        "session_id": "s", "agent": "claude", "project": "billing", "title": "t",
        "state": "waiting", "action": "waiting for you", "age": 30, "tokens": 1200000,
        "cwd": "/tmp", "model_label": "Opus", "mtime": 0,
    }], p)
    plain = re.sub(r"\033\[[0-9;]*m", "", board)
    check("board shows the project and its state", "billing" in plain and "waiting" in plain)
    check("board flags who's waiting on you", "waiting on you" in plain)
    # a working agent: animated spinner + leads with WHAT it's working on (the title/task),
    # with the momentary tool as a dim detail
    workrow = [{"session_id": "w", "agent": "claude", "project": "api",
                "title": "refactor-auth-middleware", "state": "working",
                "action": "Bash pytest", "age": 5, "tokens": 900000,
                "cwd": "/tmp", "model_label": "Opus 4.x", "mtime": 0}]
    f0 = watch_mod.render(workrow, p, 100, None, 0)
    f1 = watch_mod.render(workrow, p, 100, None, 1)
    plainf = re.sub(r"\033\[[0-9;]*m", "", f0)
    check("working agent shows an animated spinner", f0 != f1)
    check("board leads with what it's working on (the task)", "refactor-auth-middleware" in plainf)
    check("board shows the live tool as a detail", "Bash pytest" in plainf)
    # model narration, when configured, is the agentic answer and overrides the title
    narr = lambda a: "reran the eval, waiting on the score"
    plainn = re.sub(r"\033\[[0-9;]*m", "", watch_mod.render(workrow, p, 100, narr, 0))
    check("model narration overrides the task line", "reran the eval" in plainn)
    # a mixed fleet chunks into needs-you / active / idle under preview-style rules,
    # and the header carries the picker's ❯ portal wordmark — one design language
    mk = lambda proj, stt: {"session_id": proj, "agent": "claude", "project": proj,
                            "title": proj + "-task", "state": stt, "action": "x",
                            "age": 10, "tokens": 1000, "cwd": "/tmp", "model_label": "", "mtime": 0}
    mixed = [mk("a", "waiting"), mk("b", "working"), mk("c", "idle")]
    grp = re.sub(r"\033\[[0-9;]*m", "", watch_mod.render(mixed, p, 100, None, 0))
    check("board groups under 'needs you' rule", "── needs you ─" in grp, grp[:200])
    check("board groups under 'active' rule", "── active ─" in grp)
    check("board groups under 'idle' rule", "── idle ─" in grp)
    check("board header wears the picker wordmark", "❯ portal watch" in grp)
    check("board summary carries the ◇ stats idiom", "◇ 3 live" in grp)

    # notification decisions (pure) — fire when an agent starts needing you, or finishes
    A = lambda sid, state, proj="p": {"session_id": sid, "state": state, "project": proj, "action": "x"}
    prev = {"s1": A("s1", "working")}
    cur = {"s1": A("s1", "waiting")}
    tr = watch_mod.transitions(prev, cur)
    check("notify: working->waiting fires 'waiting on you'",
          any("waiting on you" in t for t, _ in tr))
    check("notify: first frame is silent", watch_mod.transitions(None, cur) == [])
    check("notify: staying waiting does not re-fire",
          watch_mod.transitions({"s1": A("s1", "waiting")}, {"s1": A("s1", "waiting")}) == [])
    check("notify: a vanished agent reports done",
          any("done" in t for t, _ in watch_mod.transitions({"s2": A("s2", "working")}, {})))

    # watch --rows: the live fleet as picker rows — same 10-field contract, so preview
    # and the resume dispatch work unchanged in live mode
    rw = watch_mod.rows(mixed, p)
    check("watch rows carry 10 fields", rw and all(len(r.split("\t")) == 10 for r in rw))
    check("watch rows kind SESSION for claude", rw[0].split("\t")[3] == "SESSION")
    check("watch rows carry cwd and sid", rw[0].split("\t")[1] == "/tmp" and rw[0].split("\t")[2] == "a")
    cxr = watch_mod.rows([dict(mk("cx", "waiting"), agent="codex")], p)[0]
    check("watch rows kind CODEX for codex", cxr.split("\t")[3] == "CODEX")
    pvw = subprocess.run([sys.executable, str(PORTAL / "preview.py"), rw[0]],
                         capture_output=True, text=True, env=env, cwd=str(PORTAL))
    check("preview renders a live watch row", pvw.returncode == 0 and bool(pvw.stdout.strip()),
          pvw.stderr[-120:])

    # ticker: POSTs reload(--rows) to fzf's --listen port; exits when fzf is gone
    import http.server
    import threading
    _hits = []

    class _FakeFzf(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            _hits.append(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)).decode())
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    _srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeFzf)
    _sth = threading.Thread(target=_srv.handle_request)   # serve exactly one POST, then die
    _sth.start()
    os.environ["FZF_PORT"] = str(_srv.server_address[1])
    _tth = threading.Thread(target=watch_mod.ticker)
    _tth.start()
    _sth.join(timeout=15)
    _srv.server_close()
    _tth.join(timeout=15)   # next POST hits a closed port → ticker must exit with fzf
    del os.environ["FZF_PORT"]
    check("ticker posts a reload(--rows) action",
          bool(_hits) and _hits[0].startswith("reload(") and "--rows" in _hits[0], str(_hits))
    check("ticker exits when fzf is gone", not _tth.is_alive())

    # the live picker's notify tick: sidecar diff — seed silently, fire on flip to waiting
    _calls = []
    _real_notify = watch_mod._notify
    watch_mod._notify = lambda t, m: _calls.append(t)
    os.environ["PORTAL_WATCHSTATE"] = str(Path(TMP) / "watchstate.json")
    try:
        watch_mod._tick_notify([mk("w9", "working")])
        check("rows tick seeds silently", _calls == [], str(_calls))
        watch_mod._tick_notify([mk("w9", "waiting")])
        check("rows tick fires the waiting transition",
              any("waiting" in t for t in _calls), str(_calls))
    finally:
        watch_mod._notify = _real_notify
        del os.environ["PORTAL_WATCHSTATE"]

    print("== hygiene ==")
    import py_compile
    ok = True
    for f in PORTAL.glob("*.py"):
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            ok = False
            print("   compile error:", e)
    check("all modules compile", ok)
    if shutil.which("zsh"):
        z = subprocess.run(["zsh", "-n", str(PORTAL / "portal.zsh")], capture_output=True)
        check("portal.zsh syntax", z.returncode == 0)
        # the fan-out command builder: what actually gets run in each new shell
        def _resume(cwd, sid, kind):
            r = subprocess.run(
                ["zsh", "-c", "source %s >/dev/null 2>&1; _portal_resume_cmd %r %r %r"
                 % (str(PORTAL / "portal.zsh"), cwd, sid, kind)],
                capture_output=True, text=True, env=dict(os.environ, PORTAL_FLAGS=""))
            return r.stdout.strip()
        rc = _resume("/tmp/proj", "abc123", "SESSION")
        check("dispatch: claude resume command", "claude --resume abc123" in rc and "cd /tmp/proj" in rc)
        check("dispatch: codex resume command",
              "codex resume xyz" in _resume("/tmp/p", "xyz", "CODEX"))
        check("dispatch: NEW session has no --resume",
              "--resume" not in _resume("/tmp/p", "", "NEW"))
    installer = REPO / "site" / "install.sh"
    if installer.exists():
        b = subprocess.run(["bash", "-n", str(installer)], capture_output=True)
        check("install.sh syntax", b.returncode == 0)
        # every shipped module must be in the installer's FILES list, or the feature
        # it powers silently dies on real installs (this is how codex.py went missing)
        text = installer.read_text()
        shipped = sorted(p.name for p in PORTAL.iterdir()
                         if p.suffix in (".py", ".zsh", ".sh") and p.name != "__init__.py")
        missing = [n for n in shipped if n not in text]
        check("installer ships every portal module", not missing, ", ".join(missing))

        # portal's branding is single-hue: warm neutrals (~35 deg, 5-10% chroma) rather
        # than pure greys — never #fff on #000. A value counts as neutral when its
        # channel spread is small in absolute terms (<=18/255) or relative to brightness;
        # dark colors need the absolute floor, since 12/50 is a big ratio but invisible.
        # Two real colors are deliberate: Claude's coral on the agent mark, and the
        # per-project hues in the list column (UX — spot a project without reading it).
        ALLOWED = {"d97757",                                    # Claude coral
                   "a9bdd1", "d1c0a9", "a9d1bd",                # project hues, site demo
                   "b9a9d1", "d1a9bd", "a9cad1"}
        chromatic = []
        for f in (sorted(PORTAL.glob("*.py")) + [PORTAL / "portal.zsh"]
                  + sorted((REPO / "site").glob("*.html")) + [installer]):
            for i, line in enumerate(f.read_text().splitlines(), 1):
                for m in re.finditer(r"#([0-9a-fA-F]{6})\b", line):
                    r, g, b = (int(m.group(1)[j:j + 2], 16) for j in (0, 2, 4))
                    spread = max(r, g, b) - min(r, g, b)
                    if spread > max(18, 0.18 * max(r, g, b)) and m.group(1).lower() not in ALLOWED:
                        chromatic.append("%s:%d %s" % (f.name, i, m.group(0)))
        check("branding stays warm-neutral (agent + project color aside)", not chromatic,
              "; ".join(chromatic[:4]))

        # ...and the warmth is the point: the ink must actually be tinted, not grey
        warm = [c for c in ("#f1ebe0", "#fff9f0", "#a79e92")
                if c not in (REPO / "site" / "index.html").read_text()]
        check("site ink carries the warm tint", not warm, ", ".join(warm))

        # project accents now come from each project's assigned native Ghostty theme
        # (themes.py) — vivid on purpose, not muted. The contract is: legible on the ground
        # and deterministic. themes.py already enforces contrast >= 3.0 when it picks them.
        def _lum(r, g, b):
            f = lambda v: (v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
            r, g, b = f(r / 255), f(g / 255), f(b / 255)
            return 0.2126 * r + 0.7152 * g + 0.0722 * b
        bg = _lum(0x0f, 0x0e, 0x0e)
        dim2 = []
        for name in ("billing", "api", "infra", "search", "portal", "acme-web"):
            mm = re.match(r"\033\[38;2;(\d+);(\d+);(\d+)m", palette_mod.pastel(name, "dark"))
            if mm:
                r, g, b = (int(x) for x in mm.groups())
                la = _lum(r, g, b)
                contrast = (max(la, bg) + 0.05) / (min(la, bg) + 0.05)
                if contrast < 2.5:
                    dim2.append("%s contrast=%.1f" % (name, contrast))
        check("project accents stay legible on the ground", not dim2, "; ".join(dim2))
        # and they are a real Ghostty theme, chosen deterministically
        import themes as themes_mod
        if themes_mod.themes_dir():
            check("project maps to a native Ghostty theme", bool(themes_mod.theme_for("api")))
            check("theme mapping is deterministic",
                  themes_mod.theme_for("billing") == themes_mod.theme_for("billing"))

    print(f"\n{PASS} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
