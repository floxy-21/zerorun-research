# Live-client pre-model amendment 1: missing support module

Recorded on 7 September 2026 UTC before any live model invocation for this
extension. The original [live-client protocol](LIVE_CLIENT_PROTOCOL.md) remains
unchanged. Its four-turn scope, statuses, timeout, fixture-only authority,
metadata approval, and no-retry rule are not amended.

The first public-layout launch stopped during Python import because the clean
public release omitted `tools/aggregate_codex_install_evidence.py`, which the
unchanged lifecycle harness imports to validate authenticated Codex installer
receipts. No synthetic lifecycle, authority creation, or model call occurred in
that failed import attempt. Preserve the original import failure separately.

## Correction and provenance

The adapter is supplied with this one original helper at
`support/tools/aggregate_codex_install_evidence.py`, relative to the adapter's
directory, outside the pinned experiment checkout. Its exact Git LF bytes were
extracted with `git archive` from source commit
`f67cac4a9067bc7dc6debc86f31d5e62e9aedf78`; the helper was not edited.
Its SHA-256 is
`a5eafbb44d36751bcf84dc353a6625a02b9dfb08e8a04a95e6b37a3bb689fc21`.

Before importing the lifecycle, the adapter verifies this amendment and helper,
requires real non-linked support directories, checks the `tools` namespace
against the pinned checkout, and appends only the verified support directory to
that namespace. It verifies the imported helper's exact origin and bytes.
Original lifecycle, smoke, installer, runtime, and public-manifest checks remain
in place. The pinned public checkout and installed runtime are not modified.

The outer receipt binds the amendment and supplied helper, preserves the
original lifecycle receipt and its original self-hash unchanged, and has its
own canonical payload hash. Post-execution source/support recheck failures
remain adverse evidence rather than disappearing or becoming a pass.

## Reproduction and limits

Copy the adapter, original protocol, this amendment, and the `support/` tree to
a separate real directory outside the pinned public checkout. The fixed
experiment source remains public commit
`681907860dc2ab9df70034f82a0025463d1fdec4`. This prerequisite correction does not
authorize retries after a model/tool failure. An actual result must still be
inspected before reporting functional success; the presence of this amendment
does not mean the trial passed or even started.
