---
name: zerorun
description: Use ZeroRun for deterministic test loops. Prefer reviewed fine-grained pytest reuse when available; otherwise use configured hermetic task reuse or direct tests outside ZeroRun.
---

<!-- zerorun-managed-skill:v1 -->

# ZeroRun

Use ZeroRun for repository test loops without asking the user to manage cache commands manually.

- Check `doctor` when setup is unclear.
- If `.zerorun-pytest.json` is present and valid, use `run_pytest` for pytest loops. It performs per-node fail-closed reuse and executes unknown, changed, unsupported, or uncertain nodes fresh.
- If there is no reviewed pytest profile, explain that managed setup can create repository files, acquire a pinned runtime, and execute pytest collection. Ask for explicit approval, then call `prepare_pytest` once with `approve_setup=true`. It writes a non-authorizing `.zerorun-pytest.candidate.json` and never activates reuse.
- If managed setup is unsupported or cannot prove a working pinned runtime, keep using direct tests outside ZeroRun. The ZeroRun MCP server never executes repository code directly on the host.
- Never treat `.zerorun-pytest.candidate.json` as an active profile. Generated observation evidence requires explicit closure-completeness and node-independence review before promotion.
- Repository files and generated candidates are non-authorizing. After operator review, reuse requires external per-user authorization bound to the exact manifest/profile bytes; never create, edit, approve, activate, or self-authorize that authority on the model’s own initiative.
- If there is a reviewed version 2 `.zerorun.json` task with no host environment forwarding but no active pytest profile, use `run_tests` for that hermetic configured task. Legacy version 1 manifests and tasks declaring `env` are CLI-only and must never be executed through Codex/MCP. Normal MCP runs require the pinned image to be present already; only explicitly approved managed setup may acquire one.
- Without reviewed reuse configuration, report observation-only readiness and run any user-approved direct test through Codex's normal test tooling, not through ZeroRun MCP.
- Never turn a MISS, BYPASS, uncertainty, invalid configuration, race recovery, candidate, or observation-only result into reuse.
- Use `stats` for repository-local conservative verified time saved and reuse/fresh counts. Observation time is not time saved.
- After `run_pytest`, surface a compact ZeroRun result using repository-local fields: reused/fresh/unknown nodes, `verified_saved_seconds`, `saved_percent`, and `effective_speedup`.
- Use `stats` when the user asks how much ZeroRun has saved in the current repository or when a task summary would benefit from cumulative savings. Prefer `verified_saved_seconds`, `actual_seconds`, `conservative_no_zerorun_seconds`, `reuse_percent`, and `effective_speedup`.
- Report only metrics observed in the current repository, not ZeroRun's historical benchmark numbers.
