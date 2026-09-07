# Source audit for the manuscript operating-contract table

Checked against primary sources on 7 September 2026 UTC (6 September local time). This is a scoped contract comparison, not a claim of exhaustive literature coverage or a performance ranking. Existing manuscript bibliography keys are reused.

| System | Supported table statement | Primary support |
| --- | --- | --- |
| Bazel | Actions have declared inputs, output names, command lines and environment variables. Its remote cache maps action hashes to metadata and stores output files and stdout/stderr. | [Official remote-caching documentation, Overview](https://bazel.build/remote/caching). |
| ToolCaching | Keys combine a tool name with serialized arguments. Semantic features and TTL inform admission; COMMAND-type requests are excluded. Entries store tool results and metadata. | [Version 1, sections 4.1, 5.1 and 5.2](https://arxiv.org/html/2601.15335v1). COMMAND denotes the paper's semantic request category, not every shell command. |
| TVCache | Tool-call prefix matching returns cached tool values. Optional state-preserving annotations alter matching; selected sandbox snapshots support continuing execution. | [Version 1, sections 3.2–3.3 and Appendix B](https://arxiv.org/html/2602.10986v1). Correctness depends on its stated state/output and annotation assumptions. |
| ZeroRun | The demonstrated mode uses reviewed local test-task inputs, command and runtime identity, returning an identified previous success rather than a historical transcript or output artifacts. | `paper/submission.tex.in`, the frozen `research/sqj/source-final/zerorun/hermetic.py`, and preserved case manifests/results. Conditional closure and determinism assumptions remain explicit. |

The table does not infer that adjacent systems lack authentication, content checks, safety mechanisms, or AI interfaces. It does not compare their headline speedups. A narrower contract is an implementation distinction, not proof of a new caching algorithm or superiority. The separate source-bound evaluation kit and client decision examples explain the practical reuse contribution.

ToolCaching and TVCache remain cited as arXiv preprints, not verified journal publications. The ToolCaching HTML retains unfilled conference metadata; that placeholder is not copied into the manuscript. The nearby eidos citation remains a separate venue/integration example and is not used to infer undocumented cache behavior.
