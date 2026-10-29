# Focused pre-execution comparator review — 29 October 2026

Scope: the initially supplied 172-line `reference_service.py`, 119-line `qualification_gate.py`, 20-line `q1q2_capture.py`, and 17-line `PILOT_PROTOCOL.md`, compared with unchanged ZeroRun source. This supplements the early reproducibility review. It is a read-only review of exploratory code, not approval of the main study or an executed comparison. The parent is repairing these files concurrently; findings below identify the inspected version, and those proposed repairs are not represented as already validated.

**Decision:** the independent receipt baseline is the right comparison to build, but this first version cannot yet be called contract-matched. Four grouped repairs/clarifications are material. No further checklist is proposed.

## C1. Execution metadata and identity do not yet match

**Defect.** `reference_service.py:128` uses `shutil.copytree`'s default metadata-preserving copy, while native ZeroRun uses byte copies, applies keyed modes, and zeroes timestamps (`src/zerorun/workspace.py:124`, `:149`, `:317`). Receipt identity omits timestamps, but execution sees preserved times and potentially copied extended attributes. Both `reference_service.tree` and `qualification_gate.inventory` enumerate children without recording the source root's mode; native `execution_directory_records` includes that mode. This is a concrete mismatch between key/qualification scope and executed state, not merely a difference in implementation style.

A metadata-reading fixture can distinguish the two staged executions. A root-mode-only change leaves the proposed gate and receipt key unchanged, although it changes native identity and may change fresh results. A timestamp-only change can alter the reference's fresh behavior while leaving its key and qualification unchanged. The source is quiescent throughout these examples; no hostile concurrent race is needed.

**Closure evidence.** Use an independently implemented equivalent metadata policy: copy ordinary file bytes, apply source modes, avoid inherited source xattrs/ACLs, zero all relevant file/directory/root timestamps after adding mount markers, and bind root mode in both gate and receipt identity. A small fixture must compare observed metadata and outcome across actual native staged and reference staged execution. Root-mode changes must invalidate qualification/identity; timestamp-only changes must have the documented invariant behavior after normalization. Preserve an initial failing challenge if run before repair.

**Fairness condition on the proposed repair.** A normalized private full-tree snapshot is a defensible fresh-only reference for a matched isolated-execution contract. It is not ordinary live-tree pytest: staging becomes a cost ordinary direct execution need not incur. If the fresh arm is changed this way, update its name and protocol and limit improvement claims to that deployment contract. A direct full-tree execution sensitivity/control is another valid option. Do not retain the “ordinary fresh full-tree” label while quietly adding a source-copy requirement that benefits reuse in the timing comparison.

Impact 4/5; confidence 5/5 in the source mismatch. No runtime counterexample was executed by this reviewer.

## C2. Required mountpoints are absent from the cold receipt snapshot

**Defect.** The Docker command mounts tmpfs at both `/workspace/.git` and `/workspace/.zerorun` (`reference_service.py:69`), but the cold snapshot excludes both and creates only `.git` (`:128`). The unchanged native workspace explicitly creates both locations before mounting the read-only source because current runc cannot create these mountpoints within that read-only bind (`workspace.py:305`). This threatens the first cold miss before the intended test runs. A fresh arm without prepared markers has the analogous precondition.

**Closure evidence.** Prepare both ordinary control directories before final metadata normalization and execute the real Docker path from an initially clean fixture. Retain a successful cold receipt request, a subsequent hit, a nonzero test outcome, and a fresh-only request. Inspect that masks are present, the real control contents are inaccessible, and execution did not merely fail at container creation/start. These checks can share the C1 fixture.

Impact 4/5 for executable feasibility; confidence 5/5 in the missing directory, with actual daemon behavior still requiring the smoke test.

## C3. Container completion and cleanup are not sufficiently established

**Concrete cleanup defect.** `created` remains false until `docker create` returns successfully (`reference_service.py:77`). A create timeout or transport failure after the daemon allocated the named container therefore skips the `finally` cleanup altogether. This can leave an orphan and contaminate later measurements. A later start timeout is cleaned only if this flag was set; cleanup errors can also replace the original failure rather than preserving both diagnoses.

**Completion/fairness defect.** The code discards the container ID and interprets the attach client's return code directly as the task's exit status. It does not independently attest the exact container's completed state before publishing authenticated success. Native ZeroRun explicitly checks `.State` and distinguishes infrastructure/transport errors from completed test exits (`oci.py:1192`). No falsely successful attach was reproduced here, but the proposed baseline does not yet implement the same “a completed execution succeeded” evidence rule or failure accounting. This difference cannot be hidden when comparing complete deployment costs.

`capture_output=True` additionally buffers entire streams in the host process before tail slicing. The container's memory limit does not bound that buffer. Native output retention is bounded while draining. This is relevant to claimed resource/failure parity, though a deliberately output-bounded workload scope can avoid requiring identical general-purpose hardening.

**Closure evidence.** Independently record/validate the exact created ID; require a completed state and coherent exit before any success receipt is published; distinguish infrastructure failure from ordinary nonzero tests. Cleanup must handle uncertain creation using the request's unique name, confirm removal or explicit absence, and retain both primary and cleanup errors. Add a deterministic fault-injection case where creation allocates then raises/times out; require confirmed absence and no receipt. Run live success, ordinary failure and timeout smoke cases and record no remaining task-owned container. Either bound stream draining or explicitly restrict/report output scope and ensure a large-output challenge cannot silently alter success or kill the host reader. These need not import ZeroRun or duplicate its implementation line-for-line.

Impact 4/5; confidence 5/5 for the create-error cleanup path and missing completion check; no claim that every normal Docker attach is unreliable.

## C4. Fresh bypass is not equivalent to native verification, and results need an explicit common interpretation

**Defect in the inspected version.** `verify=True` returns `fresh(root, task)` immediately (`reference_service.py:99`), bypassing the receipt identity, private snapshot, post-execution recheck and conflict handling. Native verification executes the staged contract and can invalidate a conflicting stored success. The receipt service could report a fresh failure and leave its earlier success available for the next normal lookup. Also, a nonzero miss is labeled `MISS_EXECUTED`, and exceptions are returned as text content without an MCP `isError` flag. Thus consumers must not silently interpret identical status names or successful JSON-RPC delivery as equivalent success semantics.

**Accepted bounded repair.** The parent proposes not advertising or timing this operation as cache audit: it will be an explicit fresh-output bypass, while native `VERIFY_MATCH` retains its separate meaning. That scope is acceptable. The protocol and analyzer must make the distinction visible; use the normalized execution path chosen under C1 and do not present the bypass as an implementation of ZeroRun's verification behavior. If audit equivalence is instead claimed, conflict detection and invalidation must be implemented and tested.

**Closure evidence.** Freeze a result-normalization table and shared client decision rules covering historical success, fresh success, ordinary test failure, infrastructure failure, qualification refusal and missing/inconsistent evidence. A deliberate nonzero test and malformed/error response must never be counted as success. Fresh-output requests must execute and return new output with an explicit nonhistorical label. If bypass semantics are retained, a test must demonstrate and document that no cache-audit promise is inferred. If verification semantics are chosen, a conflicting verified failure must prevent the next historical hit.

`q1q2_capture.py` is a reasonable independent data collector; by itself it is not a validator. The consuming correctness check must require a loaded capture, exactly one usable terminal record, nonempty collection, the intended per-phase outcomes and exit agreement with independently attested container completion. Missing capture or unsupported collection must be an explicit failure/refusal, never an empty matching success. No separate publication-critical algorithmic defect was found in that 20-line collector.

Impact 4/5 if verification equivalence or protocol conformance is claimed; the parent's explicitly separate bypass contract can close most of this through bounded implementation and accurate specification.

## What this review does not require

The trusted-host, quiescent-checkout and serial-request assumptions permit a small comparator. It need not reproduce every hostile multi-user defense or lock implementation, prove arbitrary Python determinism, or share implementation code with ZeroRun. The shared external qualification gate remains a sensible deployment treatment; before timing, the outer harness must visibly call and time it symmetrically around both reuse services. Runtime source must remain unchanged, and the comparator, gate, normalized execution policy and result interpretation must be frozen before main execution. Passing the pilot closes feasibility only.

### Parent repair decision recorded after this review

The parent will retain ordinary direct full-tree fresh execution as the primary practical baseline and intended-target oracle, while normalizing receipt misses to native staged execution. Before cost comparison, the exact selected target must have identical nonempty full-target outcomes under direct, native-staged and reference-staged execution. Metadata-sensitive cases that differ are retained as qualification counterexamples and excluded from an equivalence claim, not labeled cache correctness successes. This resolves the proposed fresh-baseline staging-cost concern at the design level and limits equivalence to qualified targets. Root-mode binding, metadata normalization, mountpoint preparation, exact-container completion attestation and cleanup after uncertain creation remain implementation/experimental closures; this reviewer has not certified their eventual execution.
