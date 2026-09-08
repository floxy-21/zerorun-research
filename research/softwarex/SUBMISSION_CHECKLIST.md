# SoftwareX submission checks

Preparation checklist updated 8 September 2026 (UTC). This is an internal AI-assisted audit; it is not an independent human peer review. The current official original-software template, reviewer form and publisher policies were checked. After an initial web-tool retrieval failure, the coordinating agent read the complete official author guide in the in-app browser; `JOURNAL_REQUIREMENTS_REVIEW.md` records that verification. This is not a submission receipt or an acceptance prediction. Final checks must use the exact submitted release, not an earlier successful build. Unchecked author-controlled items are not waived by the time limit.

## Journal choice and permissions

- [x] SoftwareX is the authorized target, selected for software-publication fit. Its displayed impact factor of **1.9** was disclosed during preparation, below the earlier requested floor; it is not presented as satisfying that earlier floor.
- [x] A clean research-code repository is explicitly authorized for public release and verified accessible without authentication. The initial research-only publication is commit `8f1cb4b078532c066c15ef4a388a7f97f491f639`; `generated/initial-publication.json` records anonymous access verification. The original development repository's visibility and history are unchanged. This initial code publication is not yet the final manuscript release.
- [x] The author has authorized preparation and public release verification. Journal submission is held pending the user's explicit approval of the final package.
- [ ] Reconcile the exact final 0.5.3 manuscript, declarations, upload files and portal-generated review PDF, then obtain the user's explicit final approval before submitting.
- [ ] Submission occurs at only one journal at a time. Another journal can be considered after rejection or confirmed withdrawal, with its format and disclosures updated.

The author has authorized the stated USD 1,920 article publishing charge plus applicable tax, contingent on acceptance. No payment has been made. Verify that any offered post-acceptance amount and terms match that authorization; this checklist does not execute a financial transaction. [Official guide](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors)

## Required package

- [x] The manuscript is prepared as **Original Software Publication**, not Software Update, and preserves the five template sections: Motivation and significance; Software description; Illustrative examples; Impact; Conclusions. The author must select this category in the submission system.
- [x] The manuscript uses the official editable LaTeX template/class.
- [ ] The final 0.5.3 artifact builder and offline verifier pass for the exact source and reviewer archives. Require matching compilation-input QA hashes and `generated/final-readiness.json`; an earlier PDF or receipt is insufficient.
- [x] The controlling word limit is 4,000, with at most six figures. The verified guide excludes title, authors, affiliations, references and metadata tables, but includes abstract, running text, captions and footnotes. `generated/pdf-review.json` supplies the final counts and scope; do not copy historical counts. The template prioritizes its word limit over its separate main-text page target.
- [x] The guide limits the abstract to 250 words and keywords to one through seven; the software template recommends an abstract of approximately 100 words. Check the final text against both, without confusing the template's drafting target with the guide's upper bound.
- [x] `HIGHLIGHTS.docx` supplies separate Word highlights matching `HIGHLIGHTS.txt`. Structural and rendered-layout checks passed in `generated/highlights-docx-review.json`; recheck if edited. No graphical abstract is included in this package.
- [x] Figure and tables are editable LaTeX, with captions and a data-availability statement.
- [ ] The final 0.5.3 PDF-review receipt records citation resolution, compilation-source hashes, scoped word/figure counts and all-page visual inspection for the exact submitted PDF. Historical QA does not certify later edits.

Sources: [current guide](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors), [official LaTeX template](https://legacyfileshare.elsevier.com/promis_misc/softwarex-osp-template.tex), [official Word template](https://legacyfileshare.elsevier.com/promis_misc/softwarex-osp-template.docx).

Publisher-wide [highlights guidance](https://www.elsevier.com/en-gb/researcher/author/tools-and-resources/highlights) specifies three to five bullets, each at most 85 characters including spaces, in a Word document. `UPLOAD_GUIDE.md` identifies canonical upload files; older cover/highlight `.md` copies are not the submission source.

The verified journal guide encourages separate editable highlights and a graphical abstract; the latter is not mandatory. Editable text graphics may be embedded in LaTeX. Upload the manuscript PDF under **Manuscript** and the complete LaTeX archive under **LaTeX source files**, retaining the prescribed template format.

## Code metadata and release

Keep the template's C1–C8 labels and fill these values from the released artifact, not from a proposed URL:

| Field | Required final value/check |
| --- | --- |
| C1 version | Submitted distribution 0.5.3, reviewed core `e5194d340a09bccb667d3021a6a4a9a9a054123b`, and the exact completed release commit. Historical 0.5.1 study runtime and 0.5.2 receipts remain separately identified. |
| C2 GitHub pointer | Public immutable commit or release link for the submitted code, with repository access verified. |
| C3 license | MIT for ZeroRun-owned code; identify separately licensed evidence, templates, and dependencies. |
| C4 version control | Git. |
| C5 implementation | Python, CLI and MCP interfaces; name external runtime tools used by the example. |
| C6 environment | Supported Python version, OS/runtime restrictions, Docker requirements, and exact benchmark environment. |
| C7 documentation | Verified public installation, API, operator-review, limitations, and reproduction documentation links. |
| C8 support | kapoorjishan2@gmail.com. |

Distinguish the two Python requirements in C6 and reviewer instructions:
ZeroRun and its account-free quickstart support **Python 3.10+**; canonical
timing-analysis reproduction and final manuscript generation require
**CPython 3.12–3.14**. Follow `REPRODUCIBILITY.md` for the checked execution route.
`analysis_reproduction.py` requires exact equality against the source-bound
archived analysis, allowing only order variation in the top-level
`unparseable_zero_byte_receipts` inventory. Duplicate, missing or modified rows,
changed numeric values and changes elsewhere remain failures. It introduces no
numeric tolerance and changes neither raw evidence, frozen analyzers nor gates.

- [x] Public repository has `README.md`, `LICENSE.txt`, `src/`, and an identical `Licence.txt` alias; exact files are inventoried by the public-release manifest.
- [x] Historical Windows wheel installation, CLI and public-layout tests passed under their own source bindings and platform skips. Read actual counts from `generated/public-install-smoke.json` and `generated/public-release-tests.json`; do not combine overlapping suites into one test total.
- [x] The Linux laboratory fresh installation and actual STDIO checks passed under `evidence/quickstart-lab-v1/`. A separate literal public-guide replay also passed under `evidence/quickstart-public-v1/`, pinned to public commit `860675c041c5190dcbae0892d64c8ba82b257bb8`. Non-model installation/server checks are not autonomous Codex tasks or independent-human validation. Their timing excludes clone, image acquisition and researcher preparation.
- [x] Current 0.5.3 Windows wheel installation/regression evidence is retained at `evidence/current-runtime-0.5.3-v1/`; actual anonymous-source Linux installation and five STDIO stages are at `evidence/quickstart-public-053-v1/`, bound to public commit `0e4c1cee2d3b59f6e61a7f8feacb8a36e334f277`. These are internal checks, not external developer adoption or installation of a later release commit. Read the exact wheel, runtime, helper, guide and command bindings from those receipts.
- [x] Recorded-data validation and laboratory examples do not require private GitHub credentials or distributed private keys. Final automated-check receipts identify the exact executed suite, source hashes and skips. Normal production integration still requires explicit external operator review.
- [x] Third-party notices, CC-BY-4.0 trajectory attribution, observed dataset revision, raw hashes, and excluded/failed rows are retained; third-party data is not relicensed as MIT.
- [x] The guide's research-data Option C requires deposited, cited and linked research data. The final public raw evidence, immutable source pointer, explicit data reference and data-availability statement must agree; do not substitute a proposed deposit or an inaccessible link.
- [x] Earlier public source/evidence snapshots remain recorded.
- [ ] The final `generated/final-readiness.json` identifies the completed 0.5.3 release, archive and publication bindings; C2/C7 match actual anonymously accessible files in that snapshot. A planned release URL or historical installation commit cannot substitute for this check.

## Evidence and contribution checks

- [x] Trace-summary validator reruns the frozen analyzer and reconciles main and pilot separately. Main: 128 selected, 122 analyzable episodes; 109 repositories and 126 issues among selected rows. Six excluded episodes remain explicit.
- [x] Main exact-command repetitions are not described as safe hits: all 120 pairs have intervening barriers (112 include reported editor mutation; eight have other unmeasured effects). Pilot results are not pooled into the main sample.
- [x] The earlier balanced four-library replication completed all 24 planned blocks under its tested contract, retaining unfavorable outcomes, interrupted costs, explicit post-crash recovery, and missing setup measurements. This is separate from the image-handoff cohorts and their retained partial attempts. Read the final complete-case ledger, compatibility corrections and denominators in `FRESH_REAL_WORKLOAD_REPRODUCTION.md` and the reconciled handoff evidence; the early 2-of-24 result is historical. No end-to-end AI latency, token, task-success, or demand claims are inferred.
- [x] Explain the usable software contribution: explicit eligibility review, configured deterministic execution, conservative reuse decisions, and auditable evidence. The manuscript distinguishes these from existing caching algorithms and general-purpose function-call middleware.
- [x] Reproducible examples cover fresh execution, unchanged reuse, input invalidation, repeated failure and restoration, with separate refusal-boundary tests. The independently reconciled real-agent-derived controlled case (`agent-state-rejoin-v3`) records four actual requests and one hit, with 14/15/0/14 fresh-node outcomes and explicit counterfactual/runtime limits. A failed collection exits 5 and is not mislabeled as a safety refusal. The original seven-state example and separate inventory/refusal evidence remain distinct.
- [x] State that whole-task cache hits do not reproduce historical stdout/stderr. An AI client requiring a fresh transcript needs execution; cached success alone does not establish equivalent agent behavior.
- [x] State that there are no measured external users or established commercial deployments. Research and commercial uses are described as potential uses, not adoption evidence.

The journal's reviewer form weighs potential research impact, working installation, API/user documentation, automated tests, and reproducibility. It does not prescribe a new caching algorithm, but a usable artifact and credible benefit remain essential. [SoftwareX reviewer form](https://legacyfileshare.elsevier.com/promis_misc/softwarex-reviewer-form.pdf)

## Model-backed application evidence

Scripted consumer cases, installed-server checks, non-model diagnostics and
model-backed trials remain separate evidence classes; they are not pooled into
an agent success rate. Historical 0.5.1 trials retain their operator-managed
trust-path conflict; 0.5.2 changed version metadata and MCP descriptions. The
current 0.5.3 integration defaults to whole-task validation and accepts a narrowly
matched, operator-configured external trust path. Its separate runtime and public
quickstart receipts do not relabel earlier trials. New real-agent producer, fresh
oracle and consumer evidence must retain their own denominators and failed
attempts; only sealed, reconciled outcomes belong in the final article.

- V1 stopped at an unauthorized doctor result, with an additional exact-message
  protocol issue. A later non-model trust-path diagnostic did not relabel it.
- V2 established authorized readiness but execution was denied by the client's
  approval layer. It remains unsuccessful.
- V3 used explicitly approved invocation-local preauthorization for the same
  isolated fixture. Its four-call readiness/execution/reuse/verification core
  and two fresh oracles passed. The fifth model turn invented
  `reuse_prior_success`, which the API rejected; the model declined success and
  freshness. The trial stopped and remains overall unsuccessful. The second
  decision and six interpretation cases were not run.
- The separately frozen API-guided demonstration passed two actual model-chosen
  validation decisions, two separate fresh oracles and six no-tool
  interpretations. Actual discovery, doctor and seed were non-model setup.
  `evidence/guided-client-v1/receipt.json` retains raw streams, the input
  reference, prompts, prior-trial bindings and recomputable judgments. The
  positive result is not substituted for V3, and does not establish a causal
  documentation effect or general autonomous coding effectiveness.

The final publication suite and source-integrity checks are indexed by
`generated/final-readiness.json`; use its exact source-bound receipt after the
analysis-reproduction repair. Earlier publication-suite receipts remain
historical. Retain actual passed, skipped and total counts rather than copying
numbers from older suites. These
checks do not constitute independent external reproduction or an acceptance
probability.

## Author-controlled declarations

- [x] Author-provided name, contact and affiliation are included: Jishan Kapoor; kapoorjishan2@gmail.com; Independent researcher, Toronto, Canada.
- [x] The author has supplied the full postal address privately. Keep the street address and postal code out of public repository documents.
- [ ] Verify the supplied address in the journal portal.
- [x] The manuscript's funding statement reflects the author-confirmed absence of external funding.
- [x] The publisher-generated competing-interest Word document is already present in the portal and its approved possible-commercialization declaration has been verified: 13,749 bytes, SHA-256 `3a483b337f1b62df6d96b183f1f145b17cea00c1fd6851d0d8def9768da6efba`. Preserve this existing document; do not replace it with an invented no-interests statement. The final portal-generated review PDF remains a separate pending check.
- [x] The author has supplied actual CRediT contributions privately.
- [ ] Verify that the portal and final contribution statement reflect those supplied roles, rather than every available taxonomy role.
- [ ] Review and approve the substantial generative-AI disclosure: Codex assisted code, research support, analysis and manuscript preparation, not merely spelling. Research assistance belongs in the methods description; the AI-assisted LaTeX diagram also needs caption attribution with accurate recorded tool/version details. AI is not an author, and automated checks do not substitute for the human author's actual intellectual contribution and final approval. [Current Elsevier AI policy](https://www.elsevier.com/about/policies-and-standards/generative-ai-policies-for-journals)
- [ ] Confirm originality, no simultaneous submission, permission to distribute the included material, and final author approval. Use the author's actual confirmations in the conversation and portal; generating this checklist or cover letter is not itself a confirmation.

## Fit assessment and remaining risks

The software-publication category is a closer match than presenting the current work as a new general caching algorithm. A recent SoftwareX article, **eidos**, presents middleware for LLM function execution and validation; it establishes a relevant category precedent but must also be treated as related work, not evidence of ZeroRun's originality. [Published article](https://doi.org/10.1016/j.softx.2025.102290)

**Kneeliverse** combines established algorithms with extensions, installation/API documentation, illustrative code, and measured applications. Its presentation supports reuse-oriented positioning, but its measured benefits cannot be borrowed as evidence for this tool. [Author-hosted published paper](https://www.fsl.cs.stonybrook.edu/docs/mtcache/antunes2025softwarex.pdf)

The material scientific risks are differentiation from existing test/build caches and the limited evidence of actual AI-workflow benefit. The clean-install receipt addresses one artifact risk but does not replace final archive, public-pointer, source-compilation, or reproduction checks. The software-publication category reduces the need to claim a new algorithm; it does not eliminate these risks or establish a numerical acceptance probability.

## Completion rule

Check remaining artifact and timing boxes only after final raw-evidence reconciliation, final generated manuscript, complete PDF visual review, and archive verification. Retain any VM interruption and recovery record; a resumed experiment must not be described as an uninterrupted run. Author approval, competing-interest form completion, originality, and single-journal submission remain author-controlled actions even after the technical package is complete. No journal submission or payment has been made by preparing or publishing these files.
