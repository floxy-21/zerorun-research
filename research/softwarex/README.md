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
**CPython 3.12â€“3.14**, using the route in `REPRODUCIBILITY.md`. This is an analysis
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

The separate real-issue pilot contains one actual model invocation and one independently verified SQLGlot regression repair among two selected cases; the other stopped before invocation. Its scripted downstream handoff passed fresh checks but remained 27.1% slower over the complete chain. The original copy pilot was 42.3% slower, the clean image repeat 10.7% slower, and the original 2/24 image main 23.2% faster. A separately amended repeat attempted all 24 selected cases: 15 completed across seven repositories and nine were incomplete or unsupported. Its timings remain qualified by the host-storage interruption. A public-source recipe rebuild also completed both original pilot cases with four paired blocks and fresh-oracle agreement, reducing consumer latency 75.8% while increasing chain cost 10.5%. Existing VM/cache state was reused. [Coverage](APPLICATION_COVERAGE_AUDIT.md), [reproduction scope](FRESH_REAL_WORKLOAD_REPRODUCTION.md), and `generated/handoff-evidence-v1.json` preserve each cohort and all failures separately.

A subsequent public-source run disabled Docker build-cache reuse and evaluated the unchanged 24-case ledger. It completed 10 cases across five repositories and 20 paired blocks, with six incomplete or unsupported cases and eight cases not run within the fixed budget. All 20 reused successes agreed with fresh oracles. Complete producer-consumer cost was near break-even (0.12% lower), while consumer waiting was 79.1% lower; five completed cases were slower. Image preparation added 66.070 seconds. Host continuity checks passed within the recorded sampling bounds. This author-side run used the existing VM and pre-existing public base images; it is not independent human replication or a clean operating-system installation. See [the v3 reproduction scope](FRESH_REAL_WORKLOAD_REPRODUCTION.md) and [all case dispositions](APPLICATION_COVERAGE_AUDIT.md).

This is a submission package, not a journal submission. Jishan Kapoor has approved the article and AI-assistance disclosure and confirmed originality and no concurrent submission. The official competing-interest declaration has been generated; the final portal-generated PDF and actual submission receipt remain to be checked. No submission, publisher agreement, or payment is performed by the preparation workflow.

The Software Quality Journal materials in the original development repository are preserved as historical work, not a second simultaneous submission. SoftwareX is the current primary package. The public research repository excludes the private development history and unrelated product material.
