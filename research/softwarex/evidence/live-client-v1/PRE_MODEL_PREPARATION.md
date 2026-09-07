# Live-client preparation record

These are infrastructure checks before the first live model invocation, not
failed or successful agent tasks. The research deadline is not an outcome filter.

- A new clone of public commit `681907860dc2ab9df70034f82a0025463d1fdec4`
  was installed into an external Linux virtual environment. Its source checkout
  remained clean. `public-experiment-manifest.json` preserves the exact manifest.
- The first verified Codex 0.153.3 installation identified existing Node 12.
  A separate official Node 22.14.0 download passed the vendor's SHA256 inventory;
  a second verified Codex installation binds that actual Node executable. Both
  installer receipts are retained. No model was invoked using the first receipt.
- A Linux adapter-test command was attempted before its incoming SCP transfer
  finished and reported a missing test path. After the completed transfer,
  the actual tests ran successfully. This was a file-transfer ordering error,
  not a ZeroRun execution or model trial.
- At 2026-09-07 00:05 UTC, a read-only import preflight of the public lifecycle
  harness failed with `ImportError: cannot import name
  'aggregate_codex_install_evidence' from 'tools'`. The original public packaging
  omitted this existing research helper. There had been zero model invocations.
  A separate amendment records its exact-byte external provision; the corrected
  public release also includes the original helper. Runtime code, eligibility,
  fixture authority, prompts and outcome requirements are unchanged.

The existing Codex account was made available to the user's own VM using the
officially documented headless-login method over SSH. No credentials are in this
record or the research repository. Removal of the temporary VM credential copy
is recorded separately after the trial; the user's host login is not modified.
