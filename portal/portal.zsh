# portal — smart pre-Claude session picker (monochrome CRT, live semantic search).
# `portal` browse/search · `portal go "<query>"` teleport · `portal --here` this dir.
# Sourced from ~/.zshrc. Must be a function (not a script) so `cd` moves the live shell.

export PORTAL_DIR="${PORTAL_DIR:-$HOME/.claude/portal}"
export PORTAL_VERSION="1.3.0"
# How portal launches Claude. Override in your ~/.zshrc BEFORE sourcing this file, e.g.:
#   PORTAL_FLAGS=(--dangerously-skip-permissions --chrome)
if (( ! ${+PORTAL_FLAGS} )); then
  typeset -ga PORTAL_FLAGS
  PORTAL_FLAGS=()
fi

# Build the resume command line for a session (as a single string for -e / do script).
_portal_resume_cmd() {
  local cwd="$1" sid="$2" kind="$3"
  local q_cwd="${(q)cwd}"
  if [[ "$kind" == "CODEX" ]]; then
    print -r -- "cd $q_cwd && exec codex resume ${(q)sid}"
  elif [[ -z "$sid" || "$kind" == "NEW" ]]; then
    print -r -- "cd $q_cwd && exec claude ${(j: :)${(q)PORTAL_FLAGS}}"
  else
    print -r -- "cd $q_cwd && exec claude --resume ${(q)sid} ${(j: :)${(q)PORTAL_FLAGS}}"
  fi
}

# Send one session off to its OWN shell (a new window), themed to its project. macOS+Ghostty
# gets a native window via `open`; other setups fall back to printing the command to run.
# This is what makes `portal` fan a fleet out instead of taking over the current shell.
_portal_send() {
  local cwd="$1" sid="$2" kind="$3" proj="$4"
  local runcmd; runcmd="$(_portal_resume_cmd "$cwd" "$sid" "$kind")"
  local theme; theme="$(python3 $PORTAL_DIR/themes.py name "$proj" 2>/dev/null)"
  local title="◆ ${proj}"
  if [[ "$TERM_PROGRAM" == "ghostty" ]] && [[ "$OSTYPE" == darwin* ]]; then
    local -a a
    a=(--working-directory="$cwd" --title="$title"
       --notify-on-command-finish-action=notify -e zsh -lc "$runcmd")
    [[ -n "$theme" ]] && a=(--theme="$theme" $a)
    open -na Ghostty.app --args "${a[@]}"
    print -P "  %F{230}◆%f ${proj//\%/%%}${theme:+  %F{245}($theme)%f}"
  elif command -v tmux >/dev/null && [[ -n "$TMUX" ]]; then
    tmux new-window -n "$proj" "zsh -lc ${(q)runcmd}"
    print -P "  %F{230}◆%f ${proj//\%/%%}  %F{245}(tmux window)%f"
  else
    print -P "  %F{245}run:%f zsh -lc ${(q)runcmd}"
  fi
}

# One fzf skin for every portal surface (picker and live watch) — warm monochrome.
_portal_fzf_colors() {
  if [[ "$1" == "light" ]]; then
    # warm black ink on paper
    print -r -- 'fg:#4a443c,fg+:#14110e,bg:-1,bg+:#eae3d7,hl:#14110e,hl+:#14110e,info:#8f8578,border:#a89e90,prompt:#14110e,pointer:#14110e,marker:#14110e,spinner:#7d7467,header:#8f8578,label:#7d7467,query:#14110e,separator:#d3cabc,scrollbar:#a89e90,gutter:-1,preview-border:#d3cabc'
  else
    # warm ink on near-black
    print -r -- 'fg:#cfc7ba,fg+:#fff9f0,bg:-1,bg+:#221f1d,hl:#fff9f0,hl+:#fff9f0,info:#837a6e,border:#4c463e,prompt:#fff9f0,pointer:#fff9f0,marker:#fff9f0,spinner:#a79e92,header:#837a6e,label:#8f8578,query:#fff9f0,separator:#302b28,scrollbar:#4c463e,gutter:-1,preview-border:#302b28'
  fi
}

# `portal watch`: the picker, pointed at the live fleet. Same frame, same keys — but the
# rows are agents running RIGHT NOW, auto-refreshed via fzf --listen, and ↵ jumps into
# the selected agent. ^O sends it to its own window instead.
_portal_watch_live() {
  emulate -L zsh
  autoload -Uz is-at-least
  local fzf_ver="${$(fzf --version 2>/dev/null)%% *}"
  if ! is-at-least 0.38 "$fzf_ver"; then
    python3 $PORTAL_DIR/watch.py; return $?   # old fzf: classic board still works
  fi
  local qdir="${(q)PORTAL_DIR}"
  local fzf_color; fzf_color="$(_portal_fzf_colors "$PORTAL_THEME")"
  # per-picker notify sidecar: two watches open at once must not steal transitions
  local -x PORTAL_WATCHSTATE="${TMPDIR:-/tmp}/portal-watchstate.$$"
  trap 'rm -f "$PORTAL_WATCHSTATE"' EXIT INT TERM
  local rows="python3 $qdir/watch.py --rows"

  # full-screen, no --height: the board owns the screen like the classic watch did —
  # and fzf's height mode needs a real terminal size, which CI ptys don't have
  local -a fargs
  fargs=(--ansi --delimiter=$'\t' --with-nth=1 --layout=reverse
    --border=rounded --border-label=" ⌁ live agents — ↵ jump · ^O own window "
    --border-label-pos=3 --padding=0,1 --info=inline --separator='─' --scrollbar='▏'
    --ellipsis='…' --pointer='▸' --prompt='⌁ ' --color="$fzf_color"
    --preview="python3 $qdir/preview.py {}" --preview-window='right,48%,border-left,wrap'
    --expect=ctrl-o --bind="ctrl-/:toggle-preview")
  if [[ -n "$PORTAL_WATCH_STATIC" ]]; then
    # no auto-refresh: a static snapshot of the fleet (constrained machines / debugging)
    fargs+=(--bind="start:reload($rows)")
  else
    fargs+=(--listen=0
      --bind="start:reload($rows)+execute-silent(nohup python3 $qdir/watch.py --ticker </dev/null >/dev/null 2>&1 &)")
  fi

  local out key line
  out=$(: | fzf "${fargs[@]}")
  key=$(printf '%s\n' "$out" | sed -n 1p)
  line=$(printf '%s\n' "$out" | sed -n 2p)
  [[ -z "$line" ]] && return 0

  local cwd sid kind proj
  cwd=$(printf '%s' "$line" | cut -f2)
  sid=$(printf '%s' "$line" | cut -f3)
  kind=$(printf '%s' "$line" | cut -f4)
  proj=$(printf '%s' "$line" | cut -f9)
  [[ -z "$proj" ]] && proj="${cwd:t}"
  if [[ ! -d "$cwd" ]]; then
    print -P "%F{223}⌁ folder gone:%f ${${cwd/#$HOME/~}//\%/%%}"
    return 1
  fi
  if [[ "$key" == "ctrl-o" ]]; then
    _portal_send "$cwd" "$sid" "$kind" "$proj"
    return 0
  fi
  local c_go c_dir c_res
  if [[ "$PORTAL_THEME" == "light" ]]; then c_go=235; c_dir=241; c_res=235
  else c_go=230; c_dir=187; c_res=230; fi
  cd "$cwd" || return 1
  print -P "%F{$c_go}◇ portal · watch%f %F{$c_dir}${${cwd/#$HOME/~}//\%/%%}%f"
  if [[ "$kind" == "CODEX" ]]; then
    print -P "%F{$c_res}▸ resume%f ${sid}  codex resume"
    codex resume "$sid"
  else
    print -P "%F{$c_res}▸ resume%f ${sid}  claude --resume … ${PORTAL_FLAGS[*]}"
    claude --resume "$sid" "${PORTAL_FLAGS[@]}"
  fi
}

portal() {
  emulate -L zsh
  # subcommands: session mobility + scripting (none of these need fzf — the fzf
  # gate comes after, so `portal update` can still run when fzf is old or missing)
  case "$1" in
    version|-v|--version)
      print "portal $PORTAL_VERSION"; return 0 ;;
    update)
      print "◇ updating portal…"
      curl -fsSL https://portal.cybercorpresearch.com/install.sh | bash && \
        print "◇ updated — open a new terminal (or: source ~/.zshrc)"
      return $? ;;
    doctor|ls|status|overlap|heavy|grep)
      python3 $PORTAL_DIR/mobility.py "$@"; return $? ;;
    watch|agents)
      export PORTAL_THEME="$(python3 $PORTAL_DIR/theme.py 2>/dev/null)"; [[ -z "$PORTAL_THEME" ]] && PORTAL_THEME=dark
      shift
      # no args + fzf present → the live picker; any flag (--once, --interval, --board)
      # or no fzf → the classic full-screen board
      if (( $# == 0 )) && command -v fzf >/dev/null 2>&1; then
        _portal_watch_live; return $?
      fi
      python3 $PORTAL_DIR/watch.py "${@:#--board}"; return $? ;;
    mv)
      shift; python3 $PORTAL_DIR/mobility.py mv "$@"; return $? ;;
    pull)
      shift
      local pres psid pcwd
      pres=$(python3 $PORTAL_DIR/mobility.py pull "$@") || return $?
      psid=${pres%%$'\t'*}; pcwd=${pres#*$'\t'}
      [[ -z "$psid" ]] && return 1
      print -P "%F{230}◇ portal · pull%f resuming forked here"
      claude --resume "$psid" --fork-session "${PORTAL_FLAGS[@]}"
      return $? ;;
  esac

  # everything below drives fzf (the picker, or teleport's ambiguous fallback)
  if ! command -v fzf >/dev/null 2>&1; then
    print -u2 "portal: fzf is not installed. Run:  brew install fzf"
    return 1
  fi
  autoload -Uz is-at-least
  local fzf_ver="${$(fzf --version 2>/dev/null)%% *}"
  if ! is-at-least 0.38 "$fzf_ver"; then
    print -u2 "portal: fzf $fzf_ver is too old (need ≥ 0.38)."
    print -u2 "  upgrade: brew upgrade fzf · or grab a release: https://github.com/junegunn/fzf/releases"
    return 1
  fi

  # `portal go <words...>` = teleport: everything after "go" is one natural-language query.
  local teleport_q=""
  if [[ "$1" == "go" ]]; then
    shift
    teleport_q="$*"
    set --
  fi

  local here="" query=""
  while (( $# )); do
    case "$1" in
      --here) here=1 ;;
      *) query="$1" ;;
    esac
    shift
  done

  # Detect light/dark terminal and shape the aesthetic to match.
  export PORTAL_THEME="$(python3 $PORTAL_DIR/theme.py 2>/dev/null)"
  [[ -z "$PORTAL_THEME" ]] && PORTAL_THEME=dark

  # Teleport: high confidence -> jump straight in; ambiguous -> open the picker pre-filled.
  if [[ -n "$teleport_q" ]]; then
    local gores gconf gcwd gsid gkind
    gores=$(python3 $PORTAL_DIR/rank.py --go "$teleport_q" 2>/dev/null)
    gconf=$(printf '%s' "$gores" | cut -f1); [[ -z "$gconf" ]] && gconf=0
    gcwd=$(printf '%s' "$gores" | cut -f2)
    gsid=$(printf '%s' "$gores" | cut -f3)
    gkind=$(printf '%s' "$gores" | cut -f4)
    if [[ -n "$gcwd" && -d "$gcwd" ]] && (( gconf >= 0.60 )); then
      cd "$gcwd" || return 1
      print -P "%F{230}◇ portal · teleport%f %F{223}${${gcwd/#$HOME/~}//\%/%%}%f %F{187}(${gconf})%f"
      if [[ "$gkind" == "NEW" || -z "$gsid" ]]; then
        claude "${PORTAL_FLAGS[@]}"
      elif [[ "$gkind" == "CODEX" ]]; then
        codex resume "$gsid"
      else
        claude --resume "$gsid" "${PORTAL_FLAGS[@]}"
      fi
      return
    fi
    query="$teleport_q"   # ambiguous -> fall through to the picker, pre-filtered
  fi

  local fzf_color; fzf_color="$(_portal_fzf_colors "$PORTAL_THEME")"

  # preflight: if the brain is broken (bad update, syntax error), fail loudly with
  # the real traceback instead of opening an empty picker.
  if ! python3 $PORTAL_DIR/rank.py --selftest >/dev/null 2>/tmp/portal-selftest.$$; then
    print -u2 "portal: engine failed self-test — reinstall with:"
    print -u2 "  curl -fsSL https://portal.cybercorpresearch.com/install.sh | bash"
    print -u2 ""
    tail -3 /tmp/portal-selftest.$$ >&2; rm -f /tmp/portal-selftest.$$
    return 1
  fi
  rm -f /tmp/portal-selftest.$$

  # per-picker sidecars: two portals open at once must not share state
  export PORTAL_WHY_FILE="${TMPDIR:-/tmp}/portal-why.$$"
  export PORTAL_EXPAND_FILE="${TMPDIR:-/tmp}/portal-expand.$$"
  trap 'rm -f "$PORTAL_WHY_FILE" "$PORTAL_EXPAND_FILE"' EXIT INT TERM
  # start each launch with superseded sessions collapsed
  print -n 0 > "$PORTAL_EXPAND_FILE" 2>/dev/null

  # live-search brain: rank.py {q} re-ranks on every keystroke (empty {q} = browse).
  # These strings run via fzf's $SHELL -c, so paths must be quoted for THAT shell —
  # and --here compares via ENVIRON, not string-splicing $PWD into awk source.
  local qdir="${(q)PORTAL_DIR}"
  local rank="python3 $qdir/rank.py {q}"
  if [[ -n "$here" ]]; then
    local -x PORTAL_HERE="$PWD"   # -x: exported to fzf's children, gone when we return
    rank="$rank | awk -F'\t' '\$2==ENVIRON[\"PORTAL_HERE\"]'"
  fi
  local why_file="$PORTAL_WHY_FILE"

  local stats banner
  stats="$(python3 $PORTAL_DIR/index.py --stats 2>/dev/null)"
  banner="$(python3 $PORTAL_DIR/banner.py --theme $PORTAL_THEME --width ${COLUMNS:-100} --stats "$stats")"

  # Print the header full-width above fzf (a right-side preview would otherwise
  # confine a --header to the left pane — this keeps the wordmark front and center).
  clear
  print ""
  print -r -- "$banner"
  print ""

  # live-agent nudge: if anything's running right now, say so above the picker (silent if not)
  local nudge nudge_rows=0; nudge="$(python3 $PORTAL_DIR/agents.py --nudge 2>/dev/null)"
  if [[ -n "$nudge" ]]; then
    if [[ "$PORTAL_THEME" == "light" ]]; then print -P "  %F{238}${nudge//\%/%%}%f"; else print -P "  %F{223}${nudge//\%/%%}%f"; fi
    print ""
    nudge_rows=2
  fi

  # picker fills whatever's left below the header — measured, not guessed, so the
  # compact header (3 rows) hands those rows to the session list instead of wasting them
  local banner_rows=$(( $(print -r -- "$banner" | wc -l) + 3 + nudge_rows ))  # +blanks +prompt +nudge
  local pick_height=$(( ${LINES:-40} - banner_rows ))
  (( pick_height < 8 )) && pick_height=8

  local flagstr="${PORTAL_FLAGS[*]}"

  local out key line
  out=$(: | fzf \
    --ansi \
    --disabled \
    --delimiter=$'\t' \
    --with-nth=1 \
    --layout=reverse \
    --height=$pick_height \
    --border=rounded \
    --border-label-pos=3 \
    --padding=0,1 \
    --info=inline \
    --separator='─' \
    --scrollbar='▏' \
    --ellipsis='…' \
    --pointer='▸' \
    --marker='◆' \
    --multi \
    --prompt='❯ ' \
    --query="$query" \
    --color="$fzf_color" \
    --preview="python3 $qdir/preview.py {}" \
    --preview-window='right,48%,border-left,wrap' \
    --expect=ctrl-n,ctrl-o \
    --bind="start:reload($rank)+transform-prompt(if [ -n {q} ]; then printf '◇ '; else printf '❯ '; fi)" \
    --bind="change:reload($rank)+transform-prompt(if [ -n {q} ]; then printf '◇ '; else printf '❯ '; fi)" \
    --bind="load:transform-border-label(cat $why_file 2>/dev/null)" \
    --bind="ctrl-r:reload(python3 $qdir/index.py --stats >/dev/null 2>&1; $rank)" \
    --bind="ctrl-e:execute-silent(zsh $qdir/toggle_expand.sh)+reload($rank)" \
    --bind="ctrl-/:toggle-preview" \
    --bind="ctrl-y:execute-silent(zsh $qdir/copycmd.sh {2} {3} {4} $flagstr)")

  # line 1 = pressed key (ctrl-n / ctrl-o / empty); lines 2.. = selected rows (--multi)
  key=$(printf '%s\n' "$out" | sed -n 1p)
  local -a sel
  sel=("${(@f)$(printf '%s\n' "$out" | sed '1d')}")
  sel=(${sel:#})   # drop empties
  (( ${#sel[@]} )) || return 0

  # Fan-out: several marked, or ^O on a selection → send each to its own shell, then open
  # the board. This is the "many shells" mode. A plain single pick still jumps in HERE.
  if (( ${#sel[@]} > 1 )) || [[ "$key" == "ctrl-o" ]]; then
    print -P "%F{230}◇ portal%f sending ${#sel[@]} to their own shells"
    local row rc rs rk rp
    for row in "${sel[@]}"; do
      rc=$(printf '%s' "$row" | cut -f2); rs=$(printf '%s' "$row" | cut -f3)
      rk=$(printf '%s' "$row" | cut -f4); rp=$(printf '%s' "$row" | cut -f9)
      [[ -z "$rp" ]] && rp="${rc:t}"
      if [[ ! -d "$rc" ]]; then print -P "  %F{245}skip — folder gone: ${rp//\%/%%}%f"; continue; fi
      _portal_send "$rc" "$rs" "$rk" "$rp"
    done
    print -P "%F{245}◇ board — ^C to exit%f"
    python3 $PORTAL_DIR/watch.py
    return 0
  fi

  # ---- single selection: the classic jump into THIS shell ----
  local line="${sel[1]}"
  local cwd sid kind force_new=0
  cwd=$(printf '%s' "$line" | cut -f2)
  sid=$(printf '%s' "$line" | cut -f3)
  kind=$(printf '%s' "$line" | cut -f4)
  [[ "$key" == "ctrl-n" ]] && force_new=1

  if [[ ! -d "$cwd" ]]; then
    # orphan: folder moved/deleted — offer to relink instead of failing
    print -P "%F{223}⌁ folder moved or deleted%f — running portal doctor"
    local fixed
    python3 $PORTAL_DIR/mobility.py doctor --sid "${sid:0:8}" || return 1
    python3 $PORTAL_DIR/index.py --stats >/dev/null 2>&1   # refresh cache post-relink
    fixed=$(python3 - "$sid" <<'PYEOF'
import json,os,sys
c=json.load(open(os.path.expanduser("~/.claude/portal/cache.json")))
for p,v in c.items():
    if os.path.basename(p).startswith(sys.argv[1][:8]) and os.path.isdir(v.get("cwd","")):
        print(v["cwd"]); break
PYEOF
)
    [[ -z "$fixed" || ! -d "$fixed" ]] && return 1
    cwd="$fixed"
  fi

  # theme-aware status colors (greyscale)
  local c_go c_dir c_new c_res
  if [[ "$PORTAL_THEME" == "light" ]]; then
    c_go=235; c_dir=241; c_new=238; c_res=235
  else
    c_go=230; c_dir=187; c_new=223; c_res=230
  fi

  cd "$cwd" || return 1
  print -P "%F{$c_go}◇ portal%f %F{$c_dir}${${cwd/#$HOME/~}//\%/%%}%f"
  if [[ $force_new == 1 || "$kind" == "NEW" || -z "$sid" ]]; then
    print -P "%F{$c_new}+ new session%f  claude ${PORTAL_FLAGS[*]}"
    claude "${PORTAL_FLAGS[@]}"
  elif [[ "$kind" == "CODEX" ]]; then
    print -P "%F{$c_res}▸ resume%f ${sid}  %F{$c_new}codex%f resume"
    codex resume "$sid"
  else
    print -P "%F{$c_res}▸ resume%f ${sid}  claude --resume … ${PORTAL_FLAGS[*]}"
    claude --resume "$sid" "${PORTAL_FLAGS[@]}"
  fi
}

# aliases so old muscle-memory still works
alias bootup='portal'
alias jump='portal'
