#!/usr/bin/env bash
# portal — install script.  Usage:
#   curl -fsSL https://portal.cybercorpresearch.com/install.sh | bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/merinids212/portal-terminal/main/portal"
DEST="${PORTAL_DIR:-$HOME/.claude/portal}"
FILES=(core.py index.py rank.py preview.py banner.py theme.py palette.py mobility.py \
       codex.py themes.py watch.py agents.py narrate.py portal.zsh copycmd.sh toggle_expand.sh)

grn() { printf '\033[38;5;230m%s\033[0m\n' "$1"; }
dim() { printf '\033[38;5;187m%s\033[0m\n' "$1"; }
err() { printf '\033[38;5;203m%s\033[0m\n' "$1" >&2; }

grn "◇ installing portal"

# --- deps ---------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 is required (portal is written in Python). Install it and re-run."
  exit 1
fi
if ! command -v zsh >/dev/null 2>&1; then
  err "zsh is required (portal is a zsh function so it can cd your shell)."
  err "  macOS: built in · Debian/Ubuntu: sudo apt-get install zsh · Fedora: sudo dnf install zsh"
  exit 1
fi
if ! command -v fzf >/dev/null 2>&1; then
  dim "  fzf not found — installing…"
  SUDO="sudo"; { [ "$(id -u)" -eq 0 ] || ! command -v sudo >/dev/null 2>&1; } && SUDO=""
  if command -v brew >/dev/null 2>&1; then
    brew install fzf
  elif command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -qq && $SUDO apt-get install -y fzf
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y fzf
  elif command -v pacman >/dev/null 2>&1; then
    $SUDO pacman -S --noconfirm fzf
  else
    err "  couldn't auto-install fzf. Install it (https://github.com/junegunn/fzf) and re-run."
    exit 1
  fi
fi
FZF_VER="$(fzf --version 2>/dev/null | cut -d" " -f1)"
FZF_MAJ="${FZF_VER%%.*}"; FZF_REST="${FZF_VER#*.}"; FZF_MIN="${FZF_REST%%.*}"
case "${FZF_MAJ}${FZF_MIN}" in *[!0-9]*|"") FZF_MAJ=1; FZF_MIN=0;; esac  # unparsable -> don't block
if [ "$FZF_MAJ" -eq 0 ] && [ "$FZF_MIN" -lt 38 ]; then
  err "fzf $FZF_VER is too old — portal needs ≥ 0.38 (Ubuntu 22.04's apt version is 0.29)."
  err "  get a current build: https://github.com/junegunn/fzf/releases  (or brew upgrade fzf)"
  exit 1
fi
if [ ! -f /usr/share/dict/words ]; then
  dim "  note: no /usr/share/dict/words — compound-word search improves with:"
  dim "        sudo apt-get install wamerican   (or your distro's words package)"
fi

# --- download the tool --------------------------------------------------
# Stage everything first, then move into place: a dropped connection mid-loop must
# never leave a working install half-updated (new rank.py + old core.py = broken).
dim "  fetching portal into $DEST"
# stage INSIDE $DEST so every mv below is a same-filesystem rename — staging in /tmp
# would make mv a copy+unlink on split mounts, reopening the half-updated window
mkdir -p "$DEST"
STAGE="$(mktemp -d "$DEST/.stage-XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
for f in "${FILES[@]}"; do
  curl -fsSL "$REPO_RAW/$f" -o "$STAGE/$f"
done
python3 - "$STAGE" <<'PYEOF'
import ast, glob, sys
for p in glob.glob(sys.argv[1] + "/*.py"):
    ast.parse(open(p).read())   # a truncated download must not replace a working file
PYEOF
for f in "${FILES[@]}"; do
  mv -f "$STAGE/$f" "$DEST/$f"
done

# --- wire the shell -----------------------------------------------------
LINE="source $DEST/portal.zsh"
RC="$HOME/.zshrc"
# match only an ACTIVE source line — a commented-out one means portal was disabled
# on purpose, but a fresh install should still wire itself up
if [ -f "$RC" ] && grep -qE "^[[:space:]]*source[[:space:]].*/portal\.zsh" "$RC"; then
  dim "  ~/.zshrc already sources portal"
else
  printf '\n# portal — Claude Code session picker (portal.cybercorpresearch.com)\n%s\n' "$LINE" >> "$RC"
  dim "  added source line to ~/.zshrc"
fi

case "${SHELL:-}" in
  *zsh) : ;;
  *) dim "  note: your login shell is ${SHELL:-unknown} — portal is a zsh function."
     dim "        run it from zsh, or make zsh your shell:  chsh -s \$(command -v zsh)" ;;
esac

grn "◇ done"
dim "  open a new terminal (or: source ~/.zshrc), then run:  portal"
dim "  optional — set your launch flags in ~/.zshrc before the source line:"
dim "    PORTAL_FLAGS=(--dangerously-skip-permissions --chrome)"
