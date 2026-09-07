# Author approval and journal upload

Target: **SoftwareX**, **Original Software Publication**. This is the sole active submission package; the older Software Quality Journal materials are historical. The package has not been sent to a journal and no payment has been made.

## Files to use

Use the completed files at the repository's `main` branch, indexed by `research/softwarex/generated/final-readiness.json`. The article itself pins an immutable source-and-evidence commit; the later main commit adds the compiled article and upload files. This separation avoids embedding a commit's own unknown hash inside itself. Use the completed submission snapshot or reviewer ZIP for manuscript rebuilding; the earlier pinned commit suffices for code and raw-evidence analysis.

| Submission item | File from the repository root |
| --- | --- |
| Manuscript for review | `output/pdf/zerorun-softwarex.pdf` |
| Editable LaTeX source | `output/submission/SoftwareX_source.zip` |
| Reviewer software and evidence | `output/submission/ZeroRun_SoftwareX_reviewer.zip` |
| Highlights, separate editable text | `research/softwarex/HIGHLIGHTS.txt` |
| Cover letter text | `research/softwarex/COVER_LETTER.txt` |
| Public code repository | <https://github.com/floxy-21/zerorun-research> |

The source ZIP is flat: `main.tex`, `references.bib`, compiled `main.bbl`, publisher class/style, and their notice. Its architecture figure and tables are editable LaTeX; no figure image is missing. The ZIP manifest is an integrity file, not manuscript content. If the submission system asks for individual source files, upload those files at the same folder level. The reviewer ZIP is a separate supplementary software/evidence item, not the LaTeX source archive.

The cover letter can be pasted into the corresponding text field. Highlights are plain editable text; if the portal restricts that field to a word-processing format, import these five lines without changing their wording. Do not upload older PDFs or the earlier `ESM_1.zip` as the current article.

## Author-controlled steps

1. Read and approve the final article, tables, limitations, public software, and substantive AI-assistance disclosure. Jishan Kapoor is the sole author; AI is not an author.
2. Confirm originality and that the article is not under consideration elsewhere. Submit to only one journal at a time; after rejection or confirmed withdrawal, another suitable journal can be considered with its own format and declarations.
3. Confirm the disclosed interest in ZeroRun's possible commercialization, and complete the publisher's competing-interest declaration using its required form/tool. No external funding was reported. Do not replace this with an invented "no interests" declaration.
4. Provide any required private postal-address details and actual CRediT contributions in the portal. The supplied author information is Jishan Kapoor, Independent researcher, Toronto, Canada; kapoorjishan2@gmail.com.
5. Inspect the portal's generated review PDF before approving submission. Source compilation and local PDF review do not establish that the portal rendered it identically.

The current guide and submission link are on the [official SoftwareX author page](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors). Check any publisher terms personally. The user's permission covers considering charges after acceptance, not advance payment or an automatic transaction.

## Publication scope

The publicly displayed impact factor checked during preparation was 1.9. SoftwareX was chosen for software-publication fit after the request prioritized a reputable journal, not because a manuscript-specific acceptance percentage was known. Peer review remains an editorial decision. This package supports its bounded software claims; it does not claim universal AI acceleration, commercial qualification, or immigration eligibility.
