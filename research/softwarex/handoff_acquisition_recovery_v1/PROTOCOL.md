# Prospective acquisition transport amendment: recovery v1

The original acquisition ended before selection because the second immutable
parquet exceeded the original 180-second between-read deadline. Preserve that
attempt, every download record and both complete/partial raw files. This is a
new named attempt, not a relabeling of the original as successful.

Before network access, validate the old plan against the unchanged original
collector/protocol and bind its failure receipt, metadata, notices, first
complete parquet and second partial parquet. Recompute their byte counts and
SHA-256 values. Refuse a prior attempt that already wrote selection, pilot or
main manifests, changed source bytes, or failed for a different reason.

Use a new output directory. Copy the first complete parquet, successful metadata
and notices into it with streaming byte checks; copy the small original plan,
receipt and download records under `initial-attempt/`. Do not mutate, truncate,
resume or overwrite the original files. Record the original partial's hash and
location. Download the second parquet from the exact original revision-pinned
URL once, starting from byte zero, with a 600-second between-read deadline and
the same 45-second socket timeout and 512-MiB limit. An in-flight socket read
can exceed the between-read budget by at most its socket timeout. No automatic
retry or fallback URL is allowed. Check any declared Content-Length and the
PAR1 container boundary before parsing with the original required pyarrow.

The source revision, 21,336-row total, license, ordered support shortlist, seeds,
validity rules, three main cases per selected repository, ten repositories,
two disjoint pilot cases and their ordering are unchanged. Call the original
collector's selection and archive acquisition logic without modifying those
functions. Repository archive requests retain the original one-attempt,
180-second, 64-MiB limits. Selection still occurs before archive requests and
does not read outcomes or timings. No downloaded code is imported or executed,
no archive is extracted and no repository authority is created.

Freeze this amendment's source hashes and initial-attempt bindings inside the
new plan before any new request. New receipt fields identify transport recovery,
the original failed receipt and source preservation checks. A new failure is
retained in its own output; it does not trigger another automatic attempt.

This amendment repairs source availability only. It is not new performance,
compatibility, model, safety or acceptance evidence.
