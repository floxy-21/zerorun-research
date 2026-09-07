# One-command offline submission verification

From a clean checkout of the **completed public submission snapshot**, run:

```sh
python -B -m research.softwarex.verify_submission
```

Use CPython **3.12–3.14**. No package installation, Docker, API account, payment,
network access or model subscription is required. This checks saved evidence;
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
administration). `-B` avoids creating bytecode files. Do not save the report
inside the checkout; stdout may be redirected to a new file outside it.

This verification does not submit to a journal, claim independent human use,
predict acceptance, regenerate the paper, or call the readiness builder.
