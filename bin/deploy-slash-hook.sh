#!/usr/bin/env bash
#
# Claude Code UserPromptSubmit hook -- token-free deploy commands.
#
# When the submitted prompt is one of the allow-listed "/deploy-<name>"
# commands, this runs bin/deploy-<name>.sh directly and exits 2, so Claude
# Code shows the output to the user and never sends the prompt to the model
# (no tokens spent). Any other prompt: exit 0, handled normally.
#
#   /deploy-webserver [args]  -> bin/deploy-webserver.sh
#   /deploy-arduino   [args]  -> bin/deploy-arduino.sh
#   /deploy-pi4       [args]  -> bin/deploy-pi4.sh
#
# Wired in .claude/settings.json -> hooks.UserPromptSubmit. Claude Code
# passes a JSON object on stdin whose "prompt" field holds the user's text.
set -uo pipefail

ALLOWED=" webserver arduino pi4 "   # space-delimited allow-list

# CLAUDE_PROJECT_DIR may arrive as a Windows path; normalise for Git Bash.
root="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$root" ]; then
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
if command -v cygpath >/dev/null 2>&1; then
  root="$(cygpath -u "$root" 2>/dev/null || printf '%s' "$root")"
fi

input="$(cat)"

# Pull out the "prompt" string. python is reliable; sed is the fallback.
prompt="$(
  printf '%s' "$input" | python -c 'import sys, json
try:
    sys.stdout.write(json.load(sys.stdin).get("prompt", "") or "")
except Exception:
    pass' 2>/dev/null
)"
if [ -z "$prompt" ]; then
  prompt="$(printf '%s' "$input" \
    | sed -n 's/.*"prompt"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
fi

# Find a line that is exactly a /deploy-<name> command (optionally with args).
name=""; args=""
while IFS= read -r line; do
  t="${line#"${line%%[![:space:]]*}"}"   # ltrim
  t="${t%"${t##*[![:space:]]}"}"          # rtrim
  case "$t" in
    /deploy-*)
      rest="${t#/deploy-}"
      n="${rest%%[[:space:]]*}"           # command name = up to first blank
      if [ -n "$n" ] && [ "$ALLOWED" != "${ALLOWED/ $n /}" ]; then
        name="$n"
        a="${rest#"$n"}"
        args="${a//\$ARGUMENTS/}"         # drop an unexpanded placeholder
        break
      fi
      ;;
  esac
done <<EOF
$prompt
EOF

[ -n "$name" ] || exit 0   # not one of our commands -- let the prompt through

script="$root/bin/deploy-$name.sh"
{
  echo "▶ /deploy-$name${args}  — running bin/deploy-$name.sh (no model turn)"
  echo
  if [ -f "$script" ]; then
    # shellcheck disable=SC2086
    bash "$script" $args
    echo
    echo "— deploy-$name.sh exited $? —"
  else
    echo "hook error: not found: $script"
  fi
} 1>&2

exit 2
