# Reviewer decision memo: bounded SoftwareX strengthening

Prepared 7 September 2026, 01:03 UTC. This memo is a prospective recommendation,
not a record of new experiments, an editorial decision, or an acceptance forecast.
It does not authorize execution on third-party repositories.

## Decision

Prioritize a **model-assisted demonstration of the result contract**, then a
cleanly executed first-use walkthrough. This can resolve the conspicuous gap
between an AI-tool title and the previously incomplete model-backed lifecycle.
It only partially addresses usefulness: natural reuse frequency, complete-agent
benefit, independent adoption and comparative superiority remain unmeasured.
Keep the existing software direction and frozen runtime. A new algorithm or
broad benchmark is not needed to make this bounded contribution clearer.

## Three reviewer objections and the strongest available response

1. **Does an actual client use this interface appropriately?** The original
   live Codex trial stopped at `doctor`; the successful correction was non-model.
   A new, separately frozen run should exercise the original authorized synthetic
   fixture with actual model tool calls and independently scored interpretations.
   Preserve the original failure and label the new run controlled integration,
   not an autonomous issue-solving benchmark. Do not silently authorize any of
   the real-library repositories under the earlier fixture-only permission.
2. **Why use it instead of ordinary pytest or an existing cache?** Demonstrate
   the concrete decision boundary: a status-only request may consume an identified
   prior success, whereas a request for fresh diagnostics must obtain fresh
   execution. A model must not invent stdout or test counts from a hit. Ordinary
   pytest already supplies fresh execution and diagnostics; it is not an unsafe
   comparator. Do not implement a deliberately defective command-only cache and
   portray its failure as superiority over established systems.
3. **Can a reviewer reach that result without reconstructing the development
   history?** Execute one short, complete laboratory quickstart using an external
   installation, the known pinned image, an explicit authority handoff and exact
   inputs. Record prerequisites, commands, elapsed setup time, actual statuses and
   any repair. The custom `ZERORUN_TRUST_ROOT` route is operator-managed; frozen
   `init --codex` rejects nonempty `env`/`env_vars`. Do not describe documentation
   of that limitation as a runtime integration fix.

## Highest-value next-hour evidence

Freeze the case list and scoring before calling a model. Prefer a small complete
set over an arbitrary number of superficially different cases:

| Claim | Controlled observation required | Failure interpretation |
| --- | --- | --- |
| Client can consume fresh success | Actual MCP fresh-success response and model classification as fresh | Do not count tool success alone as model interpretation |
| Client identifies prior success | Actual `HIT_REUSED`; model marks no fresh execution and no replayed output | A claim of newly executed tests fails the case |
| Diagnostic demand changes the decision | Start from reused status, ask for fresh diagnostic evidence; model requests approved fresh verification before claiming fresh evidence | Fabricated logs/counts or accepting prior status as new diagnostics fails |
| Changed/failing state is not stale success | An explicitly permitted fixture state change; actual fresh failure/refusal; model does not report a passed test | Stop and investigate an incorrect accepted success |
| Authority is an operational prerequisite | Missing-authority control refuses; explicitly configured fixture authority enables the allowed lifecycle | No self-authorization, copied production authority, or weaker eligibility |

Use structured semantic decisions, a fixed schema, actual tool results and
independent fresh checks. Retain all model messages; additional harmless
commentary is not intrinsically a wrong test interpretation. Do not give the
model the expected answer for each case. Disclose the generic interface
instructions supplied to it. Separate protocol violations, tool failures,
semantic mistakes and fresh-result disagreement. Never replace failed cases with
new ones or pool a rerun into the original experiment's denominator.

The strongest fresh-diagnostic test concerns **provenance**, not a claimed model
intelligence advance: the result-only hit lacks the old transcript. Fresh MCP
responses also contain bounded tails, so the test must not demand or claim a
complete untruncated transcript. Record tool-only time and complete model-turn
time separately. A single controlled sequence cannot establish agent speedup.

## What the frozen real-library evidence can honestly add

The source-bound operating-region analysis already contains all 24 completed
blocks, 168 requests, 96 hits and 72 non-hits. The following arithmetic means
are read from `generated/operating-region-v1.json`; they are descriptive, not new
measurements or estimates of natural request frequency.

| Subject | Direct hit-category time (s) | Reuse hit time (s) | Extra reuse non-hit cost (s) | Complete direct/reuse ratio |
| --- | ---: | ---: | ---: | ---: |
| Click | 7.361 | 1.817 | 6.573 | 1.054 |
| pycparser | 7.062 | 2.265 | 8.376 | 0.890 |
| Colorama | 7.152 | 2.171 | 6.417 | 1.014 |
| Packaging | 7.413 | 1.729 | 6.963 | 1.038 |

Thus hits avoid about 4.80–5.68 seconds relative to these direct helper calls,
while non-hits add about 6.42–8.38 seconds. The imposed 4/7 hit share, VM/Docker
control costs and successful/failing non-hit mix matter. The article's existing
54.25–63.58% crossovers already express this tradeoff. Adding another favorable
curve, hit-only headline or selected library would not strengthen the empirical
claim. Keep all subjects, unfavorable blocks, interrupted costs and unavailable
setup data. These are not measurements of pure pytest time or model workflow time.

The existing public-trajectory audit records 120 exact-command repeat pairs,
all with intervening barriers; it does not establish 120 eligible hits. The
purposively selected django-environ restoration has four controlled requests and
one reused success agreeing with a fresh 14-test outcome. Its changed-state
15-test request is extra instrumentation, not an original agent action. These
records support the need for state identity and one restoration mechanism, not
its prevalence. A new no-execution presentation may pair **all four** preserved
case outputs with the client decisions, but the existing 14-case scripted
consumer already substantially covers that result; an actual model-mediated
diagnostic decision is a higher-value extension.

## Completion priorities and exclusions

Within the four-hour strengthening window, reserve at least the final 30 minutes
for claim reconciliation, exact source/evidence bindings, archive regeneration
and visual PDF verification. Update the manuscript only after outcomes are
available. The final Impact section should say who can use the demonstrated
components, for which requirements, and when direct testing is preferable.

Do not spend this window on another literature-contract table, generic regression
test-count increase, 100-repository registration rerun, new platform, learned cache
policy, synthetic claim of independent users, or 5x/50% performance target.
Existing artifact verification and adverse evidence should remain intact.

A completed controlled model example plus a tested first-use path removes
specific objections. It does not turn one fixture into a deployment study,
independently validate commercial demand, establish a new caching algorithm,
or supply a numerical probability of journal acceptance.

## Materials inspected

- `paper/main.tex`, `RELATED_SYSTEMS.md`, `OPERATING_GUIDE.md`.
- `OPERATING_REGION.md`, `analyze_operating_region.py`, and
  `generated/operating-region-v1.json`.
- `client_conformance.py` and its fixed four preserved positive source records.
- `generated/state-rejoin-review.json`, preserved restoration/failure outputs,
  and `research/sqj/strengthening/trace_analysis.py`.
- `research/sqj/COST_MODEL_NOTE.md`.

No runtime, manuscript, builder, VM or model was modified or executed to prepare
this memo. Reading and arithmetic summarization are distinct from a new
experimental verification of the archived results.
