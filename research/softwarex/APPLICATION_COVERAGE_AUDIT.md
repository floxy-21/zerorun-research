# Application coverage audit

## Current recovered V5/V6 evidence (8 September 2026)

The current [V6 raw ledger](evidence/application-revision-20260907-v6/record-only/run/completion.json)
completes **24 of 24 selected cases across eight repositories**, with **48 paired
blocks, 48 reused successes and 96 agreeing paired fresh-oracle captures**.
There is no remaining unattempted or unsupported selected case in this corrected
repeat. The original acquisition goal of 30 cases from ten repositories remains
unmet. [The versioned checker](verify_recovered_handoff_v6.py) independently
rechecks all **1,381 files** bound by the recovered V6 record manifest, source
inventories, full target outcomes, case order and costs. This is a purposive
controlled reference-patch cohort, separate from actual coding-agent outcomes.

| Current V6 complete-pair measure | Fresh execution | ZeroRun |
| --- | ---: | ---: |
| Producer plus consumer | 564.565 s | 541.170 s |
| Consumer waiting | 270.386 s | 57.039 s |
| Per-arm setup-inclusive chain | 572.541 s | 545.457 s |

Aggregate chain cost is **4.1438% lower** and consumer waiting **78.9045% lower**;
**15 cases are faster and nine are slower**. The 48 blocks are repeated
measurements of 24 selected cases, not 48 independent subjects. Acquisition,
shared image preparation, fresh diagnostics and oracle instrumentation retain
separate clocks. No general speedup, natural reuse frequency or agent
productivity effect follows from these totals.

V5 completed **23 of 24 cases** in the compatible environment, retaining the
Lizard-191 `Test_Big.test_typedef` failure (expected complexity 2, observed 3).
V6 uses the explicitly [corrected acquisition](evidence/application-revision-20260907-v6/corrected-acquisition/):
only that supplied test expectation changes **2 to 3**. Its corrected method AST
matches the [independently retrieved public upstream method](https://github.com/terryyin/lizard/blob/67d87968e9fecd459c9a9a1dcb01cf6ceac5721d/test/test_languages/testCAndCPP.py),
with exact retained source and retrieval hashes in
[upstream-fixture-reference](evidence/application-revision-20260907-v6/upstream-fixture-reference/).
All other selected test content, case IDs, order, targets, base archives and
supplied production patches remain unchanged. The original ledger SHA-256 is
`4767391ca8f99ba3ad698bf1577b0e66c9dccc97fbd78565447ba1a4b1281997`;
the explicitly derived V6 ledger SHA-256 is
`e6453b256f80e0eb79d280acc6bb8a143c006e56854f1702300cdc6550ab9ee2`.
V5's failure is preserved; V6 is not relabeled as an unchanged V5 run. The two
historical Lizard-174 skips remain skips. The preliminary extra no-skip
preflight refusal is also retained.

Both cohorts use historical **ZeroRun 0.5.1**, public harness
`0528905a52b74df78aa4e5a09219df34620282dd` and engine
`ebf2884df12573d63f45813200e0675288d12096`. The new **0.5.3 integration release**
and its install/regression receipts are separate. The
[compatible-image audit](validate_compatible_image.py) checks the exact 52-file
build record and complete 16-wheel hash-locked closure: Python 3.10.21, pytest
8.4.2 and the recorded application dependencies. The actual build used
`--no-cache --pull=false --network=none`; wheel acquisition used the network and
the public base pull reported an existing image up to date. Image preparation
was 82.289 s, including a nested 57.720 s build clock. It occurred before V5/V6
and is not newly incurred V6 setup. The
[reproduction guide](FRESH_REAL_WORKLOAD_REPRODUCTION.md) provides the sealed
recipe and distinguishes a reader's new image identity from the historical
loopback image, which is not publicly pullable.

**The original V6 230-sample host monitor has not been recovered.** Its surviving
hash does not substitute for those observations. The restored guest archive and
source records do not certify uninterrupted or quiet-host timing. A separately
retained VirtualBox event-log analysis, if supplied, supports only its stated
logged-state interval; it cannot recreate the missing sampled monitor. These
are recovered author-side experiments in an existing VM, not independent human
replication or a clean operating-system reproduction. Earlier unfavorable
results, partial campaigns and interruptions remain below.

### Separate outcome-informed repeated-consumer follow-up

The [prospective follow-up](AMORTIZATION_FOLLOWUP_V1.md) selected **lkml-85** by
its largest summed V6 cold-producer snapshot-preparation cost. It then fixed
N=1,2,4 consumers in that order, with two counterbalanced blocks per N. All six
blocks and all 14 reused successes reconcile; this is one selected case, not
six new subjects. Historical lkml-85 one-consumer slowdowns of 64.96% and 57.47%
remain part of V6. The new full-chain observations are all shown here:

| Consumers | Block | Fresh chain | ZeroRun chain | Chain saving |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0 | 6.886 s | 32.066 s | -365.64% |
| 1 | 1 | 6.743 s | 10.560 s | -56.62% |
| 2 | 0 | 10.406 s | 9.157 s | 12.00% |
| 2 | 1 | 11.717 s | 11.801 s | -0.71% |
| 4 | 0 | 17.304 s | 11.663 s | 32.60% |
| 4 | 1 | 16.505 s | 11.390 s | 30.99% |

The first ZeroRun producer took **30.742 s** and its separate oracle took
**101.847 s**; neither is removed. Per-arm setup, every consumer, both oracles
and fresh diagnostics are in the
[sealed records](evidence/amortization-followup-20260908-v1/record-only/).
The operator reported concurrent Windows publication-export I/O at approximately
04:22–04:24 UTC, recorded in the separate
[timing qualification](evidence/amortization-followup-20260908-v1/timing-context.json).
This does not establish the cause of an individual timing anomaly. The
follow-up is not pooled with V6 or presented as quiet-host performance
replication. Reused identified status and a fresh execution transcript are
different services; no coding-agent speedup is inferred.

## Earlier evidence retained in its original scope

This audit keeps the original observations, interrupted expanded-budget campaign and separate v3 no-cache-build campaign distinct. It does not treat an
unattempted case as a passing or failing test, and does not establish natural
reuse frequency.

## Frozen selection and original run

The [main ledger](evidence/handoff-acquisition-recovery-v1/main.json) contains
24 cases from eight repositories: three cases per repository, in the fixed
repository shortlist order and then the prescribed case-hash order. Its exact
15,555 bytes have SHA-256
`4767391ca8f99ba3ad698bf1577b0e66c9dccc97fbd78565447ba1a4b1281997`.
The acquisition target was 30 cases from ten repositories; that target remains
unmet. The selected source archives are retained and bound to the ledger.

The original [run protocol](evidence/handoff-main-v2/run/protocol.json) used a
300-second campaign budget, a symmetric 120-second execution cap and two
counterbalanced paired blocks per case. Its
[completion record](evidence/handoff-main-v2/run/completion.json) reports two
complete cases, one compatibility failure, one case interrupted by the campaign
budget before a paired block, and 20 cases not run because of the budget.
It reports no material correctness stop or campaign-level exception.

| Order | Frozen case | Original 300-second run | Expanded repeat |
| --- | --- | --- | --- |
| 1 | `eliben__pycparser-364` | COMPLETE | COMPLETE |
| 2 | `eliben__pycparser-346` | COMPLETE | COMPLETE |
| 3 | `eliben__pycparser-236` | INCOMPLETE_OR_UNSUPPORTED (compatibility) | INCOMPLETE_OR_UNSUPPORTED — C1 |
| 4 | `joke2k__django-environ-329` | INCOMPLETE_OR_UNSUPPORTED (budget before paired block) | COMPLETE |
| 5 | `joke2k__django-environ-450` | NOT_RUN_BUDGET | COMPLETE |
| 6 | `joke2k__django-environ-174` | NOT_RUN_BUDGET | INCOMPLETE_OR_UNSUPPORTED — C2 |
| 7 | `tobymao__sqlglot-3182` | NOT_RUN_BUDGET | COMPLETE |
| 8 | `tobymao__sqlglot-1765` | NOT_RUN_BUDGET | INCOMPLETE_OR_UNSUPPORTED — C3 |
| 9 | `tobymao__sqlglot-2658` | NOT_RUN_BUDGET | COMPLETE |
| 10 | `terryyin__lizard-174` | NOT_RUN_BUDGET | COMPLETE |
| 11 | `terryyin__lizard-241` | NOT_RUN_BUDGET | COMPLETE |
| 12 | `terryyin__lizard-191` | NOT_RUN_BUDGET | INCOMPLETE_OR_UNSUPPORTED — C4 |
| 13 | `eyeseast__python-frontmatter-34` | NOT_RUN_BUDGET | INCOMPLETE_OR_UNSUPPORTED — C2 |
| 14 | `eyeseast__python-frontmatter-56` | NOT_RUN_BUDGET | INCOMPLETE_OR_UNSUPPORTED — C2 |
| 15 | `eyeseast__python-frontmatter-31` | NOT_RUN_BUDGET | INCOMPLETE_OR_UNSUPPORTED — C2 |
| 16 | `joshtemple__lkml-85` | NOT_RUN_BUDGET | COMPLETE |
| 17 | `joshtemple__lkml-97` | NOT_RUN_BUDGET | COMPLETE |
| 18 | `joshtemple__lkml-87` | NOT_RUN_BUDGET | COMPLETE |
| 19 | `mahmoud__boltons-302` | NOT_RUN_BUDGET | INCOMPLETE_OR_UNSUPPORTED — C5 |
| 20 | `mahmoud__boltons-203` | NOT_RUN_BUDGET | INCOMPLETE_OR_UNSUPPORTED — C5 |
| 21 | `mahmoud__boltons-31` | NOT_RUN_BUDGET | COMPLETE |
| 22 | `lepture__mistune-393` | NOT_RUN_BUDGET | COMPLETE |
| 23 | `lepture__mistune-143` | NOT_RUN_BUDGET | COMPLETE |
| 24 | `lepture__mistune-105` | NOT_RUN_BUDGET | COMPLETE |

For case 3, the [fresh compatibility record](evidence/handoff-main-v2/run/cases/eliben__pycparser-236/compatibility-oracle.json)
contains 44 passing and two failing tests. Both failures use `open(name, 'rU')`,
which raises `ValueError: invalid mode: 'rU'` in the frozen Python 3.12 runtime.
This occurred before a measured reuse pair. Case 4's
[compatibility record](evidence/handoff-main-v2/run/cases/joke2k__django-environ-329/compatibility-oracle.json)
contains 193 passing tests; its subsequent budget disposition is not evidence
that the repository is unsupported.

Consequently, the original two-case timing result describes controlled handoffs
on two pycparser repairs. It cannot establish representative performance over
the selected eight repositories. These cases apply the benchmark's supplied
reference patch and test patch; they are separate from the genuine coding-agent
producer and its independent patch evaluation.

## What the unchanged environment covers

The [dependency inventory](evidence/handoff-image-build-v2b/dependency-files.json)
contains pytest 9.1.1, pretend 1.0.9 and pytest's locked dependency closure.
It does not contain Django, six or PyYAML. Read-only inspection of the bound
source archives identifies specific coverage risks before additional execution:

- `joke2k__django-environ-174`: archived `setup.py:15` declares `django` and
  `six`; target `environ/test.py:7` directly imports Django.
- All three `python-frontmatter` cases: archived `setup.py:14-17` declares
  PyYAML and six; each selected `test.py` directly imports six. Their
  `requirements.txt` files also declare TOML.

These static dependency limitations were identified before the expanded repeat.
The four affected cases subsequently produced collection exit code 2 with no
node outcomes; the retained error records do not contain an import traceback,
so a missing-module cause is not claimed as independently established. The
selected archives were checked against their ledger hashes before inspection.
No selected case was removed and dependencies were not changed after outcomes.
The experiment uses one declared modern runtime; it does not recreate each
upstream project's original development environment.

## Expanded repeat and interpretation

The [prospective amendment](APPLICATION_REVISION_AMENDMENT.md) fixes a new
1,200-second campaign using the same 24-case ledger, order, image, runtime and
120-second execution cap. The frozen runner has no resume offset: a new run
repeats the complete selection in a new external directory. The first two cases
remain the same subjects, even when measured again. The original 300-second
records remain unchanged and their costs are not pooled with the repeat.

The campaign checks its deadline between cases, blocks and arms; an operation
already in progress can finish after that deadline. The sealed repeat now
reconciles **15 complete cases from seven repositories, nine
`INCOMPLETE_OR_UNSUPPORTED` cases and zero unrun cases**, retaining all 24
selected positions. There were 30 complete paired blocks and no material
fresh-oracle correctness stop. This completes attempted coverage of the selected
ledger; it does not make the nine incomplete cases successful or meet the
original 30-case acquisition target.

The [repeat completion ledger](evidence/application-revision-20260907-v2/record-only/handoff-main-repeat-v1/run/completion.json)
retains these exact error classes and messages. The codes in the table mean:

- **C1 — runtime/test compatibility:** `ValueError: repaired-state compatibility
  oracle failed`. The fresh record has 44 passing tests and two failures caused
  by `open(..., 'rU')`.
- **C2 — collection without usable node outcomes:** `ValueError: oracle node
  identity absent/duplicate`. Each of the four raw captures has exit code 2,
  zero node IDs and zero outcomes. The static dependency limits above are
  relevant, but the receipt does not establish a specific missing-import error.
- **C3 — container lifecycle uncertainty:** `ConfigurationError: independent
  plain-pytest cleanup was not confirmed for uncertain create name ...`. The
  SQLGlot-1765 compatibility capture is empty. This occurred during the campaign
  affected by the host interruption; the records do not independently prove
  that the pause was its sole cause.
- **C4 — repaired-state assertion failure:** `ValueError: repaired-state
  compatibility oracle failed`. Lizard-191 has 86 passing tests and one failure:
  `Test_Big::test_typedef` expected cyclomatic complexity 2 and observed 3.
  The cause beyond that observed assertion is not established.
- **C5 — missing usable capture:** `JSONDecodeError: Expecting value: line 1
  column 1 (char 0)`. Both Boltons captures are zero bytes. Their underlying
  execution failure is not diagnosed by the retained record.

All nine incomplete cases stopped in compatibility preparation before a complete
paired handoff. None is recategorized as an incorrect reuse, a passing task or a
budget exclusion. Per-case raw records are under the linked completion ledger's
`cases/<case_id>/` directory.

The [host interruption record](evidence/application-revision-20260907-v2/host-interruption.json)
is outside the frozen wrapper bundle. The operator observed a VM pause at
08:07:57 UTC and issued the resume command within 08:24:17–08:24:30 UTC. The
exact resume instant is not established. At host 08:25:22 UTC the guest reported
08:08:51.915852 UTC. Raw guest timestamps were preserved and no clock
synchronization was requested during the run. Therefore the repeat is an
**interrupted campaign, not a clean performance replication**.

For its 30 complete pairs, the descriptive producer-plus-consumer totals are
240.692 seconds fresh and 235.872 seconds with ZeroRun (2.0% lower); consumer
totals are 113.499 and 24.359 seconds (78.5% lower). These ratios exclude
incomplete work and do not support an uninterrupted timing claim. Per-arm setup,
common image preparation, acquisition, fresh diagnostics and independent-oracle
costs remain separately reported. The original measurements are not pooled with
the repeat.

The [generated reconciliation](generated/handoff-evidence-v1.json), under
`controlled_runs.v2_main_extended`, reconstructs the case ledger and costs from
raw records. `application_revision_attempts.revision` confirms the wrapper's
public checkout and execution binding; its complete-sequence status does not
mean every main case succeeded.

The [fresh public-source reproduction guide](FRESH_REAL_WORKLOAD_REPRODUCTION.md)
and [separate pilot records](evidence/application-revision-20260907-v2/record-only/handoff-pilot-fresh-image-v1/)
now accompany a strictly verified author-side rebuild and rerun from public
source. All two original pilot cases and four paired blocks completed using the
new image binding, with fresh-oracle agreement. The new build took 26.396 seconds;
the pilot's producer-plus-consumer totals were 55.272 seconds fresh and 61.101
seconds with ZeroRun (10.5% higher), while consumer totals were 20.060 and 4.857
seconds (75.8% lower). Common image construction is outside those chain ratios.
The new image and pilot have separate provenance and are not substituted into
the historical cohorts. The retained Docker build output reports cached layers;
the rebuilt image has the same content digest as the historical image. This
checks the public recipe and rerun with the available build cache, and does not
establish an uncached rebuild.

This establishes author-side public-source recipe reproduction on the existing
VM, with an already available pinned base image. It does not establish a clean
operating-system installation, independent human replication, a publicly
pullable historical derived image or a general bit-identical rebuild guarantee.
Natural demand for repeated validation and end-to-end coding-agent acceleration
remain separate questions that these controlled experiments do not measure.

## v3 full-ledger campaign on the no-cache build

A subsequent public-source run disabled Docker build-cache reuse and evaluated the unchanged 24-case ledger. It completed 10 cases across five repositories and 20 paired blocks, with six incomplete or unsupported cases and eight cases not run within the fixed budget. All 20 reused successes agreed with fresh oracles. Complete producer-consumer cost was near break-even (0.12% lower), while consumer waiting was 79.1% lower; five completed cases were slower. Image preparation added 66.070 seconds. Host continuity checks passed within the recorded sampling bounds. This author-side run used the existing VM and pre-existing public base images; it is not independent human replication or a clean operating-system installation.

The earlier 15-case result across seven repositories remains separate; it is not pooled with these ten cases. The six incomplete cases comprise two repaired-state compatibility failures and four records lacking usable oracle node identities. The eight budget exclusions remain unattempted. No substitute cases or retries were used.

| Order | Frozen case | v3 disposition |
| --- | --- | --- |
| 1 | `eliben__pycparser-364` | COMPLETE |
| 2 | `eliben__pycparser-346` | COMPLETE |
| 3 | `eliben__pycparser-236` | INCOMPLETE_OR_UNSUPPORTED |
| 4 | `joke2k__django-environ-329` | COMPLETE |
| 5 | `joke2k__django-environ-450` | COMPLETE |
| 6 | `joke2k__django-environ-174` | INCOMPLETE_OR_UNSUPPORTED |
| 7 | `tobymao__sqlglot-3182` | COMPLETE |
| 8 | `tobymao__sqlglot-1765` | COMPLETE |
| 9 | `tobymao__sqlglot-2658` | COMPLETE |
| 10 | `terryyin__lizard-174` | COMPLETE |
| 11 | `terryyin__lizard-241` | COMPLETE |
| 12 | `terryyin__lizard-191` | INCOMPLETE_OR_UNSUPPORTED |
| 13 | `eyeseast__python-frontmatter-34` | INCOMPLETE_OR_UNSUPPORTED |
| 14 | `eyeseast__python-frontmatter-56` | INCOMPLETE_OR_UNSUPPORTED |
| 15 | `eyeseast__python-frontmatter-31` | INCOMPLETE_OR_UNSUPPORTED |
| 16 | `joshtemple__lkml-85` | COMPLETE |
| 17 | `joshtemple__lkml-97` | NOT_RUN_BUDGET |
| 18 | `joshtemple__lkml-87` | NOT_RUN_BUDGET |
| 19 | `mahmoud__boltons-302` | NOT_RUN_BUDGET |
| 20 | `mahmoud__boltons-203` | NOT_RUN_BUDGET |
| 21 | `mahmoud__boltons-31` | NOT_RUN_BUDGET |
| 22 | `lepture__mistune-393` | NOT_RUN_BUDGET |
| 23 | `lepture__mistune-143` | NOT_RUN_BUDGET |
| 24 | `lepture__mistune-105` | NOT_RUN_BUDGET |

Exact complete-pair totals were 281.329 s fresh versus 280.985 s ZeroRun, consumer totals 135.856 s versus 28.353 s, and per-arm setup-inclusive totals 283.951 s versus 283.812 s. Common image preparation was 66.070 s separately. Five of ten complete cases were slower (SQLGlot 3182 and 2658, lizard 174 and 241, lkml 85); the median complete case was 1.707% slower. Thus aggregate near break-even behavior is not a universal case-level benefit.

The [v3 ledger](evidence/application-revision-20260907-v3/record-only/handoff-main-clean-v1/run/completion.json), [host records](evidence/application-revision-20260907-v3/host-observation/) and [reproduction guide](FRESH_REAL_WORKLOAD_REPRODUCTION.md) bind the full scope. Host reconciliation passed 132 samples with zero errors and a maximum gap of 16.737447 s; no pause was detected within those checks. Existing VM, pre-existing public base layers, external storage and shared-host limits remain disclosed.
