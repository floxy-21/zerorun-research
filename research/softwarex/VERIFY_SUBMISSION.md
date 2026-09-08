# One-command offline submission verification

Use a clean Git checkout and **CPython 3.12–3.14**. No package installation is required. For the 0.5.3 submission snapshot, use the following commands once its named release and reviewer asset are available:

```sh
git clone --depth 1 --branch softwarex-0.5.3-20260908-r1 https://github.com/floxy-21/zerorun-research.git zerorun-review
cd zerorun-review
git rev-parse HEAD
```

Compare the printed commit with the commit shown on the `softwarex-0.5.3-20260908-r1` GitHub Release page. The tagged release and its archives remain unchanged when later navigation corrections are published on `main`; each checkout is checked against its own complete manifest. The 0.5.3 software binding is `e5194d340a09bccb667d3021a6a4a9a9a054123b`; the publication checkout has its own commit and includes the separately retained historical runtime.

**Do not run this complete verifier from an extracted reviewer ZIP.** That archive seals an earlier inventory and does not contain itself or the later artifact receipt. Its README remains bound to that inventory. Use these instructions with the Git checkout; individual evidence checkers can still inspect the extracted archive.

First download the complete reviewer ZIP. It is a GitHub Release asset because it exceeds the repository-file size limit; the source ZIP is already in Git. Run from the checkout root:

```sh
curl --fail --location --output output/submission/ZeroRun_SoftwareX_reviewer.zip https://github.com/floxy-21/zerorun-research/releases/download/softwarex-0.5.3-20260908-r1/ZeroRun_SoftwareX_reviewer.zip
# Windows PowerShell: use curl.exe with the same arguments.
```

The manifest binds this declared external asset's exact path, size and SHA-256. It is ignored by Git. A missing or altered reviewer archive is a verification failure; no download occurs inside the verifier. Once the release files are available locally, run:

```sh
python3 --version
python3 -B -m research.softwarex.verify_submission
```

Use the executable that reports CPython **3.12, 3.13, or 3.14**; replace `python3` if needed. In Windows PowerShell, use `python` and `curl.exe` with the same arguments. The verification command requires no package installation, Docker, API account, payment,
network access or model subscription. This checks saved evidence;
it does not execute downloaded AI commands or run a new experiment. Product
installation and the account-free laboratory remain separate workflows in
[REPRODUCIBILITY.md](REPRODUCIBILITY.md) and [QUICKSTART_053.md](QUICKSTART_053.md).
The retained [0.5.2 guide](QUICKSTART_052.md), its version-specific validators and receipts,
and the [0.5.1 laboratory guide](QUICKSTART_LAB.md) describe historical evidence.

The command validates the complete public manifest before and after checking,
the current PDF and both ZIPs against their artifact receipt, eight existing
analysis/manuscript checks, the current 0.5.3 installation/test receipt, the
handoff-evidence reconciliation, and the current public-guide installation
and five-stage STDIO receipts. Archive members are checked without being
extracted or executed. A saved visual-review receipt binds the PDF; this command
does not perform a fresh visual review.

It prints one JSON report with each outcome, timing and any error. Exit status
`0` means every required check passed; nonzero means a missing, stale, failed or
timed-out check. Each checker has a 120-second bound. Negative research findings
are valid evidence, not automatically failed verification. A missing final
paper, handoff summary, current-runtime receipt or current quickstart receipt
is **not** silently skipped.

Run before adding local files or installing in the checkout. The exact manifest
deliberately rejects missing, changed and additional payload files (except Git
administration and the exact declared external reviewer asset). `-B` avoids creating bytecode files. Do not save the report
inside the checkout; stdout may be redirected to a new file outside it.

If the verifier reports missing or unlisted files after a source installation or earlier command, preserve that working copy and use a fresh checkout. `git status` can appear clean while ignored build, `.egg-info`, or bytecode files are present; the exact manifest still detects them. Use the included wheel for installation into an external environment, and keep source builds or new test evidence in a separate working copy. If a download failed or the ZIP hash differs, preserve the failed download and obtain a new copy of the exact asset. Do not edit the manifest or expected hashes to clear a failure.

The final verification report must bind the exact publication snapshot and current
0.5.3 receipts. This guide does not itself establish that those checks have
passed or that the planned release has been published. A report for an earlier
release does not validate this snapshot; run the command for the files you are reviewing.

This verification does not submit to a journal, claim independent human use,
predict acceptance, regenerate the paper, or call the readiness builder.
