# Prospective corrected Codex application experiment (v2)

This protocol is a new experiment. It does not amend, replace, or reclassify the
failed `live-client-v1` model trial. Freeze this file, the runner, validator, and
offline tests before the first model call; retain their exact SHA-256 inventory
and every resulting attempt, including startup failures. No retries until pass.

## Scope and identities

Use public source commit `ebf2884df12573d63f45813200e0675288d12096` and its
`PUBLIC_RELEASE_MANIFEST.json` SHA-256
`f7a01e6a16f32022ce686a4a213e073c83a8f77de82df0c9df357c9c31ab6564`.
The installed ZeroRun 0.5.1 runtime must match all 36 public Python files exactly
before and after execution. No runtime or cache-eligibility changes are allowed.
The live trial may use the existing byte-verified installation; the clean-install
quickstart is a separate experiment and must not be represented as the same
installation. Codex's existing non-authorizing server registration remains
unchanged; each experiment turn overrides configuration prospectively.
The authenticated Codex installation retains its original installer receipt and
source provenance separately from this experiment's public source commit.
The receipt must validate that installed Codex, native binary, and Node bytes
match that authenticated installer record, and retain the precise CLI version.
No model override: use the account's default model and report available identity
metadata without inventing a more specific model identity than the CLI records.

Only the original harness-built two-file deterministic synthetic fixture is
authorized. The operator's explicit synthetic-fixture approval covers a fresh,
isolated, exact-manifest authority record outside that fixture. It does not
authorize any caller-provided or real research repository. The OCI image must
already be installed, digest-pinned, and unchanged. No runtime acquisition.
The model receives only synthetic paths, request/result metadata, and controlled
interpretation fixtures. It receives no credentials or authority key.

## Corrected configuration, not altered authorization

Each live Codex invocation supplies the operator-managed explicit setting
`mcp_servers.zerorun.env.ZERORUN_TRUST_ROOT` for the same isolated external
authority used by the harness. No other host variables are forwarded by this
setting. This is not a change to the managed `init --codex` path. The original
v1 failure and subsequent non-model diagnostic remain separate evidence.
Official configuration reference:
https://learn.chatgpt.com/docs/extend/mcp?surface=cli

## Fixed experiment and stopping rules

1. Four separate ephemeral Codex turns in order: `doctor`, `run_tests` with
   `verify=false`, the same request again, then `run_tests` with `verify=true`.
   Each turn permits exactly one ZeroRun MCP call with the sealed arguments.
   Expected tool outcomes are CACHEABLE readiness, MISS_EXECUTED, HIT_REUSED,
   and VERIFY_MATCH, with one common cache key. These expected outcomes are
   grader-only and are not supplied as answers in the model prompt.
2. Prompts ask for a JSON judgment of the actual result, not an expected success
   marker. Generic field definitions and the response schema are supplied.
   Record pre-call commentary without mistaking it for the final answer. Require
   exactly one final JSON message after the tool completion. Report protocol,
   tool-result, and final-response semantic correctness separately.
3. Stop the live lifecycle at the first failure; keep its complete bounded raw
   stdout/stderr and analysis. Never retry a failed stage. A failed result does
   not become a successful trial because its format was valid.
4. After a complete lifecycle, run two decision turns with only `run_tests`
   available. One natural-language request explicitly accepts previous success;
   the other requires freshly executed validation. Do not prescribe `verify`
   or the expected status in these prompts. The independent grader requires
   `verify=false`/HIT_REUSED and `verify=true`/VERIFY_MATCH respectively. Record
   `task` and `root` explicitly; omitted `verify` is accepted as its documented
   false default only in the status-only decision turn. All core requests remain
   exact, and unknown/extra arguments are rejected. Record
   the actual chosen arguments and final interpretation. Run the original
   fixture directly in the same pinned, network-disabled, read-only container
   after each reused or verified result as a separate fresh oracle. These direct
   calls are not model decisions or a timing baseline.
5. After successful live turns, run exactly six additional isolated no-tool
   Codex interpretation turns. Cases 1--3 use the actual captured fresh, reused,
   and verified results. Cases 4--5 are explicitly controlled fresh-failure and
   refusal fixtures. Case 6 reuses the actual hit but states that a fresh
   diagnostic transcript is needed. This is not six new real tool executions.
   Use a fixed order and retain all cases, including wrong answers. These cases
   have no tool access and request only a structured interpretation.
6. No shell/file/browser/planning/collaboration/other tool calls; any such call,
   duplicate MCP request, source drift, or execution-directory mutation is a
   protocol failure. Stop all remaining model work on such a boundary failure.
   Interpretation semantic errors alone are recorded and the remaining fixed
   cases continue. No corrective examples, prompts, or gold labels are fed back.
7. Per-turn timeout: 120 seconds; maximum 2 MiB per stdout/stderr stream; maximum
   twelve model turns; no sampling for a favorable result. Authentication, capacity,
   startup, timeout, and malformed output failures remain failures/unknowns.

## Evidence, grading, and permissible claims

Before/after bind the source, harness, protocol, installed package, Codex
installation, fixture, and external-authority structure (never the secret key).
Retain exact commands, prompts, raw JSONL, return code, timeout/truncation flags,
   tool calls, usage metadata, final messages, and re-computable judgments. Write
an immutable freeze record and a new receipt without overwriting prior results.
The strict read-only validator reconstructs event and semantic assessments from
raw bytes and checks all source and payload bindings.
The authenticated Codex JSONL schema can omit the MCP `isError` field: in that
case derive error from the completed/failed call status while checking the
structured status/exit fields independently. If either error spelling is
present it must be boolean, and two spellings must agree. Interpretation inputs
always retain an explicit boolean error flag.

A successful result supports only one synthetic, operator-configured Codex
application lifecycle, two bounded model-selected validation decisions, and six
bounded contract-interpretation cases. It is not
an autonomous coding benchmark, user study, independent external reproduction,
population accuracy estimate, production deployment, speedup measurement,
commercial-viability proof, or journal-acceptance probability. Report all v1 and
v2 attempts together with their different prospective configurations.
