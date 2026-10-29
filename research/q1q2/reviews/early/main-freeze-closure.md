# Focused pre-B2 methods closure

Date: 2026-10-29. Scope: F1, F2 and F3 from `main-protocol-focused-review.md` only. No main timings were inspected and no remote experiment was launched. Implementation was not changed.

**Verdict: F1, F2 and F3 are closed at the definition and inspected-implementation level. Proceed to B2 after the existing B1 disposition and final execution freeze. No additional scientific requirement, workload, block, or review campaign is requested.** This closes the specified methods concerns; it does not certify future execution, result validity, or publication readiness.

## F1: cost boundary and qualification policy — closed

`MAIN_PROTOCOL.md` and `MAIN_SELECTION.json` now specify an operational scenario with one additional independently fresh full-tree qualification oracle per reuse arm, followed by one producer and N consumers. Source materialization and authority setup are included. The separate prequalified-source sensitivity explicitly omits an earlier-paid oracle. Unknown human review remains a variable; measurement replicates do not amortize it.

The inspected implementation matches that definition. `run_main.py:timed_block` calls `oracle` once for each reuse arm and records its separate setup and response. The producer remains the first ordinary request, counted once. `analyze_main.py:components` adds oracle materialization, oracle setup and oracle invocation; `cost` includes that sum only in `operational_qualification`. The artificial cost test confirms that exact accounting at N=1 and N=4 and distinguishes the two conditional scenarios.

Common runtime/acquisition preparation is retained separately with an explicit identical first-use allocation. That common addition cancels from absolute paired differences and the additional-review budget. Reporting must preserve the existing scenario distinction: the analyzer's scenario ratios and 5% margin do not include that separate common preparation; a percentage labeled **first-use total** must add the displayed common allocation to its denominators. This is the meaning of the frozen accounting, not a request for another experiment.

## F2: contrasts, scheduling and conditional uncertainty — closed

The schedule contains all six selected targets, 12 blocks per target, six in each session, and every three-arm permutation exactly once per target/session. I checked all 72 scheduled block entries. Target order is explicitly fixed and disclosed; arm orders are shuffled. Fixed target order is acceptable for the stated per-target estimand and is not relabeled as randomized project ordering.

The analyzer implements the three stated signed contrasts: fresh minus ZeroRun, fresh minus receipt, and ZeroRun minus receipt. Positive values favor the second system. It generates 10,000 seeded draws by sampling six complete blocks within each realized session, using the same paired indices across arms, scenarios and N prefixes. Equal session weighting follows from the required six blocks in each session. It reports raw values and both session means, and declines a balanced estimate for incomplete or failed blocks.

For the two fresh comparisons, each bootstrap draw computes the specified margin `mean(fresh-arm) - 0.05*mean(fresh)` and requires its lower 2.5th percentile to exceed zero. Pointwise 95% interval scope is explicit. No family-wide, repository-population or future-host inference is claimed. The constant-cost artificial test checks the signed interval and margin exactly; the failure test checks that missing or invalid blocks do not become a favorable complete-case estimate.

This design remains a conditional timing study on two realized sessions. Twelve blocks are not a power guarantee. Imprecision, session disagreement or a negative result completes the study without further sampling being required by this review.

## F3: workflow-record unit — closed

The protocol and selection now identify B3 as 84 latest workflow-run record snapshots, with eight natsort records carrying `run_attempt=2`. They state that ordering is by original record creation, intermediate retry chronology is unknown, and replay is a standardized counterfactual sequence. Action-required/cancelled records and the zero-observed-workflow project remain visible. Original execution, attempt-level demand and desire for historical answers are not inferred.

B3's later implementation and result ledger have not been audited here. This closure is of the previously requested sampling-unit and interpretation definition; it does not certify a replay that has not yet occurred.

## Verification and source boundary

The three existing artificial analysis tests passed: **3 passed in 0.24 s**. The default Python lacked pytest, and the evaluation environment lacked NumPy; using the existing evaluation environment with the primary runtime's installed NumPy resolved the local test prerequisites without installation or code changes. No benchmark data were used.

The coordinator identifies published commit `8b85699f8e14f3a45668222635998c72b7a270fb`. The shared local checkout reports HEAD `ed8104ddb0a25b7c27b2f5507a885501530f9ccc` and does not contain that published commit object. Accordingly, this review binds the local bytes below and does **not** claim to have independently verified the remote commit. The ordinary final execution freeze should bind the actual executed bytes to these reviewed definitions.

| Inspected file | SHA-256 |
| --- | --- |
| `MAIN_PROTOCOL.md` | `e7b14b76658eda42e780236031d26b3bcd951b46461eff543c6be3c30db77abb` |
| `MAIN_SELECTION.json` | `4f5c72bdd5dd8fb98272f3244ce5a5fc03a1f37af5ab70f906d1d410fd0d66a2` |
| `MAIN_SCHEDULE.json` | `4088b0e40f3733719522ea9a9845b0ed69eadd562642efdc5e29077c1135a152` |
| `analyze_main.py` | `eec42050f1effecc76aaee65df96b7cbc98f91e069a758b21a53141d8c3c732a` |
| `test_analyze_main.py` | `6dbe392b6a8499c3934f27742c9a330dc7ddbe2d2ccd7172b6bb19cfb122044c` |
| `run_main.py` | `9f0b7004e386ace4ec769a4c9b0d499f832caa261cc0e3cc7fbe739fb944b1df` |

The reported prerequisite repair for ftfy and refusal for dotenv are compatible with the retained six-target denominator and existing versioned preflight policy. This focused review does not reopen their technical dispositions or treat them as evidence of favorable timing selection.
