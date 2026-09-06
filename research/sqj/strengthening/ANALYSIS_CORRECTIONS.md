# Independent reconciliation correction log

## Direct/oracle control-call timeout assumption

The first reconciliation of the imported September 6, 2026 replication
artifact stopped with `ValueError: phase timeout differs`. The independent
analyzer and its synthetic fixtures had assumed that Docker control calls
used 60 seconds. The source-bound plain helper actually declares
`_DOCKER_CONTROL_TIMEOUT_SECONDS = 30` in
`research/sqj/source-final/tools/product_generalization_benchmark.py`, and the
observed direct/oracle phase receipts specify 30 seconds. The product's OCI
inspect and cleanup limits are 60 seconds. Both paths use a 900-second test
execution limit. Thus equal execution limits do **not** imply equal control
limits, and the manuscript must not make the latter claim.

The analyzer and its synthetic fixtures were corrected to require exactly
the observed, source-declared 30 seconds for the plain helper's control
phases. No production code, frozen experiment driver, recorded duration,
correctness criterion, or performance gate was changed. The previous
analyzer is preserved as `analyze_replication_pre_real_validation_v1.py`
(SHA-256 `d954c16e61de784634d2f9a23deb76bacb2f7c7b230755ceea3afd40c68daf1c`).
The corrected full reconciliation succeeds on the complete original and
explicit recovery artifacts. Adversarial regression results are retained
in `evidence/recovery-real-validation-tests.xml`.

## Recovery preflight and crash-flush records

The first recovery runner refused the interrupted campaign because an
observation file was empty. It created no recovery output directory and
executed no experimental request. Its source is preserved as
`run_replication_recovery_preflight_v1.py`. The amended runner preserves
all original bytes and retries the complete interrupted preselected block
in a separate attempt. The independent analyzer recognizes only zero-byte
records as unavailable, lists their paths and hashes, and never turns them
into successful observations or measured zero-duration requests. Nonempty
malformed observations still fail validation. The recorded host incident,
original malformed files, and failed preflight log remain in the artifact.

The completed-block estimator contains 168 requests across 24 planned
blocks, including explicitly restarted packaging block 4. The original
attempt additionally contains one complete request, one incomplete request
with three completed arm timings, and an empty next-invocation record.
Their costs remain separately visible and are included in an all-recorded-
attempt cost sensitivity. Missing oracle, active interruption, and lost
workload-level setup costs are not reconstructed or invented.

## Platform-independent receipt paths

Derived partial/error receipt references now use slash-separated artifact
paths, not a caller's absolute host path. Running the complete actual
reconciliation with relative and absolute input roots gives identical
analysis, equal to the saved result. The prior platform-path result remains
preserved as `evidence/replication-analysis-platform-paths-v1.json`.

Targeted tests caught an initial path-label implementation that rejected
evidence outside the source tree. Stable `external-evidence/` labels now
support those input roots while leaving all in-artifact labels unchanged.
The initial two test failures and corrected nine passing tests are retained
in `evidence/recovery-portability-tests.xml` and
`evidence/recovery-portability-tests-2.xml`. Only reference formatting
changed; no raw evidence, measurement, denominator, or result changed.
