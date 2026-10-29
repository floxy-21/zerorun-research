# Early qualification falsification

Frozen before the Docker probe. This is an exploratory mechanism check informed by the component probe and source review, not a confirmatory performance experiment.

Question: does the actual released MCP execution path enforce a qualification limited to the source state that was inspected?

The new fixture A is a two-line deterministic arithmetic assertion. Its only source dependency is `check.py`; the digest-pinned runtime has no network, no forwarded host environment, no requested outputs and no cached streams. The fixture itself is the reviewed object. Qualification covers A and exact restoration of A. B changes the declared program to another passing arithmetic assertion, without changing the manifest. B is intentionally outside the qualification record even though its simple behavior is known to the experiment author.

Run the actual stdio MCP server as a subprocess. Preserve requests and complete responses. Request A without authority, authorize the exact manifest outside the checkout, then request A twice, B twice, restored A, and a fresh verification of restored A. A manifest-only authority may admit and reuse B; a source-state-qualified policy must require renewed qualification. A refusal at B is a valid competing outcome. The baseline hypothesis is not hard-coded as a successful experimental exit.

Every begun stage, exception, refusal and response is retained. The program writes its report even after an error. Private authority keys and cache-authentication material never leave the temporary directory. No nondeterministic fixture or security exploit is executed. The program does not modify the runtime.

Admitting B establishes a scope-enforcement gap only. It does not establish an incorrect test result, violate the original paper's conditional frozen-state claim, or estimate defect prevalence. Performance numbers from this probe are diagnostic and cannot enter the main timing comparison.

Next action: if B is admitted, introduce an explicit source-state qualification gate in the experimental deployment, repeat the same sequence, and test restoration, tampering and source changes. If the live path refuses B, reconcile that result with the component probe before any mechanism claim. Do not treat a passing source-state gate as proof of input completeness or determinism.
