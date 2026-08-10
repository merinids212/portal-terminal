# portal

[![tests](https://github.com/merinids212/portal-terminal/actions/workflows/test.yml/badge.svg)](https://github.com/merinids212/portal-terminal/actions/workflows/test.yml)

A session picker for [Claude Code](https://claude.com/claude-code) and
[Codex](https://developers.openai.com/codex/cli). Type what you remember, land back in the
session. Runs locally against the transcripts already on your disk — no cloud, no model, no index
service.

```
██████╗  ██████╗ ██████╗ ████████╗ █████╗ ██╗
██╔══██╗██╔═══██╗██╔══██╗╚══██╔══╝██╔══██╗██║
██████╔╝██║   ██║██████╔╝   ██║   ███████║██║
██╔═══╝ ██║   ██║██╔══██╗   ██║   ██╔══██║██║
██║     ╚██████╔╝██║  ██║   ██║   ██║  ██║███████╗
╚═╝      ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚══════╝
```

## Install

```bash
curl -fsSL https://portal.cybercorpresearch.com/install.sh | bash
```

Open a new terminal (or `source ~/.zshrc`) and run `portal`.

macOS or Linux · zsh · python3 ≥ 3.8 · [fzf](https://github.com/junegunn/fzf) ≥ 0.38 (the
installer adds it via brew/apt/dnf/pacman if missing). Codex sessions are indexed automatically
when `~/.codex` exists.

## Commands

| | |
|---|---|
| `portal` | the picker — type a phrase, the list re-ranks per keystroke |
| `portal watch` | the picker over every agent running right now — ↵ jumps into one (`--once`/`--interval N` for the classic board) |
| `portal go "the webhook retry fix"` | rank headless and jump straight in; ambiguous matches open the picker pre-filtered |
| `portal pull "the auth refactor"` | copy that session's history into the current folder and resume it forked — original untouched (`--move` relocates instead) |
| `portal mv <src> <dst>` | move a project folder *and* its session history together |
| `portal doctor [--list] [--sid <sid8>]` | find sessions orphaned by a moved or deleted folder, relink them |
| `portal status` | git state across every session folder — dirty, unpushed, live |
| `portal overlap` | flag recent sessions in one repo that touched the same files |
| `portal heavy [--repo NAME]` | past sessions ranked by tokens spent |
| `portal grep [-i] [-F] [--repo NAME] <pattern>` | search inside transcript text, not just titles |
| `portal ls [--json]` | every session as rows or JSON, for scripting |
| `portal --here` | limit the picker to the current directory |
| `portal update` · `portal version` | self-update · print version |

Inside the picker:

| key | |
|---|---|
| type | re-rank live (empty = browse by recency) |
| `↵` | cd to the session's folder and resume it *here* |
| `⇥` | mark more than one — then `↵` sends each to its own shell |
| `^O` | send the selection to their own shells (fan out) even if it's just one |
| `^N` | start a fresh session in that folder |
| `^Y` | copy the launch command instead |
| `^E` | reveal collapsed older versions |
| `^/` | toggle the preview pane |
| `^R` | rescan for sessions started since the picker opened |

## What you get

**Search that ranks, not filters.** Tuned BM25 over titles and your actual prompts — compound
splitting (`dealflow` → `deal flow`), query expansion, title boost — blended with recency, so a
session you touched an hour ago wins near-ties. ~60 ms per keystroke, entirely offline.

**Both agents in one list.** Claude Code transcripts (`~/.claude/projects`) and Codex rollouts
(`~/.codex/sessions`) are indexed side by side. Each row wears its agent's mark; `↵` runs
`claude --resume` or `codex resume` accordingly. Codex reports cumulative usage, so its token
totals are exact rather than sampled.

**A preview worth reading.** Folder, topic chips, the session's arc (first ask → last thing
touched), files edited, commits made during the session, related threads elsewhere, an activity
sparkline, and which model ran it — Opus, Sonnet, GPT-5.

**Cost, in hindsight.** `portal heavy` ranks sessions by tokens burned, which is the retrospective
version of deciding what to `/compact` before you reopen one.

**Full-text recall.** `portal grep` searches transcript bodies, so "the session where I worked out
the Cloudflare Worker setup" is a findable thing. Bounded reads — transcripts run to hundreds of MB.

**Sessions that survive a reorg.** Claude Code keys transcripts to a folder path, so a plain `mv`
strands them. `portal mv` moves both; `portal pull` brings a session's history to where you are now
(forked, original untouched); `portal doctor` finds and relinks whatever already got orphaned.

**A list you can scan.** `time · project · title`, near-duplicate reruns collapsed under a `+N`
badge, `●` for sessions being written right now, `⌁` for a folder that moved. Monochrome, except
each project's stable color and each agent's own mark.

**A cockpit for a fleet.** Mark several sessions with `⇥` and `↵` sends each into its own shell —
on macOS + [Ghostty](https://ghostty.org) a real window per agent, colored by that project's
theme (below); inside tmux, a window per agent. Then `portal watch` is the live board: every
agent running right now, its state (`working` with a live spinner / `thinking` / `waiting` /
`idle`), and — the point — **what it's working on**. That comes in three tiers: always the
session's title (the task) plus the momentary tool as a dim detail, and, optionally, an agentic one-liner if you turn narration on (below). It reads transcripts, so
it shows *all* live agents — whether portal launched them or not — deduped to the same clean set
the picker shows. A plain `portal` still just jumps into one, as always.

**Project color from your themes, not a random wash.** Each project maps deterministically to one
of Ghostty's own bundled themes; the board tints it with a vivid accent from that theme, and a
dispatched window *is* that theme — so the row on the board and the window on screen are one
thing. Falls back to a muted hue-hash where Ghostty's themes aren't present.

**Plain-English status — optional.** By default the board shows the mechanical line (state, task
title, current tool) — instant and free. Opt in for a narrated sentence instead: `PORTAL_MODEL=claude`
(Claude Code CLI), `=codex` (Codex CLI), or `PORTAL_MODEL_URL=http://localhost:11434/v1` for a local
model (Ollama, LM Studio — cheapest, no tokens). Background-computed, cached, throttled to ~once/20s
per agent; the transcript is fed as data, never instructions.

## Configure

Put these in `~/.zshrc` **above** the `source` line:

```bash
PORTAL_FLAGS=(--dangerously-skip-permissions --chrome)   # flags portal launches Claude with
export PORTAL_CODE_ROOT=~/code                           # where doctor looks for folders
export PORTAL_BANNER=compact                             # header: compact (default) · full · off
export PORTAL_MODEL=claude                               # optional watch narration: claude / codex / a URL (default off)
export PORTAL_NOTIFY=1                                   # silent notify when an agent needs you (default off)
export PORTAL_WATCH_WINDOW=45                            # minutes an agent can be quiet and still show on the board
```

The header is a single line by default so the list starts at the top. `PORTAL_BANNER=full` shows
the ANSI-Shadow `PORTAL` art (the same as the site); `off` drops the mark entirely.

## How it works

`index.py` reads `~/.claude/projects/*/*.jsonl` and, when present, `~/.codex/sessions/**`,
pulling each session's real folder, title, prompts, model, and usage out of the file with bounded
head+tail reads, then caching a compact index. `rank.py` is the search brain; fzf is a dumb
renderer driven per keystroke via `change:reload`. Pure python + zsh + fzf, no dependencies.

Nothing is ever written back to your transcripts except by `mv`, `pull`, and `doctor`, which
stream atomically and preserve timestamps.

## Development

```bash
python3 tests/run_tests.py    # unit + integration, throwaway $HOME
bash tests/e2e_pty.sh         # drives the real picker in a pty
```

CI runs the unit suite on macOS and Linux; the pty e2e, shellcheck, and installer smoke test run on the Linux job.

## License

MIT
