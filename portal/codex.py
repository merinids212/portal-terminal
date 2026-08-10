#!/usr/bin/env python3
"""Codex session source — bring ~/.codex/sessions rollouts into the portal picker,
alongside Claude Code sessions. Same house rules: zero-dep, bounded reads,
retrospective, defensive on malformed JSON.

Rollout format (~/.codex/sessions/YYYY/MM/DD/rollout-<iso>-<sid>.jsonl):
  {"type":"session_meta","payload":{"cwd":..,"session_id":..,"timestamp":..}}
  {"type":"turn_context","payload":{"cwd":..}}
  {"type":"response_item","payload":{"type":"message","role":"user"|"assistant",
      "content":[{"type":"input_text"|"output_text","text":..}]}}
  {"type":"event_msg","payload":{"type":"token_count",
      "info":{"total_token_usage":{"input_tokens":N,"output_tokens":N,...}}}}
  model id appears as  "model":"gpt-5..."

Codex reports CUMULATIVE token totals (the last token_count is the session total),
so — unlike the sampled Claude path — token counts here are exact.
"""
import json
import os

import index
from core import clean_user, classify_type, model_label

CODEX_ROOT = os.path.join(index.HOME, ".codex", "sessions")

# system-injected user turns that aren't real prompts (Codex wraps context in these)
_INJECTED = ("<environment_context", "<permissions", "<user_instructions",
             "<permissions instructions", "<turn_context", "<repo_context",
             "<system", "<current_")


def available():
    return os.path.isdir(CODEX_ROOT)


def _clean_codex_prompt(txt):
    """A real user prompt, or None for Codex's system-injected context turns."""
    s = (txt or "").lstrip()
    # only known injected tags — a blanket "<" would drop real prompts that happen
    # to start with markup, like "<div> isn't rendering"
    if not s or s.lower().startswith(_INJECTED):
        return None
    return clean_user(txt)


def _msg(payload):
    """(role, text) for a response_item message payload, else None."""
    if not isinstance(payload, dict) or payload.get("type") != "message":
        return None
    parts = payload.get("content") or []
    txt = "".join(p.get("text", "") for p in parts
                  if isinstance(p, dict) and p.get("type") in ("input_text", "output_text", "text"))
    return payload.get("role"), txt


def parse_rollout(path, size=0):
    """Extract a portal record from one Codex rollout. Bounded head+tail reads."""
    cwd = sid = None
    model = None
    in_tok = out_tok = 0
    first_prompt = last_prompt = None
    prompts = []

    def add(u):
        if u and len(u) > 3 and u not in prompts:
            prompts.append(u)

    head = index.read_head(path, 400)
    tail = index.read_tail(path, 200_000)

    for line in head:
        try:
            o = json.loads(line)
        except Exception:
            continue
        t, p = o.get("type"), (o.get("payload") or {})
        if t == "session_meta":
            cwd = cwd or p.get("cwd")
            sid = sid or p.get("session_id") or p.get("id")
        elif t == "turn_context":
            cwd = cwd or p.get("cwd")
        elif t == "response_item":
            r = _msg(p)
            if r and r[0] == "user":
                u = _clean_codex_prompt(r[1])
                if u:
                    if first_prompt is None:
                        first_prompt = u
                    add(u)
        if model is None:
            mm = index._RE_MODEL.search(line)
            if mm:
                model = index._junq(mm.group(1))

    # tail: last cumulative token total wins; last real user prompt; late cwd/model fallback
    for line in tail:
        if model is None:
            mm = index._RE_MODEL.search(line)
            if mm:
                model = index._junq(mm.group(1))
        try:
            o = json.loads(line)
        except Exception:
            continue
        t, p = o.get("type"), (o.get("payload") or {})
        if t == "event_msg" and p.get("type") == "token_count":
            info = p.get("info") or {}
            tot = info.get("total_token_usage") or {}
            if tot.get("input_tokens") or tot.get("output_tokens"):
                in_tok = tot.get("input_tokens", 0) or 0
                out_tok = tot.get("output_tokens", 0) or 0
        elif t == "response_item":
            r = _msg(p)
            if r and r[0] == "user":
                u = _clean_codex_prompt(r[1])
                if u:
                    last_prompt = u
                    add(u)
        elif t == "turn_context" and not cwd:
            cwd = p.get("cwd")

    title = (first_prompt or "codex session")[:60]
    last_worked = last_prompt or first_prompt or ""
    search = " ".join(prompts[:24])[:1600]
    stype = classify_type(title, search, 0)
    return {
        "cwd": cwd or "", "branch": "", "title": title,
        "last_worked": last_worked[:200], "search": search,
        "first": (first_prompt or "")[:160], "files": [], "type": stype,
        "model": model, "size": size, "dur": 0,
        "spark": "", "msgs": 0, "tools": 0, "edits": 0,
        "in_tok": in_tok, "out_tok": out_tok, "agent": "codex",
    }


def _iter_rollouts():
    """Yield (path, stat) for every rollout .jsonl under the codex sessions tree."""
    for dirpath, _dirs, files in os.walk(CODEX_ROOT):
        for name in files:
            if name.endswith(".jsonl") and name.startswith("rollout-"):
                path = os.path.join(dirpath, name)
                try:
                    yield path, os.stat(path)
                except OSError:
                    continue


def _sid_from_name(name):
    # rollout-2026-07-19T13-02-51-<uuid>.jsonl  ->  <uuid>
    base = name[:-6] if name.endswith(".jsonl") else name
    parts = base.split("-")
    return "-".join(parts[-5:]) if len(parts) >= 5 else base


def locate(sid):
    """Rollout path for a codex session id, or None — walks the date-keyed tree."""
    if not available():
        return None
    for dirpath, _dirs, files in os.walk(CODEX_ROOT):
        for fn in files:
            if sid in fn and fn.endswith(".jsonl"):
                return os.path.join(dirpath, fn)
    return None


def gather_codex(cache):
    """Portal rows for Codex sessions, cached by (mtime,size) like the Claude path.
    Returns (rows, cache_updates). Silent no-op when Codex isn't installed."""
    rows, updates = [], {}
    if not available():
        return rows, updates
    for path, st in _iter_rollouts():
        sig = [index.CACHE_VERSION, st.st_mtime_ns, st.st_size, "codex"]
        cached = cache.get(path)
        if cached and cached.get("sig") == sig:
            info = cached
        else:
            try:
                info = {"sig": sig, **parse_rollout(path, st.st_size)}
            except Exception:
                continue
        cwd = info.get("cwd")
        if not cwd:
            # cwd unknown (fresh/truncated rollout): keep it out of the cache too, so
            # cache and index never disagree — retried next scan
            continue
        if index.is_machinery(info):
            continue               # narrator-spawned codex exec rollouts are not sessions
        updates[path] = info
        sid = _sid_from_name(os.path.basename(path))
        rows.append({
            "mtime": st.st_mtime, "cwd": cwd, "session_id": sid,
            "kind": "CODEX", "title": info["title"], "last_worked": info["last_worked"],
            "branch": "", "project": os.path.basename(cwd) or cwd,
            "search": info.get("search", ""), "type": info.get("type", "·"),
            "stats": {
                "size": info.get("size", 0), "dur": 0, "msgs": 0, "tools": 0,
                "edits": 0, "spark": "", "first": info.get("first", ""), "files": [],
                "model": info.get("model"), "model_label": model_label(info.get("model")),
                "in_tok": info.get("in_tok", 0), "out_tok": info.get("out_tok", 0),
                "agent": "codex",
            },
        })
    return rows, updates
