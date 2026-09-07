# Prospective application coverage and fresh-rebuild amendment

Recorded 7 September 2026 before the following new executions. This amendment
responds to the unresolved coverage and fresh-environment reproduction limits;
it does not change the original selection, measurements, or eligibility rules.

## Expanded-budget main repeat

Run the original 24-case main ledger, in its original order, in a new external
directory. Its SHA-256 is
`4767391ca8f99ba3ad698bf1577b0e66c9dccc97fbd78565447ba1a4b1281997`.
Use the same historical 0.5.1 runtime, unchanged v1/v2 experiment helpers,
existing inspected dependency image and two counterbalanced paired blocks.
The campaign budget is fixed at 1,200 seconds, with the same symmetric
120-second per-execution cap. The original 300-second run remains separate.

The existing runner has no resume offset. This is therefore a full repeat,
including its first two cases, not an unreported subset of unfinished cases.
Repeated cases are not additional independent subjects. Do not replace cases,
change dependencies after outcomes, add consumers, or retry toward a favorable
speed ratio. Preserve compatibility failures, unsupported states, incomplete
blocks, budget exclusions, and all measured costs. A material correctness
disagreement stops the campaign. Completion does not require supporting every
selected repository.

## Fresh public-source dependency-image reproduction

Obtain fresh anonymous checkouts of the published experiment harness and exact
historical engine. Record commit identities and unchanged source checks. Build
a new dependency image from the pinned public base and hash-locked dependency
wheels using the existing builder, an isolated loopback registry on port 19519,
and a new build directory. Record the actual image digest, configuration,
dependency inventories and all preparation commands. No old private keys,
operator authority or model credentials are required.

Run the unchanged complete two-case pilot ledger with the new image in another
new directory, with two paired blocks, a 600-second campaign budget and the same
120-second execution cap. This experiment follows the main repeat; benchmarks
are not run concurrently. Compare each result with a separate fresh oracle.
Keep its timings separate from the historical image and the main repeat.

A new recipe build may have a different digest. Success establishes an
author-side rebuild-and-rerun from public source on the existing VM, not
independent human replication, a clean operating-system installation, a
publicly pullable historical derived image, or a bit-identical rebuild. Public
base images may already be available locally; record that preparation boundary.
The new run creates fresh laboratory cache keys. Export only the existing
record allowlist, excluding workspaces, private keys and registry stores.

The wrapper must retain this amendment before execution, its own source,
environment checks, command outcomes and failures. The presence of this plan
alone is not evidence that either experiment completed or produced a benefit.
