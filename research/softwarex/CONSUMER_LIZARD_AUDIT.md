# Consumer responses and Lizard fixture correction

Source-indexed observations from existing sealed records, with literal field comparison and exact excerpts. No human author review, independent human study, new model call, experiment, or acceptance probability is claimed. Other prose assertions remain subject to review.

Run `python -B -m research.softwarex.build_consumer_lizard_audit --check` from the complete source checkout to reconcile the saved records and compare both this Markdown and the machine-readable [audit](generated/consumer-lizard-audit-v1.json). This does not invoke models, Docker or upstream tests.

## Six original selected issues

These are actual model-produced final patches with unchanged supplied targets. The separate assisted SQLGlot repair passes 99 nodes but changes neither its original primary outcome nor consumer eligibility.

| Selected issue | Baseline passed / failed | Original final passed / failed | Consumer stages | Fresh source |
|---|---:|---:|---:|---|
| `eliben__pycparser-236` | 45 / 1 | 46 / 0 | 3 | [baseline](evidence/agent-application-053-oracles-v2/record-only/cases/eliben__pycparser-236/baseline-capture/raw-outcomes.json) / [final](evidence/agent-application-053-oracles-v2/record-only/cases/eliben__pycparser-236/final-capture/raw-outcomes.json) |
| `joke2k__django-environ-174` | 111 / 3 | 114 / 0 | 3 | [baseline](evidence/agent-application-053-oracles-v2/record-only/cases/joke2k__django-environ-174/baseline-capture/raw-outcomes.json) / [final](evidence/agent-application-053-oracles-v2/record-only/cases/joke2k__django-environ-174/final-capture/raw-outcomes.json) |
| `tobymao__sqlglot-3182` | 96 / 3 | 97 / 2 | 0 | [baseline](evidence/agent-application-053-oracles-v2/record-only/cases/tobymao__sqlglot-3182/baseline-capture/raw-outcomes.json) / [final](evidence/agent-application-053-oracles-v2/record-only/cases/tobymao__sqlglot-3182/final-capture/raw-outcomes.json) |
| `terryyin__lizard-241` | 16 / 5 | 21 / 0 | 3 | [baseline](evidence/agent-application-053-oracles-v2/record-only/cases/terryyin__lizard-241/baseline-capture/raw-outcomes.json) / [final](evidence/agent-application-053-oracles-v2/record-only/cases/terryyin__lizard-241/final-capture/raw-outcomes.json) |
| `eyeseast__python-frontmatter-56` | 17 / 1 | 18 / 0 | 3 | [baseline](evidence/agent-application-053-oracles-v2/record-only/cases/eyeseast__python-frontmatter-56/baseline-capture/raw-outcomes.json) / [final](evidence/agent-application-053-oracles-v2/record-only/cases/eyeseast__python-frontmatter-56/final-capture/raw-outcomes.json) |
| `joshtemple__lkml-87` | 48 / 1 | 49 / 0 | 3 | [baseline](evidence/agent-application-053-oracles-v2/record-only/cases/joshtemple__lkml-87/baseline-capture/raw-outcomes.json) / [final](evidence/agent-application-053-oracles-v2/record-only/cases/joshtemple__lkml-87/final-capture/raw-outcomes.json) |

## All fifteen actual consumer responses

Each source link identifies the physical JSONL line containing the final agent message (`/item/text`). Decode that text as JSON: `/execution_in_this_call` is the literal historical-versus-fresh claim and `/validation_status` is the literal outcome claim. The observed result is separately at `/item/result/structured_content`; its precise line, full excerpts, response hash and decoded-message hash are in the JSON audit. `false` means no execution in this call; `true` means newly executed validation.

The table compares those two explicit claims with recorded status and exit code. It does not certify every detail of prose. All fifteen exports omit the wire `isError` field. Thirteen messages additionally assert it; those assertions are **unverifiable from these records, not established false**. Client `status=failed` with an intact `MISS_FAILED` payload is separately observed for the five restored states. Restoration was operator-controlled, not an autonomous model edit. No human-author review is asserted.

| Case / stage | Recorded status; exit | Model executes now; outcome | Exact diagnostics excerpt | Extra wire claim | Response locator |
|---|---|---|---|---|---|
| `eliben__pycparser-236` / available | `HIT_REUSED`; 0 | `false`; `success` | ZeroRun returned HIT_REUSED for agent-tests with exit_code 0 and isError false. | `isError false` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/eliben__pycparser-236/plan/attempts/available/model.stdout.log#L6) |
| `eliben__pycparser-236` / fresh | `VERIFY_MATCH`; 0 | `true`; `success` | agent-tests ran with verify=true. | Not asserted | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/eliben__pycparser-236/plan/attempts/fresh/model.stdout.log#L6) |
| `eliben__pycparser-236` / restored | `MISS_FAILED`; 1 | `true`; `failure` | ZeroRun agent-tests with verify=true returned MISS_FAILED, exit_code=1, isError=true. | `isError=true` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/eliben__pycparser-236/plan/attempts/restored/model.stdout.log#L6) |
| `joke2k__django-environ-174` / available | `HIT_REUSED`; 0 | `false`; `success` | ZeroRun returned HIT_REUSED for agent-tests with exit_code 0 and isError false. | `isError false` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/joke2k__django-environ-174/plan/attempts/available/model.stdout.log#L6) |
| `joke2k__django-environ-174` / fresh | `VERIFY_MATCH`; 0 | `true`; `success` | agent-tests ran with verify=true. | `isError=false` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/joke2k__django-environ-174/plan/attempts/fresh/model.stdout.log#L6) |
| `joke2k__django-environ-174` / restored | `MISS_FAILED`; 1 | `true`; `failure` | ZeroRun agent-tests with verify=true returned MISS_FAILED, exit_code=1, isError=true. | `isError=true` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/joke2k__django-environ-174/plan/attempts/restored/model.stdout.log#L6) |
| `terryyin__lizard-241` / available | `HIT_REUSED`; 0 | `false`; `success` | ZeroRun returned HIT_REUSED for agent-tests, exit_code 0, isError false. | `isError false` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/terryyin__lizard-241/plan/attempts/available/model.stdout.log#L6) |
| `terryyin__lizard-241` / fresh | `VERIFY_MATCH`; 0 | `true`; `success` | agent-tests ran with verify=true. | `isError=false` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/terryyin__lizard-241/plan/attempts/fresh/model.stdout.log#L6) |
| `terryyin__lizard-241` / restored | `MISS_FAILED`; 1 | `true`; `failure` | Fresh hermetic verification of agent-tests returned MISS_FAILED with exit code 1: 5 failed, 16 passed, 2 warnings. | Not asserted | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/terryyin__lizard-241/plan/attempts/restored/model.stdout.log#L6) |
| `eyeseast__python-frontmatter-56` / available | `HIT_REUSED`; 0 | `false`; `success` | ZeroRun agent-tests returned HIT_REUSED, exit_code 0, and isError false. | `isError false` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/eyeseast__python-frontmatter-56/plan/attempts/available/model.stdout.log#L6) |
| `eyeseast__python-frontmatter-56` / fresh | `VERIFY_MATCH`; 0 | `true`; `success` | agent-tests ran with verify=true. | `isError was false` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/eyeseast__python-frontmatter-56/plan/attempts/fresh/model.stdout.log#L6) |
| `eyeseast__python-frontmatter-56` / restored | `MISS_FAILED`; 1 | `true`; `failure` | Fresh hermetic verification of agent-tests returned MISS_FAILED, exit_code 1, and isError=true: 18 tests collected, 17 passed, 1 failed. | `isError=true` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/eyeseast__python-frontmatter-56/plan/attempts/restored/model.stdout.log#L6) |
| `joshtemple__lkml-87` / available | `HIT_REUSED`; 0 | `false`; `success` | ZeroRun returned HIT_REUSED for agent-tests, exit_code 0, isError false. | `isError false` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/joshtemple__lkml-87/plan/attempts/available/model.stdout.log#L6) |
| `joshtemple__lkml-87` / fresh | `VERIFY_MATCH`; 0 | `true`; `success` | ZeroRun run_tests for agent-tests with verify=true returned VERIFY_MATCH, verified=true, exit_code=0, and isError=false. | `isError=false` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/joshtemple__lkml-87/plan/attempts/fresh/model.stdout.log#L6) |
| `joshtemple__lkml-87` / restored | `MISS_FAILED`; 1 | `true`; `failure` | ZeroRun run_tests(task='agent-tests', verify=true) returned MISS_FAILED, exit_code=1, isError=true: fresh hermetic execution failed; no reusable success was stored. | `isError=true` — unverifiable | [JSONL line 6](evidence/agent-application-053-consumers-v2/record-only/cases/joshtemple__lkml-87/plan/attempts/restored/model.stdout.log#L6) |

## Indexed Lizard correction

V5 remains 23/24 with the original failure. V6 is a separately labeled corrected-fixture 24/24 repeat, not an unmodified replay or a new algorithm. The pinned upstream method was retrieved during the recovery audit; missing original diagnostic bytes are not claimed recovered. Missing fine-grained host monitoring still qualifies V6 timing.

Only `Test_Big.test_typedef` in `test/test_languages/testCAndCPP.py` changes the expected cyclomatic complexity from 2 to 3. The existing strict reader reconstructs both targets from the same original archive and test patches, checks every other byte, and confirms unchanged case order, targets, base archives and production/reference patches. The corrected method's AST matches the pinned public upstream method. This is an explicit intervention, not an original-cohort pass.

Records: [exact diff](evidence/application-revision-20260907-v6/corrected-acquisition/lizard191-fixture.patch), [correction record](evidence/application-revision-20260907-v6/corrected-acquisition/FIXTURE_CORRECTION.json), [prospective V6 amendment](evidence/application-revision-20260907-v6/record-only/provenance/APPLICATION_REVISION_V6_AMENDMENT.md), [upstream retrieval](evidence/application-revision-20260907-v6/upstream-fixture-reference/retrieval.json). Upstream: [67d87968e9fecd459c9a9a1dcb01cf6ceac5721d](https://raw.githubusercontent.com/terryyin/lizard/67d87968e9fecd459c9a9a1dcb01cf6ceac5721d/test/test_languages/testCAndCPP.py), method lines 685–701; retained source SHA-256 `34e839761f3a09f1193bab78318ca46cbd3819b4650a470057f0f918307372de`.

```diff
--- a/test/test_languages/testCAndCPP.py
+++ b/test/test_languages/testCAndCPP.py
@@ -576,7 +576,7 @@
         """
         result = get_cpp_function_list(code)
         self.assertEqual(1, len(result))
-        self.assertEqual(2, result[0].cyclomatic_complexity)
+        self.assertEqual(3, result[0].cyclomatic_complexity)
```

| Cohort | Completed / selected | Unmodified outcome ledger |
|---|---:|---|
| V5 | 23 / 24 | [record](evidence/application-revision-20260907-v5/record-only/run/completion.json) |
| V6 | 24 / 24 | [record](evidence/application-revision-20260907-v6/record-only/run/completion.json) |

| Retained fresh check | Collected | Passed | Failed | Exit | Raw per-node evidence |
|---|---:|---:|---:|---:|---|
| V5 compatibility | 87 | 86 | 1 | 1 | [record](evidence/application-revision-20260907-v5/record-only/run/cases/terryyin__lizard-191/compatibility-capture/raw-outcomes.json) |
| V6 compatibility | 87 | 87 | 0 | 0 | [record](evidence/application-revision-20260907-v6/record-only/run/cases/terryyin__lizard-191/compatibility-capture/raw-outcomes.json) |
| V6 block-0-fresh-fresh-oracle | 87 | 87 | 0 | 0 | [record](evidence/application-revision-20260907-v6/record-only/run/cases/terryyin__lizard-191/block-0/fresh/oracle-capture/raw-outcomes.json) |
| V6 block-0-zerorun-fresh-oracle | 87 | 87 | 0 | 0 | [record](evidence/application-revision-20260907-v6/record-only/run/cases/terryyin__lizard-191/block-0/zerorun/oracle-capture/raw-outcomes.json) |
| V6 block-1-fresh-fresh-oracle | 87 | 87 | 0 | 0 | [record](evidence/application-revision-20260907-v6/record-only/run/cases/terryyin__lizard-191/block-1/fresh/oracle-capture/raw-outcomes.json) |
| V6 block-1-zerorun-fresh-oracle | 87 | 87 | 0 | 0 | [record](evidence/application-revision-20260907-v6/record-only/run/cases/terryyin__lizard-191/block-1/zerorun/oracle-capture/raw-outcomes.json) |
| V6 separate preflight combined target | 102 | 102 | 0 | 0 | [record](evidence/application-revision-20260907-v6/launch-evidence/preflight-records/lizard-first/raw-outcomes.json) |

The separate combined-target preflight has its own denominator; it is not an extra paired block. These are saved fresh-check observations, not tests executed by this audit. The before/after method text, reconstructed source hashes, archive/metadata bindings, actual failed node and every linked file's byte count/hash are in the JSON audit.
