# SoftwareX submission checks

Checked against the live official guide and current original-software template on 6 September 2026. This is a preparation checklist, not a submission receipt or an acceptance prediction. Unchecked items require verification against the final publication package; they are not waived by the time limit.

## Journal choice and permissions

- [ ] Jishan Kapoor confirms this journal choice after being informed that its current displayed Journal Impact Factor is **1.9**, below the earlier requested 2.4 threshold.
- [x] A clean research-code repository is explicitly authorized for public release and verified accessible without authentication. The initial research-only publication is commit `8f1cb4b078532c066c15ef4a388a7f97f491f639`; `generated/initial-publication.json` records anonymous access verification. The original development repository's visibility and history are unchanged. This initial code publication is not yet the final manuscript release.
- [ ] The author approves the final manuscript, factual declarations, and exact files to submit. Do not submit, accept publisher terms, or make payment on the author's behalf under this checklist.
- [ ] Submission occurs at only one journal at a time. Another journal can be considered after rejection or confirmed withdrawal, with its format and disclosures updated.

The journal charges an article publishing charge if the manuscript is accepted; the author has permitted consideration of post-acceptance charges, not an advance payment or automatic financial transaction. Verify the offered amount and terms with the author after a decision. [Official guide](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors)

## Required package

- [x] The manuscript is prepared as **Original Software Publication**, not Software Update, and preserves the five template sections: Motivation and significance; Software description; Illustrative examples; Impact; Conclusions. The author must select this category in the submission system.
- [x] Official editable LaTeX template/class used; the reviewed PDF and all six flat compilation inputs are complete. The artifact builder requires their exact QA hashes before creating the source archive; PDF alone is insufficient.
- [x] Final conservative entire-PDF count is 3,413 words, including even metadata and references; the permitted manuscript count is therefore below 4,000. One editable figure. The reviewed preprint has 12 pages; the template's main-text page preference is distinguished from its controlling word limit.
- [x] Abstract has 113 words and five keywords; the maximum is 250 words and 1–7 keywords.
- [x] `HIGHLIGHTS.txt` is a separate editable file with four highlights, each below 85 characters including spaces; final readiness checks the actual text. A graphical abstract is not required for this package.
- [x] Figure and tables are editable LaTeX, with captions and a data-availability statement. All ten cited keys resolve to the compiled bibliography; primary references were checked. All twelve PDF pages were visually reviewed (`generated/pdf-review.json`).

Sources: [current guide](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors), [official LaTeX template](https://legacyfileshare.elsevier.com/promis_misc/softwarex-osp-template.tex), [official Word template](https://legacyfileshare.elsevier.com/promis_misc/softwarex-osp-template.docx).

## Code metadata and release

Keep the template's C1–C8 labels and fill these values from the released artifact, not from a proposed URL:

| Field | Required final value/check |
| --- | --- |
| C1 version | Exact code release/version and commit; package currently identifies itself as 0.5.1. |
| C2 GitHub pointer | Public immutable commit or release link for the submitted code, with repository access verified. |
| C3 license | MIT for ZeroRun-owned code; identify separately licensed evidence, templates, and dependencies. |
| C4 version control | Git. |
| C5 implementation | Python, CLI and MCP interfaces; name external runtime tools used by the example. |
| C6 environment | Supported Python version, OS/runtime restrictions, Docker requirements, and exact benchmark environment. |
| C7 documentation | Verified public installation, API, operator-review, limitations, and reproduction documentation links. |
| C8 support | kapoorjishan2@gmail.com. |

- [x] Public repository has `README.md`, `LICENSE.txt`, `src/`, and an identical `Licence.txt` alias; exact files are inventoried by the public-release manifest.
- [x] Clean Windows wheel installation and CLI inspection passed; all 36 installed runtime files match the frozen source, with no third-party runtime Python dependencies (`generated/public-install-smoke.json`). Selected public-layout tests separately passed 465 primary cases, with 21 platform skips and eight passing subtests (`generated/public-release-tests.json`). These checks do not claim a fresh Linux/Docker re-execution from the final manuscript snapshot or an autonomous Codex task.
- [x] Recorded-data validation and documented laboratory examples do not require private GitHub credentials or distributed private keys. All four offline evidence validators passed from the clean public stage. Normal production integration still requires explicit external operator review.
- [x] Third-party notices, CC-BY-4.0 trajectory attribution, observed dataset revision, raw hashes, and excluded/failed rows are retained; third-party data is not relicensed as MIT.
- [x] The strengthened public source/evidence snapshot is commit `08b3b9649af46e720dbff53060276381ae0d9f1d`, anonymously verified by exact manifest and extension-receipt hashes. Both new analysis checks pass from its clean public layout. The later final-readiness receipt identifies the separate completed submission archives and review PDF.

## Evidence and contribution checks

- [x] Trace-summary validator reruns the frozen analyzer and reconciles main and pilot separately. Main: 128 selected, 122 analyzable episodes; 109 repositories and 126 issues among selected rows. Six excluded episodes remain explicit.
- [x] Main exact-command repetitions are not described as safe hits: all 120 pairs have intervening barriers (112 include reported editor mutation; eight have other unmeasured effects). Pilot results are not pooled into the main sample.
- [x] Controlled timing is limited to its tested contract, with all 24 completed planned blocks, unfavorable outcomes, separately retained interrupted costs, explicit post-crash recovery, and missing setup measurements. No end-to-end AI latency, token, task-success, or demand claims are inferred.
- [x] Explain the usable software contribution: explicit eligibility review, configured deterministic execution, conservative reuse decisions, and auditable evidence. The manuscript distinguishes these from existing caching algorithms and general-purpose function-call middleware.
- [x] Reproducible examples cover fresh execution, unchanged reuse, input invalidation, repeated failure and restoration, with separate refusal-boundary tests. The independently reconciled real-agent-derived controlled case (`agent-state-rejoin-v3`) records four actual requests and one hit, with 14/15/0/14 fresh-node outcomes and explicit counterfactual/runtime limits. A failed collection exits 5 and is not mislabeled as a safety refusal. The original seven-state example and separate inventory/refusal evidence remain distinct.
- [x] State that whole-task cache hits do not reproduce historical stdout/stderr. An AI client requiring a fresh transcript needs execution; cached success alone does not establish equivalent agent behavior.
- [x] State that there are no measured external users or established commercial deployments. Research and commercial uses are described as potential uses, not adoption evidence.

The journal's reviewer form weighs potential research impact, working installation, API/user documentation, automated tests, and reproducibility. It does not prescribe a new caching algorithm, but a usable artifact and credible benefit remain essential. [SoftwareX reviewer form](https://legacyfileshare.elsevier.com/promis_misc/softwarex-reviewer-form.pdf)

## Author-controlled declarations

The final client extension separately records 14 scripted response cases, 11
installed-server checks across eight stdio requests, one failed Codex doctor
turn, and a successful five-call non-model configuration diagnostic. These are
not pooled into an agent success rate. The model trial is not rerun or called a
corrected Codex success. The operator-managed trust-path route and its conflict
with the frozen managed initializer are documented. The exact runtime remains
unchanged.

The final direct publication suite passed 372 cases with two Windows symlink
skips, binding 50 public source/test files before and after execution. Its
`publication-extension-final-v2` receipt follows the separately retained
newline-only analysis-helper packaging correction. This is additional research
tooling validation, not a rerun of the complete runtime suite or proof of a
journal acceptance probability.

- [x] Author-provided name, contact and affiliation are included: Jishan Kapoor; kapoorjishan2@gmail.com; Independent researcher, Toronto, Canada. Any additional postal-address details required by the submission system must be supplied privately, not invented.
- [x] The manuscript's funding statement reflects the author-confirmed absence of external funding.
- [ ] Complete Elsevier's competing-interest declaration. The author's ownership/development and intended commercialization of ZeroRun require explicit review; do not automatically declare no interests.
- [ ] CRediT roles describe the author's actual contributions, not every role available in the taxonomy.
- [x] A complete generative-AI disclosure is included before the references: Codex assistance included code, research support, analysis tooling, and manuscript preparation, not merely spelling correction. AI is not listed as an author. This inclusion does not substitute for the human author's still-required final review and approval.
- [ ] Confirm originality, no simultaneous submission, permission to distribute the included material, and final author approval. These confirmations have not been supplied merely by generating this checklist or cover letter.

## Fit assessment and remaining risks

The software-publication category is a closer match than presenting the current work as a new general caching algorithm. A recent SoftwareX article, **eidos**, presents middleware for LLM function execution and validation; it establishes a relevant category precedent but must also be treated as related work, not evidence of ZeroRun's originality. [Published article](https://doi.org/10.1016/j.softx.2025.102290)

**Kneeliverse** combines established algorithms with extensions, installation/API documentation, illustrative code, and measured applications. Its presentation supports reuse-oriented positioning, but its measured benefits cannot be borrowed as evidence for this tool. [Author-hosted published paper](https://www.fsl.cs.stonybrook.edu/docs/mtcache/antunes2025softwarex.pdf)

The material scientific risks are differentiation from existing test/build caches and the limited evidence of actual AI-workflow benefit. The clean-install receipt addresses one artifact risk but does not replace final archive, public-pointer, source-compilation, or reproduction checks. The software-publication category reduces the need to claim a new algorithm; it does not eliminate these risks or establish a numerical acceptance probability.

## Completion rule

Check remaining artifact and timing boxes only after final raw-evidence reconciliation, final generated manuscript, complete PDF visual review, and archive verification. Retain any VM interruption and recovery record; a resumed experiment must not be described as an uninterrupted run. Author approval, competing-interest form completion, originality, and single-journal submission remain author-controlled actions even after the technical package is complete. No journal submission or payment has been made by preparing or publishing these files.
