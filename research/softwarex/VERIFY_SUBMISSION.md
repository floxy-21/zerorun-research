# One-command offline submission verification

Use a clean Git checkout and **CPython 3.12–3.14**. No package installation is required. To select the published submission from scratch:

```sh
git clone --depth 1 --branch softwarex-0.5.2-20260907-r2 https://github.com/floxy-21/zerorun-research.git zerorun-review
cd zerorun-review
git rev-parse HEAD
```

Compare the printed commit with the commit shown on the `softwarex-0.5.2-20260907-r2` GitHub Release page. The tagged release and its archives remain unchanged when later navigation corrections are published on `main`; each checkout is checked against its own complete manifest.

**Do not run this complete verifier from an extracted reviewer ZIP.** That archive seals an earlier inventory and does not contain itself or the later artifact receipt. Its README remains bound to that inventory. Use these instructions with the Git checkout; individual evidence checkers can still inspect the extracted archive.

First download the complete reviewer ZIP. It is a GitHub Release asset because it exceeds the repository-file size limit; the source ZIP is already in Git. Run from the checkout root:

```sh
curl --fail --location --output output/submission/ZeroRun_SoftwareX_reviewer.zip https://github.com/floxy-21/zerorun-research/releases/download/softwarex-0.5.2-20260907-r2/ZeroRun_SoftwareX_reviewer.zip
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
[REPRODUCIBILITY.md](REPRODUCIBILITY.md) and [QUICKSTART_052.md](QUICKSTART_052.md).
The retained [0.5.1 laboratory guide](QUICKSTART_LAB.md) describes historical evidence.

The command validates the complete public manifest before and after checking,
the current PDF and both ZIPs against their artifact receipt, eight existing
analysis/manuscript checks, the current 0.5.2 installation/test receipt, the
new handoff-evidence reconciliation, and the current public-guide installation
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

The published fresh-VM verification report binds the tagged commit above. It does not establish that later documentation edits have already passed; run this command for the snapshot you are reviewing.

This verification does not submit to a journal, claim independent human use,
predict acceptance, regenerate the paper, or call the readiness builder.
