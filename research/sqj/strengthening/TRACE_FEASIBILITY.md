# Recorded-trace replay feasibility audit

Audit date: 2026-09-06. This is a read-only inspection of the already collected
pilot rows 0-9, not an execution experiment or a claim that replay is impossible.
No new data, source checkout, container image, package, or model call was acquired.

## Scope and decision

The ten raw responses are in `evidence/trace-pilot-1/rows/`; their exact byte
hashes and acquisition outcomes are in `evidence/trace-pilot-1/collection.json`.
The separately generated `evidence/trace-pilot-analysis-v1.json` validates all
ten episodes and reports the conservative syntactic analysis. The frozen
analyzer SHA-256 is
`89682c29f2d8720f6191325d91c5798a10d3f05ac3f7c26741eb784f9c531352`.

None of these episodes concerns packaging, pycparser, colorama, or click, the
four already prepared short-subject environments. Their repositories are
sdf-xarray, edk2-pytool-library, python-androidtv, linopy, numpydoc, geopandas,
tools-python, pygmt, kafka-python, and tda-api. No faithful pilot using only
those four existing subject checkouts/dependency environments has been
established. This does not prove that a small compatible environment could
not be prepared later.

All ten trajectory rows omit a `base_commit` field; they provide an issue
identity and final model patch instead. Initial source must be bound through
the paired SWE-rebench task metadata and independently checked. Partial file
views and a final patch are not a complete sequence of source snapshots.
None of these ten raw traces mentions a `.zerorun` declaration. Their newly
created files therefore must not be assumed covered by any reviewed ZeroRun
input profile. These are missing replay prerequisites, not findings of
incorrect behavior by the recorded agent.

## Three manually checked examples

Call numbers below are one-based within the flattened recorded tool-call
sequence. The machine-readable analysis uses zero-based `call_index`.

### 1. Row 0: installation changes between identical commands

Task: `PlasmaFAIR__sdf-xarray-24`, raw `row-00000.json`.

**Recorded evidence.** The same full pytest command appears at calls 8 and 10:
`python -m pytest tests/ -v`, preceded by the same repository `cd`.
Call 8 (`chatcmpl-tool-e2d2bd64384f4c8cb72fa11e2c0e866c`) has recorded shell
exit 2. Between them, call 9
(`chatcmpl-tool-d15e7d95e4ba4a9885105dcf4218a29e`) runs `pip install -e .`
and records a successful installation. Call 10
(`chatcmpl-tool-29bb6124f938468eba1b2594d79b6ac8`) has exit 1 and an original
OpenHands observation-truncation marker.

Call 17 (`chatcmpl-tool-e07c30fcd84145dcaeac73d0e75c37f2`) creates a new
repository-root `reproduce_issue.py`. Later call 20
(`chatcmpl-tool-e1031f6fc2a84db780ae383edea43b7b`) fails to import the build
backend, and call 21 (`chatcmpl-tool-feb9a164aa464851965d29d04ff38857`)
installs scikit-build-core. Call 32
(`chatcmpl-tool-477edaadc37844948de4c593fa538b08`) runs `pip install netcdf4`;
its observation explicitly records downloading and installing NetCDF4,
cftime, and certifi packages.

**Replay implication, not a measured result.** Repository-text equality alone
cannot represent this environment-changing sequence. Faithful offline replay
would need the initial installed environment, exact installation artifacts,
build effects, and complete generated-file state. Substituting our existing
four-library image or silently omitting these installations would be an
adapted workload, not the recorded episode. Identical command text here is
not evidence of valid reuse.

### 2. Row 2: failed edits, generated root inputs, and feedback-directed changes

Task: `JeffLIrion__python-androidtv-351`, raw `row-00002.json`.

**Recorded evidence.** Call 39
(`chatcmpl-tool-c5691196744e4149b49089897f18eaa7`) attempts to replace text in
the generated `comprehensive_test.py`; its response explicitly says the
replacement was not performed. Call 53
(`chatcmpl-tool-8f352db7d6b8465b8b933cd494ac28ee`) successfully creates a new
repository-root `test_broken_pipe_fix.py`. Call 54
(`chatcmpl-tool-cfe1d94175f549a4a72d68319564ee3f`) runs pytest on that file
and records two failed tests and shell exit 1. After viewing source, the
assistant explains that the current matching condition is too generic, and
call 56 (`chatcmpl-tool-ddd446fa7c534195b37043c49724db97`) successfully
changes `androidtv/basetv/basetv.py`.

The exact targeted pytest command repeats at calls 50/59 and 52/62. Their
prior/current IDs are respectively
`chatcmpl-tool-f102825339c947bfaabde72f650332e3` /
`chatcmpl-tool-39e79e5f1a844807827cea19e6ec8451`, and
`chatcmpl-tool-21ca90dce236432eaf393868d79125ec` /
`chatcmpl-tool-0dc8003705314a989ea44555529dfc1d`.
Both pairs cross the successful source edit at call 56 and the generated-file
creation at call 53.

**Replay implication, not a measured result.** Treating every requested edit
as a successful mutation would misclassify this sequence: the recorded failed
replacement is a failed/no-change event, not a new source state. Newly created
root inputs need explicit closure review; they cannot be silently omitted
to increase a reuse count. The observed failure/diagnosis/edit sequence also
shows why freezing subsequent actions is only an open-loop tool replay:
it does not demonstrate that an interactive agent would make the same later
choices after receiving a substituted or reformatted tool response. Preserving
test outcomes and preserving the entire agent-visible response are different
requirements.

### 3. Row 1: shell pipelines change output and status semantics

Task: `tianocore__edk2-pytool-library-372`, raw `row-00001.json`.

**Recorded evidence.** Call 7
(`chatcmpl-tool-88721aa1183d4281b57a8897b106c8bd`) runs a pytest suite piped
to `head -30`. The observation contains a BrokenPipe warning but records
shell exit zero. Call 43
(`chatcmpl-tool-46641d8740fc4924a10e1a2151821972`) runs two pytest files with
output piped to `tail -10`. Its recorded summary is one failed, 38 passed,
and two skipped; the enclosing shell again reports exit zero.

**Replay implication, not a measured result.** Replacing these compound
commands with direct pytest would alter the observable contract. Using only
the enclosing return code would lose the recorded test failure. A proper
replay must decide whether it preserves pipeline behavior, full pytest
outcomes, or both, and independently validate that choice. The current parser
counts these as pytest mentions but excludes them from supported direct
pytest signatures; it retains shell status and test-summary evidence
separately.

## What is established, and what would enable execution later

The pilot establishes access to genuine recorded AI coding workflows and
identifies concrete state, closure, and response-semantics requirements.
All ten episodes remain in the observational denominator. Eleven original
tool observations are marked clipped; clipping is not silently treated as
a complete transcript or fresh node inventory. No recorded test timing is
used as a counterfactual cache speedup.

A future executable pilot can remain small: bind exact initial source and an
available compatible environment; reconstruct successful edits and generated
files while honoring failed operations; review or refuse every other shell
effect; freeze complete input declarations; and compare each replayed request
with an independently fresh execution. If its environment, commands, or
outputs are adapted, name the adaptation and limit the claim to that workload.
No recruited users or new model inference are necessary for that limited
tool-execution experiment. The present audit does not supply its results.
