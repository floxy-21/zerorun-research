# Actual agent application with ZeroRun 0.5.3

This application contains **six actual model producer attempts, five independently
verified fixes, five eligible consumer cases and fifteen actual model consumer
stages**. It is separate from the scripted, reference-patch 24-case timing
cohorts, synthetic interface checks and the single-case amortization follow-up.
It does not establish natural reuse frequency, population reliability or net
agent acceleration.

| Original selected case | Independent final-state target result | Primary consumer eligibility |
| --- | --- | --- |
| `eliben__pycparser-236` | 46 passed | Eligible |
| `joke2k__django-environ-174` | 114 passed | Eligible |
| `tobymao__sqlglot-3182` | 97 passed, 2 failed | Ineligible |
| `terryyin__lizard-241` | 21 passed | Eligible |
| `eyeseast__python-frontmatter-56` | 18 passed | Eligible |
| `joshtemple__lkml-87` | 49 passed | Eligible |

The independently executed baseline failed in every selected case. The five
successful final patches repaired those baseline regressions with the same
collected target nodes and unchanged supplied tests. Actual model-produced
snapshots were used; reference production solutions were not applied to them.
The separately assisted SQLGlot supplement preserves the unsuccessful original
patch, rechecks its two failures, and obtains 99 passes after an additional
production-only repair. It includes no new model invocation or consumer and
does **not** change the primary result to six verified model fixes.

Both producer and consumer requests specified `gpt-6-astra`, effort `medium`,
through pinned Codex CLI 0.153.3. The captured event streams did not supply an
independently observed backend model identifier. Producer exploration used
Python 3.10.12 and pytest 8.4.2. Independent oracles and consumer test execution
used the existing Python 3.10.21/pytest 8.4.2 dependency image, digest
`sha256:78039048251ecf889454509c6ef203f922d9dae9fa3b570a4ade3a9d4b552465`.
The consumer server was the externally installed 0.5.3 wheel, SHA-256
`dbac896587365b010b6c1c2c5913e1f75e79da99cfdb00c407e67f3442deddb9`.
This used an existing VM and image; it was not a fresh operating-system or
public-registry replication.

## Qualification and observed consumer behavior

An AI-assisted inspection bound both the actual final state and the exact
original-production restoration for each eligible case. The manifests cover
the complete literal top-level input union, including tests, fixtures and
configuration. The command, dependency image, result-only contract, absence of
forwarded host environment and disposable container effects were inspected.
Qualification was scoped to these two states; changes or new uncertainty
require renewed review. `human_review_seconds` is null: expert review time and
independent human qualification were not measured or inferred from
`closure_reviewed`.

The operator created exact external manifest authority under the authorized
laboratory scope. Five actual installed-server `MISS_EXECUTED` seeds were
independently reconciled against raw STDIO, source, manifest and 36-module
runtime identities. The consumer models could not create authority or edit
files; their enabled tools were `list_tasks` and `run_tests`. Each stage had one
180-second attempt, with no outcome-based retry. The stage requests were:

1. “Report the validation evidence available for this source state and explain
   what it does and does not establish.”
2. “I need validation executed anew now, with the fresh diagnostics that are
   available. Report the outcome and the limits of those diagnostics.”
3. “The operator has restored the declared production files to the recorded
   original issue state. Report the validation evidence for this current state
   and explain what it establishes.”

The full prompts also bind the root/task and include the exact packaged skill.
They remain in each plan's `prompts/` directory. Only the predeclared production
file changed before stage three; protected tests and manifest bytes remained
identical. That restoration is a controlled intervention, not an autonomous
model edit or an observation of natural task demand.

All fifteen model processes and source/runtime boundary checks completed.
The five available-evidence calls returned `HIT_REUSED`; the five fresh requests
returned `VERIFY_MATCH`; the five restored-state calls carried `MISS_FAILED`
payloads with exit code 1. These are execution observations. This guide does
not by itself grade the natural-language responses. The separately hash-bound
internal AI-assisted review inspected all fifteen messages and confirmed their
core status, freshness and coverage-limit interpretations. Thirteen messages
also stated wire `isError` values omitted from the retained client events;
these details are unverifiable from the archive, not established false. The
other two messages omitted that detail. This is not independent human review
or a claim that every detail of every model response was verified.

## Preserved failures and representation limits

The first oracle adapter lacked inert Git metadata in reconstructed final
snapshots. Its failed final-state checks and completed baselines remain in
`agent-application-053-oracles-v1`; corrected v2 oracles are separate.

The first consumer transport package omitted a transitive import and stopped
before execution. A later complete preparation produced five valid seeds but
five pre-model plan-reader refusals: text reading normalized the packaged
skill's CRLF newlines. The original package, raw failures, seeds and helpers
remain unchanged in `agent-application-053-consumers-v1`. Versioned v2 plans
compare exact prompt bytes and execute only the fifteen previously unattempted
stages, using the existing seeds, installation and authority. See the
[prelaunch correction](agent_application_053/CONSUMER_PRELAUNCH_CORRECTION.md)
and [reader correction](agent_application_053/CONSUMER_V2_READER_CORRECTION.md).

Codex records test-failure tool results with client status `failed`, intact
matching structured/text payloads and `error=null`, while omitting a wire
`isError` field. The frozen analyzer conservatively leaves those five results
unclassified. The separate
[additive audit](agent_application_053/consumer_event_audit_v1.py) checks this
specific representation and records five fresh failures. It explicitly records
that the wire error flag was not captured, preserves the original analysis and
does not rewrite raw responses or infer model-interpretation success.

## Recorded cost ledger

Values below are seconds, rounded to six decimals. JSON records retain the
original precision. Contained rows overlap their enclosing campaign intervals
and must not be added again.

| Recorded interval | Seconds | Scope |
| --- | ---: | --- |
| Six original producer-session outer intervals | 377.000885 | Actual producer receipts |
| Failed v1 oracle attempt: six baselines | 32.711609 | Preserved earlier executions |
| Failed v1 oracle attempt: six final operations | 0.536149 | Preparation refusals, not final tests |
| Corrected v2 oracle: six baselines | 32.551730 | Independent original-state checks |
| Corrected v2 oracle: six finals | 24.467691 | Independent actual-patch checks |
| Corrected v2 source reconstruction | 4.801641 | Separate reconstruction intervals |
| Original consumer preparation campaign | 61.786740 | Installation, seeds, probes and five plan refusals |
| Five seed calls, contained in preparation | 31.312697 | Actual installed MCP executions |
| Versioned consumer continuation | 456.452755 | New plan checks and fifteen model stages |
| Fifteen model calls, contained in continuation | 415.911567 | Recorded client-process intervals |
| SQLGlot supplement: original / assisted repair oracles | 11.356494 / 5.560977 | Separate follow-up, no model calls |

These intervals are not an end-to-end saving estimate. They do not measure
operator inspection, repair effort, waiting gaps or every infrastructure step.
The compatible image's previously recorded 82.289365-second construction was
not incurred again. Model-call durations are not isolated server latency or
model inference time.

## Offline inspection

From the complete public source checkout, use Python 3.12–3.14. These commands
read saved files and print JSON; they do not invoke models, Docker or authority
creation. Keep `-B` to avoid writing bytecode into the sealed package.

```text
python -B -m research.softwarex.agent_application_053.oracles_v2 verify research/softwarex/evidence/agent-application-053-oracles-v2/record-only
python -B -m research.softwarex.agent_application_053.consumer_campaign check research/softwarex/evidence/agent-application-053-consumers-v1/record-only
python -B -m research.softwarex.agent_application_053.consumer_continuation_v2 check research/softwarex/evidence/agent-application-053-consumers-v2/record-only
python -B -m research.softwarex.sqlglot_supplement_053 research/softwarex/evidence/agent-application-053-sqlglot-supplement-v1/record-only
```

The continuation contains a byte-identical `prior/` copy of the original sealed
consumer preparation; this is preservation, not another sample. Its own
`cases/<case_id>/plan/attempts/<stage>/model.stdout.log` files contain the fifteen
actual consumer transcripts. `qualification.json`, `plan.json`, seed records,
raw process streams, restoration inventories and manifests expose the bindings.
The continuation manifest SHA-256 is
`196374d43964868f02130adc75cf305d529c474a4693329060951915f8c5cd8b`.

For example, independently inspect the first restored-result representation:

```text
python -B -m research.softwarex.agent_application_053.consumer_event_audit_v1 --raw research/softwarex/evidence/agent-application-053-consumers-v2/record-only/cases/eliben__pycparser-236/plan/attempts/restored/model.stdout.log --root /home/floxy/zerorun-agent-application-053-consumers-20260908-v1/workspaces/eliben__pycparser-236 --task agent-tests --stage restored
```

The root argument above is the recorded identity; offline inspection does not
access that VM path. After the final interpretation review and aggregate are
present, `python -B -m research.softwarex.build_agent_application_evidence --check`
reconciles the combined summary without changing it. Use the complete
[submission verification procedure](VERIFY_SUBMISSION.md) for package-wide
integrity; these focused commands do not replace it.
