# Author approval and journal upload

Target: **SoftwareX**, **Original Software Publication**. This is the sole active submission package; the older Software Quality Journal materials are historical. The package has not been sent to a journal and no payment has been made.

Preparation and public release verification are authorized. Journal submission is held pending the user's explicit approval of the final package, after review of the exact upload files and portal-generated PDF.

## Files to use

The planned current release is `softwarex-0.5.3-20260908-r1`. Use its completed submission snapshot only after `research/softwarex/generated/final-readiness.json` and anonymous release verification bind the exact upload files. Current 0.5.3 installation checks do not by themselves establish that the final manuscript and archives are ready. The article pins an immutable source-and-evidence commit; a later main commit adds the compiled article and upload files. This separation avoids embedding a commit's own unknown hash inside itself. Use the completed submission snapshot or reviewer ZIP for manuscript rebuilding; the earlier pinned commit supports the code and evidence it actually contains. Historical readiness receipts do not certify files edited afterward.

| Submission item | File from the repository root |
| --- | --- |
| Manuscript for review | `output/pdf/zerorun-softwarex.pdf` |
| Editable LaTeX source | `output/submission/SoftwareX_source.zip` |
| Reviewer software and evidence | `output/submission/ZeroRun_SoftwareX_reviewer.zip` |
| Highlights, separate Word document | `research/softwarex/HIGHLIGHTS.docx` |
| Cover letter text | `research/softwarex/COVER_LETTER.txt` |
| Public code repository | <https://github.com/floxy-21/zerorun-research> |

Once the release is published, download the full reviewer ZIP from the [versioned GitHub Release asset](https://github.com/floxy-21/zerorun-research/releases/download/softwarex-0.5.3-20260908-r1/ZeroRun_SoftwareX_reviewer.zip). It exceeds GitHub's repository-file limit, so a Git clone contains the source ZIP and a hash-bound reference to the reviewer asset. Save the downloaded file at the path listed above; [the verification guide](VERIFY_SUBMISSION.md) provides the command and checks both archives without network access. Every raw evidence file remains in the complete reviewer ZIP.

The source ZIP is flat: `main.tex`, `references.bib`, compiled `main.bbl`, publisher class/style, and their notice. Its architecture figure and tables are editable LaTeX; no figure image is missing. The ZIP manifest is an integrity file, not manuscript content. If the submission system asks for individual source files, upload those files at the same folder level. The reviewer ZIP is a separate supplementary software/evidence item, not the LaTeX source archive.

Use the portal category **Manuscript** for the PDF and **LaTeX source files** for the
complete editable LaTeX source ZIP, matching the observed portal labels.

Paste `COVER_LETTER.txt` into the corresponding text field. Upload `HIGHLIGHTS.docx` as Highlights; its wording matches `HIGHLIGHTS.txt`, and `generated/highlights-docx-review.json` records its structural and visual checks. The `.txt` file is the highlights source, not the preferred Word upload. Do not use older `.md` cover/highlight copies, older PDFs, or `ESM_1.zip` as the current submission.

Final PDF counts, visual review, references, and compilation-source hashes come from `generated/pdf-review.json`; archive and public-release bindings come from `generated/final-readiness.json`. Do not copy counts from an earlier checklist into the portal.

For reviewer reproduction of the canonical timing analysis or final manuscript,
use **CPython 3.12–3.14** and the instructions in `REPRODUCIBILITY.md`, including
its checked pinned-container route. A default Python 3.10 interpreter is not the
canonical analysis environment, although it remains supported for ZeroRun and
the account-free installation/server quickstart. The analysis checker requires
exact archived values, normalizing only the ordering of one explicitly named
zero-byte receipt inventory; no numeric tolerance, dropped records or relaxed
performance criteria are used.

## Author-controlled steps

1. Read and approve the final article, tables, limitations, public software, methods disclosure of AI assistance, and diagram-caption attribution. Jishan Kapoor is the sole author; AI is not an author. Author review and intellectual responsibility are substantive requirements, not a formality completed by automated checks.
2. Confirm originality and that the article is not under consideration elsewhere. Submit to only one journal at a time; after rejection or confirmed withdrawal, another suitable journal can be considered with its own format and declarations.
3. Retain the publisher-generated competing-interest Word document already verified in the portal: 13,749 bytes, SHA-256 `3a483b337f1b62df6d96b183f1f145b17cea00c1fd6851d0d8def9768da6efba`. It contains the approved possible-commercialization declaration. No external funding was reported; do not replace the document with an invented no-interests declaration.
4. Enter the full postal address and actual CRediT contributions already supplied privately by the author, and verify the portal entries. Keep the private street address and postal code out of the public repository. Public author information is Jishan Kapoor, Independent researcher, Toronto, Canada; kapoorjishan2@gmail.com.
5. Inspect the portal's generated review PDF before approving submission. Source compilation and local PDF review do not establish that the portal rendered it identically.

The current guide and submission link are on the [official SoftwareX author page](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors). Check any publisher terms personally. The author has authorized the stated USD 1,920 article publishing charge plus applicable tax, contingent on acceptance. No payment has been made; any payment transaction remains a separate post-acceptance step. Confirm that the offered terms and amount match that authorization.

## Publication scope

SoftwareX is the authorized target; its publicly displayed impact factor of 1.9 was disclosed during preparation. Its current original-software template controls the article format. The final author-side requirements audit is `JOURNAL_REQUIREMENTS_REVIEW.md`; the live author guide should also be checked in the portal.

In the historical synthetic-fixture experiment, the v3 model-backed core lifecycle passed, but its later unsupported-argument decision failed and remains recorded. The separately frozen guided demonstration passed two model decisions, two fresh oracles and six no-tool interpretations after non-model setup. The genuine issue-fixing pilots, 0.5.3 application records and controlled real-repository handoffs have separate protocols and denominators. Use the final manuscript and reconciled evidence for their actual outcomes; do not infer completion from a prepared protocol or an earlier successful check. These observations do not establish independent developer adoption, population reliability, universal AI acceleration, commercial qualification, or a numerical acceptance probability.
