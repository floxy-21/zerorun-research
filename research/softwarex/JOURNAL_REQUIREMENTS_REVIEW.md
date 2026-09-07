# SoftwareX requirements review

Reviewed 7 September 2026 (UTC). This is an author-side, AI-assisted preparation
audit, not external peer review, an acceptance estimate, or a submission receipt.
The manuscript and final package are being revised concurrently; observations
below identify the inspected state rather than certify a later release.

## Official source checks

The live [Original Software Publication LaTeX template](https://legacyfileshare.elsevier.com/promis_misc/softwarex-osp-template.tex)
was fetched and byte-matched to `template/softwarex-osp-template.tex`:
SHA-256 `2b18dfd14aa3893bc4e82a90caa19337bb1ce76f1fae5ea2c0eaa7e3563b8649`.
Its copyright year is 2026. The initial web-tool retrieval of the
[main author guide](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors)
failed; the subsequent complete browser verification is recorded below.
Uncontrolled copies of older templates are not substitutes for the current
official template.

### Browser verification addendum — 7 September 2026 (UTC)

The coordinating agent subsequently read the complete official author guide in
the in-app browser. The earlier web-tool failure is historical, not a remaining
access blocker. The [verified guide](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors)
specifies:

- 4,000 words, excluding title, authors, affiliations, references and metadata
  tables, but including abstract, running text, captions and footnotes; at most
  six figures.
- Abstract at most 250 words and one to seven keywords; the software template's
  approximately 100-word abstract remains the more specific drafting target.
- Three to five highlights, at most 85 characters each, in a separate editable
  file; highlights and graphical abstracts are encouraged. Text graphics may
  be embedded in LaTeX.
- Public code with README, `LICENSE.txt` and a `src` directory; preserve the
  prescribed software-template format. The template additionally names
  `Licence.txt`, which the package supplies as an identical alias.
- Research-data Option C: deposit, cite and link the research data. Bind the
  actual public raw evidence and explicit data reference, not a proposed deposit.
- Complete the declarations tool and upload its Word output. Supply the
  corresponding author's full postal address.
- Upload the PDF as Manuscript and the complete LaTeX ZIP under Zip files.

The package already supplies reviewed Word highlights. The final data citation
and immutable links must match the released evidence. The author's declaration
form, actual contribution confirmation and full private postal address remain
author-controlled; Toronto, Canada alone is not a complete postal address.

| Current template check | Inspected package / required final action |
| --- | --- |
| Original Software Publication template; five prescribed main sections | Present in `paper/main.tex`; retain the section structure. |
| 4,000 words; maximum six figures | Previous reviewed PDF reports 3,796 whole-PDF words and one figure. Recount after revisions. |
| Six main-text pages is a target; word limit takes priority | The existing 13-page preprint is not automatically a limit failure; exclusions include metadata, tables, figures and references. |
| Abstract approximately 100 words | Existing abstract is 113 words. |
| Public GitHub code, documented README.md and Licence.txt | Initial publication is recorded; check all three in the exact final anonymous-access snapshot. |
| C1–C8 metadata, including permanent GitHub pointer and support contact | Present; final C2/C7 must match released code/documentation, not planned files. |
| Software description, illustrative application and impact | Present; actual new trial results must replace neither failed attempts nor scope limitations. |

The controlling template limit is 4,000, not the 3,000 shown by some old indexed
copies. It uses C1–C8, not an older C1–C9 layout. No template exception is being
requested by this review.

## Final-package actions

1. **Reconcile final counts and links once.** At inspection,
   `SUBMISSION_CHECKLIST.md` still says 3,413 words, 12 pages and four highlights.
   `generated/pdf-review.json` instead records 3,796 words and 13 reviewed pages;
   `HIGHLIGHTS.txt` contains five lines. Update checklist, upload guide, cover
   letter, manuscript, source archive and public manifest from the final
   evidence/QA receipts. Preserve historical receipts; do not retroactively
   change their counts. The inspected PDF hash is
   `ebf630d65a3cacb446c3a7c664928a945937f978f4226cdb67cfa999a07522e9`.

2. **Close the small disclosure gap.** The architecture flow diagram's inspected
   caption lacks AI-assistance attribution. Elsevier permits AI-assisted
   explanatory diagrams but asks for tool, version and purpose in the caption,
   as well as a general declaration. Describe actual Codex assistance with the
   editable LaTeX; do not call it image generation or invent an authoring model
   version. Research/code/analysis assistance also belongs in the methods
   description, not only the general declaration. Existing text already names
   substantial Codex assistance, which should remain. The human author's actual
   review and approval must not be declared complete before it occurs.
   [Current Elsevier AI policy](https://www.elsevier.com/about/policies-and-standards/generative-ai-policies-for-journals)

3. **Provide a portal-compatible highlights file.** All five inspected highlights
   are within 85 characters. Publisher-wide guidance specifies three to five
   bullets and a Word document at final-files stage. Retain the editable text
   source and a matching `HIGHLIGHTS.docx`. This gap was closed by the separately
   reviewed Word artifact in `generated/highlights-docx-review.json`; the guide
   also accepts a separate editable highlights file. This is a file-format
   check, not a reason to undertake another experiment.
   [Elsevier highlights guidance](https://www.elsevier.com/en-gb/researcher/author/tools-and-resources/highlights)

4. **Bind the final usable release.** Verify anonymous access, immutable C2,
   matching C7 installation instructions, packaged raw receipts, executable
   offline validators, editable compilation sources and all third-party
   notices. A successful external-adapter installation is not the same claim as
   a later in-clone public quickstart replay. Distinguish those receipts.

5. **Keep the live-client denominators explicit.** V1 and V2 remain failed
   prospective configurations. V3 is a new user-approved configuration trial,
   not a relabeled V2 pass. Its maximum twelve model turns comprise six live
   tool-enabled turns and six no-tool interpretation turns; four separate fresh
   oracles are not additional model decisions. A successful synthetic trial
   would not establish an autonomous coding benchmark, external adoption or
   workflow speedup. Describe any actual failures without deleting them.

## Reviewer-facing substance, not invented entry requirements

The official [SoftwareX reviewer form](https://legacyfileshare.elsevier.com/promis_misc/softwarex-reviewer-form.pdf)
assesses potential research impact, convincing evidence, relevant comparisons,
working installation, usable API/manuals, dependencies, automated tests and
reproducibility. It also asks about portability and license notices in source
files. A package-level MIT license is already present; per-file notice coverage
should not be asserted without an inventory. Do not alter a frozen tested
runtime merely to change its licensing headers during this evidence run.

The form does **not** impose 100 repositories, five external developers, a new
caching algorithm, 5x speedup or a 20–30% workflow threshold. External uptake can
support impact but is not the sole route. Conversely, meeting artifact checks
alone does not establish usefulness. The strongest bounded addition is a
working, clearly scoped client application that distinguishes prior success
from fresh evidence, accompanied by an independently recomputable installation
record. It still leaves natural eligible-repeat prevalence and end-to-end
benefit unmeasured.

## Human-controlled submission items

These are not waived by a completed technical package:

- Jishan Kapoor reviews and approves the exact final text, evidence, references,
  disclosures and submitted files, and confirms actual intellectual contribution.
- Confirm originality, material-distribution permissions and no simultaneous
  journal consideration.
- Complete the publisher's declarations tool and upload its Word output.
  Existing manuscript text
  discloses ZeroRun development and potential commercialization; do not replace
  it with an unsupported declaration of no interests.
- Confirm actual CRediT roles and provide the required full postal address
  privately. Known author details are Jishan Kapoor,
  Independent researcher, Toronto, Canada; kapoorjishan2@gmail.com; no external
  funding.
- Review the portal-generated PDF before submitting. Submission, publishing
  terms and any later fee decision are distinct from uploading the research
  package to GitHub.

## Handoff

Root owns the manuscript, cover letter, builders, final tests and publication.
This audit changed none of those files. Its actionable findings were sent
before the final rebuild so that one reconciled package can close them.
