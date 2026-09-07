# Independent SoftwareX manuscript claim audit

Reviewed the complete `research/softwarex/paper/submission.tex.in` on 6 September 2026, including declarations and metadata. Replication, state-rejoin and public-commit placeholders were unresolved at review time. This report does not certify those future substitutions or publication availability. No manuscript, runtime, frozen producer or VM was changed by this audit.

The initial review and its time-specific pending items are retained below as an audit trail, not presented as the current submission checklist. The application-evidence follow-up at the end records the later observed outcomes separately; final artifact status belongs to `generated/final-readiness.json`.

## Required corrections before finalization

1. **Name the correct more-itertools comparator.** The sentence saying it "remained slower overall with caching" is misleading. Direct cost was 1,492.510316 s, snapshot-first 611.771878 s and optimized 713.537338 s. Therefore direct/optimized is **2.091706**, while optimized is **16.634544% slower than snapshot-first**. Suggested wording: "For more-itertools, optimized reuse remained 16.6% slower than snapshot-first over complete sequences despite faster eligible hits." This is the material numerical/interpretive defect found in the fixed prose.
2. **Specify the old conditional estimator and hit denominator.** Say "40 optimized-arm hits" and "67.3–71.0% reduction in per-library mean eligible-hit latency versus snapshot-first (eight hits per arm per library)." Both cache arms have hits; 40 is not a count pooling the two arms. The new replication uses medians, so the original mean-based estimator should not be left implicit.
3. **Resolve the bibliography key.** The supplied additions file has `zhai2026toolcaching`, `kumar2026tvcache`, and **`aldanamartin2025eidos`**. The template's `eidos2025` is not that key. Either rename the imported entry or its citation consistently. Cite both 2026 cache papers as arXiv preprints, not invented conference publications.
4. **Keep release and new-results assertions conditional until verified.** Replace every `@@...@@` only from reconciled evidence. A source-bound commit link, runnable public artifact, release installation, author approval, and state-rejoin outcome are finalization checks, not established by the current template. The strict replication analyzer must reject a complete-success description if any planned block/request, expected outcome, source binding or failure receipt is missing.

## Reconciled numerical ledger

Read-only verification succeeded with `python -m research.sqj.analyze_comparison --check`, the current-revision analyzer, the existing `engineering_evidence()` validator, and `python -m research.sqj.strengthening.validate_traces --check`. The engineering validator required a read-only run outside the filesystem sandbox to read existing wheel bytes; it then passed without altered inputs.

| Subject | Fresh exit agreements / logical requests | Optimized hits | Mean eligible-hit reduction vs snapshot-first | Complete direct/optimized |
|---|---:|---:|---:|---:|
| packaging | 14 / 14 | 8 | 68.820104% | 0.994980 |
| pycparser | 14 / 14 | 8 | 69.715151% | 0.846088 |
| Colorama | 14 / 14 | 8 | 67.335624% | 1.562356 |
| Click | 14 / 14 | 8 | 71.049231% | 0.973366 |
| more-itertools | 14 / 14 | 8 | 69.182416% | 2.091706 |

The 70 requests contain 40 optimized hits and **30 optimized nonhits**, including seeds and failing requests. These are logical request agreements against a fresh oracle, not 70 users or independent projects. Strict analysis resolves the documented packaging correction and more-itertools whole-task recovery while retaining earlier failures. Source: `research/sqj/generated/comparison-summary.json`, regenerated in memory and checked against `research/sqj/evidence/comparison-final-1`, its documented correction/recovery directories, and `research/sqj/source-final`.

Other fixed claims are supported:

- **Windows:** 1,593 primary tests passed; 55 skipped; 19 additional passing subtests. Source: `docs/evidence/softwarex-regression/windows-20260906-readonly-hit-final/{receipt.json,pytest.log,junit.xml}`. Do not turn 1,667 JUnit aggregate entries into 1,667 passing primary tests.
- **Linux CI:** Python 3.10 through 3.14 all succeeded on `86f42289c2f59a74b1f642f0b20d1a26b3d55e57`. Respectively: 1,636/12, 1,635/13, 1,645/3, 1,640/8, 1,645/3 primary passed/skipped, with 19 subtests each. Source: `research/sqj/evidence/github-86f4228/{jobs.json,regression-python-*.log}`.
- **Integration:** 100 distinct attempted repositories, 99 passes, one safe refusal, zero failures. The strict commercial-smoke validator passed. This remains CLI/skill/MCP integration, not 100 upstream test suites or autonomous tasks. Source: `research/sqj/evidence/github-86f4228/commercial-100/commercial-100-repository-result.json`.
- **Fine-grained revision suite:** five completed subjects, 200 comparisons, zero reused nodes in every subject; product performance gates are false and unchanged at 5x/50%. Source: `research/sqj/evidence/github-86f4228/revision-final/product-generalization-five-repo-receipt.json`.
- **Input inventory:** 38 passing Linux cases comprise 32 production/oracle comparisons and six explicit oracle-only scope refusals. Use `research/sqj/strengthening/evidence/strengthening-linux-2.xml`. The earlier `inventory-linux-1.xml` contains three retained failures and is not the passing receipt. Missing-input, symlink and traversal-refusal cases are included within the 32; do not describe all 32 as cache hits.
- **Trace audit:** 67,074 source rows minus ten distinct pilot rows leaves 67,064; main selection 128, valid 122, excluded six without replacement; valid episodes span 104 repositories and 120 issues. There are 745 supported direct pytest calls and 120 repeat pairs, partitioned into 112 with reported editor mutation and eight with other unmeasured effects. All 120 have barriers; none is a demonstrated cache hit. Source: `research/sqj/strengthening/evidence/trace-summary-v1.json` and its frozen raw-response bindings. Selected rows, before exclusions, span 109 repositories and 126 issues; do not mix those denominators.

## Scientific positioning and software usefulness

The existing prose avoids unsupported algorithmic novelty and distinguishes integration from autonomous-agent evaluation. Its claim of conditional rather than universal correctness is appropriate. The no-transcript/no-artifact contract is supported by `zerorun/model.py` v2 checks; authenticated rechecks and the hit-only staging change are supported by `zerorun/hermetic.py`. The explicit undeclared-input and concurrent-host-mutation limitations should remain.

The main weakness is **demonstrated usefulness in an AI workflow**, not insufficient benchmark volume. The observation audit establishes that command-text repetition is inadequate; it does not show that an agent benefits from status-only reuse. Existing prose names the right primary users but could more concretely show their workflow: configure a bounded target, obtain operator review where required, inspect eligibility, receive a provenance-bearing hit, request fresh output when needed, and compare complete cost before enabling reuse. A short real CLI/MCP request-and-response example copied from the tested artifact would provide more value than another generic motivation paragraph. Do not invent an execution receipt or imply that an agent has adopted the interface.

A full comparison table is optional for this short software article. The current literature paragraph is accurate but broad. One additional sentence would make the technical distinction explicit: "Unlike feature/TTL-guided tool-result caching or trajectory-prefix reuse during RL post-training, ZeroRun revalidates a declared local test-task input identity and returns explicitly identified prior success without reconstructing arbitrary tool state or transcripts." This is a contract distinction, not superiority. ToolCaching and TVCache already establish tool-result caching, state awareness, and snapshot-cost optimization; eidos is adjacent function-integration software, not a tested cache baseline. See `RELATED_WORK_AUDIT.md` for primary-source links and inspection limits.

## Length and final checks

The current unfilled template has approximately **2,458 whitespace tokens / 2,399 conservatively extracted prose-and-metadata words**, comfortably below 4,000 before substitutions; these are diagnostic counts, not the final journal count. Recount after generated results, bibliography and examples are populated, using the journal's inclusion rules. One control-flow figure is present. Its content is scientifically appropriate; PDF rendering was not part of this textual audit.

Final review should confirm the old mean/new median distinction, all six complete blocks per short subject or explicit incomplete reporting, setup allocation, retained influential observations, exact artifact filenames, accessible public commit, compiling references, actual installation instructions, final author declarations, and no remaining placeholders. No numerical acceptance probability follows from these checks.

## Builder follow-up and final numerical checklist

The updated `build_paper.py` now generates the old-results paragraph from the strict analyzer, fixes the more-itertools comparator, identifies optimized-arm hits and the mean estimator, and uses the matching eidos bibliography key. State-case prose is admitted only if its reviewed text hash matches, independent state analysis reruns successfully and equals the saved JSON, the review pins that analysis file, and the case has exactly four requests and one hit. Nine offline builder-binding tests pass, including rejection of missing review, changed text, unverified review, stale analysis, stale review-to-analysis binding, incomplete analysis, wrong denominator and an unverified public release. No experiments or publication actions run in these tests.

The final export reconciliation must complete these checks before numbers enter the article:

| Evidence | Final invariant | Narrative interpretation |
|---|---|---|
| Original comparison | 70 requests; 40 optimized hits; 30 nonhits; 67.3–71.0% per-library mean eligible-hit reduction; direct/optimized 0.85–2.09 | Original five-subject data retained separately; more-itertools 16.6% slower than snapshot-first, not direct. |
| Short-subject replication | Four subjects, six blocks each, every three-arm permutation; 168 requests, 504 timed arms, 168 fresh captures, 96 optimized hits and 72 nonhits | These are planned invariants until raw exports pass the analyzer. Every block and failure must remain in the record. |
| Replicated cost table | Four rows from `paper_summary.subjects`: summed direct/optimized, setup-inclusive ratio, six-block minimum/maximum, and median eligible-hit saving versus snapshot-first | A ratio above one favors optimized reuse; hit saving is not complete-sequence or agent speedup. Do not substitute old mean-based numbers. |
| State-restoration case | Four actual statuses MISS_EXECUTED, MISS_EXECUTED, MISS_FAILED, HIT_REUSED; fresh counts 14, 15, 0, 14 and exits 0, 0, 5, 0; original/restored source, action key and per-node verdict agree | Pending actual raw validation. The extra 15-node request is counterfactual instrumentation, not an original agent test call. One purposive controlled mechanism example, not full agent replay or a TVCache comparison. |
| Public trace audit | 128 selected, 122 valid, six excluded; valid 104 repositories/120 issues; 745 direct pytest calls; 120 repeat pairs partitioned 112+8 | Descriptive observations; none of those pairs has demonstrated cache eligibility from command text alone. |
| Final document | No placeholders, matching bibliography, pinned accessible release, matching `paper-evidence.json`, fewer than 4,000 counted words | Artifact and evidence completion, not an acceptance probability. |

The final state paragraph is admitted after independent validation of the actual `agent-state-rejoin-v3` evidence: four requests, one reused success, and fresh node counts 14/15/0/14. `generated/state-rejoin-review.json` binds the text and exact analysis. The paragraph distinguishes the modern pinned experimental environment from the recorded Python 3.9 environment and explicitly states that transcript equivalence and autonomous-agent outcome preservation were not evaluated. The timing study and state case are not pooled.

## Application-evidence follow-up: 7 September 2026 UTC

The later evidence addresses the original request for a concrete model-backed use example without converting it into a population or productivity study. The [application-results ledger](APPLICATION_RESULTS.md) links the frozen protocols, raw records, API card, and exact source identities. `python -m research.softwarex.build_application_evidence --check` independently reconciles the following separate outcomes:

| Record | Supported result | Required qualification |
|---|---|---|
| V1 | One doctor turn reported missing authority; the original completion-message rule also failed. | No lifecycle or test execution; retain unchanged. |
| V2 | Doctor passed; the second call was refused by the client approval policy before a server result. | The model's conservative final refusal is not a passing execution or completed lifecycle. |
| V3 | Four core model turns and two separate fresh oracles passed; the fifth turn used an unsupported argument and stopped. | Core lifecycle passed, full prospective plan failed; zero completed successful decision cases. |
| API-guided demonstration | Two model-selected actions, two fresh oracles, and six no-tool interpretation cases passed; setup comprised two non-model tool calls plus discovery. | Fixed synthetic cases, not eight real tasks or a causal demonstration that documentation improved the model. |
| Literal public quickstart | Clean public clone, seven installation commands, and five actual STDIO stages passed without intervention; 36 runtime files matched. | Researcher-run reproduction, no model and no independent human participants; installation and server clocks exclude cloning, Docker setup, and preparation. |

Permitted manuscript strengthening is a concrete, documented application: the guided model distinguished acceptable prior success from a demand for fresh validation, selected the corresponding tool behavior, and interpreted the fixed result/error cases under the stated contract. The API card contains allowed inputs and semantics, not scenario-specific gold answers. Fresh failing-test evidence must remain distinguishable from protocol refusal. Prior adverse trials cannot disappear from the narrative or supplement.

Runtime source is unchanged. Invocation-local client preauthorization was separately approved only for the original synthetic fixture; no global policy or real-repository authority was relaxed. V1's source pin remains distinct from V2/V3/guided source `ebf2884df12573d63f45813200e0675288d12096` and the later literal-public-guide commit. Keep those identities and the model/non-model denominators separate in all exports.

Still unsupported: autonomous task completion benefit, workflow or token savings, general model reliability, external adoption, commercial demand, or numerical acceptance odds. The original fine-grained 5x/50% gates remain unmet and unchanged, and are not SoftwareX manuscript performance claims. This follow-up is an evidence/claim reconciliation, not a new page-by-page PDF review.
