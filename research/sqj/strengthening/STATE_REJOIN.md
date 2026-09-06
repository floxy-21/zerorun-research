# Recorded-agent source-restoration case

This is a purposively selected mechanism case discovered after inspecting the
fixed OpenHands cohort, not a representative performance sample, a new
autonomous-agent run, or a reproduction of TVCache.

## Source and observation identity

- Task: joke2k__django-environ-322; trajectory row 51126.
- Trajectory dataset: https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories (CC-BY-4.0).
- Paired metadata: https://huggingface.co/datasets/nebius/SWE-rebench (CC-BY-4.0).
- Base commit: 31c8f98343bd871d51c5bf6d73c3573c6cbde72e.
- Environment-setup commit recorded in metadata: 192b813fc97fd395cb432eba5d0064a73d4ab9f0.
- Source archive: https://codeload.github.com/joke2k/django-environ/tar.gz/31c8f98343bd871d51c5bf6d73c3573c6cbde72e (MIT, copyright notices preserved in LICENSE.txt).
- Archive SHA256: 6264853af7ab12c7265be9b10115007fe4977de9b1636c1d86875ca1a0654425; 35,937 bytes.
- Metadata response SHA256: b70d84f366c70456c2c8aeedd1ba675b70b71270b7237282709f90924f02d828.
- Raw trajectory response SHA256: 16a0438ab2bc234587dc50129320fbb64063b7c90927fcb501f39eeea0c434ae.

The metadata API response is a downloaded, hash-bound snapshot, not claimed to
be an immutable revision-specific Hugging Face API response. The exact
acquisition URL is in the inert candidate plan. No original task Docker image
was downloaded. Ground-truth task patches are present in the metadata response
but are not used to construct source states.

## Reviewed bounded reconstruction

The inert candidate plan preserves all calls before the seed, except the
non-executing think call, and all calls in the tested segment with exact IDs
and argument values. Source reconstruction applies only the four recorded
prior mutations: creation of test_reproduce_issue.py (19), two edits to
environ/environ.py (21,22), and a whole-file replacement of the reproduction
script (24). It never executes the recorded shell strings or reproduction
scripts.

The seed is call 26, followed by an edit of tests/test_search.py (27), its
exact inverse (28), the separate collection request (29, recorded exit 5),
and an exact target repeat (30). Reconstructed source-only evidence is in
evidence/state-rejoin-source-1/reconstruction.json: 47 files and 6 directories,
including the created root reproduction script. Seed and restored complete
path/content digests are equal; the intermediate digest differs.

Manual source review covered both complete reproduction-script versions,
the search tests, conftest, fixtures, all other test modules imported by the
collection request, package initialization/compatibility code, search parser
and parse_value, and executed top-level/class statements. The search tests
parse URL strings without initiating remote connections. Package import
registers schemes in process-local urllib.parse lists. Compatibility imports
depend on the installed package set. Some other test bodies mutate process
environment or write a temporary file; they are deselected by the bounded
collection request, not asserted globally harmless. All executions below run
in separate network-disabled containers with a read-only workspace.

## Controlled experiment contract

The existing CPython 3.12.14 digest-pinned image and hash-locked pytest 9.1.1
dependency layer replace the recorded Python 3.9.21 / pytest 8.3.4 environment.
The runtime lock is SHA256
a04f815c62754114a0f0a4b7db15d02c920e7c7d006d813ac9955be00482c02c.
Original randomly, Hypothesis, asyncio and other plugins are not reproduced.
Original random seeds, test order and output differ between the recorded
seed/repeat, so output-preserving agent-behavior claims are explicitly excluded.
This experiment uses ZeroRun's existing result-only contract.

The research driver makes four requests:

1. Seed at reconstructed call-26 source: target tests, expected 14 passes.
2. Changed call-27 source: target tests, expected 15 passes. This is **additional
   counterfactual oracle instrumentation**, not a test call made in the trace.
3. Restored call-28 source: recorded collection target and keyword, expected
   no selected tests and exit 5; not successful cacheable work.
4. Restored call-30 source: original target, expected 14 passes and an actual
   content-state cache hit if the implementation admits it.

Every request has an independent fresh complete-node oracle. The driver
checks the entire reconstructed source/dependency path-content manifest
before and after execution, excluding only explicit engine control/cache
state. Failed execution, source drift or incomplete/mismatched outcomes stop
the case and preserve a failure receipt. No retry or unsupported-state
omission is hidden. Additional oracle instrumentation and dependency setup
are not part of a claimed autonomous-agent timing result.

A separate history-event digest illustrates divergent recorded history.
It is not an implemented competing cache, a faithful full-history policy,
or a TVCache baseline. A successful case demonstrates this particular
state-restoration use case, not algorithmic novelty or broad AI acceleration.

## Running

Source-only reconstruction imports no downloaded package and executes no
downloaded source:

    python -m research.sqj.strengthening.state_rejoin --output /absolute/new/source-output

### Source-bound campaign execution

For manuscript-provenance reproduction, first complete a new short-subject
replication using the same isolated engine checkout, following SoftwareX's
REPRODUCIBILITY.md and the original REEXECUTION.md. Do not run the case while
that VM is still collecting timings. The wrapper requires the completed
replication's protocol.json and adjacent campaign-summary.json; it refuses a
different engine/core/helper identity instead of silently rebinding the case.

From the public release root, with study_dir set to that existing laboratory
directory, run:

    "$study_dir/venv/bin/python" -m research.sqj.strengthening.run_bound_state_rejoin \
      --engine "$study_dir/engine" \
      --evidence research/sqj/strengthening/evidence \
      --replication-protocol "$study_dir/short-randomized-replication-v1/protocol.json" \
      --output "$study_dir/agent-state-rejoin-v1"

    "$study_dir/venv/bin/python" -m research.sqj.strengthening.analyze_state_rejoin \
      --directory "$study_dir/agent-state-rejoin-v1" \
      --replication-directory "$study_dir/short-randomized-replication-v1" \
      --engine-archive "$study_dir/engine" \
      --output "$study_dir/state-rejoin-analysis-v1.json"

The outer protocol.json is written before the unchanged case runner executes
into case/. The outer completion.json preserves success or failure and
pre/post engine, protected-source, helper and evidence identity checks. The
independent analyzer reconstructs the expected source states and checks
actual request receipts, complete fresh-node outcomes, runtime identity and
wrapper completion. A producer's success boolean alone is insufficient.
The analysis output is create-only; add --check to verify an existing saved
analysis without replacing it. Fresh runs keep their truthful new Git
context and timestamps; they do not impersonate the original campaign.

### Recorded post-interruption execution route

The original timing VM was interrupted before its campaign could be certified
complete. The separately named `run_recovered_state_rejoin.py` wrapper allows
the unchanged four-request state case to run as a **standalone,
source-bound post-interruption case**. It does not create or claim an original
completed timing campaign. The old completed-campaign wrapper remains
unchanged, and a clean new reproduction may still use that route above.

The new wrapper accepts the same engine, evidence, original protocol and
output arguments, plus `--interruption-receipt PATH`. The supplied JSON must
record schema `zerorun.vm-interruption.v1`, the exact original
`replication_protocol_sha256`, the actual `observed_utc`, a factual
`description`, and the actual `recovery_action`. It is copied byte-for-byte
into the case export. Do not invent or backdate such a receipt to bypass a
failed timing campaign. Original completion presence and contents are
recorded as observed; absence remains absence.

This wrapper freezes its own source, the unchanged state runner, the original
prospective timing protocol, all four helper files, engine/core identities,
and the candidate's source/data hashes before executing any case request.
The independent analyzer recognizes the different schema, checks the
interruption's hash and chronology, requires the same before/after source
bindings and all four full fresh outcomes, and labels the resulting evidence
`standalone-after-vm-interruption`. Source binding does not certify timing
campaign completion. Neither route runs concurrently with timed VM work.

Offline validation of the new binding and existing strict case checks passed
55 tests, retained in `evidence/recovered-state-binding-unit-v1.xml`. These
are fixture-based binding tests, not 55 real agent episodes or execution
measurements.

The first execution exposed a fixture defect: the downloaded source archive
has no Git metadata, but the independent plain-pytest baseline intentionally
requires an ordinary Git marker so it can mask that metadata in its
container. The failed case is retained, not recoded as successful. Separate
`state_rejoin_v2.py` and `run_recovered_state_rejoin_v2.py` files correct only
this laboratory setup: after unchanged source reconstruction they initialize
a real local Git repository with templates disabled, record the operation,
and explicitly exclude `.git` as engine-control state. Both execution paths
continue to mask Git metadata under their existing contracts. No original
Git history is claimed or reconstructed, and the baseline guard is unchanged.
The old producers remain byte-identical. The correction's independent
analyzer verifies the pinned new producer, Git-fixture receipt, explicit
exclusion and all original four-request outcomes. Its combined offline
suite passes 71 tests (`evidence/state-git-fixture-unit-v1.xml`), including a
real local Git initialization exercised against the unchanged masking guard;
those tests are not a substitute for the separately exported VM result.

### Verified execution result

The corrected VM case is retained as `evidence/agent-state-rejoin-v3`, not
renamed to conceal prior attempts. Its outer completion is dated
2026-09-06T22:54:32.324387+00:00. Independent raw reconciliation passes and is
saved in `evidence/state-rejoin-analysis-v1.json`: four requests, one reused
success, complete fresh-node counts 14, 15, 0, 14, and exit codes 0, 0, 5, 0.
Actual statuses are `MISS_EXECUTED`, `MISS_EXECUTED`, `MISS_FAILED`, and
`HIT_REUSED`. The seed/restored declared source-runtime identity and target
cache key are equal; the changed state and collection target have different
keys. Source, helper, runner, wrapper and engine bindings remain unchanged
through the successful run; no operator authority was created.

The successful case remains standalone after the VM interruption, not a
certification that the interrupted timing campaign completed. The 15-node
intermediate check is counterfactual instrumentation, and the original
recorded environment and agent behavior were not reproduced. Prior failed
attempts stay separate. To verify the saved result, use the analyzer command
above with directory `research/sqj/strengthening/evidence/agent-state-rejoin-v3`,
output `research/sqj/strengthening/evidence/state-rejoin-analysis-v1.json`, and
the `--check` flag, retaining the matching original replication directory.

### Offline checks and unbound mechanism development

Reading supplied source-only receipts or running the independent analyzer on
already supplied results does not require Docker, a new timing campaign,
private credentials or an AI model. The source-only command above remains
the smallest reconstruction check. To inspect an exported case, pass its
outer directory, the corresponding exported replication directory, the
archived engine source, and a fresh analysis-output path to the analyzer;
use --check only when that saved analysis already exists.

The direct state_rejoin runner's --execute-reviewed-lab option is reserved
for separately labeled mechanism-development runs. Such a standalone run
without either documented external binding wrapper is **not** a
source-bound case used for manuscript provenance. The recovered wrapper's
standalone case must not be relabeled as a completed-campaign run.

The established dependency-layer helper may acquire hash-locked wheels.
The image is never pulled by the case. No production source file or user
authorization is modified. Do not publish the
private-cache-authentication-NOT-FOR-PUBLICATION directory; use the named
study exporter instead. There is no operator-review or customer-validation
claim.

The Windows restricted-token run had 17 passes and 13 pytest temporary-directory
permission errors before affected test bodies ran. The authorized rerun of
the final expanded test suite passed all 32 tests in 2.72 seconds; preserve
both receipts. No VM execution occurred during preparation.
