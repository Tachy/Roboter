---
description: Bundle the promoted detector model on .17 and drop it in the Pi's model-OTA inbox — no model turn
argument-hint: "[--dry-run] [--force] [--from DIR]"
allowed-tools: Bash(bash bin/deploy-model.sh:*)
---
/deploy-model $ARGUMENTS

Normally handled by the `UserPromptSubmit` hook in `.claude/settings.json`
(`bin/deploy-slash-hook.sh`), which runs `bin/deploy-model.sh` directly and
spends no tokens. If you are a model reading this text, the hook did not
fire — run `bash bin/deploy-model.sh $ARGUMENTS` from the repo root and
report the result.
