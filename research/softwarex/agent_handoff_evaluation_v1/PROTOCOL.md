# Prospective post-agent fresh verification and controlled handoff v1

This companion evaluates the fixed six main producer cases, or the two separate
pilot cases, from `agent_handoff_v1`. It makes no model calls. Preserve every
selected case, including missing, unready, incomplete, invalid, and boundary-
failed producer attempts. Do not replace cases or apply the benchmark reference
source patch. This is test-assisted issue fixing followed by a controlled
handoff, not a SWE-bench leaderboard or natural autonomous-cache experiment.

Freeze this companion, the unchanged producer and handoff helper sources, the
producer freeze and all captured input files, runtime identity, image choice,
120-second symmetric execution cap, campaign budget, and UTC start cutoff before
repository execution. Never create MCP authority, change runtime source or
eligibility, reveal a reference fix, or request new model sessions. The operator
must finish capturing producer sessions before this companion begins.

For each scope-valid producer with a complete final source snapshot, reconstruct
the immutable base archive and apply only the exact public test patch. Require
its complete non-Git source inventory to equal the producer's before inventory.
Independently reconstruct the full final-source archive, including untracked
production files, with no links or special members, and require exact agreement
with the producer's after inventory. Source bytes and test protection are
rechecked; the proposed textual Git diff is not used as the final source.

Run an independent fresh-container oracle on the base-plus-public-tests state.
Retain ordinary test failure, missing dependencies, collection failures and
unavailable baselines. Run the unchanged handoff-v1 final compatibility oracle
before any timing. A passing baseline does not establish a repaired regression.
Count `completed_verified_fix` only when the producer completed within its
captured boundary, proposed a nonempty allowed source patch, and the same test
node set contains an ordinary non-xfail failing call before the change that
passes in the final fresh oracle, with final exit zero. Report baseline status,
final fresh pass, producer completion and verified-fix status separately.

Use the existing handoff-v1 `run_case` operation code for the final state: two
counterbalanced blocks, each with fresh and ZeroRun cold-producer-plus-one-
consumer arms, independent fresh node/outcome oracles, and a separate fresh-
diagnostics operation. The sole source adapter supplies the verified agent final
state in place of v1's reference-patch `make_state`. No reference patch is
executed. An optional, already-reviewed handoff-image-v2 image uses its genuine
RepoDigest and explicit image-contained workspace adapter in BOTH arms.

Include producer and consumer in the chain ratio; separately report source/
environment preparation, baseline/final oracle costs, model-session cost and
shared dependency/image setup. Do not claim that a fast consumer accelerates
the complete agent workflow. A failed final fresh check prevents timing that
case. A material reuse/fresh mismatch stops later cases. Other unsupported or
unfinished cases remain in the selected denominator. Retain all partial
operation records and negative timing results; do not retry until passing.

Do not start a new case after the operator's UTC cutoff or exhausted campaign
budget; allow already-started bounded operations to finish and disclose that
the cutoff is a start boundary, not an instantaneous process kill. The default
execution cap remains 120 seconds per repository process. No new long-running
job is authorized after the operator's 06:50 UTC study cutoff.

Reconcile receipts read-only from their raw operation and node records. Producer
completion alone, a model's stated success, an empty patch, a passing baseline,
or unsupported baseline collection is never counted as a verified repaired bug.
Report the selected agent denominator separately from the larger controlled
reference-patch campaign. No natural hit-rate, end-to-end agent acceleration,
external-user, commercial-demand, universal correctness or acceptance-odds
claim is supported by this experiment.
