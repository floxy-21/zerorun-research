# What the package adds to a saved test receipt

A researcher can retain a command, source revision, runtime identifier, exit
status and transcript without ZeroRun. That is a useful baseline for preserving
what happened. This comparison describes supplied functionality, not measured
superiority or a claim that other receipt tools cannot implement these checks.

| Researcher's question | A retained receipt alone | ZeroRun's qualified whole-task interface |
| --- | --- | --- |
| Which validation happened earlier? | Read the recorded command, identity and status. | Return an identified earlier successful result when the checked declared identity and authenticated entry agree. |
| Do the current dirty files still match? | An external comparison is needed; a Git commit alone does not describe dirty files. | Fingerprint declared inputs and relevant execution identity on the request; recheck before admitting a hit. Undeclared inputs remain an operator responsibility. |
| Was this configuration approved for reuse? | Approval provenance must be recorded and checked separately. | MCP admission checks external per-user authority for the exact configuration and repository. |
| Has a saved success been altered? | Requires a separately implemented integrity/authentication mechanism. | Validate authenticated records under the local authority boundary; this does not defend against arbitrary host code executing as the same user. |
| Does the caller need a new test run? | Re-execute using a separately arranged command/workflow. | Request fresh verification and return its actual fresh result; preserve conflict/refusal semantics. |
| Can the caller mistake history for fresh evidence? | Depends on receipt schema and consumer behavior. | Explicit result statuses distinguish eligible reuse, fresh execution, verification and refusal. Actual consumer interpretation still needs evaluation. |
| Are historical output artifacts restored? | Depends on what was retained. | No. The demonstrated contract returns result metadata and does not replay historical stdout/stderr or restore output artifacts. |

The contribution is the inspectable, integrated workflow and its checked
boundaries. It does not introduce content hashing, automatically prove input
completeness, establish demand for historical status, or show that a simpler
receipt implementation is slower. A researcher who only needs an archive of a
past run may find an ordinary receipt sufficient. A researcher implementing
repeated status requests can use the supplied admission, revalidation and
response handling instead of implementing those components separately.

Use the operating guide to qualify a frozen producer source state and inspect
the explicit status returned to its consumer. The real-client evaluation must
show whether the consumer interprets that distinction correctly; this functional
comparison is not a substitute for that evidence.
