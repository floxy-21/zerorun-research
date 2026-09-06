# SoftwareX submission checks

Checked against the live official guide and current original-software template on 6 September 2026. This is a preparation checklist, not a submission receipt or an acceptance prediction. Unchecked items require verification against the final publication package; they are not waived by the time limit.

## Journal choice and permissions

- [ ] Jishan Kapoor confirms this journal choice after being informed that its current displayed Journal Impact Factor is **1.9**, below the earlier requested 2.4 threshold.
- [ ] A clean research-code repository is explicitly authorized for public release and verified accessible without authentication. Do not change the visibility of the original development repository or publish its history by implication.
- [ ] The author approves the final manuscript, factual declarations, and exact files to submit. Do not submit, accept publisher terms, or make payment on the author's behalf under this checklist.
- [ ] Submission occurs at only one journal at a time. Another journal can be considered after rejection or confirmed withdrawal, with its format and disclosures updated.

The journal charges an article publishing charge if the manuscript is accepted; the author has permitted consideration of post-acceptance charges, not an advance payment or automatic financial transaction. Verify the offered amount and terms with the author after a decision. [Official guide](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors)

## Required package

- [ ] Select **Original Software Publication**, not Software Update. Preserve the five template sections: Motivation and significance; Software description; Illustrative examples; Impact; Conclusions.
- [ ] Use the official editable template. For LaTeX, include the review PDF and a complete compiling source archive with bibliography, figures, and required style files; PDF alone is insufficient.
- [ ] Count no more than 4,000 words, including abstract, prose, captions, and footnotes, excluding title, author information, references, and metadata tables. Use no more than six figures. The template prefers six main-text pages while prioritizing word count.
- [ ] Keep the abstract at most 250 words; approximately 100 words is consistent with the template. Include 1–7 keywords.
- [ ] If supplied, make highlights a separate editable file with 3–5 bullets, each at most 85 characters including spaces. A graphical abstract is optional.
- [ ] Include all source figures, editable tables, captions, cited supplements, and a data-availability statement. Check reference-to-citation correspondence and primary-source accuracy.

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

- [ ] Public repository has the required `README.md`, `LICENSE.txt`, and `src/` source layout. The template spells the license file `Licence.txt` while the guide specifies `LICENSE.txt`; preserve the guide's exact filename and provide an alias if needed, without inconsistent license texts.
- [ ] Install and run the submitted snapshot from a clean environment using only its instructions. A successful run against the development checkout is not a release-install test.
- [ ] Do not require private credentials, a private image, external cache authority created by the agent, or unavailable local paths for the reviewer example. Explicit human operator review remains required where the runtime contract requires it.
- [ ] Retain third-party notices. Public OpenHands trajectory data remains CC-BY-4.0, with attribution, observed dataset revision, raw hashes, and excluded/failed rows preserved; it is not relicensed as MIT.
- [ ] Cite and link a deposited evidence artifact and its version. Verify public artifact bytes against the local manifest before stating availability.

## Evidence and contribution checks

- [x] Trace-summary validator reruns the frozen analyzer and reconciles main and pilot separately. Main: 128 selected, 122 analyzable episodes; 109 repositories and 126 issues among selected rows. Six excluded episodes remain explicit.
- [x] Main exact-command repetitions are not described as safe hits: all 120 pairs have intervening barriers (112 include reported editor mutation; eight have other unmeasured effects). Pilot results are not pooled into the main sample.
- [ ] Report controlled execution timing only for its tested contract and environment, including unfavorable results and all preselected orders. Do not infer end-to-end AI latency, token savings, task success, or commercial demand from these timings.
- [ ] Explain the usable software contribution: explicit eligibility review, configured deterministic execution, conservative reuse decisions, and auditable evidence. Distinguish these from existing caching algorithms and general-purpose function-call middleware.
- [ ] Demonstrate at least one reproducible example covering fresh execution, unchanged reuse, invalidation, and refusal/bypass, with expected outputs and limitations.
- [ ] State that whole-task cache hits do not reproduce historical stdout/stderr. An AI client requiring a fresh transcript needs execution; cached success alone does not establish equivalent agent behavior.
- [ ] State that there are no measured external users or established commercial deployments. Describe research and commercial uses as potential uses, not adoption evidence.

The journal's reviewer form weighs potential research impact, working installation, API/user documentation, automated tests, and reproducibility. It does not prescribe a new caching algorithm, but a usable artifact and credible benefit remain essential. [SoftwareX reviewer form](https://legacyfileshare.elsevier.com/promis_misc/softwarex-reviewer-form.pdf)

## Author-controlled declarations

- [ ] Author name: Jishan Kapoor. Corresponding author: kapoorjishan2@gmail.com. Affiliation: Independent researcher, Toronto, Canada. Supply any additional postal-address details required by the submission system privately; do not invent them.
- [ ] Funding statement reflects the confirmed absence of external funding.
- [ ] Complete Elsevier's competing-interest declaration. The author's ownership/development and intended commercialization of ZeroRun require explicit review; do not automatically declare no interests.
- [ ] CRediT roles describe the author's actual contributions, not every role available in the taxonomy.
- [ ] Include a complete generative-AI disclosure before the references: Codex assistance included code, research support, analysis tooling, and manuscript preparation, not merely spelling correction. Human verification and final responsibility must be exercised before the author asserts them. AI is not listed as an author.
- [ ] Confirm originality, no simultaneous submission, permission to distribute the included material, and final author approval. These confirmations have not been supplied merely by generating this checklist or cover letter.

## Fit assessment and remaining risks

The software-publication category is a closer match than presenting the current work as a new general caching algorithm. A recent SoftwareX article, **eidos**, presents middleware for LLM function execution and validation; it establishes a relevant category precedent but must also be treated as related work, not evidence of ZeroRun's originality. [Published article](https://doi.org/10.1016/j.softx.2025.102290)

**Kneeliverse** combines established algorithms with extensions, installation/API documentation, illustrative code, and measured applications. Its presentation supports reuse-oriented positioning, but its measured benefits cannot be borrowed as evidence for this tool. [Author-hosted published paper](https://www.fsl.cs.stonybrook.edu/docs/mtcache/antunes2025softwarex.pdf)

The three material remaining rejection risks are insufficient differentiation from existing test/build caches, limited evidence of actual AI-workflow benefit, and a release that reviewers cannot independently install or reproduce. The proposed category reduces the need to claim a new algorithm; it does not eliminate these risks or establish a numerical acceptance probability.
