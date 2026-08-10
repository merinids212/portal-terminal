# portal — next steps

Curated from anthems01's `portal-upgrade` kit, reconciled against what's already shipped.
Verified on a real transcript: sessions **do** carry `model` (`claude-opus-4-8`), token `usage`
(input/output/cache counters), and Bash `tool_use` blocks — so the data-dependent features work.

## Tier 1 — genuine upgrades, do first (each its own reviewed PR)

- **Model per session** (kit add 01) — extract `model` in `index.parse_session`, thread through
  `statsjson` (field 10, **not** a new column — the 10-field row contract is asserted by the test),
  show in `preview.py`, bump `CACHE_VERSION` (→7). Low risk, high signal: no picker shows this.
- **Token spend + `portal heavy`** (kit add 02) — sum `usage` counters via the existing sampled
  read in `index.sample_activity`; new `portal heavy` ranks sessions by tokens burned (what to
  compact). The standout feature — quantifies cost per past session.
- **`portal grep <pattern>`** (kit add 11) — literal/regex search *inside* transcript content
  (not just titles). Bounded reads, same defensive parsing. Answers "the session where I discussed X."
- **Shell completions** (kit add 12) — zsh+bash completion for subcommands/flags. Pure "finished
  tool" polish, near-zero risk.

## Tier 2 — nice, lower priority

- **Stats dashboard** (add 04) — hours/tokens per project, busiest day. We sketched this earlier.
- **Pin favourites** (add 10) — sidecar + `rank.emit_browse`, a `★` prefix.
- **Export (+redaction)** (add 05) / **audit `<sid>`** (add 06) — share/compliance; more niche.

## Already shipped — reconcile, don't reapply

The kit predates recent work. These overlap what portal already has; cherry-pick only net-new bits:
- **status: stale/unsafe/attention** (add 07) → we have `portal status` (dirty/unpushed/live).
- **git panel + commit→session** (add 09) → we have commits-in-preview via `git log` windowing.
- **filters/sort** (add 03), **commands-run** (add 08) → partial overlap with search + preview.

## Process

- Fork → feature branch → **`tests/run_tests.py` green** → one PR per feature → review + merge.
- **Do not** run the kit's `MASTER-PROMPT.md` (builds all 12 at once) — collides with shipped work.
- Every add re-encodes portal's rules correctly (zero-dep, no daemon, retrospective, no-loss,
  10-field contract, cache-version bump). Keep that discipline.

## Scaffolding (optional, free)

`portal-upgrade/scaffolding/` has issue/PR templates, SECURITY.md, CoC, ROADMAP, CHANGELOG —
standard open-source health files. Drop in as-is if you want the "maintained project" signal.
