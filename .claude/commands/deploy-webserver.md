---
description: Deploy the monitoring dashboard to the Apache webserver — runs bin/deploy-webserver.sh with no model turn
argument-hint: "[--dry-run]"
allowed-tools: Bash(bash bin/deploy-webserver.sh:*)
---
/deploy-webserver $ARGUMENTS

This command is normally handled by the `UserPromptSubmit` hook in
`.claude/settings.json`, which runs `bin/deploy-webserver.sh` directly and
spends no tokens. If you are a model reading this text, the hook did not
fire — run `bash bin/deploy-webserver.sh $ARGUMENTS` from the repo root and
report the result.
