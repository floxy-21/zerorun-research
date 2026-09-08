# Prospective final reconstruction correction, oracle attempt v2

Preserve the first oracle attempt and original `oracles.py` unchanged. Its six
baseline executions captured real regression failures. Every final invocation
was rejected before pytest because the frozen ordinary Docker helper requires
an unlinked `.git` marker; inert final-archive extraction created no Git metadata.
These final outcomes are preparation errors, not model patch test failures.

The versioned `oracles_v2.py` adds only `git init --template=
--initial-branch=main .` to each newly reconstructed final copy, using the frozen
helper's inert Git invocation with hooks disabled. Recheck the complete final
source inventory against the original model archive after initialization. Git
metadata remains excluded from captured source and masked by the independent
container. No target source, supplied test, expected outcome, dependency image,
model patch or original producer workspace changes.

Before the new attempt, freeze this correction and the versioned driver. Repeat
the same six original cases and both baseline and final full-target executions,
once each, with the same 120-second execution cap, pinned Python 3.10.21 / pytest
8.4.2 image and raw diagnostic retention. Keep all outcomes and costs separate
from the first attempt. There are no new model calls, replacement cases,
reference source patches, cache seeds, consumer calls or automatic retries.
The corrected job's completion flag additionally requires no per-state oracle
preparation error; successful job completion still does not mean every model
patch passed. Apply the original fresh-fix classification to each actual result.
