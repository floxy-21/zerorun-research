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

### Subsequent public-clone check

On public commit `8e555eab8663ef9f056e2181baecef532172846d`, the documented
read-only pinned-container commands successfully checked trace selection,
the original 70-request comparison, canonical recovered analysis, state rejoin,
operating-region output and the complete application ledger. The operating
output matched byte-for-byte. The extension-summary command still refused its
saved output; this failure is retained, not represented as a full passing run.

A recursive comparison isolated that remaining discrepancy to the ordering
of three `bounded_live_client.retained_files` inventory rows. Windows path
comparison is case-insensitive, while Linux path comparison places uppercase
names before lowercase names. The affected files were
`NON_MODEL_SETUP_NOTES.md`, `PRE_MODEL_PREPARATION.md`, and
`non-model-diagnostic.json`; their bytes and hashes were unchanged. The prior
extension output is retained as `prior-extension-before-posix-sort.json`,
SHA-256 `4e3512ac7054e768c713cc28b87ceee1ee3644aec5c734c5ae2dafdf529ac8dc`.

The extension inventory now orders paths by their explicit relative POSIX
strings, identically on both platforms. This makes serialization deterministic;
it changes no numerical tolerance, test result, raw record, model transcript,
or frozen experiment producer. The original failed configurations remain
failed. The new full publication test receipt and final Linux verification
identify the corrected implementation separately from the earlier checks.

### Corrected verification outcome

On 7 September 2026, the coordinating assistant executed all seven raw-evidence
commands from the documented pinned-container route against clean public commit
`bdf7fdc1309381b1c6762b2189547d1a1da87263`. Each reported successful validation:

1. Trace validator: 128 selected, 122 valid, 120 repeat pairs, zero pairs without
   intervening barriers.
2. Original comparison: 70 validated requests; whole-task challenges passed.
3. Canonical recovered analysis: six protected anchors, 24 blocks, 168 fresh
   agreements and 96 optimized hits; no numerical tolerance.
4. State-rejoin analysis: completed, empty error list.
5. Operating-region analysis: completed, four subjects, 168 requests; saved
   output matched exactly.
6. Extension evidence: completed and byte-identical. Its retained V1 live
   lifecycle still reports `live_pass: false`, correctly preserving that failure.
7. Application evidence: reconciled, including all failed and successful trials.

The public checkout remained clean afterward. The read-only container used
the digest and resource restrictions in `REPRODUCIBILITY.md`; no new model call
or test workload was executed. This paragraph is an internal execution
confirmation transcribed from tool outputs, not a new timing measurement,
external reproduction certificate or independent-human assessment.

The final Windows publication suite is
`../publication-four-hour-final-v3/receipt.json`: 706 passing cases and two
platform skips (708 JUnit cases), zero failures/errors, with all 69 inventoried
Python sources unchanged during execution. Earlier full suites remain separate.

### Archived manuscript and private-directory permissions

The coordinating assistant also extracted the compiled reviewer artifact into
a fresh Linux `mktemp` directory (mode 0700). The initial capability-dropped
container used its default UID 0 and could not traverse the host-owned folder;
the module import failed before article analysis. A diagnostic confirmed
`PermissionError` on `/artifact`, not absent archive members. The extraction
contained the expected `research` and `src/zerorun` trees.

The documented optional container function now supplies the invoking host
user's UID/GID with `--user`. This retains the private directory's permissions,
read-only mount, no-network policy and dropped capabilities. With that explicit
ownership configuration, `build_paper --check` passed against the extracted
reviewer artifact, including exact article, bibliography and evidence output.
No source, observation, PDF or experimental runtime was altered for this check.
The final archive includes the corrected instructions; archive-level integrity
and post-build replay records identify its exact bytes separately.
