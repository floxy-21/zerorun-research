# Per-session client configuration isolation

The first prospective coding-agent pilot completed a model turn. The second
selected pilot was refused before a model invocation because the first CLI
session had created `config.toml` in the shared, initially empty client home.
The unchanged producer requires a client home without that file. Retain the
second pilot's started/session receipts as a pre-invocation setup failure; do
not relabel it as a completed model task or rerun it under the same attempt.

For the six already specified main cases, provide a separate new, private
client-home directory for each invocation. The operator copies only the
already-authorized account authentication file, sets private directory/file
permissions, and supplies that directory through the existing `--codex-home`
argument. The harness, model, prompt, case selection, one-attempt limit,
workspace-write boundary, disabled integrations, and configuration checks are
unchanged. No credentials or client-home contents belong in the public artifact.

This is an operator configuration-isolation correction before main model
execution, not a model retry, permission expansion, or alteration of test
outcomes. Remove the temporary authentication copies after the study and record
their absence without publishing credential bytes or hashes.
