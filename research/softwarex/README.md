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
- `OPERATING_GUIDE.md`, `CLIENT_API_CARD.md`, and `QUICKSTART_053.md`: operator instructions, the configured-task API reference, and the current account-free laboratory walkthrough. `QUICKSTART_LAB.md` preserves the historical 0.5.1 guide.
- `REPRODUCIBILITY.md`: installation, offline checks, and separate fresh-execution instructions.
- `SUBMISSION_CHECKLIST.md`: verified preparation checks and author-controlled declarations.
- `UPLOAD_GUIDE.md`: exact upload files and the remaining author-controlled portal steps.

The receipts under `generated/` identify exact publication commits, file hashes, automated checks, and PDF review. `generated/pdf-review.json` is authoritative for the reviewed PDF's counts and compilation inputs; `generated/final-readiness.json` binds the completed archives and upload files. Earlier receipts remain historical evidence and do not certify later edits. Use only files whose exact hashes match final readiness.

Canonical timing-analysis reproduction and final manuscript generation require
**CPython 3.12â€“3.14**, using the route in `REPRODUCIBILITY.md`. This is an analysis
requirement, not a raised product requirement: ZeroRun and the account-free
quickstart still support Python 3.10+. `analysis_reproduction.py` compares exact
archived values, permitting only a different order of the top-level
`unparseable_zero_byte_receipts` inventory. It rejects duplicate/missing or
modified rows and changed numbers; it applies no numerical tolerance and does
not alter frozen analyzers, raw measurements, eligibility rules or performance
gates.

## Application evidence

The latest controlled V6 repeat completes all 24 selected cases across eight repositories, with 48 paired blocks, 48 reused successes and 96 agreeing fresh-oracle checks. It is a separately labeled corrected-fixture repeat: V5's Lizard-191 failure remains recorded, and V6 changes only that stale expected complexity after checking the upstream method. The complete chain is 4.1% lower in the aggregate and consumer waiting 78.9% lower; nine cases are slower. These are bounded descriptive observations, not population acceleration.

The new six-case model-producer application independently verifies five full-target fixes. SQLGlot's issue-specific transform regression is repaired, but two supplied dialect tests remain unsuccessful in the original final patch. A separate repair follow-up must retain that original outcome. Installed 0.5.3 model-consumer evidence is being finalized separately; no completed consumer claim follows from producer checks alone.

[Coverage](APPLICATION_COVERAGE_AUDIT.md), [reproduction scope](FRESH_REAL_WORKLOAD_REPRODUCTION.md), and `generated/handoff-evidence-v1.json` preserve every historical cohort, interrupted run and unfavorable cost. The recorded compatible-image build disables Docker build-cache reuse using 16 hash-bound wheels; existing VM/base-image prerequisites remain. Offline archive verification, installing the current package and generating fresh workload evidence are distinct workflows.

This is a submission package, not a journal submission. Jishan Kapoor has approved the article and AI-assistance disclosure and confirmed originality and no concurrent submission. The official competing-interest declaration has been generated; the final portal-generated PDF and actual submission receipt remain to be checked. No submission, publisher agreement, or payment is performed by the preparation workflow.

The Software Quality Journal materials in the original development repository are preserved as historical work, not a second simultaneous submission. SoftwareX is the current primary package. The public research repository excludes the private development history and unrelated product material.
