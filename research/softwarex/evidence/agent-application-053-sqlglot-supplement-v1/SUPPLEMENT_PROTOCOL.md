# SQLGlot 3182 production-only supplemental repair

This is an explicitly separate, assisted repair after the six frozen actual-agent
producer outcomes were inspected. The original SQLGlot producer's 97 passes and
two failures, and the primary five verified fixes out of six selected cases,
remain unchanged. It is not an additional successful original model attempt.

The original issue-specific transform regression already passes. The unchanged
provided test patch additionally exercises DuckDB TimeAdd generation and
Snowflake TIMEADD/TIMESTAMPADD parsing. Repair only these production dialect
paths on the exact captured original agent final snapshot. Keep the original
agent expressions.py patch, every test, every selected target, dependency,
configuration, and ZeroRun runtime byte unchanged. Do not read or apply the
benchmark reference production fix. No new model call or cache consumer is used.

After existing consumer model stages finish, run exactly two ordinary fresh
oracles: original captured agent final, then that snapshot plus this frozen
production-only supplemental patch. Use all original three selected modules and
the same pinned Python3.10.21/pytest8.4.2 compatible image, no network/pull,
read-only source mounts, and 120-second individual execution caps. Do not retry
or remove failed cases. Preserve full command logs, shadow-plugin node records,
source before/after inventories, original inputs, patch/driver/protocol bytes,
and both failed and successful outcomes. This is functional repair evidence;
no timing benefit or independent-human replication is claimed.

A successful supplement requires exactly the same 99 collected node IDs in both
fresh executions, every repaired call passing, source stable during each oracle,
and only the two named production files differing between the snapshots. Any
failure leaves the supplement incomplete; it cannot relabel the original
six-case denominator or original 5/6 result.
