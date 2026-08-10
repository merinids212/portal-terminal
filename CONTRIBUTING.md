# Contributing

## Ground rules

- **Zero runtime dependencies.** Pure Python (stdlib only, ≥3.8) + zsh + fzf (≥0.38). PRs that
  add a package, a daemon, or a build step will be declined — that constraint is the product.
- **Everything measurable gets a test.** `tests/run_tests.py` builds a throwaway `$HOME` with
  synthetic transcripts and runs ~50 checks in a few seconds. Add yours next to the feature.
- **Never lose a session.** The suite's no-loss invariant (every transcript in the cache appears
  in the index) must hold. Anything that filters rows needs a counted, revealable path (like the `+N` badge).
- **Claude Code owns the transcript format.** We read defensively (bounded head/tail, regex over
  raw lines, graceful on malformed JSON) and never write to `~/.claude/projects` except through
  `mobility.migrate`, which streams, preserves mtimes, and is atomic.

## Working on it

```bash
git clone https://github.com/merinids212/portal-terminal
cd portal-terminal
python3 tests/run_tests.py        # must stay green
shellcheck site/install.sh        # CI enforces this
```

Point your shell at your checkout to live-test the picker:

```bash
PORTAL_DIR=$PWD/portal source portal/portal.zsh && portal
```

CI runs the unit suite on macOS + Ubuntu on every push; the pty e2e, shellcheck, and
installer smoke test run on the Ubuntu job. The code targets Python ≥3.8 (stdlib only).

## Layout

| file | role |
|---|---|
| `portal/core.py` | tokenizer, cleaner, BM25, topics — the language layer |
| `portal/index.py` | transcript scanning + cache (bounded reads; files can be 200 MB) |
| `portal/rank.py` | search brain: browse / search / `--go` / `--why`, enrichment |
| `portal/mobility.py` | doctor / mv / pull / status / overlap / ls |
| `portal/preview.py` | the right-hand session card |
| `portal/portal.zsh` | the user-facing function (must stay a function — it `cd`s your shell) |
| `tests/run_tests.py` | the whole suite, zero deps |
