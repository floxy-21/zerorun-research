# ZeroRun manifest v2 reference

This reference covers the evaluated **whole-task, result-only** mode of ZeroRun 0.5.1. Its manifest is executable policy, not a declaration an AI agent may approve automatically. See the [operating guide](OPERATING_GUIDE.md) for installation, exact-byte operator authorization, CLI/MCP use, and response handling.

## Example shape, not an approved configuration

The following JSON is an illustrative shape for an **already reviewed** pytest task. It is not a runnable supplied example: replace the image placeholder, choose existing target paths, and review the complete dependency closure before writing the true assertions. Do not paste `closure_reviewed: true` into an unreviewed project.

```json
{
  "version": 2,
  "tasks": {
    "tests": {
      "command": ["python", "-m", "pytest", "-p", "no:cacheprovider", "tests"],
      "inputs": ["src", "tests", "pyproject.toml", "requirements.txt"],
      "input_symbols": [],
      "outputs": [],
      "env": [],
      "cacheable": true,
      "unsafe_effects": [],
      "cache_streams": false,
      "result_only": true,
      "closure_reviewed": true,
      "image": "registry.example/reviewed-tests@sha256:0000000000000000000000000000000000000000000000000000000000000000",
      "platform": "linux/amd64"
    }
  }
}
```

The placeholder image does not exist by implication and is not a validated runtime. Use the exact digest of an operator-reviewed image containing Python, pytest, the needed project dependencies, and the intended import behavior. Listing a lock file as an input does not install it in the container. If this project has root-level `conftest.py`, helper modules, generated source, schemas, or data, account for those too. A manifest copied from another repository cannot establish this project's closure.

## Top-level fields

| Field | Meaning |
| --- | --- |
| `version` | Set explicitly to integer `2`. Legacy v1 is CLI-only and outside the evaluated result-only/MCP contract. |
| `tasks` | A nonempty object mapping nonempty task names to task objects. CLI and MCP requests use those exact names. |

The manifest must be a stable regular UTF-8 JSON file, not a link or junction. Duplicate JSON member names and malformed or excessively large/nested input are rejected. Do not depend on undocumented extra top-level members. Unknown **task** members are configuration errors.

## Task fields

| Field | Whole-task v2 meaning and requirement |
| --- | --- |
| `command` | Required nonempty array of nonempty argv strings. ZeroRun does not add shell parsing; an explicitly named shell is still executable policy that needs review. |
| `inputs` | Required nonempty array of repository-relative files, directories, or supported patterns. Include the complete result-affecting project closure. Missing, escaped, linked, unsupported, or runtime-state inputs can cause refusal. A newly created undeclared file is not automatically discovered. |
| `input_symbols` | Optional symbol-projection selectors such as `relative.py::qualified.symbol`. Use `[]` for whole-file identity as evaluated here. A narrower symbol boundary needs separate semantic review and does not use the optimized whole-file hit path. |
| `outputs` | Required empty array. No generated artifacts are restored by v2 reuse. |
| `env` | Use `[]` for Codex/MCP: host environment forwarding is refused there. Direct CLI operators can name host variables; their presence/value fingerprints join identity and their values are explicitly forwarded to the container. Do not forward secrets to untrusted test code. |
| `cacheable` | Explicitly `true` to enable task-level reuse. `false` does not waive the rest of the v2 execution contract. |
| `unsafe_effects` | Must be empty for a cacheable task. Recognized disclosures are `network`, `clock`, `randomness`, `interactive`, `ipc`, `daemon`, and `external-write`; an effect cannot be hidden merely to make reuse eligible. |
| `cache_streams` | Explicitly `false` for the result-only contract. A hit does not replay saved stdout or stderr. |
| `result_only` | Required `true`. Callers must accept identified status reuse without outputs or a fresh test transcript. |
| `closure_reviewed` | Required `true` for v2 only after an actual review. This assertion is not inferred or proved by ZeroRun, nor does its presence establish operator identity. |
| `image` | Required immutable OCI reference `name@sha256:<64 lowercase hex>`. Runtime inspection must resolve the expected image and Linux/amd64 platform. A mutable tag alone is invalid. |
| `platform` | Exactly `linux/amd64` in this release. |
| `result_normalizer` | Not supported in v2; omit it. This legacy option does not enable transcript equivalence for result-only reuse. |

For normal MCP execution, the task image must already be present and `env` must be empty. Explicitly approved managed setup can acquire a runtime; a normal `run_tests` call cannot. The Docker client's own connection settings are bounded host configuration, not task-declared environment values. The selected daemon and its credentials remain under operator control.

## What the identity covers, and what it does not

The implementation combines declared input content and relevant metadata, the command and declared environment policy, and inspected runtime identity. The optimized eligible whole-file hit path checks the full fingerprint twice around the authenticated lookup. It avoids constructing an execution source copy on that path. A miss, explicit verification, or symbol-projection path retains fresh-execution staging.

These checks establish identity **within the declared contract**, not a proof of arbitrary Python behavior. They do not automatically discover external state, unknown dependencies, or nondeterminism. Two filesystem scans are not an atomic snapshot against arbitrary concurrent host mutations. A successful test run does not certify the closure.

Review at least:

- implementation and test files, helpers, fixtures, `conftest.py`, plugins, and bootstrap code;
- configuration, requirements/lock files, generated source inputs, static data, and schemas;
- import paths, interpreter/native/system dependencies, and optional features in the pinned image;
- all result-affecting environment, locale, hardware, time, randomness, network, IPC, subprocesses, daemon state, and interactive input;
- filesystem effects and whether any caller expects generated artifacts or a fresh transcript.

If a result depends on something outside the reviewed inputs, declared environment, and runtime identity, do not enable reuse. A result-only task may use isolated disposable temporary storage, but that does not make externally observable or nondeterministic effects reusable.

## Exact-byte authority is separate from manifest validity

Codex/MCP execution requires a matching external per-user authority receipt bound to the canonical checkout and SHA-256 of the **exact parsed manifest bytes**. Whitespace changes can therefore invalidate authorization even when the task's intended meaning is unchanged. An existing repository-local review record, generated candidate, copied cache entry, or `closure_reviewed: true` cannot create that authority.

The human operator independently computes and compares the digest, then separately runs:

```sh
zerorun --manifest .zerorun.json --json authorize --manifest-sha256 REVIEWED_MANIFEST_SHA256
```

This is an approval action, not a routine agent setup step. The direct CLI is a trusted-operator route and does not enforce the same MCP authorization gate on every command. Read the [operating guide](OPERATING_GUIDE.md#4-make-the-manual-authorization-decision) before using it.

Successful cache records also require authenticated provenance, and Git-tracked `.zerorun` runtime state is refused. Private keys and operator authority must stay outside shared artifacts. This boundary is not protection from arbitrary code deliberately executed as the same host user.

## Whole-task and pytest-node review are different

Whole-task reuse asks whether the **complete command** is a deterministic result-only computation under its declared identity. It does not require individual tests to be independent.

The optional `.zerorun-pytest.json` profile additionally governs which individual pytest nodes may be omitted. Its prepare/review/activate workflow requires closure-completeness and node-independence reviews tied to the exact candidate. Activation writes a profile but does not create external user authority. Codex/MCP node execution requires authorization of **both** the exact manifest and profile bytes. Follow the [separate activation procedure](OPERATING_GUIDE.md#7-optional-separately-reviewed-pytest-node-reuse); do not hand-author an active profile or let an agent approve it.

## Result interpretation

Use actual `status`, `exit_code`, and the MCP `isError` envelope together. `mode: "reuse"` labels the whole-task interface and appears on misses and failures too. Only `HIT_REUSED` identifies the reused-success result; `MISS_EXECUTED` is fresh execution and `VERIFY_MATCH` is a fresh comparison. Failed, refused, or uncertain requests are not successes. See the [response reference](OPERATING_GUIDE.md#interpret-results-not-reassuring-labels) for the complete distinctions and bounded diagnostic tails.

## Implementation references

In the public source layout, the corresponding implementation is in `src/zerorun/model.py`, `manifest.py`, `hermetic.py`, `oci.py`, `trust.py`, and `mcp.py`. The [source-bound reproduction guide](REPRODUCIBILITY.md) distinguishes the published source snapshot, the original experiment source archive, recorded observations, and fresh re-execution. This reference does not expand the supported platform or make a production reliability claim.
