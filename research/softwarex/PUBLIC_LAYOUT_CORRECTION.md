# Exact analysis-helper bytes in the public layout

The first strengthened code-only snapshot was
`437efad77e4a6425c43149a9c402e7b3fd5eee1f`. A read-only reproduction check from
that clean public stage correctly refused to validate the operating-region
analysis: the top-level research helper had different physical newline bytes
from the archived helper bound to the observations.

The Git-exported `tools/product_generalization_benchmark.py` was 217,833 bytes,
SHA-256 `d5085faa818008bb043eb4f9f7477aff1f86ba256a7f9cc557eaa139559e1e2f`.
The recorded helper was 212,857 bytes,
SHA-256 `96588c65ee674597e4c459651345b9e89ac92cbdc0c85baaa3de4f57f6a44359`.
Their contents match exactly after CRLF-to-LF normalization. The public builder
now verifies that equality and publishes the exact recorded helper bytes with
an explicit archived-source origin. The independent validator is not weakened,
and no analysis output or runtime source is changed.

The historical 465-pass runtime/public-layout test inventory does not include
this research helper and remains valid. The first new publication test run
(370 passes, two platform skips) is retained under
`evidence/publication-extension-final-v1`. Its source bindings precede this
builder-only correction. A separately retained final publication run validates
the corrected builder and its byte-equivalence guard. No earlier run is edited
or represented as testing the later builder.
