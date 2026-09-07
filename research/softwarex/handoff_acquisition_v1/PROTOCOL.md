# Prospective source-only handoff case acquisition v1

Freeze this protocol, collector bytes, ordered shortlist, fixed seed, bounds,
and selection rule in `plan.json` before the collector's first network request.
Earlier dataset metadata/schema inspection is feasibility information, not an
execution result. This collector never executes patches, imports downloaded
code, extracts source archives, authenticates to a private service, or grants
repository authority.

Use `nebius/SWE-rebench`, config `default`, split `test`, revision
`89cdfbab4ab1bd8f5a658bb212d1b63624f4f881` (21,336 rows; CC-BY-4.0). Download the two
revision-pinned `data/test-00000-of-00002.parquet` and
`data/test-00001-of-00002.parquet` files; retain raw bytes and SHA-256 records.
Use pyarrow 23.0.1 only to read data. Preserve metadata and source attribution.

The collector constant `SHORTLIST` is an ordered convenience/support shortlist,
not a speed ranking or a random repository sample. Select the first ten listed
repositories having at least three syntactically valid cases. A valid case has
an instance identifier, exact 40-hex base commit, nonempty repository-license
label, nonempty source and test patches, and one to sixteen distinct safe
relative Python-file targets in the test patch.
Reject duplicate candidate identifiers, malformed or oversized patches, and
unsafe target paths. Record every rejected candidate and every absent or
underpopulated shortlist repository. Do not inspect test outcomes or timings.

Within each selected repository, order valid cases by SHA-256 of
`zerorun-handoff-acquisition-v1:main:repo:instance_id:base_commit`, breaking ties
by instance identifier. Take three per repository for the main cohort. From
the remaining valid cases in those same repositories, take the first two under
the analogous `pilot` hash order. Pilot and main cases are disjoint. Freeze
both selections before downloading any selected repository archive. If ten
repositories or two extra pilot cases are unavailable, retain an incomplete
selection and report that fact; do not change the shortlist, seeds, or counts.

For each selected case, retain an HF-style one-row metadata wrapper and download
`https://codeload.github.com/{repo}/tar.gz/{base_commit}` without extraction.
Each source archive is bounded to 64 MiB; parquet files to 512 MiB each; metadata
and notices to 4 MiB. Use anonymous HTTPS, a 45-second socket timeout and a
180-second deadline checked between reads (an in-flight read can overrun the
deadline by up to its socket timeout), and one
attempt per URL. Preserve failed and partial downloads; no replacement cases,
automatic retry-until-success, or outcome-based selection. Archive availability
does not establish compatibility, successful tests, or an accepted patch.

Write separate `pilot.json` and `main.json` manifests with schema
`zerorun.handoff-selection.v1`. Paths are relative to the acquisition directory.
Each case binds its metadata and immutable-base archive by path, byte count,
and SHA-256 and lists relative Python test targets. A failed download remains a
selected case with an unavailable archive; such an incomplete manifest is not
execution-ready. `receipt.json` records complete acquisition status separately
from any downstream test or model result. Keep every initial outcome.
