# ZeroRun research release

Software for **ZeroRun: Reproducible test-result reuse for AI coding tools**, by Jishan Kapoor, Independent researcher, Toronto, Canada. Correspondence: kapoorjishan2@gmail.com.

This clean research distribution packages **ZeroRun 0.5.2**. `PUBLIC_RELEASE_MANIFEST.json` records the exact current runtime commit, package version, and each file's bytes, origin, and SHA256. Version 0.5.2 changes the package/module version and the discoverable MCP `run_tests` descriptions relative to the preserved 0.5.1 runtime at commit `86f42289c2f59a74b1f642f0b20d1a26b3d55e57`; its execution and reuse logic is unchanged. The release includes selected runtime regression tests, research drivers, immutable observations, and independently checked summaries. Packaging uses a `src/` layout, with selected test-path adaptations recorded in the manifest. Historical experiments and their original 0.5.1 installation/test receipts remain identified separately; they do not certify the current 0.5.2 candidate. The distribution does not contain development history or private operator credentials.

Git-derived files preserve the tested archive representation: the builder explicitly fixes `core.autocrlf=true` and `core.eol=crlf`, so text newline conversion is independent of host defaults. Manifest `git:` origins identify committed paths; each payload hash identifies its exported bytes, which can differ from the raw Git blob's line endings.

The analysis helper uses its exact recorded physical bytes; the [public-layout correction](research/softwarex/PUBLIC_LAYOUT_CORRECTION.md) documents the newline-only packaging mismatch caught by clean-copy validation and the separately retested correction. The original refusal and pre-correction test receipts remain available.

ZeroRun can reuse a prior successful **result** for explicitly configured deterministic tasks after checking the declared input contract. It provides CLI and MCP interfaces for integration with coding tools. It does not cache a language model's inference, infer that arbitrary commands are deterministic, or establish that repeated command text implies reusable state.

## Submission package

The submission artifacts, when included in the checked snapshot, are the [manuscript PDF](output/pdf/zerorun-softwarex.pdf), [editable source](output/submission/SoftwareX_source.zip), [reviewer software and evidence release asset](https://github.com/floxy-21/zerorun-research/releases/download/softwarex-0.5.2-20260907-r2/ZeroRun_SoftwareX_reviewer.zip), and [author upload guide](research/softwarex/UPLOAD_GUIDE.md). The complete reviewer ZIP exceeds GitHub's repository-file limit and is distributed as a release asset; its exact size and SHA-256 are recorded in the public manifest and artifact receipt. Use [final-readiness.json](research/softwarex/generated/final-readiness.json) for the exact checked version/hashes and remaining author-controlled steps. Completion requires a receipt bound to the actual current distribution; a retained 0.5.1 receipt or the existence of an archive is not sufficient. The manuscript's immutable code/evidence pointer is intentionally distinct from the later submission snapshot. No journal submission or payment is performed by publishing these files.

## Verify the submission first

Use a clean checkout and **CPython 3.12–3.14**. Verification needs no package installation, Docker, AI account, or model credits. If starting from scratch, these Git commands select the published submission:

```sh
git clone --depth 1 --branch softwarex-0.5.2-20260907-r2 https://github.com/floxy-21/zerorun-research.git zerorun-review
cd zerorun-review
git rev-parse HEAD
```

Compare the printed commit with the commit shown on the `softwarex-0.5.2-20260907-r2` GitHub Release page. From the checkout root, download the separately distributed reviewer ZIP, then run the offline verifier:

```sh
python3 --version
curl --fail --location --output output/submission/ZeroRun_SoftwareX_reviewer.zip https://github.com/floxy-21/zerorun-research/releases/download/softwarex-0.5.2-20260907-r2/ZeroRun_SoftwareX_reviewer.zip
python3 -B -m research.softwarex.verify_submission
```

Use a Python executable reporting 3.12, 3.13, or 3.14; replace `python3` if necessary. In Windows PowerShell, use `python` and `curl.exe` with the same arguments. The download needs network access; the verification command does not. Exit status `0` and JSON `"passed": true` mean all required checks passed. Keep any redirected report outside the checkout.

**Use the Git checkout for complete verification, not an extracted reviewer ZIP.** The ZIP contains the earlier sealed evidence inventory and cannot contain itself or the later artifact receipt. Its bundled README describes the complete-checkout procedure; use that procedure and the [verification guide](research/softwarex/VERIFY_SUBMISSION.md). Do not install from source, add notes, or run tests inside the pristine verification copy. An external virtual environment alone does not prevent `pip install .` from creating build files in the source directory.

## Install

For an account-free runnable Linux example, start with the [0.5.2 laboratory quickstart](research/softwarex/QUICKSTART_052.md). It records a new external installation and five actual synthetic STDIO requests; no Codex login or historical evidence bundle is needed.

Python 3.10 or later is required. From this repository root, create a fresh environment outside the checkout. Linux/macOS:

```sh
study_env="$(mktemp -d)/venv"
python3 -m venv "$study_env"
"$study_env/bin/python" -m pip install --no-index output/packages/zerorun-softwarex/zerorun-0.5.2-py3-none-any.whl
"$study_env/bin/python" -m zerorun --help
```

Windows PowerShell:

```powershell
$studyEnv = Join-Path $env:TEMP ('zerorun-env-' + [guid]::NewGuid().ToString('N'))
python -m venv $studyEnv
$studyPython = Join-Path $studyEnv 'Scripts/python.exe'
& $studyPython -m pip install --no-index output/packages/zerorun-softwarex/zerorun-0.5.2-py3-none-any.whl
& $studyPython -m zerorun --help
```

These commands install the included, checked wheel without a network download or a source build. The package has no third-party Python runtime dependency. If building from source with setuptools, use a separate working copy so build artifacts cannot contaminate the verified snapshot. Pinned research containers, Git, Docker, and pytest are additional requirements for the relevant experiments. Recorded whole-task timings were obtained on Linux with a pinned Linux/amd64 Python container; do not assume those timings or all permission semantics transfer to Windows or macOS.

**For Codex/MCP integration, keep the ZeroRun and Codex console launchers outside the target checkout.** See the [operating guide](research/softwarex/OPERATING_GUIDE.md) for manual exact-byte authorization, seven MCP tools, and the fresh/reused/error response contract. The [manifest v2 reference](research/softwarex/MANIFEST_REFERENCE.md) explains the result-only fields and input-review requirements. Neither installation nor an automatically generated candidate authorizes reuse.

## Offline evidence checks

Run these from the repository root using **CPython 3.12–3.14**; package installation is unnecessary. This is separate from the runtime and laboratory quickstart's Python 3.10+ requirement. The older float-aggregation behavior is not byte-identical to the archived canonical analysis. The versioned reproduction check also reconciles one unordered crash-file inventory without changing measurements or permitting numeric tolerances. See [the portability record](research/softwarex/evidence/analysis-portability-v1/README.md).

Complete submission verification is the first procedure above. It checks the public file manifest, both submission archives and evidence reconciliations, then verifies that the snapshot is unchanged. A missing or altered reviewer ZIP fails verification. It does not execute recorded agent commands or rerun repository experiments.

The individual commands below are useful for investigating a specific analysis check; they do not replace complete snapshot verification:

```sh
python3 -B -m research.sqj.strengthening.validate_traces --check
python3 -B -m research.sqj.analyze_comparison --check
python3 -B -m research.softwarex.analysis_reproduction --check
python3 -B -m research.softwarex.analyze_operating_region --check
python3 -B -m research.softwarex.build_extension_evidence --check
python3 -B -m research.softwarex.build_application_evidence --check
# Windows PowerShell uses the corresponding form:
# python -B -m research.softwarex.build_extension_evidence --check
```

To run the included unit tests, install pytest in the same environment and use a new temporary directory for each run:

```sh
"$study_env/bin/python" -m pip install pytest==9.1.1
"$study_env/bin/python" -B -m pytest tests research/sqj/strengthening/tests -q -p no:cacheprovider --basetemp=/tmp/zerorun-research-tests-unique
# Windows PowerShell, with a new name on every run:
# & $studyPython -m pip install pytest==9.1.1
# & $studyPython -B -m pytest tests research/sqj/strengthening/tests -q -p no:cacheprovider --basetemp="$env:TEMP/zerorun-research-tests-unique"
```

The temporary directory **must be outside every Git checkout**, on every platform. Some trust tests intentionally create incomplete `.git` fixtures; Git must not discover an ancestor repository. The first staging test attempt used a nested development-repository directory and exposed this harness constraint; its [failure report](research/softwarex/evidence/public-release-tests-1.xml) is retained separately from the corrected-location run. On Windows, use a fresh absolute path under a non-repository temporary directory. POSIX permission checks require a non-root Linux user and are explicitly skipped when the platform cannot express the tested behavior. The included tests are a selected research/runtime set, not a claim that every development-repository test is included.

The publication runner checks `research/softwarex/tests` and all ten explicitly listed nested offline test modules, including the client, acquisition, handoff, image and agent-evaluation validators. Running only `research/softwarex/tests` omits those nested modules. After complete snapshot verification, use a new output name on every run:

```sh
"$study_env/bin/python" -B -m research.softwarex.run_publication_tests --output research/softwarex/evidence/reviewer-offline-tests-unique
# Windows PowerShell:
# & $studyPython -B -m research.softwarex.run_publication_tests --output research/softwarex/evidence/reviewer-offline-tests-unique
```

This optional test run creates new evidence files inside the checkout, so it changes the file inventory relative to the distributed manifest. Keep that modified test copy separate from the pristine snapshot used for manifest verification. Retained publication test records, including prior failures and their explanations, remain separate from the historical 465-pass public-layout regression run.

`research/sqj/REEXECUTION.md` documents fresh container experiments separately. Those procedures acquire pinned public dependencies and execute upstream test code, require a suitable isolated laboratory, and create **new** evidence; they are not needed to inspect the recorded data. Preserve the original evidence when rerunning. Balanced short-subject replication is described in `research/sqj/strengthening/PROTOCOL.md`; any subsequently included state-rejoin experiment is a separately labeled bounded example, not a replay of the sampled AI cohort.

## Operating and AI-integration boundaries

- Whole-task reuse is result-only: cache hits do **not** replay historical stdout/stderr or generated files. If an AI client needs a fresh diagnostic transcript, execute the tests; do not substitute cached success for equivalent agent behavior.
- Reuse requires complete declared inputs, a deterministic execution contract, and the required external operator review/authorization. Generated candidates, repository files, and observation receipts are not authorization. Do not let an AI agent approve its own cache eligibility.
- Unknown, unsupported, changed, failed, or uncertain requests must remain fresh execution or refusal/bypass according to the API contract. Failure is not published as a successful reusable result.
- The [MCP tool and response reference](research/softwarex/OPERATING_GUIDE.md#the-seven-shipped-tools) distinguishes actual `HIT_REUSED` results from fresh execution and errors. In particular, `mode: "reuse"` is not evidence of a hit. Client compatibility checks are not evidence of improved AI task completion, token savings, or production reliability.
- The compact [client API card](research/softwarex/CLIENT_API_CARD.md) documents the only accepted `run_tests` arguments and fresh-validation semantics. Client tool permission and ZeroRun's external authority are separate checks. The experiment's invocation-local approval covered only its isolated synthetic fixture, not real repositories or global client settings.
- Never apply the laboratory's disposable cache-authentication fixtures to a production repository. Public artifacts contain neither those keys nor production authority receipts.

## What the evidence establishes

**Practical interpretation.** Consumer wait time and complete producer-consumer cost answer different questions. The earlier clean image pilot repeat reduced consumer latency about 71% while increasing chain time 10.7%; the original two-case main observation reduced chain time 23.2%. A separately amended repeat has now attempted all 24 selected cases: 15 completed across seven repositories and nine remained incomplete or unsupported. Its guest-clock totals were near break-even, but a host-storage interruption prevents treating it as uninterrupted performance replication. No cases or dependencies were replaced after outcomes. See the [complete coverage audit](research/softwarex/APPLICATION_COVERAGE_AUDIT.md).

The [tested public-source reproduction procedure](research/softwarex/FRESH_REAL_WORKLOAD_REPRODUCTION.md) rebuilt the dependency-image recipe and completed both original pilot cases with all four paired blocks and agreeing fresh oracles. Consumer latency fell 75.8%, while complete chain time increased 10.5%. The existing VM and Docker cache were reused; neither independent-laboratory nor cache-independent bit-identical rebuilding is claimed. The original 42.3% copy-pilot overhead and every retained failure remain visible. These observations support qualified prior-status handoffs, not population-wide acceleration.

The controlled experiments measure complete request costs and compare fresh outcomes under their stated contract, including failure and restoration requests. The independent inventory tests examine input-key boundaries; they are not a proof that arbitrary programs are deterministic.

The separate public AI-trace cohort contains 128 selected episodes and 122 analyzable episodes, with six exclusions retained. All 120 exact-command repeat pairs have intervening barriers. These observations motivate explicit state validation; they are **not measured cache hits, AI speedups, or demonstrated reuse eligibility**. The ten-episode parser pilot and the first rate-limited acquisition attempt remain separate and preserved.

The [application-results ledger](research/softwarex/APPLICATION_RESULTS.md) preserves three earlier adverse client trials and the successful bounded API-guided demonstration. V3 completed four actual model lifecycle turns and two fresh oracle checks before an unsupported argument stopped its next turn. The guided treatment separately passed two model-selected actions, two fresh oracles, and six no-tool interpretation cases. These are synthetic interface demonstrations, not eight autonomous coding tasks, population accuracy, or a causal evaluation of the documentation.

The current 0.5.2 [laboratory procedure](research/softwarex/QUICKSTART_052.md) has separately retained [public installation](research/softwarex/evidence/quickstart-public-052-v1/install.json) and [five-stage STDIO](research/softwarex/evidence/quickstart-public-052-v1/check.json) receipts. The historical 0.5.1 [public quickstart](research/softwarex/QUICKSTART_LAB.md) and its [installation](research/softwarex/evidence/quickstart-public-v1/install.json) and [server check](research/softwarex/evidence/quickstart-public-v1/check.json) remain separate. Use the 0.5.2 procedure for a new run. Both are internal reproduction, not external user validation or model evidence. The original fine-grained 5x/50% product gates remain unmet; they are not claims made by this SoftwareX manuscript.

There are no measured external users, commercial deployments, or established market-demand results in this release. Its contribution is a reusable tool and evaluation artifact with explicit limitations, not a new general caching algorithm or a claim of journal acceptance.

## Submission archive provenance

The final submission files, when present under `output/submission/`, are assembled in two stages. First, the checked code/evidence/manuscript inventory is frozen and used to build `SoftwareX_source.zip` and `ZeroRun_SoftwareX_reviewer.zip`. Each archive contains its own exact member inventory; the reviewer archive also preserves the **pre-archive** `PUBLIC_RELEASE_MANIFEST.json`. The artifact-build receipt identifies the input inventory and the resulting archive hashes.

Second, the public release is refreshed to include those completed archives and their receipt. Its newer root manifest hashes the archive bytes, so its hash intentionally differs from the earlier manifest sealed inside the reviewer archive. The reviewer archive does not contain itself, the subsequent enclosing manifest, or the later artifact receipt. This acyclic binding permits independent verification without a circular self-hash. Initial-publication and final-readiness receipts describe their own observed stage; they do not establish author approval or journal acceptance.

The tag `softwarex-0.5.2-20260907` and its archives remain sealed at `0528905a52b74df78aa4e5a09219df34620282dd`. The separate `softwarex-0.5.2-20260907-r2` revision preserves that earlier release and adds revised interpretation and separately identified application evidence. Earlier VM receipts retain their original source bindings; use this revision's own manifest and final verification result for the revised package.

The author-facing cover letter, highlights, declaration checklist, and final manuscript must still be reviewed and approved by Jishan Kapoor. Artifact preparation does not submit the paper, select a publisher agreement, or authorize a payment.

## License and attribution

ZeroRun-owned code is MIT licensed; `LICENSE.txt` and `Licence.txt` are identical. Public trajectory data remains CC-BY-4.0 with its original notices. See `THIRD_PARTY_NOTICES.md` and the per-collection `source-notices/` directories. Dependencies and upstream projects retain their own licenses.

This software is provided without warranty. Report reproducible issues with the exact release commit, command, platform, and a minimal non-sensitive example. Do not include access tokens, private repository contents, or operator keys.
