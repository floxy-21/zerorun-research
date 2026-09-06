# Artifact-builder offline test attempts

These runs exercised isolated temporary fixtures; none generated final manuscript or submission archives.

- The first sandboxed full run (`artifact-builder-unit-v1.xml` requested) was interrupted after repeated setup errors. It did not emit a completed JUnit file, and no pass count is claimed for it.
- A bounded sandbox reproduction is retained in `artifact-builder-sandbox-diagnostic-v1.xml`: one setup error, caused by `PermissionError` creating the pytest temporary-directory cleanup lock. The test body did not run.
- The normal-permission run is retained in `artifact-builder-unit-v2.xml`: 61 passed. Subsequent strengthening, if any, is recorded in separately numbered files; this receipt is not overwritten.
- The strengthened archive-binding run is retained in `artifact-builder-unit-v3.xml`: 66 passed, zero failures, errors, or skips (2.987 seconds). The tested builder SHA256 is `764bacae5a5d31050cc41ca888f5b82465ec55c96ee21abfca408e3a1e3a7a57`.
- The readiness-binding run is retained in `readiness-bindings-unit-v1.xml`: 14 passed, zero failures, errors, or skips (0.969 seconds). The tested readiness builder SHA256 is `6d63299b675e8f3faa361c1d96018f1be16a51834bbca45cfe222f4575c49441`. These tests reject stale publication inputs and mismatched installed-package/test receipts; they do not declare the live submission package complete.

The fixtures contain deliberately fake PDF-signature bytes and source files. They verify the archive builder's binding, inventory, and failure behavior; they do not attest to an actual PDF's visual quality or compilation. Actual final-PDF review is a separate author/reviewer receipt.
