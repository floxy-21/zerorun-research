# Frozen non-model MCP authority diagnostic

Recorded 7 September 2026 UTC after the adverse live-client trial and before
this separate diagnostic. This is not an amendment, rerun, or replacement of
that trial. Its receipt remains unchanged at `evidence/live-client-v1/receipt.json`
(file SHA-256 `d1afbce8b54a2e6b65ebb10631a6e21bfc956a2096b77396d7e1a608de221879`).

## Question and fixed intervention

The live harness created exact-manifest authority under `ZERORUN_TRUST_ROOT`,
but did not configure that variable for the Codex MCP subprocess. The actual
doctor response was `UNTRUSTED`, `manifest_authorized=false`, and observe-only.
A separate extra agent commentary message also violated the frozen marker
rule. Neither failure will be removed or reclassified.

Test whether explicitly supplying the same operator-selected external trust
path to a fresh, installed ZeroRun STDIO server restores readiness and the
synthetic task's result lifecycle. This checks server configuration, not Codex
environment forwarding or model compliance. No Codex process, model, credential,
remote metadata request, runtime acquisition, or general-repository authority
is permitted.

## Fixed procedure

1. Validate the pinned public manifest, original adapter/protocol/amendment/helper
   bindings, unchanged adverse receipt, and all 36 installed runtime files against
   the public source. Record source Git and package identities before and after.
2. Create a fresh private temporary repository using only the original harness's
   built-in two-file fixture and the same already-present digest-pinned image.
   Keep a separate empty per-process home and empty external trust directory.
3. Require the explicit `--approve-synthetic-formative-authority` flag. Authorize
   only the newly generated fixture's exact manifest hash once. Preserve the
   authorization response and authority structure without exporting the key.
4. Launch a fresh installed STDIO server with no `ZERORUN_TRUST_ROOT`; the isolated
   home prevents unrelated user authority from becoming a confound. Send initialize,
   initialized notification, and exactly one doctor request. Require `UNTRUSTED`
   and observe-only, with no cache creation. This is a missing-variable control,
   not a claim that Codex's entire environment has been reproduced.
5. Launch another fresh server with only the explicit external trust-path override
   added. Require doctor readiness and exact task/cache-key identity. Then issue
   one `run_tests` request per fresh server for false, false, true verification,
   requiring `MISS_EXECUTED`, `HIT_REUSED`, `VERIFY_MATCH` in that order.
6. Each server has a 120-second deadline and a 2-MiB per-stream output bound.
   Preserve every planned request and received stream, including failures.
   Stop at the first failed prerequisite, mismatch, timeout, or unexpected result;
   no diagnostic retry is permitted. Confirm unchanged fixture inputs, source,
   installed package and external authority, then clean up only the private
   temporary fixture and its synthetic authority.

The helper accepts no caller-supplied test repository or authority path. The
receipt records the helper/protocol hashes and clearly marks `model_called=false`
and `codex_correction_tested=false`, even if all five tool calls succeed.

## Operator configuration implication

For a real integration, an operator may explicitly set
`mcp_servers.zerorun.env.ZERORUN_TRUST_ROOT` to the same absolute, external,
operator-controlled directory used during manual exact-hash authorization.
Alternatively, `env_vars = ["ZERORUN_TRUST_ROOT"]` allowlists a pre-set host value.
Do not place the value in a repository manifest, forward arbitrary variables,
copy an authority key into the repository, or authorize from the MCP agent.
The original frozen harness deliberately remains unchanged and does not exercise
this correction. Syntax is documented in the
[official OpenAI MCP guide](https://learn.chatgpt.com/docs/extend/mcp).
