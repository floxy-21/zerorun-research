# Bounded AI-client application results

For the current release, read [the actual ZeroRun 0.5.3 agent application](REAL_AGENT_APPLICATION_053_RESULTS.md): six original producer attempts, five verified fixes, five eligible consumer cases and fifteen actual consumer stages. Its internal review supports the core status, freshness and coverage-limit interpretations, while thirteen messages contain wire error-flag details not captured in the archive. The two remaining messages omit that detail. Original unsuccessful attempts and the separate SQLGlot repair retain their own denominators.

The sections below preserve the **historical 0.5.1 synthetic-fixture studies** and their original installation bindings; they are separate from that current real-repository application.

ZeroRun completed an actual Codex core lifecycle and, in a separately frozen API-guided demonstration, both model-selected validation actions and all six fixed interpretation cases. Earlier failures remain part of the evidence. These results show a usable result-only interface on the explicitly approved synthetic fixture; they are not autonomous coding-task, external-user, or workflow-speedup measurements.

## Complete attempt ledger

| Prospective record | Actual model turns | Observed outcome |
|---|---:|---|
| [V1 protocol](LIVE_CLIENT_PROTOCOL.md), [support amendment](LIVE_CLIENT_AMENDMENT_1.md), [receipt](evidence/live-client-v1/receipt.json) | 1 | Doctor reported `UNTRUSTED`; commentary also violated the original exact-message harness. No execution, reuse, or verification followed. |
| [V2 protocol](live_client_v2/PROTOCOL.md), [receipt](evidence/application-client-v2/receipt.json) | 2 | Doctor passed. The next `run_tests` attempt was refused by the client's approval policy before a server result. No test execution or fresh oracle followed. |
| [V3 protocol](live_client_v3/PROTOCOL.md), [receipt](evidence/application-client-v3/receipt.json) | 5 | Doctor, `MISS_EXECUTED`, `HIT_REUSED`, and `VERIFY_MATCH` passed, with two separate passing fresh oracles. The fifth turn supplied an unsupported argument; the server rejected it and the model reported refusal. The full plan stopped and did not pass. |
| [Guided protocol](guided_client_v1/PROTOCOL.md), [receipt](evidence/guided-client-v1/receipt.json) | 8: two choices plus six no-tool interpretations | Both model-selected calls, both separate fresh oracles, and all six interpretation cases passed. Two non-model setup tool calls plus discovery are additional setup, not model turns. |

V2 and V3's conservative final refusals do not turn an unavailable or rejected tool result into successful execution. Their uncompleted cases are not counted as passes. The records have different prospective configurations and stopping outcomes; they are not pooled as independent repetitions, and the guided outcome does not reclassify an earlier failed plan.

The earlier [five-exchange configuration diagnostic](evidence/live-client-v1/non-model-diagnostic.json), under its [own protocol](NON_MODEL_DIAGNOSTIC_PROTOCOL.md), is also retained. It used no model and established only the server-side effect of explicitly forwarding the operator's external authority location.

## What the guided demonstration did

The [client API card](CLIENT_API_CARD.md) was frozen as prospective release documentation before the trial and is published with this artifact. It gives allowed fields, defaults, and result semantics without supplying either decision's expected arguments or classification. The installed server's actual `tools/list` response is captured in setup. No runtime or tool-schema code was changed.

1. A fresh isolated copy of the original two-file fixture received operator-approved, fixture-only external authority. Actual STDIO setup captured discovery, a readiness `doctor` call, and one successful fresh seed call. The seed is non-model evidence.
2. For a status-only request that explicitly allowed prior success, the model selected a normal `run_tests` call and received `HIT_REUSED`. For a demand for freshly executed validation, it selected fresh verification and received `VERIFY_MATCH`. Each turn was limited to one permitted tool call and independently assessed against the actual response.
3. After each decision, a separate direct execution in the same digest-pinned, network-disabled, read-only container checked the fixture afresh. Both oracles agreed. Their executions are not model turns or avoided executions.
4. Six additional model turns had no tools: actual non-model fresh seed; actual model hit; actual model verification; a controlled fresh failure; a controlled refusal; and the actual hit when fresh diagnostics were required. All six final responses met the frozen semantic rules. The two controlled adverse inputs are synthetic, not observed server failures in this guided run.

The model distinguished prior success from fresh evidence and did not invent a diagnostic transcript in these cases. A fresh failing test can provide fresh evidence of failure; a protocol refusal does not establish fresh execution. Success on these fixed cases is neither a population accuracy estimate nor a causal finding that the API card caused improvement. There were no external participants, real issue-resolution tasks, or repeated independent agent runs.

## Identity, permission, and preservation

V1 and its diagnostic pin public source `681907860dc2ab9df70034f82a0025463d1fdec4`. V2, V3, and the guided trial pin `ebf2884df12573d63f45813200e0675288d12096`, manifest SHA-256 `f7a01e6a16f32022ce686a4a213e073c83a8f77de82df0c9df357c9c31ab6564`. All use the unchanged ZeroRun 0.5.1 runtime from `86f42289c2f59a74b1f642f0b20d1a26b3d55e57`; the later trials check the exact 36-file installed/source inventory. The later enclosing artifact manifest is not substituted for any experimental manifest.

The live client was the authenticated installation of Codex CLI 0.153.3. Its installer, executable, Node runtime, and source identities are recorded separately from the ZeroRun source pin. The selected CLI/account-default model name was not disclosed in the captured fields; no model identity is inferred.

V2 explicitly forwarded `ZERORUN_TRUST_ROOT` to the MCP server but retained automatic client tool approval. Before V3, the operator separately approved invocation-local `mcp_servers.zerorun.tools.run_tests.approval_mode="approve"` for the original isolated built-in fixture only. The guided trial retained that exact scope. Global approval settings, read-only Codex sandbox, disabled unrelated tools, runtime contract, and server authority validation were unchanged. No real repository was authorized and no authority key or login credential is published.

Each later study preserves its prospective freeze, start record, raw event log, prompts, actual tool results, and unchanged earlier source/receipt hashes. Stops and failures were retained; there was no automatic retry-until-pass loop. Reproduction requires new paths and the reproducer's own explicit permissions, not reuse of the author's authority. See the [operating instructions](OPERATING_GUIDE.md#inspect-or-reproduce-the-bounded-integration-experiments).

## Separate account-free public first use

The literal [public laboratory quickstart](QUICKSTART_LAB.md) passed from a fresh clone of `860675c041c5190dcbae0892d64c8ba82b257bb8` without intervention. Its [seven installation commands](evidence/quickstart-public-v1/install.json) took 18.645573 seconds; its [five actual STDIO stages](evidence/quickstart-public-v1/check.json) took 11.340818 seconds. These separate clocks exclude cloning, Docker setup, and researcher preparation. All 36 runtime files matched. This was a fresh installation distinct from the model experiment's installed environment, not a fresh model experiment.

The earlier [laboratory installation](evidence/quickstart-lab-v1/install.json) and [laboratory server check](evidence/quickstart-lab-v1/check.json) are retained separately. Neither quickstart used a model, account, or independent human participant. They demonstrate internal clean-install reproducibility, not developer adoption or production reliability.

## Verify and interpret

From the completed release root, with the documented research Python environment:

```sh
python -m research.softwarex.build_application_evidence --check
```

This read-only check recomputes [application-evidence-v1.json](generated/application-evidence-v1.json) from frozen validators and raw records, including earlier failures, separate model/non-model counts, exact source identities, and both quickstart records. It needs no model credentials or Docker and executes no recorded agent command. The [reproduction guide](REPRODUCIBILITY.md) lists the other evidence checks.

The supported contribution is an inspectable implementation and bounded application with explicit provenance, refusal, and fresh-validation semantics. The original fine-grained 5x/50% product gates remain unmet and unchanged; they are not SoftwareX manuscript claims. The new evidence does not establish AI task-completion gains, total workflow or token savings, universal correctness, economic impact, commercial demand, or a journal acceptance probability.
