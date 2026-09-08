# Operating ZeroRun 0.5.2

This guide describes the **public research release** and its shipped interfaces. Whole-task reuse returns an explicitly identified previous successful exit status. It does not reproduce the previous stdout/stderr, restore generated files, or assert that an AI agent will make the same decision as after fresh execution.

The commands below are **operator instructions, not evidence that they have been executed on your repository**. Names and paths in capitals or `/absolute/path/...` must be replaced. The [reproduction guide](REPRODUCIBILITY.md) identifies the recorded experiments and their limits separately.

First-use path: [install](#1-install-outside-the-repository-you-will-test), [inspect](#2-inspect-the-target-before-executing-anything), [configure](#3-configure-one-reviewed-whole-task-command), [review and authorize](#4-make-the-manual-authorization-decision), [run and verify](#5-run-identify-reuse-and-verify), then [connect the client](#6-connect-an-mcp-client-or-codex). The [tool and response reference](#the-seven-shipped-tools) and [optional node-level workflow](#7-optional-separately-reviewed-pytest-node-reuse) are separate.

To try the installed server without a Codex account or a real project, use the [current 0.5.2 Linux laboratory quickstart](QUICKSTART_052.md). It creates only the explicitly approved built-in synthetic fixture and records fresh-install and actual STDIO results separately. The retained `QUICKSTART_LAB.md` describes the historical 0.5.1 procedure; its frozen checker is not the current installation route.

For a deliberate new laboratory attempt after fixing a prerequisite, choose a new tools-environment directory and new installation and check receipt filenames. Preserve the previous attempt and record the intervention as described in the versioned guide; changing only the receipt filename does not make an existing environment new.

For submission review, run [complete offline verification](VERIFY_SUBMISSION.md) first, using CPython 3.12â€“3.14 and a pristine Git checkout. The extracted reviewer ZIP contains an earlier sealed inventory and its original documentation; use the current verification instructions rather than trying to run the complete final-snapshot verifier inside that extraction.

## 1. Install outside the repository you will test

CLI installation needs Python 3.10 or later. The demonstrated whole-task execution mode additionally requires a Linux/amd64 host with Git and access to an operator-controlled Docker daemon. Use a non-root account for the documented laboratory and permission-sensitive checks. Windows and macOS CLI installation is not evidence of working whole-task reuse on those hosts.

Use separate locations for the public source, installed tools, and target project. For example, on Linux:

```sh
git clone --depth 1 --branch softwarex-0.5.2-20260907-r2 https://github.com/floxy-21/zerorun-research.git /absolute/path/zerorun-research
python3 -m venv /absolute/path/zerorun-tools
/absolute/path/zerorun-tools/bin/python -m pip install --no-index /absolute/path/zerorun-research/output/packages/zerorun-softwarex/zerorun-0.5.2-py3-none-any.whl
/absolute/path/zerorun-tools/bin/zerorun --version
/absolute/path/zerorun-tools/bin/zerorun --help
```

Check `git -C /absolute/path/zerorun-research rev-parse HEAD` against the commit displayed on the `softwarex-0.5.2-20260907-r2` GitHub Release page. The manuscript's earlier immutable C2 pointer identifies the code/evidence stage. The included wheel installs without a source build or network access. Use a separate source working copy if you choose to build with setuptools, because a local source installation creates build artifacts there. The public package uses `src/zerorun`; do not copy modules into the target project or rely on imports from the source checkout. A normal external installation keeps the tools independent of subsequent checkout edits.

On Windows, the equivalent external environment has `Scripts\python.exe` and `Scripts\zerorun.exe`. Read-only inspection can be performed there, but the Linux/amd64 execution requirement remains. Research-analysis dependencies are separate from the runtime package, which has no third-party Python runtime dependencies.

For the rest of this guide, `zerorun` means this **external installed console executable**, available on `PATH`. Before integration, inspect its resolution with `command -v zerorun` on Linux or `Get-Command zerorun` in PowerShell. If using Codex, inspect `codex` the same way. ZeroRun refuses a resolved ZeroRun or Codex launcher inside the target repository. An in-project `.venv` is suitable for offline evidence inspection, but not for this launcher trust boundary.

## 2. Inspect the target before executing anything

Change to the target's Git repository root, not to the ZeroRun source repository unless that is deliberately your target. Inspect:

```sh
git rev-parse --show-toplevel
git ls-files -- .zerorun
git status --ignored --short -- .zerorun
```

The integration accepts an ordinary `.git` directory or the ordinary Git-file marker used by a linked worktree; symbolic links, junctions, dangling markers, and special files are refused. Git-tracked `.zerorun` runtime state is refused. Do not blindly delete suspicious existing state: preserve it for investigation and use a separate clean checkout when appropriate.

Treat `.zerorun.json`, `.zerorun-pytest.json`, and any candidate/review files as repository-supplied executable policy until reviewed. A checked-in `closure_reviewed: true` does not authenticate a local operator. Neither installing a skill nor generating a candidate authorizes reuse.

## 3. Configure one reviewed whole-task command

Use the [manifest v2 reference](MANIFEST_REFERENCE.md) to define a named task, such as `tests`. The operator must review its command, complete result-affecting inputs, determinism, environment, and result-only behavior. The pinned image must already contain everything the command needs, including pytest and project dependencies when applicable. A Python base image alone does not imply those packages are installed.

Acquire the **reviewed exact image** through the operator's normal Docker workflow. For a published digest-pinned image, an example is:

```sh
docker pull --platform linux/amd64 REVIEWED_IMAGE_NAME@sha256:REVIEWED_64_HEX_DIGEST
docker image inspect REVIEWED_IMAGE_NAME@sha256:REVIEWED_64_HEX_DIGEST
```

These are placeholders, not a known runnable image. Check the result and image provenance. A tag such as `latest`, a source lock file, or a digest copied without review is not an interchangeable runtime identity. MCP preflight refuses an absent image without pulling it. Keep the pinned image available for the entire run: if runtime availability changes after preflight, later inspections can attempt to pull that pinned image. The preflight check therefore does not guarantee zero image acquisition under concurrent image removal. Explicitly approved managed preparation is a separate option described below.

Write or review the target's `.zerorun.json` using the reference. Whole-task reuse does not require a pytest-node profile. In particular, do not generate or activate node-level reuse merely to make a whole-task example work.

Now inspect the configuration through the trusted-operator CLI:

```sh
zerorun --manifest .zerorun.json --json doctor
zerorun --manifest .zerorun.json --json list
zerorun --manifest .zerorun.json --json explain tests
```

`doctor` validates the manifest/runtime contract; it does not prove input completeness or determinism. Direct CLI diagnostics can acquire runtime state under the operator's Docker policy, which is why image acquisition is explicit above. `explain` does not execute tests, but can quarantine invalid cache metadata. Its `HIT`, `MISS`, or `BYPASS` prediction is not an executed test result.

## 4. Make the manual authorization decision

**Stop here unless a human operator has completed the review.** Authorization records an assertion; it does not perform that review. An AI coding agent must not edit approval fields, approve its own manifest, or invoke authorization on its own initiative. Laboratory fixture authority is not production approval and must never be copied into a real project.

Independently hash the exact file bytes after review. On Linux:

```sh
sha256sum .zerorun.json
```

On Windows PowerShell:

```powershell
(Get-FileHash -Algorithm SHA256 -LiteralPath .zerorun.json).Hash.ToLowerInvariant()
```

Compare the digest with the exact file the operator reviewed. Do not combine hashing and authorization into an automatic pipeline. Only then, in a separate operator action:

```sh
zerorun --manifest .zerorun.json --json authorize --manifest-sha256 REVIEWED_MANIFEST_SHA256
```

Success returns `status: "AUTHORIZED"` and the exact manifest digest. The receipt resides outside the checkout, is per-user, and is bound to the canonical repository and exact bytes. Even a formatting-only manifest edit requires renewed approval for its new bytes. The authority boundary protects against repository-supplied configuration/evidence; it is not a sandbox against arbitrary host code run as the same user. Use a sanitized environment and an operator-controlled Docker daemon.

The direct CLI is a trusted-operator interface and does **not** substitute for the external-authority enforcement on MCP. A successful direct CLI run is not proof that the MCP task is authorized.

### Keep qualification valid as source changes

Record the reviewed source revision and dirty-file inventory, the command and
test target, declared inputs, runtime/dependencies, exclusions and the reason
the task is deterministic and result-only. Record who performed the review;
separate automated inventory time from actual human review time. The software
does not measure or certify that review. Do not retrospectively assign a human
review duration to an existing receipt.

For a bounded producer-to-consumer handoff, review the producer's final source
state and keep that state frozen through consumer inspection and verification.
A source edit changes the declared identity when it affects a declared input,
but a passing fresh miss does not prove the edited program remains deterministic
or that its input declaration is complete. An unchanged manifest authorization
does not certify arbitrary future source edits.

Renew the operator review when edits introduce a new input, import/dependency,
environment access, time/randomness/network dependence, side effect, test target
or execution behavior outside the reviewed assumptions. New top-level files
outside the declared inventory need inspection even when the old key is
unchanged. Review can continue across edits only when the operator's documented
qualification explicitly covers those edits and its assumptions still hold.
While qualification is uncertain, request approved fresh execution outside the
reuse workflow; do not infer eligibility from one passing execution. Changed
manifest bytes also require the explicit authorization step above.

## 5. Run, identify reuse, and verify

For the reviewed task `tests`:

```sh
zerorun --manifest .zerorun.json --json run tests
zerorun --manifest .zerorun.json --json run tests
zerorun --manifest .zerorun.json --json run tests --verify
```

With an initially empty store, a successful deterministic task, unchanged declared identity, and no interfering state, the expected statuses are `MISS_EXECUTED`, `HIT_REUSED`, then `VERIFY_MATCH`. This is an **expected example sequence**, not a promise for your repository. Preserve and investigate different results rather than editing the configuration until the expected labels appear. `--verify` executes fresh; without a matching entry, it follows the fresh miss path instead of fabricating a verification match.

In CLI JSON mode, stdout contains the result JSON and the task's emitted stdout/stderr are directed to stderr. The CLI process exit code follows the result's exit code. Configuration errors exit 2 and are written to stderr; they need not produce a result JSON object.

When a fresh diagnostic transcript is needed, an operator can use:

```sh
zerorun --manifest .zerorun.json --json run tests --force
```

`--force` and `--verify` are mutually exclusive. Both execute the command fresh; force may still compare an existing successful record and report a conflict. Container output capture is bounded, so fresh execution is not a promise of a complete, untruncated log. Coverage files, generated artifacts, or other outputs require a separately approved execution workflow outside this result-only contract.

## 6. Connect an MCP client or Codex

The shipped stdio server starts with `zerorun mcp-server` from the target Git repository. Its lifetime is bound to that repository; an optional tool `root` argument cannot redirect it to another checkout. Configure the client to launch the external executable with that working directory.

For the shipped Codex integration, a compatible local Codex CLI must already be installed outside the target repository and be on `PATH`. This release does not install Codex or provide authentication. After approving skill/configuration changes, run from the target root:

```sh
zerorun --json init --codex --root .
codex mcp get zerorun --json
codex mcp list
```

These are the registration commands used by the shipped integration, not a promise about every future client version. Inspect the installed client's help if its interface differs. Initialization refuses to overwrite a user-authored skill or a conflicting `zerorun` MCP registration. A skill conflict also prevents registration. Follow the returned `next_action`, including restarting the client when requested, and verify the seven-tool catalog.

The [official OpenAI MCP guide](https://learn.chatgpt.com/docs/extend/mcp) describes the shared configuration on a Codex host, stdio `command`/`args`/`cwd`, and client restart procedures. It also documents a default `tool_timeout_sec` of 60 seconds. This is shorter than ZeroRun's maximum test-execution allowance. The operator must set an appropriate bounded client timeout for an intended long task; registration alone does not establish that the client will wait for it. A client timeout is not a passed test or an instruction to retry blindly.

`integration_ready: true` means the integration was installed, **not** that reuse is authorized or that a hit occurred. Ask the MCP `doctor` and `list_tasks` tools to check readiness. For whole-task reuse, require the exact manifest to be authorized and the selected task/runtime to be usable. Inspect task-level reasons as well as `manifest_authorized`, `task_reuse_ready`, and `reuse_ready`; another configured task being ready does not make this task ready. A repository without a reviewed manifest can remain in `observe-only` mode.

### Match the operator's external authority location

The trusted authorization command and the MCP server must resolve the **same external per-user authority directory**. If both use the same ordinary user-home/state settings and no custom override, the default location can suffice. If an operator used `ZERORUN_TRUST_ROOT`, setting it only in the shell that starts Codex does not establish that the MCP subprocess receives it. A valid authorization receipt elsewhere does not make the current server authorized.

For a custom authority location, the operator can manage the MCP registration directly in their user-level Codex configuration. This is an **example configuration**, not the configuration or output of the recorded live trial:

```toml
[mcp_servers.zerorun]
command = "/absolute/external/venv/bin/zerorun"
args = ["mcp-server"]
cwd = "/absolute/reviewed/repository"
env = { ZERORUN_TRUST_ROOT = "/absolute/operator-owned/zerorun-trust" }
```

Replace each path with the reviewed local value. The authority directory must be the exact one used for manual exact-hash authorization, outside the repository and not containing it. Keep it operator-controlled and non-linked. Merge the settings into the existing server entry; do not create duplicate TOML tables or silently replace another registration. Restart the server/client after an operator configuration change.

**Repeatable operator-managed configuration in 0.5.3:** before invoking
`init --codex`, explicitly set `ZERORUN_TRUST_ROOT` in the operator's invocation
environment to that same canonical external directory. Initialization accepts
the existing registration only when its sole fixed environment entry is the
matching `ZERORUN_TRUST_ROOT` and the path passes the external-directory checks.
It does not create authority or silently replace the registration. Missing or
mismatched settings return an explicit corrective action; arbitrary fixed
variables and nonempty `env_vars` remain refused. The earlier 0.5.2 checker
rejected even this exact custom configuration; its historical records remain
unchanged. Check the actual MCP `doctor` response after setup. Do not put the
override into task `env`, copy authority keys into a checkout, or let the AI
agent authorize itself.

The original [V1 Codex trial](evidence/live-client-v1/receipt.json) stopped after one doctor call: the tool reported `UNTRUSTED`, and extra agent commentary violated that trial's fixed completion-marker rule. Its separate [non-model diagnostic](evidence/live-client-v1/non-model-diagnostic.json) passed five STDIO checks, but did not itself establish a model-backed lifecycle. Both records remain unchanged. Later prospective trials separately exposed client-side approval refusal (V2) and an unsupported model-generated argument after a successful four-turn lifecycle (V3). The final API-guided demonstration passed two model-selected actions, two separate fresh oracle checks, and six no-tool interpretation cases. Read [all application results and their exact denominators](APPLICATION_RESULTS.md); these bounded synthetic observations establish neither production reliability nor an AI workflow speedup.

### Keep client permission separate from server authority

A valid ZeroRun authority receipt does not override the client's tool-approval policy. A client may refuse a call before the server receives it; that refusal is not a failed test or a cache result. In the recorded V3 and guided experiments, the operator explicitly approved the invocation-local setting `mcp_servers.zerorun.tools.run_tests.approval_mode="approve"` **only for the original isolated synthetic fixture**. The global tool policy, read-only Codex sandbox, disabled unrelated tools, and server's exact-byte authority checks were unchanged. No global configuration was relaxed, and no real repository was authorized. This experimental approval is not a general deployment recommendation or permission to repeat that change elsewhere.

Example MCP **argument objects** for the reviewed task, not observed responses:

```json
{"task": "tests"}
```

Pass this object to `run_tests`. For fresh verification, call the same tool with:

```json
{"task": "tests", "verify": true}
```

There is no `force` argument in the MCP `run_tests` schema. Only `task`, `root`, and `verify` are accepted; omitted `verify` means `false`. The compact [client API card](CLIENT_API_CARD.md) explains these inputs and the result-only boundary. Use `verify: true` for a fresh configured-task check, or the client's ordinary explicitly approved testing workflow when requirements exceed the interface. Observation-only MCP does not execute arbitrary repository commands on the host. The direct CLI `observe` command is an operator-only path, not an MCP fallback.

### The seven shipped tools

| Tool | Accepted inputs | Behavior and boundary |
| --- | --- | --- |
| `doctor` | optional `root` | Diagnose configuration, runtime, authority, and profile readiness. A diagnostic failure is not a test failure. |
| `list_tasks` | optional `root` | List configured tasks and readiness; does not execute tests. |
| `explain` | `task`, optional `root` | Predict `HIT`/`MISS`/`BYPASS`; does not execute tests, but may quarantine invalid cache metadata. |
| `stats` | optional `root` | Read repository-local counts and savings fields, not market-wide or agent-effectiveness metrics. |
| `prepare_pytest` | `approve_setup`, optional `root`, `task`, `targets` | Requires `approve_setup: true` after explicit user approval; may acquire dependencies, create managed files, and execute collection. Writes a non-authorizing candidate. |
| `run_tests` | `task`, optional `root`, `verify` | Execute or reuse a configured v2 whole task; requires matching external authority, empty task `env`, and an already-present pinned image. |
| `run_pytest` | optional `root`, `profile` | Execute a reviewed per-node profile; requires exact manifest and profile authority, empty task `env`, and the present pinned image. `profile` is repository-relative. |

Unknown tools or arguments are errors. There is no `observe_test` tool and no MCP authorization tool.

### Interpret results, not reassuring labels

Successful protocol handling is not the same as successful tests. A tool result contains JSON text, `structuredContent`, and `isError`. Read the structured fields when available. Anticipated tool/configuration errors generally produce `status: "ERROR"` with `isError: true`; malformed protocol requests may instead have a top-level JSON-RPC `error`. Handle both.

For whole-task `run_tests`, `mode: "reuse"` identifies the interface path even when the task executed fresh or failed. It **never establishes a hit** by itself.

| Whole-task result | Interpretation |
| --- | --- |
| `HIT_REUSED`, `exit_code: 0`, `isError: false` | Identified previous success under the checked identity; no fresh execution or replayed transcript. |
| `MISS_EXECUTED`, `exit_code: 0`, `isError: false` | Fresh successful execution; not a hit. |
| `VERIFY_MATCH`, `exit_code: 0`, `isError: false` | Fresh execution agreed with stored success; not an avoided execution. |
| `MISS_FAILED`, nonzero `exit_code`, `isError: true` | Fresh execution failed; no reusable success was stored. Pytest exit 5, for example, means no tests were collected, not success. |
| `VERIFY_MISMATCH`, `exit_code: 86`, `isError: true` | Fresh execution contradicted stored success; the cache entry was quarantined. Investigate. |
| `REJECTED_INPUT_RACE` or `BYPASS_ACTION_BUSY`, `exit_code: 75`, `isError: true` | No acceptable success to use. Re-establish a stable reviewed state before deciding what to do next. |
| `ERROR` or top-level JSON-RPC `error` | Configuration, setup, transport, or protocol problem; never convert it into a passed test. |

The `run_tests` MCP response includes `stdout_tail` and `stderr_tail`, each decoded from at most the last 4,000 captured bytes. They can be incomplete. A hit normally has empty tails because it does not execute the tests or replay saved streams. For per-node `run_pytest`, inspect its own node counts and exit status; do not reuse the whole-task status table as a node-level hit classifier.

### Read timing fields with their provenance

| Whole-task field | Meaning and boundary |
| --- | --- |
| `wall_ms` | Current runner interval. It excludes client/model work and final event append/response delivery; measure outside the tool call for consumer waiting. |
| `execution_ms` | Execution duration from this invocation when execution occurred. On `HIT_REUSED`, it is the stored historical duration, not execution during the hit. |
| `saved_ms` | On a hit, `max(0, historical execution_ms - wall_ms)`. It is an estimate clipped at zero, not measured net savings or proof that the hit was faster. |
| `phase_ms` | Diagnostic phase clocks. They are not a complete or necessarily additive partition; preliminary hit-lookup work on a miss is not separately returned. |

Use `status`, exit code and error classification to decide what happened. Do not
use a positive duration or `mode: "reuse"` as evidence of a hit or fresh tests.
For an economic comparison, retain signed differences in externally measured
producer-plus-consumer totals and show qualification, acquisition and common
setup separately. Report oracle/diagnostic costs separately when they are
research instrumentation. Adding clipped per-hit savings cannot establish net
workflow benefit.

## 7. Optional, separately reviewed pytest-node reuse

This is not needed for the demonstrated whole-task path. The supplied five-library fine-grained evaluation accepted no node reuse; installation is not evidence that a project's nodes can be safely omitted.

After explicit approval for acquisition, managed file changes, and collection, either use MCP `prepare_pytest` or the operator CLI:

```sh
zerorun-pytest-prepare --root . --target tests --json
zerorun-pytest-review --root . --reviewer "OPERATOR_NAME" --json
```

Preparation may pull a pinned Python image, install supported requirements in `.zerorun-env/`, create `.zerorun.json` only when absent, and execute pytest collection/profiling. The prepared candidate does not authorize any reuse. Supported managed dependency syntax is restricted; URLs, VCS/local-path requirements, and requirement directives are refused rather than silently broadened.

The human operator must inspect the exact candidate and the generated `.zerorun-pytest.review.json`. The review requires a reviewer identity and all four assertions: `closure_completeness_reviewed`, `node_independence_reviewed`, `all_candidate_reviewable_nodes_covered`, and `authorizes_activation`. Set them true **only when the assertions are substantiated**, not to clear a setup checklist. If review cannot establish them, leave reuse inactive.

After that review:

```sh
zerorun-pytest-activate --root . --json
sha256sum .zerorun.json .zerorun-pytest.json
```

Activation creates the reviewed profile but does not supply external user authority. Independently compare both exact digests, then authorize them in a separate operator action:

```sh
zerorun --manifest .zerorun.json --json authorize --manifest-sha256 REVIEWED_MANIFEST_SHA256 --pytest-profile-sha256 REVIEWED_PROFILE_SHA256
```

Use MCP `doctor` to confirm `manifest_authorized`, `pytest_reuse_ready`, and the relevant reasons. A profile or manifest change requires renewed review and exact-byte authority. Unknown or unqualified nodes must run fresh; partial qualification is not permission to skip the rest.

## Inspect or reproduce the bounded integration experiments

Offline inspection needs no Codex account or model credits. From the release root with CPython 3.12â€“3.14, run `python -B -m research.softwarex.build_extension_evidence --check` for the historical extension and `python -B -m research.softwarex.build_application_evidence --check` for the later client and clean-installation records. Use `python3` instead when that names your supported interpreter. These are individual diagnostics; the [complete verifier](VERIFY_SUBMISSION.md) also checks the current runtime, 0.5.2 public quickstart, handoffs, and archives. The commands validate archived evidence without replaying agent commands. [Application results](APPLICATION_RESULTS.md) links each prospective protocol, raw receipt, and checked summary; it reports earlier failures alongside the successful guided treatment.

### Historical V1 and its non-model diagnostic only

The following pinned-clone instructions apply **only to V1 and its separate diagnostic**, not to V2, V3, or the guided demonstration. Read the [original live protocol](LIVE_CLIENT_PROTOCOL.md), [pre-model support amendment](LIVE_CLIENT_AMENDMENT_1.md), and [non-model diagnostic protocol](NON_MODEL_DIAGNOSTIC_PROTOCOL.md) first. Their original records remain separate and unchanged.

For a fresh reproduction, use a **separate clean clone** of [the public repository](https://github.com/floxy-21/zerorun-research) pinned to commit `681907860dc2ab9df70034f82a0025463d1fdec4`, not the current release's moving `main`. Its `PUBLIC_RELEASE_MANIFEST.json` SHA-256 must be `76bded3e8312517594c3977fa311c1cc4e3060bd7c7a390f34b5055f2cc632dc`. Install that pinned clone into an external Linux Python environment using the installation procedure above. The Linux/amd64 image specified in the protocols must already be present; these experiments do not pull it.

Copy the following files from this release into a separate **external adapter directory**, preserving their relative paths: `run_public_lifecycle.py`, `LIVE_CLIENT_PROTOCOL.md`, `LIVE_CLIENT_AMENDMENT_1.md`, `support/tools/aggregate_codex_install_evidence.py`, `diagnose_mcp_authority.py`, and `NON_MODEL_DIAGNOSTIC_PROTOCOL.md`. Keep the unchanged published live receipt available separately for the diagnostic's `--live-receipt` binding. Do not copy those support files into the pinned clone or modify its manifest to bypass a mismatch. The adapter deliberately accepts only the frozen manifest and original helper bytes.

Inspect argument requirements first; these help commands make no model call:

```sh
/absolute/external/venv/bin/python -B /absolute/adapter/run_public_lifecycle.py --source-root /absolute/pinned-public-clone --help
/absolute/external/venv/bin/python -B /absolute/adapter/diagnose_mcp_authority.py --help
```

The original model-backed experiment additionally requires the reviewer's **own authorized Codex account**, the pinned client/runtime provenance and installer-receipt arguments specified in its protocol, and a new explicit operator decision about model use, metadata disclosure, and fixture-only authority. The archived author's approval does not authorize a reviewer or an agent to start new model calls. Keep credentials outside all repositories and output artifacts; do not copy or publish another person's login data. Preserve any new outcome, including a failure, under a new evidence path; do not overwrite or relabel the published record.

The non-model helper needs no Codex process, account, or login. Its help lists the installed launcher/Python, pinned source, original live-receipt, and new output-file arguments. It requires an explicit `--approve-synthetic-formative-authority` acknowledgement and accepts no caller-selected test repository or authority directory. That acknowledgement applies only to its newly created, original built-in fixture. Read the diagnostic protocol before granting it. Record one new attempt in an existing external evidence directory, stop on a failure, and keep the raw result. A passing non-model diagnostic is still not a model-backed reproduction.

### Later V2, V3, and API-guided demonstration

These use a distinct immutable public source, `ebf2884df12573d63f45813200e0675288d12096`, whose manifest SHA-256 is `f7a01e6a16f32022ce686a4a213e073c83a8f77de82df0c9df357c9c31ab6564`. Their exact adapters, source inventories, installed runtime, original client installer receipt, and prior failed receipts are separately bound. Do not substitute the current enclosing release manifest for this historical experimental manifest.

Read [V2](live_client_v2/PROTOCOL.md), [V3](live_client_v3/PROTOCOL.md), and the [guided protocol](guided_client_v1/PROTOCOL.md) before considering new execution. Each runner's `--help` describes its explicit consent and identity arguments. Use a separate external adapter directory and new evidence paths, preserving the required frozen helper hierarchy; do not patch a frozen source or change its expected hashes. Model execution requires the operator's own authorized account and the applicable fixture-only decisions. The API card was frozen before the guided trial and supplied as neutral interface documentation, not as expected answers. That trial does not establish a causal benefit from the card.

For first use without model credentials or historical client installer artifacts, prefer the [account-free quickstart](QUICKSTART_LAB.md). Its literal public-guide replay passed from a clean public clone, using a new installation and five actual STDIO stages. It is an internal reproduction, not an external developer study.

## Real-workload reproduction

The [fresh-workload guide](FRESH_REAL_WORKLOAD_REPRODUCTION.md) provides the separately bound image recipes and fixed case ledgers. The v3 author-side build disabled Docker build-step cache reuse, used the observed new image for the unchanged 24-case main ledger, and completed ten cases with twenty fresh-oracle-agreeing reused successes. Six cases were incomplete/unsupported and eight remained unrun within the fixed budget. Complete-chain totals were near break-even while consumer waiting fell 79.1%; common image preparation took an additional 66.070 seconds. Existing public base layers and the author VM were retained. This is controlled reproduction, not automatic qualification of your repository or independent human validation.

## Resource limits, troubleshooting, and support

Execution uses network-disabled containers with a read-only source view, 2 CPUs, 2 GiB memory with no additional swap, 512 PIDs, a 900-second execution limit, bounded output capture, and bounded cleanup. Docker acquisition/inspection/cleanup have separate limits; a complete request is not guaranteed to finish within 900 seconds. The operator controls the Docker daemon and its credentials. These controls do not establish deterministic behavior or eliminate every host race.

| Symptom | Next step |
| --- | --- |
| Missing manifest or observation-only mode | Review and configure a supported task; do not self-authorize a generated candidate. |
| Authority missing or digest changed | Re-read the exact file, complete the review, independently hash, and obtain a separate operator decision. |
| CLI authority exists but MCP remains `UNTRUSTED` | Check whether both processes resolve the same external authority directory; review the operator-managed configuration above before assuming new authorization is needed. |
| Client requires approval before sending a tool call | Stop and obtain the operator's scoped decision. Do not treat client refusal as server execution or silently relax global approval policy. |
| Unexpected `run_tests` argument | Consult the [API card](CLIENT_API_CARD.md); only `task`, `root`, and `verify` are allowed. Preserve the failed response rather than treating it as validation. |
| Missing image | Ask the operator to acquire the reviewed exact runtime; do not let a normal execution call pull a different one. |
| Launcher or skill conflict | Inspect executable resolution and existing client/skill configuration. Initialization intentionally does not overwrite it. |
| Missing input, link, unsupported platform, or unsafe effect | Correct the actual scope/setup or execute through a separately approved fresh workflow; do not weaken the declaration to obtain a hit. |
| Empty or truncated diagnostic tails | Request fresh execution as above. A cached success never supplies the old transcript. |

For support, open a public repository issue or email kapoorjishan2@gmail.com with the release commit, platform, command, returned status, and a minimal non-sensitive example. Do not upload raw `.zerorun` state, private source, tokens, Docker credentials, or external authority keys. Keep the public research evidence separate from a customer's operational records.
