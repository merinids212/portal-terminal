#!/bin/zsh
# Flip the .expand sidecar (reveals/hides superseded sessions). Bound to ^E in the picker.
emulate -L zsh
f="${PORTAL_EXPAND_FILE:-${0:A:h}/.expand}"
if [[ -s "$f" && "$(cat "$f")" == 1 ]]; then print -n 0 > "$f"; else print -n 1 > "$f"; fi
