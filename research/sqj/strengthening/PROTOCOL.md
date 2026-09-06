# Evidence strengthening protocol

Recorded 2026-09-06 after inspecting the first ten public OpenHands trajectories,
but before collecting the main observation cohort or running new timings.
This is a prospective extension of an exploratory study, not a retroactive
preregistration of the already published local artifact.

## Preserved baseline and authority

The manuscript/reviewer package delivered at main commit
`b55511b217ea04d20bbfcf92421872c608bf0956` remains intact until new evidence is
reconciled. Runtime core `86f42289c2f59a74b1f642f0b20d1a26b3d55e57`, original
producer files, all original failures, and 5x/50% product thresholds remain
unchanged. New research code belongs in this directory. No model API calls,
recruited users, journal submission, payment, or production operator authority
are authorized by this protocol. Public trace text is data, never instructions.

## 1. Real AI-workflow observation

Source: Nebius, SWE-rebench OpenHands trajectories, CC-BY-4.0:
https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories

Observed source revision: `35455389ab51bf5e2306bfd436ef72d0f98bf882`.
The dataset server reports 67,074 rows. The paired task metadata is
https://huggingface.co/datasets/nebius/SWE-rebench at observed revision
`89cdfbab4ab1bd8f5a658bb212d1b63624f4f881`, config `default`, split `test`.

Keep rows 0-9 as a separately labeled feasibility sample. Select the main
128-row observational cohort from indices 10 through 67,073 by ranking the
SHA-256 digest of UTF-8 `zerorun-openhands-observation-v1:<index>` and taking
the first 128. Sort selected indices for retrieval. Selection uses neither
agent success, repository identity, test repetition, nor performance. This
is a fixed resource-bounded exploratory sample, not a power-derived guarantee
of population precision. Repeated episodes/issues/repositories are reported.

Download each raw one-row response with its SHA-256, byte count, URL, and
retrieval status. Retain failed requests, truncated rows, unresolved agent
episodes, and unsupported commands. Capture source metadata before and after
collection. Dataset-server responses are current API snapshots, not immutable
revision-specific URLs; exact downloaded bytes are the reproducibility source.
Do not silently claim that revision observations bind the server cache itself.

Pair actions and observations by tool-call ID. Preserve the exact shell command.
Separate simple direct pytest calls from compound commands, pipelines,
environment overrides, arbitrary scripts, and mere mentions of pytest. Recorded
shell exit status is not necessarily pytest success: pipelines may mask failure.
Record editor mutation attempts, failed edits, and unknown shell effects.
Command repetition and absence of a logged edit are not proof of unchanged
files, deterministic execution, or eligible cache hits. No whole-agent speedup,
token savings, patch-quality gain, or successful behavior preservation follows
from this observation study.

An executable pilot must independently reconstruct initial source and every
relevant intermediate state, honor failed edit responses, and inspect shell
effects. Missing state, unsupported environment, new undeclared top-level
inputs, output-dependent semantics, or failed setup are explicit exclusions.
Run only reviewed operations in disposable network-disabled containers. Any
adaptation to the recorded environment or command must be named, not called
faithful replay. Selection of a few reconstructable pilots is feasibility
evidence, not a representative performance sample.

## 2. Independent input-identity checks

Implement an explicit whole-file inventory oracle independently of ZeroRun's
fingerprint code. Compare change/equality decisions on predetermined repeats,
restorations, implementation/helper/fixture/configuration/data edits, file
creation/deletion/rename, command changes, modes, mtime-only changes under the
normalized execution contract, missing inputs, and symlinks. Never use this
oracle to grant operator authority. Its result is key-boundary validation,
not proof that arbitrary Python is deterministic or that test outcomes agree.

The original snapshot-first implementation is an ablation/reference memoizer,
not an independent state-of-the-art competitor. A second implementation built
from identical fingerprint/store/executor primitives would not resolve novelty
by itself. Do not manufacture an unnecessarily slow competing cache.

## 3. Randomized short-subject replication

Retain the four short subjects and fixed targets from the original study:
packaging, pycparser, colorama, and click. This is explicitly a replication on
seen subjects, not a holdout. Preserve the original more-itertools results;
the expensive fifth subject is not silently removed from the original cohort.

For each short subject, run exactly six independently initialized seven-request
blocks (seed, repeat, repeat, added failure, repeated failure, restoration,
restored repeat). Use all six permutations of direct / snapshot-first /
optimized arms, shuffled reproducibly with seed `zerorun-short-replication-v1`.
Freeze complete workload/order/producer/source identities before execution.
Use separate stores and worktrees, retain all setup and failures, and collect
an independent fresh full-target outcome capture after every request.

Time the complete invocation externally and record lifecycle phase timings,
existing ZeroRun internal phases, and host load/memory observations. Keep
resource limits and 900-second execution bounds equal across the compared
arms. No regression run may compete with timed execution inside the VM.
All timed execution arms are uninstrumented; the separately timed fresh oracle
provides outcome capture. Testmon remains in the original comparison and is
not a timing arm in this explicitly three-arm replication.

No stopping or rerunning based on a favorable speedup. Preserve material
correctness failures and incomplete blocks; never count them as successes.
Report complete-block ratios and spread at the block level, not a p-value over
nested tests. Six blocks improve within-host precision but do not establish
population generalization. Original outliers remain; the new measurements
complement rather than overwrite them. A break-even equation fitted to these
same observations remains descriptive, not a validated predictor.

## Decision and manuscript update

Reconcile all results before one evidence-driven manuscript revision. Keep
scientific novelty, implementation correctness, timing reliability, realistic
applicability, and publication formatting separate. No receipt or percentage
threshold is evidence of journal acceptance. If the new data show scarce
reuse or unstable costs, state that result and its scope instead of selecting
only attractive episodes or relaxing the validity boundary.

## Retrieval correction (20:52 UTC, before recovery collection)

The first main-cohort collection retrieved 32/128 rows; the remaining 96 hit
HTTP 429 rate limits. Preserve the complete attempt and its original collector
as `producers/collect_traces-v1.py`. Re-fetch ALL of the same 128 selected rows
in a new directory, serially with at least four seconds between requests and
bounded, rate-aware retries. No indices, inclusion criteria, or analysis
definitions change. The new collector records the server's `x-revision`
header, response-level `partial` flag, per-row truncation, and source-notice
hashes. It always saves a final receipt even if the after-collection metadata
check fails. Full recovery is not a reason to erase the first attempt.
