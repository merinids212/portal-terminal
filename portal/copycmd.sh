#!/bin/zsh
# Build the launch command for a row and copy it to the clipboard (used by ^Y in fzf).
# Cross-platform: pbcopy (macOS) → wl-copy (Wayland) → xclip/xsel (X11).
# Args: cwd sid kind [flags...]
emulate -L zsh
cwd=$1; sid=$2; kind=$3; shift 3
flags=("$@")
if [[ "$kind" == CODEX ]]; then
  cmd="cd ${(q)cwd} && codex resume ${sid}"
elif [[ "$kind" == NEW || -z "$sid" ]]; then
  cmd="cd ${(q)cwd} && claude ${flags[*]}"
else
  cmd="cd ${(q)cwd} && claude --resume ${sid} ${flags[*]}"
fi
if command -v pbcopy >/dev/null 2>&1; then
  printf '%s' "$cmd" | pbcopy
elif command -v wl-copy >/dev/null 2>&1; then
  printf '%s' "$cmd" | wl-copy
elif command -v xclip >/dev/null 2>&1; then
  printf '%s' "$cmd" | xclip -selection clipboard
elif command -v xsel >/dev/null 2>&1; then
  printf '%s' "$cmd" | xsel -ib
else
  print -u2 "portal: no clipboard tool (pbcopy/wl-copy/xclip/xsel) — command was: $cmd"
  exit 1
fi
