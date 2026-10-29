# Pilot attempt 1: evidence-capture defect

Run 40956629282, commit ed8104ddb0a25b7c27b2f5507a885501530f9ccc, completed technically on 29 October 2026. Runtime construction and source acquisition succeeded. Both selected targets failed the nonempty/equal/successful preflight gate; no timed block began. This is not a successful feasibility result.

The artifact's negative patterns for `preflight-*/**` and `block-*/**` also matched the correspondingly prefixed JSON records. The report retained the gate outcome but did not embed those records. Consequently the individual preflight outcomes and their detailed incurred times are **unobserved**, not zero, and cannot be reconstructed from the preserved artifact. Runtime, source, whole-attempt elapsed and host records remain in `pilot-attempt-1/`. Artifact 12932698399 had ZIP SHA-256 ecdbdbc419cda60f38c3a73a32fd112fc089b290bc7a1bd8ba04758ce5fadb65.

Repair: replace broad exclusion patterns with positive exact evidence paths, also embed preflight records in the report and print their outcomes. Rerun with this changed instrumentation to determine the substantive preflight defect. Do not classify the second attempt as independent replication or use the first attempt as a timing block.
