# SoftwareX submission package

**Title:** ZeroRun: Reproducible test-result reuse for AI coding tools  
**Author:** Jishan Kapoor, Independent researcher, Toronto, Canada  
**Correspondence:** kapoorjishan2@gmail.com  
**Article type:** Original Software Publication

The authorized target is SoftwareX. Its displayed impact factor of 1.9 was disclosed during venue selection, below the earlier requested floor. This package targets software-publication fit; no editor has assessed the manuscript and no numerical acceptance probability is claimed.

The final package is organized as follows:

- `paper/main.tex` and `paper/references.bib`: editable, evidence-generated manuscript.
- `output/pdf/zerorun-softwarex.pdf` at repository root: reviewed manuscript PDF.
- `output/submission/SoftwareX_source.zip`: flat editable manuscript sources.
- `output/submission/ZeroRun_SoftwareX_reviewer.zip`: inventoried reviewer artifact.
- `COVER_LETTER.txt`: canonical cover letter for the submission portal.
- `HIGHLIGHTS.docx`: separate Word highlights; `HIGHLIGHTS.txt` is its matching source.
- `OPERATING_GUIDE.md`, `CLIENT_API_CARD.md`, and `QUICKSTART_052.md`: operator instructions, the configured-task API reference, and the current account-free laboratory walkthrough. `QUICKSTART_LAB.md` preserves the historical 0.5.1 guide.
- `REPRODUCIBILITY.md`: installation, offline checks, and separate fresh-execution instructions.
- `SUBMISSION_CHECKLIST.md`: verified preparation checks and author-controlled declarations.
- `UPLOAD_GUIDE.md`: exact upload files and the remaining author-controlled portal steps.

The receipts under `generated/` identify exact publication commits, file hashes, automated checks, and PDF review. `generated/pdf-review.json` is authoritative for the reviewed PDF's counts and compilation inputs; `generated/final-readiness.json` binds the completed archives and upload files. Earlier receipts remain historical evidence and do not certify later edits. Use only files whose exact hashes match final readiness.

Canonical timing-analysis reproduction and final manuscript generation require
**CPython 3.12–3.14**, using the route in `REPRODUCIBILITY.md`. This is an analysis
requirement, not a raised product requirement: ZeroRun and the account-free
quickstart still support Python 3.10+. `analysis_reproduction.py` compares exact
archived values, permitting only a different order of the top-level
`unparseable_zero_byte_receipts` inventory. It rejects duplicate/missing or
modified rows and changed numbers; it applies no numerical tolerance and does
not alter frozen analyzers, raw measurements, eligibility rules or performance
gates.

## Application evidence

The four-call v3 Codex lifecycle passed: readiness, fresh execution, reuse, and fresh verification, with two separate fresh oracles. Its next model-selected call invented an unsupported argument and was rejected; that full trial remains unsuccessful. The remaining decision and six interpretation cases were not reached.

A separately frozen API-guided demonstration then passed two model-selected decisions and two fresh oracles, followed by six no-tool interpretation cases. Its discovery, readiness check, and fresh seed were non-model setup. The model correctly distinguished prior success from fresh evidence, interpreted fresh failure, and declined a reused status when fresh diagnostics were required. See `evidence/application-client-v3/receipt.json` and `evidence/guided-client-v1/receipt.json`; both preserve their prospective sources, prompts, and raw responses. Earlier failed configurations remain included.

The current 0.5.2 Linux quickstart passed from an anonymous public checkout and a fresh external installation; its source-bound records are under `evidence/quickstart-public-052-v1/`. The historical 0.5.1 quickstart remains under `evidence/quickstart-public-v1/`. These are bounded synthetic demonstrations, not independent-human validation, autonomous issue resolution, a causal estimate of documentation improvement, or end-to-end speedup.

The separate real-issue pilot contains one actual model invocation and one independently verified SQLGlot regression repair among two selected cases. The second case was refused before model invocation. Its controlled downstream handoff passed fresh checks but remained 27.1% slower over the complete producer/consumer sequence. Four distinct complete reference-patch cases across SQLGlot and pycparser provide additional controlled observations. The original copy pilot was 42.3% slower, the clean image repeat 10.7% slower, and the partial image main 23.2% faster; only two of its 24 selected cases completed, both from pycparser. These cohorts are reported separately in `generated/handoff-evidence-v1.json`; incomplete cases and earlier failures remain visible.

This is a submission package, not a journal submission. Jishan Kapoor must review and approve the final text and substantive AI-assistance disclosures, verify originality/no-concurrent-submission and competing-interest declarations, complete the publisher's author forms, and approve the portal-generated PDF. No submission, publisher agreement, or payment is performed by the preparation workflow.

The Software Quality Journal materials in the original development repository are preserved as historical work, not a second simultaneous submission. SoftwareX is the current primary package. The public research repository excludes the private development history and unrelated product material.
