# Author approval and journal upload

Target: **SoftwareX**, **Original Software Publication**. This is the sole active submission package; the older Software Quality Journal materials are historical. The package has not been sent to a journal and no payment has been made.

## Files to use

Use the completed files at the repository's `main` branch, indexed by `research/softwarex/generated/final-readiness.json`, and match their exact hashes. The article pins an immutable source-and-evidence commit; a later main commit adds the compiled article and upload files. This separation avoids embedding a commit's own unknown hash inside itself. Use the completed submission snapshot or reviewer ZIP for manuscript rebuilding; the earlier pinned commit supports the code and evidence it actually contains. Historical readiness receipts do not certify files edited afterward.

| Submission item | File from the repository root |
| --- | --- |
| Manuscript for review | `output/pdf/zerorun-softwarex.pdf` |
| Editable LaTeX source | `output/submission/SoftwareX_source.zip` |
| Reviewer software and evidence | `output/submission/ZeroRun_SoftwareX_reviewer.zip` |
| Highlights, separate Word document | `research/softwarex/HIGHLIGHTS.docx` |
| Cover letter text | `research/softwarex/COVER_LETTER.txt` |
| Public code repository | <https://github.com/floxy-21/zerorun-research> |

The source ZIP is flat: `main.tex`, `references.bib`, compiled `main.bbl`, publisher class/style, and their notice. Its architecture figure and tables are editable LaTeX; no figure image is missing. The ZIP manifest is an integrity file, not manuscript content. If the submission system asks for individual source files, upload those files at the same folder level. The reviewer ZIP is a separate supplementary software/evidence item, not the LaTeX source archive.

Paste `COVER_LETTER.txt` into the corresponding text field. Upload `HIGHLIGHTS.docx` as Highlights; its wording matches `HIGHLIGHTS.txt`, and `generated/highlights-docx-review.json` records its structural and visual checks. The `.txt` file is the highlights source, not the preferred Word upload. Do not use older `.md` cover/highlight copies, older PDFs, or `ESM_1.zip` as the current submission.

Final PDF counts, visual review, references, and compilation-source hashes come from `generated/pdf-review.json`; archive and public-release bindings come from `generated/final-readiness.json`. Do not copy counts from an earlier checklist into the portal.

## Author-controlled steps

1. Read and approve the final article, tables, limitations, public software, methods disclosure of AI assistance, and diagram-caption attribution. Jishan Kapoor is the sole author; AI is not an author. Author review and intellectual responsibility are substantive requirements, not a formality completed by automated checks.
2. Confirm originality and that the article is not under consideration elsewhere. Submit to only one journal at a time; after rejection or confirmed withdrawal, another suitable journal can be considered with its own format and declarations.
3. Confirm the disclosed interest in ZeroRun's possible commercialization, and complete the publisher's competing-interest declaration using its required form/tool. No external funding was reported. Do not replace this with an invented "no interests" declaration.
4. Provide any required private postal-address details and actual CRediT contributions in the portal. The supplied author information is Jishan Kapoor, Independent researcher, Toronto, Canada; kapoorjishan2@gmail.com.
5. Inspect the portal's generated review PDF before approving submission. Source compilation and local PDF review do not establish that the portal rendered it identically.

The current guide and submission link are on the [official SoftwareX author page](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors). Check any publisher terms personally. The user's permission covers considering charges after acceptance, not advance payment or an automatic transaction.

## Publication scope

SoftwareX is the authorized target; its publicly displayed impact factor of 1.9 was disclosed during preparation. Its current original-software template controls the article format. The final author-side requirements audit is `JOURNAL_REQUIREMENTS_REVIEW.md`; the live author guide should also be checked in the portal.

The v3 model-backed core lifecycle passed, but its later unsupported-argument decision failed and remains recorded. The separately frozen guided demonstration passed two model decisions, two fresh oracles and six no-tool interpretations after non-model setup. These are synthetic, documented application checks, not independent developers, an autonomous coding benchmark, or proof of population reliability. The package does not claim universal AI acceleration, commercial qualification, immigration eligibility, or a numerical acceptance probability.
