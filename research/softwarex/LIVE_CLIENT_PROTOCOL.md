# Bounded live-client protocol, 6 September 2026

Frozen before live model invocation. This extension addresses functional client
integration, not autonomous issue resolution, performance, users, or market
demand. It is separate from the earlier Python-API restoration example and
100-repository registration/diagnostic smoke.

## Fixed scope

- Use the public research release at commit
  `681907860dc2ab9df70034f82a0025463d1fdec4`, whose runtime is the unchanged
  36-file ZeroRun 0.5.1 core from `86f42289c2f59a74b1f642f0b20d1a26b3d55e57`.
- Install that release in a new external Linux virtual environment; retain
  exact package comparisons performed by the original lifecycle harness.
- Use authenticated Codex CLI 0.153.3 archives, its existing default model,
  and the already-present Linux/amd64 CPython 3.12.14 image digest
  `sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef`.
- The VM's existing Node 12 installation is replaced for this isolated tool
  invocation by official Node 22.14.0, verified against the official HTTPS
  SHA256 inventory. Keep both installer receipts: the first identified Node
  12 before this pre-model prerequisite correction; the second binds the
  actual Node 22 executable used for the experiment. No live trial ran under
  the first installer receipt.
- Run the shipped synthetic lifecycle once: one `doctor` turn, then separate
  `run_tests` turns expected to return `MISS_EXECUTED`, `HIT_REUSED`, and
  `VERIFY_MATCH`. Each turn permits exactly one MCP call, no shell/file/web
  tools, and no retries. Bound each turn to 120 seconds.
- Authorization applies only to the original, byte-checked built-in fixture.
  Never authorize a real repository or use an agent-generated eligibility
  review. The user approved this explicitly described fixture-only experiment
  and transfer of its metadata to the existing Codex service by approving the
  final plan. No new purchase or paid API account is created.

## Public-layout adaptation

The shipped development-layout harness expects `root/zerorun`; the public
package deliberately uses `root/src/zerorun`. A separately versioned research
adapter redirects only the harness's source-package inventory root to `src`.
It first verifies the original harness/helper bytes and the public manifest,
then invokes the original inventory implementation. Installed-package equality,
authority, tool-call restrictions, exact statuses, source-drift checks, and
failure handling remain unchanged. The original files are not edited. The
adapter and its tests are published with their exact identities.

## Outcomes and stopping rules

Preserve the complete JSON receipt whether passing or failing. A passing result
requires all four original stages, one shared cache key, matching installed
runtime bytes, unchanged fixture/source identities, and exact expected statuses.
Record actual model/client/runtime identity and elapsed time, but do not treat
this constrained lifecycle as a performance experiment or an assessment of
agent understanding. The prompt requests a fixed completion marker, not a
free-form interpretation or a real coding task.

An installation or pre-model infrastructure failure may be corrected only in a
separate recorded attempt; preserve its original evidence and explain the
correction. A model/tool failure is retained as the experiment outcome, not
retried until green. No optional experiment starts after 9 p.m. local time.

## Credentials and reproduction

Official OpenAI headless-login guidance permits securely copying the user's
existing login cache to their VM over SSH. The temporary VM copy is owner-only,
outside all repositories, never printed or exported, and removed after use.
Reproduction requires the reviewer's own authorized Codex account; offline
inspection of the archived receipt requires no model account or credits.
See <https://learn.chatgpt.com/docs/auth> and
<https://learn.chatgpt.com/docs/non-interactive-mode>.
