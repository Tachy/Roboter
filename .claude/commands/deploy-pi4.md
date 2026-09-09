---
description: Copy the Pi 4 robot software (main.py + src/, git-tracked only) to admin@192.168.179.252 — no model turn
argument-hint: "[--dry-run]"
allowed-tools: Bash(bash bin/deploy-pi4.sh:*)
---
/deploy-pi4 $ARGUMENTS

This command is normally handled by the `UserPromptSubmit` hook in
`.claude/settings.json` (`bin/deploy-slash-hook.sh`), which runs
`bin/deploy-pi4.sh` directly and spends no tokens. If you are a model
reading this text, the hook did not fire — run
`bash bin/deploy-pi4.sh $ARGUMENTS` from the repo root and report the
result.
