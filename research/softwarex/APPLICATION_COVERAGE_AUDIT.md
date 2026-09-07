# Application coverage audit

This audit separates the original completed observations from the completed
expanded-budget campaign and its incomplete cases. It does not treat an
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
