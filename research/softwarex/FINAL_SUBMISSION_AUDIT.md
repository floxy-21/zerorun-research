# ZeroRun SoftwareX final submission audit

Prepared 7 September 2026. This is an internal, AI-assisted pre-submission audit. It is not external human peer review or an editorial decision.

The article presents **auditable validation handoffs for AI coding tools**: an operator-qualified previous test-status result, explicit fresh-evidence semantics, and measured execution boundaries. This is the appropriate contribution to assess. The paper does not claim a new general caching algorithm or universal agent acceleration.

The audit follows SoftwareX's published questions about contribution and impact, support from empirical results, manuscript clarity, software quality, installation, documentation, licensing and reproduction. [Official SoftwareX reviewer form](https://legacyfileshare.elsevier.com/promis_misc/softwarex-reviewer-form.pdf)

## Evidence that supports submission

| Question | Final evidence and practical meaning |
| --- | --- |
| Does reuse agree with fresh execution? | The historical four-target replication reconciles 168 fresh comparisons and 96 reused successes. The separately reported repository and agent handoffs reconcile their observed reused successes with fresh oracles. No unresolved incorrect reuse is reported in these reconciled records. This does not prove arbitrary Python tasks deterministic. |
| Is there a real coding-agent observation? | One actual model invocation produced one nonempty SQLGlot patch; separate fresh evaluation verified the previously failing regression. Two cases were selected; the other stopped before model invocation. The downstream handoff was scripted and is not an autonomous consumer success-rate experiment. |
| Is usefulness measured on real code? | The expanded fixed-ledger campaign completed 15 of 24 reference-patch cases across seven repositories, with nine incomplete/unsupported cases and no unrun cases. The host interruption qualifies its timings. A separate public-source image rebuild and fixed two-case pilot also passed strict record reconciliation. Consumer latency and complete producer/consumer cost remain separate, and both overhead and savings are retained. |
| Does the current package install and run? | Version 0.5.2 was installed from a new anonymous public checkout and external environment. All 36 runtime files matched. Actual STDIO requests passed refusal, authorized readiness, fresh execution, reuse and fresh verification. Independent receipt checks passed and the checkout remained clean. |
| Are the submission tools tested? | The revised publication suite passed 1,156 primary tests and 17 subtests, with two skips and no failures/errors; 109 source files were unchanged. The separately recorded current-package checks passed 269 primary tests and eight subtests, with two skips. Earlier failed attempts remain retained. |
| Is the article ready for author review? | The final revised PDF has 16 pages, a 113-word abstract and a conservative journal-scope count of 3,979 words. Rendered pages were visually inspected for clipping, overlap, legibility and broken references; no material layout defect was found. The PDF review receipt records the page review and compilation-source bindings. Editable source and complete reviewer software/evidence are separately inventoried. |

Authoritative records are [handoff evidence](generated/handoff-evidence-v1.json), [current package validation](evidence/current-runtime-0.5.2-v5/receipt.json), [public installation and server receipts](evidence/quickstart-public-052-v1/), [publication tests](evidence/publication-application-revision-20260907-v3/receipt.json), and [PDF review](generated/pdf-review.json). The complete package's exact hashes and final checks are in [final readiness](generated/final-readiness.json).

## Performance findings retained in the paper

| Separate cohort | Completed/selected cases | Complete producer/consumer cost relative to fresh execution |
| --- | --- | --- |
| Original copy pilot | 2/2 | 42.3% slower |
| Recovered image pilot, qualified by interruption | 2/2 | 24.9% slower |
| Separate amended image repeat | 2/2 | 10.7% slower |
| Budget-limited image main | 2/24 | 23.2% faster across the two complete pycparser cases |
| Expanded-budget image main, interrupted campaign | 15/24 across seven repositories; nine incomplete/unsupported, zero unrun | 2.0% lower descriptive total; not a clean performance replication |
| Fresh public-source image rebuild and separate pilot | 2/2 | 10.5% slower; consumer latency 75.8% lower |
| Verified agent patch, scripted downstream evaluation | 1 completed controlled case among 2 selected | 27.1% slower |

The earlier separate image repeat reduced mean consumer latency by about 71%, while the whole sequence remained slower. The expanded main repeat reduced complete-pair consumer totals by 78.5%, with its host interruption explicitly retained. Its nine incomplete cases comprise two repaired-state test failures, four exit-code-2 runs without usable node outcomes, one uncertain container-cleanup failure and two empty oracle captures; the records do not establish every underlying cause. [Complete ordered coverage audit](APPLICATION_COVERAGE_AUDIT.md)

These observations establish a conditional handoff benefit, not a general end-to-end speedup. The cohorts are not pooled. Cross-run changes in absolute fresh costs and shared-host conditions prevent attributing the entire difference from the original pilot causally to image preparation. Image construction, per-arm preparation, acquisition and independent-oracle work have separate accounting and explicit exclusions.

The fresh image build took 26.396 seconds, separately from pilot chain costs. Strict wrapper verification confirms fresh anonymous public source checkouts, the exact historical engine, separate old/new image bindings and the completed two-case fresh-image pilot. This is author-side recipe reproduction on the existing VM, with a pre-existing pinned base image. Docker reused cached layers and produced the same content digest; an uncached rebuild is not established. Independent human replication, public pulling of the historical derived image and a general bit-identical rebuild guarantee are not established. [Fresh workload reproduction guide](FRESH_REAL_WORKLOAD_REPRODUCTION.md)

## CI and source integrity

GitHub-hosted jobs were blocked before execution by account billing/spending restrictions. They are not presented as passing CI. The retained [administrative observation](evidence/hosted-ci-20260907/receipt.json) is separate from actual VM test evidence.

The broader VM regression attempt timed out and exposed stale 0.5.1 release-test fixtures. Exactly 17 fixture-version literals in one test module were corrected for 0.5.2, without changing assertions or runtime logic. All 62 tests in that module then passed. The exact correction is product commit `a4ac905985a38308000a586140aeb4b259a7960e`; the original failure and amendment remain inspectable. Independent reconciliation of the disjoint confirmatory and other-module VM runs verified **1,662 primary passes, 12 skips and 19 passing subtests**, with no failures/errors. All 1,674 distinct collected nodes across all 91 modules were covered, with no overlap or exclusions and unchanged source in both runs. This is complementary sharded regression on Python 3.10.12, not a monolithic run or a five-version hosted CI matrix. [Independent final shard audit](evidence/full-product-fixture-amendment-20260907/final-shard-audit.json)

The current executable source remains pinned to `d02b12e43ece3526566ec69c7516579f8d2fc9dd`. The historical 0.5.1 execution evidence is not relabeled as a new 0.5.2 performance replication. The manuscript's code/evidence snapshot and the later completed submission snapshot are intentionally distinct.

## Remaining review risks and author steps

The principal scientific risks are modest differentiation from established caches, a very small genuine-agent sample, nine incomplete/unsupported cases under the declared environment, unknown natural reuse frequency, and mixed total costs. The expanded campaign reached every selected case but its host interruption limits timing interpretation. More unit-test passes cannot remove those limitations. No external adoption or independent developer study is claimed.

The author must review and approve the article and AI-assistance disclosure, confirm originality and single-journal submission, provide the full postal address and actual CRediT roles, complete the publisher's competing-interest declaration, and inspect the portal-generated PDF. [Upload guide](UPLOAD_GUIDE.md)

The full reviewer ZIP is distributed as a [versioned GitHub Release asset](https://github.com/floxy-21/zerorun-research/releases/download/softwarex-0.5.2-20260907-r2/ZeroRun_SoftwareX_reviewer.zip) with exact size and SHA-256 verification. This preserves all evidence while respecting GitHub's repository-file limit. [GitHub's large-file guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github) The [offline verification guide](VERIFY_SUBMISSION.md) explains the one-time download and subsequent checks.

No journal submission or payment has been performed by preparing this package.
