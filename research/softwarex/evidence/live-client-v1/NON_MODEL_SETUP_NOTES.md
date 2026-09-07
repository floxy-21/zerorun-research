# Non-model diagnostic setup and outcome

The first Linux unit invocation of the diagnostic helper had 25 passes and one
failure because its external adapter directory did not yet contain the preserved
live receipt at the relative fixture path expected by one unit test. The original
live receipt already existed separately on the VM. Copying those exact bytes to
the test fixture path completed the transfer; no producer or assertion changed.
The second Linux unit invocation passed all 26 tests. Both JUnit files remain.

The actual diagnostic then ran once and passed all five planned MCP exchanges.
The original Codex experiment was not retried. The new record explicitly states
`model_called=false` and `codex_correction_tested=false`. Its missing-variable
control remained untrusted; supplying the exact external trust path yielded
readiness, `MISS_EXECUTED`, `HIT_REUSED`, and `VERIFY_MATCH`. All sources and
installed runtime bytes remained unchanged, and its temporary fixture and
synthetic authority were cleaned up. These outcomes are subject to independent
receipt reconciliation before entering the manuscript.
