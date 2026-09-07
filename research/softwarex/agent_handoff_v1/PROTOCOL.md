# Prospective test-assisted coding-agent producers v1

This is separate from the controlled ZeroRun handoff experiment. Six real Codex
CLI issue-fix sessions receive public issue text, the immutable base source, and
the benchmark's public test patch. The reference source fix is withheld from
their prompts and workspaces. This is test-assisted issue fixing, not a
SWE-bench leaderboard evaluation. Normal workspace-write permits host reads;
withholding is not claimed to be a technical barrier against arbitrary host
read access. No ZeroRun server, cache authority, or reused test result is made
available to these producer sessions.

Before outcomes, choose the first six repositories in the main acquisition
ledger and one case per repository by SHA-256 of
`zerorun-agent-handoff-v1:repo:case_id:base_commit`. Retain unavailable chosen
cases; do not substitute other tasks. The two cases in the separate acquisition
pilot ledger are disjoint and remain pilot-only. Freeze the selection, exact
study/helper sources, input-ledger bytes, requested model, client installation
identity, and limits before any model invocation. Prepare each workspace using
safe archive extraction and the test patch ONLY; never use `make_state`, which
applies the reference source fix. Write the selected plan before inert
preparation, then freeze each preparation-receipt hash before model execution.

Request `gpt-6-astra`, reasoning effort `medium`, with Codex CLI 0.153.3. Preserve
the distinction between the requested model and any backing-model identifier
actually disclosed in raw events. Use `--ephemeral --ignore-user-config
--ignore-rules --strict-config --sandbox workspace-write --json`, approval
policy `never`, no MCP servers, no plugins/hooks/apps/browser/multi-agent tools,
and no sandbox network access. Keep ordinary file editing and shell tools.
Ignore repository instruction/configuration layers. Do not broaden permissions
if a command is refused. Authentication is supplied externally by the operator
only after preparation; neither the harness nor the model should read or emit
credential contents. The operator removes temporary authentication afterwards.

Each selected case gets one invocation, at most 600 seconds and 8 MiB captured
per stdout/stderr stream. A truncation or timeout remains incomplete, with the
bounded raw output retained. No automatic retry, feedback-based replacement,
reference-fix reveal, network/package installation, or second repair session.
Sessions run in their selected order. A prohibited tool or protected-file
change stops later producer sessions; ordinary unresolved issues, unavailable
dependencies, test failures, or timeouts are retained and do not select away
later cases.

The agent may change Python source only, outside tests, dependency/configuration
files, integration/authority state, and Git metadata. Preserve the supplied test
patch and all other protected bytes. Before/after full source inventories,
protected-file comparisons, Git metadata bindings, final source snapshots, raw
JSONL, exact prompt/command, actual client version, final message, and proposed
patch are recorded. An empty patch, incomplete output, modified test/config,
and a model's unverified success claim are not silently counted as a solved
issue. All prompts identify repository content as untrusted task data.

The offered validation command uses an already-installed external Python and
fresh pytest, with bytecode/cache-provider writes disabled. If dependencies or
the sandbox prevent this, the model reports the limitation without installing
anything or using cached status. Root then evaluates final source states with
the independently recorded pinned-container fresh oracle, keeping node outcomes
and the unchanged supplied tests. Producer completion and source-scope checks
do not establish patch correctness; oracle results are separate evidence.
The operator-provided exploratory test interpreter is CPython 3.10.12 with
pytest 9.1.1; the authoritative fresh-container oracle separately uses
CPython 3.12.14. These are different environments, not interchangeable results.

Any later ZeroRun handoff on these final states is a controlled post-agent
experiment, not a natural ZeroRun hit rate or avoided agent validation. Any
read-only receipt-consumer model evaluation likewise has its own prospective
protocol and denominator. Do not claim end-to-end agent acceleration, universal
correctness, independent users, or acceptance odds from this study.
