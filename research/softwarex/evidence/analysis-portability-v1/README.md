# Canonical analysis portability record

This is a publication-tooling correction, not a new experiment or a changed
performance gate. It was identified during final Linux verification of public
commit `915bbb062171b91630ad427dda9d2088700ba151` on 7 September 2026 UTC.
The account-free installation and five server checks had already passed; the
additional historical-analysis command is a different workflow.

## Preserved pre-correction state

- `prior-operating-region.json`: SHA-256
  `ce06c8960ed1a30ceda64a4fa9de06c47e429045c3dfbc5b8fc84280991c29bb`.
- `prior-extension-evidence.json`: SHA-256
  `7d4e941c195cfc0c97de5e6bc04e7c382b4f056826150f53067244b9f56dffa3`.
- The original recovered-analysis JSON remains unchanged at
  `research/sqj/strengthening/evidence/replication-analysis-v1.json`, SHA-256
  `287c3f79beb90ac12913a9b6002e8c02c5bdd8fcda2bbf3644b0118661f5ac0b`.
  Both original analyzer files remain bound to their original hashes.

An initial Windows staging command used a Python environment without the
public runtime on its import path and failed to import `zerorun.oci`.
Repeating the read-only check with the explicitly staged `src` import path
passed. This was not a package-installation result or a runtime-source fix.

## Linux diagnosis

Application-evidence validation passed in the new Linux CPython 3.10 environment,
but operating-region and extension checks refused a nonidentical recomputed
historical analysis. A full recursive comparison identified eight last-bit
floating differences and a permutation of the three zero-byte crash records.
For example, Packaging's summed optimized time was
`289759.8444699958` instead of the archived `289759.8444699959` milliseconds.
No changed test result, integer denominator, raw observation, source hash,
experiment order, or eligibility decision was identified.

The older interpreter's aggregation behavior is not the canonical analysis
environment. [Python documents the float-sum change in 3.12](https://docs.python.org/3/library/functions.html#sum).
The historical recovery parser also traverses a filesystem incident inventory
without sorting; filesystem enumeration order is not an experimental sequence.

A separate read-only run in the already-present digest-pinned CPython 3.12.14
container recomputed the entire historical analysis. After sorting **only**
the top-level `unparseable_zero_byte_receipts` list by path, its complete value
matched the Windows CPython 3.14 reference exactly. No numeric tolerance or
rounding was needed. The three paths were:

- `packaging/trajectory-4/request-1/fresh.json`
- `packaging/trajectory-4/request-1/observation.json`
- `packaging/trajectory-4/request-2/invocation.json`

Each record still identifies a zero-byte file, not an inferred test result.
The container used the same already-reviewed Python image digest as the
laboratory example, a read-only public-source mount, no network, and no model
or operator-authority credentials. It executed only local evidence analysis.

## Versioned correction and validation

The supported canonical analysis environment is CPython 3.12–3.14, separately
from ZeroRun runtime and laboratory quickstart support for Python 3.10+.
`analysis_reproduction.py` rechecks the frozen recovery analysis and permits
only the explicitly unordered incident-list ordering difference. All values,
including floats, remain exactly compared; duplicate or altered incident rows,
changed hashes and reordered experimental results must fail. The checked
archived ordering is then used for stable downstream serialization. Neither
the original raw evidence nor its frozen producer is edited.

Use `python -m research.softwarex.analysis_reproduction --check`, then the
documented operating-region, extension and article checks. Final tests and
cross-platform verification records accompany the completed release; a retained
earlier check is not retroactively described as successful.
