# Final repository usability audit

Reviewed 7 September 2026 after publication of tag `softwarex-0.5.2-20260907`
at commit `0528905a52b74df78aa4e5a09219df34620282dd`. This is a bounded,
AI-assisted author-side audit, not independent human peer review or a claim
that all possible defects have been excluded.

## Corrections

- Lead the README with complete verification using uninstalled CPython
  3.12-3.14. Use `-B`, keep reports outside the checkout, and explain that an
  external virtual environment does not prevent source-install build debris.
- Install the already checked 0.5.2 wheel into an external environment with
  `--no-index`, avoiding an in-place source build and network installation.
- Direct new users to the current 0.5.2 laboratory procedure. The earlier 0.5.1
  guide and its receipts remain historical evidence.
- Explain that the reviewer ZIP is an earlier sealed evidence inventory. It
  cannot contain itself or later artifact receipts. Run complete verification
  from the public Git checkout with the downloaded ZIP; the embedded older
  README is not the complete-snapshot procedure.
- Add explicit handling for bytecode, ignored build files, failed downloads,
  and the distinction between optional new tests and unchanged evidence.
- Summarize consumer latency versus complete chain cost in the README, with
  the 2/24 denominator, unfavorable pilots and bounded-feasibility interpretation
  stated together. The article already reports these facts and limitations.

The changes affect the exported README, operating guide, reproduction guide,
and complete-verification guide. The runtime, public Python helpers, frozen
quickstart procedures, manuscript, PDF, source ZIP and reviewer ZIP are
unchanged. Existing test and VM records retain their original source bindings;
they are not relabeled as executions of later documentation. The enclosing
manifest and readiness record are refreshed for the corrected documentation.

## Remaining runtime limitation

Static review found a pre-existing image-acquisition policy edge case. MCP
preflight refuses an initially absent image without pulling it, but later
runtime inspections use a default that permits pulling the pinned image. If
the image disappears or inspection fails after preflight, acquisition can be
attempted. Operators should keep the pinned runtime available for the run.
This finding does not demonstrate an unpinned image, incorrect reuse, or a new
0.5.2 regression. It was not dynamically reproduced in this audit. Relevant
tagged sources are `src/zerorun/mcp.py:322`, `src/zerorun/hermetic.py:104`,
`src/zerorun/hermetic.py:344`, `src/zerorun/oci.py:1087` and the analogous
node-level inspection in `src/zerorun/pytest_runtime.py:3391`.

The operating guide now qualifies the stronger unconditional no-acquisition
claim. A future implementation change should propagate the MCP no-acquisition
policy through all downstream inspections and test the present-at-preflight,
absent-later case. This audit does not change the frozen implementation.

## Evidence and filing status

The reviewed PDF/source bindings, adverse performance findings, partial 2/24
cohort denominator, one verified agent repair, licenses and references remain
consistent. The historical full regression and publication suites are preserved;
this documentation audit does not claim another full runtime test campaign.
The final public tag passed all 15 offline checks in the separately recorded
VM verification. Later documentation is subject to its own complete check.

The exact upload artifacts remain those in the
[submission release](https://github.com/floxy-21/zerorun-research/releases/tag/softwarex-0.5.2-20260907).
Use the [current verification instructions](VERIFY_SUBMISSION.md) and
[author upload guide](UPLOAD_GUIDE.md). Author approval, accurate contributions,
competing-interest declarations and the portal-generated PDF remain separate
filing requirements. No acceptance guarantee follows from this audit.
