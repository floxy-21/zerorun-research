# Controlled granularity comparison — fixed before its execution

Recorded 2026-09-06. This is an exploratory extension following the documented
pycparser pilot, not a preregistered confirmatory study or an AI-agent trial.

Retain the five workload definitions and exact requested targets in
`run_frozen_campaign.WORKLOADS`. Use the frozen tip of each workload, not a
performance-selected revision. Preflight its complete Git tree before setup.
The repaired more-itertools clone replaces the incomplete download; preserve
the original download failure. Time-dependent behavior in an upstream target
remains a limitation of its deterministic-task assumption, not proof that an
operator has reviewed that target.

Each workload has two independent cache/selector trajectories in reversed arm
order. Each trajectory contains exactly seven requests: seed, repeat, repeat,
append a failing test, repeat the failure, restore exact bytes, repeat restored
bytes. Every request runs ordinary direct pytest, the original snapshot-first
whole-task path, the optimized authenticated read-only hit path, genuine
pytest-testmon 2.2.0, and an independent fresh full-target outcome capture.
Both ZeroRun arms have separate task names and therefore separate cache keys.
The snapshot-first ablation disables only the new hit-only helper, never a
correctness check. Testmon state is isolated per trajectory and persistent
across its requests. Its initial request uses --testmon-noselect. Coverage
7.10.7 and Testmon wheels are hash-pinned; no cloud Testmon service is used.

The direct arm is uninstrumented. Testmon's timed arm includes the small outcome
capture plugin needed to observe selections; report that asymmetry. Independent
fresh captures are validation cost, not the uninstrumented timing baseline.
Testmon also requires pytest's cacheprovider because it reads its `lf` option;
the first pilot exposed that dependency. Enable it for Testmon with an ephemeral
`/tmp` cache while retaining Testmon's separate persistent database. Preserve the
failed first pilot and record the corrected producer identity before rerunning.
Report both operational totals and validation/setup cost. Retain all failed,
unsupported, slower, or incomplete cells. Seed and mutation costs cannot be
removed from end-to-end ratios; conditional hit ratios are labeled separately.

Report descriptive per-workload totals and both trajectory orders, not a
p-value over nested test nodes or a population confidence interval from five
convenience subjects. Whole-task reuse preserves the requested command's
successful exit status, not per-node output, skipped-test evidence, coverage,
side effects, or agent task success. Testmon is a selector with a different
contract: selected outcomes and coverage by prior successful nodes are reported
separately; a subset exit status is never relabeled a complete fresh oracle.

No external operator authority is created. Cache authentication keys stay in a
private laboratory fixture and are excluded from publication. No model calls,
users, API charges, market-demand inference, or immigration outcome inference
are part of this experiment. Existing 5x/50% release gates remain unchanged.

Primary decision: does the implemented optimization reduce repeat-request cost
while the fixed failure/restoration challenges continue to agree with fresh
execution? The comparison characterizes an implemented optimization and reuse
granularity; it does not claim that memoization itself is new.

## Packaging compatibility correction (12:15 UTC, before corrective results)

The full cohort exposed a second upstream-configuration dependency: packaging
explicitly disables pytest's legacy-path plugin, while Testmon 2.2.0 uses its
`Config.rootdir` API. Its `-m not property` marker filter also disables Testmon
selection unless the documented `--testmon-forceselect` option is supplied.
Keep this failed attempt. After the original cohort finishes, repeat **all four
arms and all 14 requests** for packaging in a separate correction directory,
using stock `-p legacypath` and, after the full seed, `--testmon-forceselect` only
in the Testmon arm. No upstream file or target is edited to repair configuration.
Retain the original producer as `producers/controlled_comparison-v3.py` and bind
the corrected producer separately. Use this complete, predetermined corrected
packaging block in comparative tables; do not choose between runs by speedup.
The original uncorrected block remains visible as a baseline setup failure.

## Recorded recovery amendment (13:14 UTC, before recovery execution)

The first packaging correction exposed its strict warning policy: re-enabling
the stock legacy-path plugin produces `PytestAssertRewriteWarning` for that
already imported stock module, and the project treats warnings as errors.
Retain that failed block too. Rerun all four arms and all 14 packaging requests
in `comparison-packaging-final`, filtering only this exact stock-plugin rewrite
warning in the Testmon arm. Preserve every other warning and the upstream test
configuration. The full predetermined latest block, not a favorable timing,
is the packaging comparison. The recovery producer is separately archived and
hash-bound; the earlier two producer files remain immutable.

More-itertools completed three four-arm requests, then Testmon's failing-source
request exceeded its recorded 600-second harness limit. That abort and its
partial records remain in `comparison-final-1`; the timeout is not a successful
test or a complete 14-request Testmon subject. Complete a separate full
14-request **three-arm** whole-task block in `comparison-more-whole-task` using
the identical frozen commit, targets, source states, and engine, with relative
orders direct/snapshot/fast and fast/snapshot/direct. Do not derive any
more-itertools Testmon ratio from this recovery block or extrapolate from its
successful prefix. Report Testmon's completed comparative denominator as four
libraries (56 requests); its fifth-subject attempt remains visibly unavailable.
The whole-task comparison still requires all five libraries and 70 complete
freshly checked requests. This does not change the 5x/50% release thresholds,
the source closure, the injected failure, or any correctness condition.

The Testmon harness limit was 600 seconds, whereas the ordinary independent
pytest helper allows 900 seconds. Thus the timeout establishes non-completion
within that configured limit, not inherent non-termination or a matched-budget
superiority result. A larger-budget Testmon campaign is outside this recovery.
