# Focused main-protocol methods review

Date: 2026-10-29. Scope: selection, estimands, cost accounting, and the 12-block/two-session uncertainty plan. This is internal fresh-context criticism, not independent replication. No implementation was changed or experiment launched by this reviewer.

**Verdict: revise the three definitions below before their dependent main analysis is frozen; then proceed.** The selected empirical route is viable. No new workloads, human recruitment, additional host campaign, or larger checklist is required. B1 preparation can proceed while these definitions are closed. Main B2 timing should wait for the cost/statistic definitions and the already-planned executable freeze; the B3 unit clarification is required before replay inference.

## What is adequate now

The six fixed projects agree with `COHORT_RULE.json`. Main source screening explicitly reports no inspected outcomes; `MAIN_SELECTION.json` retains all six projects and prohibits favorable replacements or test exclusions. Pilot targets are separate. This supports a prospective evaluation on these selected tasks, conditional on honest execution of the recorded selection history. It does not support random-repository inference.

The primary N=1 producer-plus-consumer endpoint is clear. N=0,2,4 are correctly treated as dependent prefixes. The three services have a sensible bounded comparison: fresh execution, unchanged native ZeroRun plus the external scope gate, and an independent receipt service plus that gate. The plan permits a receipt advantage and negative qualification budgets. Unknown human qualification is explicitly a sensitivity variable, not invented measured labor.

I verified the selected record counts as 20,20,4,0,20,20, totaling 84. All original conclusions are retained. The absence of a test workflow for python-ftfy is kept as zero observed records, not proof of no testing. Standardized source replay is explicitly separated from original CI execution and consumer desire for historical status.

Pilot attempt 3 has four complete paired blocks across cachetools and Blinker. Its saved N=1 summary reports ZeroRun savings versus fresh of 0.6784, 0.6522, 0.3124 and 0.1997 seconds; receipt savings are larger in every block. These are exploratory values. I reconciled the summary to raw block receipts: it equals the first two request outer times plus `arm_setup_seconds`, excluding `materialization_seconds`. The exclusion is small in this pilot but matters to the precise meaning of “complete cost.” Earlier attempts remain separately recorded.

## Three required closures

### F1. Freeze the cost ledger and qualification-policy scenario

The remaining ambiguity is material: `MAIN_SELECTION.json` requires full-tree/staged outcome equivalence “before admitting mechanical reuse,” whereas the experiment plan excludes research-only oracles from operational cost. An equality check cannot simultaneously be a required step in the evaluated deployment's qualification policy and a free research-only check.

**Closure evidence:** put the following explicit accounting rule in the frozen main protocol and implement it in the analyzer:

- For each arm, define `C_a(N)` as the sum of named, nonoverlapping deployment costs: the applicable allocation of common preparation, source materialization, arm-specific setup/qualification automation, producer request, N consumer requests, and incurred fallback/cleanup/renewal. Mark missing costs unknown. Show request-only and first-use totals separately.
- Choose the role of the full/staged equivalence preflight. If deployment admission requires it, charge the actual necessary executions and setup as qualification/renewal cost. If it is research validation of an externally assumed qualification, exclude it from that conditional deployment estimate, label that assumption, and show a separately costed operational-preflight scenario. Running all three implementations for experimental validation need not be charged to each deployed arm; identify the minimal qualification checks actually required by each scenario.
- Define signed additional-review budgets as `B_a(N)=C_fresh(N)-C_a(N)`, with human review still unmeasured. A nonpositive budget means no additional serialized review time fits. Positive seconds are a bound under the stated serialized elapsed-time scenario, not measured human active effort or money.
- State the deployment horizon used to allocate one-time costs. Twelve measurement blocks are replicates, not twelve production deployments or natural consumers over which to amortize review. For B3, include each actual required source-scope renewal under the declared policy, rather than silently reusing an earlier authorization over different content.

The pilot summary need not be overwritten. Label its narrower boundary and calculate any extended cost view additively from retained records. A small difference in source-copy cost is not grounds to rerun the pilot.

### F2. Freeze the exact paired statistic and conditional uncertainty interpretation

Twelve blocks, with all six arm orders in each of two sessions, are sufficient for a bounded repeat-variability study. No increase is required by this review. They are not a power guarantee or twelve independent hosts. A percentile bootstrap within session conditions on the two realized sessions; it cannot quantify variation across a population of future hosted runners. The current text should say this precisely.

**Closure evidence:** the frozen schedule and analyzer must specify:

1. The randomized order of the six permutations within each session and the target order, generated before main results. Keep session identity and all arm pairings. Report each session's estimate alongside the combined estimate, with fixed equal session weights. If a session is incomplete, retain the missing/failed cells and do not silently replace the planned balanced estimator with a favorable complete-case estimate.
2. The exact planned contrasts, including the direct ZeroRun-versus-receipt contrast needed for C1. Prefer explicit `fresh-ZeroRun`, `fresh-receipt`, and `receipt-ZeroRun` signed differences. State which are primary; do not infer a direct comparison by whether separate intervals overlap.
3. For each of 10,000 seeded resamples, draw whole paired blocks within each session, retain all N prefixes together, and recompute the complete statistic. State the interval's 95% level. Report the 12 raw block values and session means; the interval is conditional empirical repeat variability, not a repository or host population interval.
4. Make the 5% decision rule executable. For example, recompute the fractional saving `(mean comparator cost - mean tested-arm cost)/mean comparator cost` inside each bootstrap draw and require its lower endpoint above 0.05. Alternatively retain the common fresh-cost scale, but explicitly form the margin-adjusted contrast `mean(comparator-tested)-0.05*mean(fresh)` in each draw. Do not switch the denominator or pair after seeing results.
5. Label intervals pointwise by target and contrast. No family-wide “at least one win” or all-project superiority claim follows from selecting favorable pointwise intervals. A broader claim needs simultaneous inference or a separately frozen aggregate, but neither is necessary for the current per-target report.

This is a specification closure, not a demand for extra hypothesis tests. No p-value is required. A wide interval or session disagreement completes the experiment as uncertainty. Preserve all started costs and the predeclared stopping/recovery rule if the two 30-minute job limits prevent completion.

### F3. Name the B3 sampling unit correctly

The 84 entries are GitHub workflow-run records at retrieval, not necessarily 84 original execution attempts. Eight natsort rows have `run_attempt=2`; only the latest run-record snapshot is retained for each ID. Sorting by `created_at,id` orders initial workflow-run creation, not necessarily the later retry attempts. Earlier attempt results and exact retry chronology are not reconstructed by these objects.

**Closure evidence:** retain the current 84 records and state that B3 standardizes **one replay per selected workflow-run record, ordered by original run creation**. Treat omitted attempt history and original execution as unknown. Preserve `run_attempt`, original conclusion, materialization status, reconstructed identity and replay outcome in the final ledger. Report source-equivalent repeats and actual replay reuse within this imposed retained-record sequence, conditional on historical-status demand; do not call it the original chronology or total rerun opportunity. No additional API collection is required for that bounded claim.

If the intended claim instead concerns original attempt-level opportunity, attempt records and attempt ordering would be required before that claim could proceed. This review does not recommend that expansion. Action-required/cancelled entries remain in the selected denominator, and a model-imposed replay of them must not be counted as evidence that their original tests ran. State both `eligible/84 selected records` and `eligible/reconstructable records` where useful; do not hide unavailable records by reporting only the latter. Retain per-project denominators because the 60-all-workflow sampling window yields different observation periods and selection probabilities.

## Freeze and bounded completion

At review time, `MAIN_PROTOCOL.md`, `run_main.py` and `analyze_main.py` referenced by the pack were not yet present. That is ordinary implementation work already required by `execution-bridge.md`, not a fourth scientific objection. Before B2, bind their final bytes, the main manifests, runtime and balanced schedule; check the analyzer's accounting and resampling on small artificial records without using main outcomes. Resolve F1 and F2 in that freeze, and F3 before interpreting B3.

Once those conditions are met, **proceed with the selected main study**. Its possible final results include receipt-cache dominance, small or negative additional-review budgets, unsupported selected tasks, no reusable states among retained records, or imprecise timing differences. Each is a valid result. No natural-demand claim, actual human-review cost claim, or independent-replication label is needed to complete this bounded route.

Coordinator response after this review: F1 will charge one additional independently fresh full-tree oracle per reuse arm's newly qualified state, followed by its single counted producer and consumers. A prequalified-source scenario excludes that qualification phase only as a separate conditional sensitivity; human review stays unknown and no cost is amortized over measurement replicates. F3 will use the 84 latest workflow-run snapshots ordered by record creation, retaining `run_attempt` and unknown intermediate chronology. These proposed amendments satisfy F1 and F3 in substance; implementation in the final frozen protocol/analyzer remains to be checked. F2's exact contrast, margin and conditional interval definitions remain part of that same pre-timing freeze. No additional blocks or scientific checks are requested.

Reviewed input anchors (SHA-256): `experiment-plan/experiment-plan.md` `e7439d84bc7e58b7ca6b6c24ecc359cd389362c77754b24ca13718929fe845b0`; `experiment-plan/claim-map.json` `cbea218dfb19b3d8e9c4e7e2ce09577252a5334e3486ffac39a3e01c179b34e1`; `MAIN_SELECTION.json` `661c4690b6af632932c76fae3c08ceeb6d5268dddcecb0b8d4bd2b859f79b917`; `MAIN_REQUESTS.json` `03106b96f80eda156dafa82271bd2f29f039552e86b48f642014910e5eccb46c`. Pilot checks used `evidence/pilot-attempt-3/report.json`, `paired-n1-summary.json`, all four `block-*.json` records, and representative preflight/acquisition records. Main execution and outcomes were not reviewed.

Prose validation passed before the coordinator-response addition: 1,421 words, 11 flags, 7.74 per 1,000 words, below the required maximum of 20.
