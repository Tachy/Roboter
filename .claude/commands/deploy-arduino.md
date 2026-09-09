---
description: Compile the motor-control firmware and scp the app-only .ino.hex to the Pi's flash inbox — no model turn
argument-hint: "[--dry-run]"
allowed-tools: Bash(bash bin/deploy-arduino.sh:*)
---
/deploy-arduino $ARGUMENTS

This command is normally handled by the `UserPromptSubmit` hook in
`.claude/settings.json` (`bin/deploy-slash-hook.sh`), which runs
`bin/deploy-arduino.sh` directly and spends no tokens. If you are a model
reading this text, the hook did not fire — run
`bash bin/deploy-arduino.sh $ARGUMENTS` from the repo root and report the
result.
