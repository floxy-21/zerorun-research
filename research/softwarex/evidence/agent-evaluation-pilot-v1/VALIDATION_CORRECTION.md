# Final-source inventory ordering correction

The first independent agent evaluation completed one controlled case and retained
the other selected producer's pre-invocation failure. Its original read-only
validator refused the completed case with `archived final source bytes differ`.
The original evaluator's five source files were copied byte-for-byte into
`attempt-sources/` before any correction; `source-preservation.json` records
their hashes. The original protocol, completion, operation, and refusal records
remain associated with those source bytes and are not relabeled as a new pass.

Inspection of the captured final archive found 358 source entries. Every path,
entry kind, file byte count, and SHA256 agrees with the producer's recorded
`after` inventory when both lists use path-string ordering. The only disagreement
was list order: the producer uses `Path` ordering, while the archive inventory
uses path strings, which differ for paths such as `docs/sqlglot.html` and
`docs/sqlglot/_typing.html`. The producer validator and execution reconstruction
already compare the sorted inventory correctly.

The correction applies the same ordering to this final read-only comparison.
It does not remove entries, change source hashes, rewrite captured inventories,
alter test targets or runtime eligibility, or apply a reference source patch.
Positive and adverse fixtures check that reordering is accepted while omitted,
duplicate, renamed, retyped, resized, or rehashed entries remain refused.

The follow-up independent evaluation must use a new `agent-evaluation-pilot-v2`
directory with the corrected source inventory frozen before execution. It reuses the
original captured model sessions and makes no new model calls. Both attempts
remain separate; repeated checks of the same patch are not additional agent
tasks or additional independently solved issues.
