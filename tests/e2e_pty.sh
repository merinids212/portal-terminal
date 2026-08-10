#!/usr/bin/env bash
# True end-to-end picker test: real fzf in a real pty. Builds a fixture session,
# stubs `claude`, types a query, presses Enter, and asserts portal cd'd to the
# session's folder and exec'd `claude --resume <sid>`. (This exact test caught a
# bind action that broke picker startup on distro fzf.)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
export HOME="$T"

mkdir -p "$T/work/api" "$T/.claude/projects/-fx" "$T/.claude/portal" "$T/bin"
printf '%s\n%s\n%s\n' \
 '{"type":"q","cwd":"'"$T"'/work/api","timestamp":"2026-07-17T10:00:00.000Z"}' \
 '{"type":"t","aiTitle":"refactor-auth-middleware"}' \
 '{"type":"user","message":{"role":"user","content":"refactor the auth middleware tokens"}}' \
 > "$T/.claude/projects/-fx/aaaa1111-0000-0000-0000-000000000001.jsonl"

printf '#!/bin/bash\necho "CLAUDE-STUB argv: $@"\necho "CLAUDE-STUB pwd: $(pwd)"\n' > "$T/bin/claude"
chmod +x "$T/bin/claude"
export PATH="$T/bin:$PATH"

printf 'export PORTAL_DIR=%s/portal\nsource %s/portal/portal.zsh\nportal\n' "$ROOT" "$ROOT" > "$T/drive.zsh"

if script -qec true /dev/null >/dev/null 2>&1; then
  ( sleep 3; printf "auth"; sleep 2; printf "\r"; sleep 2 ) \
    | script -qec "zsh $T/drive.zsh" /dev/null > "$T/out.txt" 2>&1 || true
else
  # macOS script(1) has a different interface
  ( sleep 3; printf "auth"; sleep 2; printf "\r"; sleep 2 ) \
    | script -q "$T/out.txt" zsh "$T/drive.zsh" >/dev/null 2>&1 || true
fi

if grep -aq "CLAUDE-STUB argv: --resume aaaa1111" "$T/out.txt" \
   && grep -aq "CLAUDE-STUB pwd: $T/work/api" "$T/out.txt"; then
  echo "e2e pty: picker → search → enter → cd + resume ✓"
else
  echo "e2e pty FAILED — output tail:"
  tail -8 "$T/out.txt" | cat -v
  exit 1
fi

# ^N: start a NEW session in the selected row's folder (no --resume)
if script -qec true /dev/null >/dev/null 2>&1; then
  ( sleep 3; printf "auth"; sleep 2; printf "\x0e"; sleep 2 ) \
    | script -qec "zsh $T/drive.zsh" /dev/null > "$T/out2.txt" 2>&1 || true
else
  ( sleep 3; printf "auth"; sleep 2; printf "\x0e"; sleep 2 ) \
    | script -q "$T/out2.txt" zsh "$T/drive.zsh" >/dev/null 2>&1 || true
fi
if grep -aq "CLAUDE-STUB pwd: $T/work/api" "$T/out2.txt" \
   && ! grep -aq -- "--resume" "$T/out2.txt"; then
  echo "e2e pty ^N: fresh session in the row's folder ✓"
else
  echo "e2e ^N FAILED — output tail:"
  tail -8 "$T/out2.txt" | cat -v
  exit 1
fi

# ^Y: copy the launch command (clipboard stubbed to a file)
printf '#!/bin/sh\ncat > %s\n' "$T/clip.txt" > "$T/bin/pbcopy"
chmod +x "$T/bin/pbcopy"
if script -qec true /dev/null >/dev/null 2>&1; then
  ( sleep 3; printf "auth"; sleep 2; printf "\x19"; sleep 2; printf "\x1b"; sleep 1 ) \
    | script -qec "zsh $T/drive.zsh" /dev/null >/dev/null 2>&1 || true
else
  ( sleep 3; printf "auth"; sleep 2; printf "\x19"; sleep 2; printf "\x1b"; sleep 1 ) \
    | script -q /dev/null zsh "$T/drive.zsh" >/dev/null 2>&1 || true
fi
if grep -q -- "--resume aaaa1111" "$T/clip.txt" 2>/dev/null; then
  echo "e2e pty ^Y: launch command copied ✓"
else
  echo "e2e ^Y FAILED — clip: $(cat -v "$T/clip.txt" 2>/dev/null)"
  exit 1
fi

# live watch picker: Enter on the (only, waiting) agent resumes it here
printf 'export PORTAL_DIR=%s/portal\nexport PORTAL_NO_PROC=1\nsource %s/portal/portal.zsh\nportal watch\n' "$ROOT" "$ROOT" > "$T/drivew.zsh"
touch "$T/.claude/projects/-fx/aaaa1111-0000-0000-0000-000000000001.jsonl"  # freshen: live
# precondition: --rows must see the fixture before we drive the TUI, else fail loudly
PORTAL_NO_PROC=1 python3 "$ROOT/portal/watch.py" --rows > "$T/rows.txt" 2>"$T/rows.err" || true
if ! grep -aq "aaaa1111" "$T/rows.txt"; then
  echo "watch --rows precondition FAILED — stderr:"
  cat "$T/rows.err"
  echo "--- rows out:"; cat -v "$T/rows.txt"
  exit 1
fi
echo "e2e watch rows precondition ✓"
# A/B: static first (no --listen / ticker — isolates those), then the full live mode
printf 'export PORTAL_DIR=%s/portal\nexport PORTAL_NO_PROC=1\nexport PORTAL_WATCH_STATIC=1\nsource %s/portal/portal.zsh\nportal watch\nprint "\\nWATCH_EXIT=$?"\n' "$ROOT" "$ROOT" > "$T/drivews.zsh"
run_watch() {  # $1: drive script  $2: out file
  if script -qec true /dev/null >/dev/null 2>&1; then
    ( sleep 4; printf "\r"; sleep 2 ) \
      | script -qec "zsh $1" /dev/null > "$2" 2>&1 || true
  else
    ( sleep 4; printf "\r"; sleep 2 ) \
      | script -q "$2" zsh "$1" >/dev/null 2>&1 || true
  fi
}
run_watch "$T/drivews.zsh" "$T/outws.txt"
if grep -aq "CLAUDE-STUB argv: --resume aaaa1111" "$T/outws.txt"; then
  echo "e2e pty watch (static): enter → jump into agent ✓"
else
  echo "e2e watch STATIC FAILED"
  echo "exit marker: $(grep -ao 'WATCH_EXIT=[0-9]*' "$T/outws.txt" | tail -1)"
  echo "readable words in capture:"
  strings "$T/outws.txt" | grep -av "^.\{0,2\}$" | tail -20
  exit 1
fi
run_watch "$T/drivew.zsh" "$T/outw.txt"
if grep -aq "CLAUDE-STUB argv: --resume aaaa1111" "$T/outw.txt" \
   && grep -aq "CLAUDE-STUB pwd: $T/work/api" "$T/outw.txt"; then
  echo "e2e pty watch (live): enter → jump into agent ✓"
else
  echo "e2e watch LIVE FAILED — full output:"
  cat -v "$T/outw.txt"
  exit 1
fi

# teleport path needs no pty: confident query must cd + resume directly
GO_OUT="$(zsh -c "export PORTAL_DIR=$ROOT/portal; source $ROOT/portal/portal.zsh; portal go \"refactor auth middleware tokens\"" 2>&1 || true)"
if grep -q "CLAUDE-STUB argv: --resume aaaa1111" <<<"$GO_OUT" \
   && grep -q "CLAUDE-STUB pwd: $T/work/api" <<<"$GO_OUT"; then
  echo "e2e go: teleport → cd + resume ✓"
else
  echo "e2e go FAILED:"; tail -5 <<<"$GO_OUT"; exit 1
fi

# pull path: from another dir, history must follow + resume forked
mkdir -p "$T/work/gateway"
PULL_OUT="$(cd "$T/work/gateway" && zsh -c "export PORTAL_DIR=$ROOT/portal; source $ROOT/portal/portal.zsh; portal pull \"auth middleware\"" 2>&1 || true)"
if grep -q -- "--resume aaaa1111-0000-0000-0000-000000000001 --fork-session" <<<"$PULL_OUT" \
   && grep -q "CLAUDE-STUB pwd: $T/work/gateway" <<<"$PULL_OUT"; then
  echo "e2e pull: import → forked resume in new dir ✓"
else
  echo "e2e pull FAILED:"; tail -5 <<<"$PULL_OUT"; exit 1
fi

# version command
V_OUT="$(zsh -c "export PORTAL_DIR=$ROOT/portal; source $ROOT/portal/portal.zsh; portal version")"
grep -q "portal 1" <<<"$V_OUT" && echo "e2e version: $V_OUT ✓" || { echo "version FAILED: $V_OUT"; exit 1; }
