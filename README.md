# ZeroRun research release

Software for **ZeroRun: Reproducible test-result reuse for AI coding tools**, by Jishan Kapoor, Independent researcher, Toronto, Canada. Correspondence: kapoorjishan2@gmail.com.

This is a clean research distribution of the runtime at commit `86f42289c2f59a74b1f642f0b20d1a26b3d55e57`, packaged as version 0.5.1. It includes selected runtime regression tests, research drivers, immutable observations, and independently checked summaries. `PUBLIC_RELEASE_MANIFEST.json` records each file's bytes, origin, and SHA256. Runtime source bytes are unchanged; packaging uses a `src/` layout. Five file-path references in two selected tests are adapted to that layout and the relocated skill document; this is recorded in the manifest. The distribution does not contain development history or private operator credentials.

ZeroRun can reuse a prior successful **result** for explicitly configured deterministic tasks after checking the declared input contract. It provides CLI and MCP interfaces for integration with coding tools. It does not cache a language model's inference, infer that arbitrary commands are deterministic, or establish that repeated command text implies reusable state.

## Submission package

The completed submission snapshot includes the [manuscript PDF](output/pdf/zerorun-softwarex.pdf), [editable source](output/submission/SoftwareX_source.zip), [reviewer software and evidence](output/submission/ZeroRun_SoftwareX_reviewer.zip), and [author upload guide](research/softwarex/UPLOAD_GUIDE.md). Use [final-readiness.json](research/softwarex/generated/final-readiness.json) for exact checked hashes and remaining author-controlled steps. The manuscript's earlier immutable code/evidence pointer is intentionally distinct from this later submission snapshot. No journal submission or payment is performed by publishing these files.

## Install

Python 3.10 or later is required. From this repository root, create a fresh environment outside the checkout. Linux/macOS:

```sh
study_env="$(mktemp -d)/venv"
python3 -m venv "$study_env"
"$study_env/bin/python" -m pip install .
"$study_env/bin/python" -m zerorun --help
```

Windows PowerShell:

```powershell
$studyEnv = Join-Path $env:TEMP ('zerorun-env-' + [guid]::NewGuid().ToString('N'))
python -m venv $studyEnv
$studyPython = Join-Path $studyEnv 'Scripts/python.exe'
& $studyPython -m pip install .
& $studyPython -m zerorun --help
```

The package has no third-party Python runtime dependency; building uses setuptools. Pinned research containers, Git, Docker, and pytest are additional requirements for the relevant experiments. Recorded whole-task timings were obtained on Linux with a pinned Linux/amd64 Python container; do not assume those timings or all permission semantics transfer to Windows or macOS.

**For Codex/MCP integration, keep the ZeroRun and Codex console launchers outside the target checkout.** See the [operating guide](research/softwarex/OPERATING_GUIDE.md) for manual exact-byte authorization, seven MCP tools, and the fresh/reused/error response contract. The [manifest v2 reference](research/softwarex/MANIFEST_REFERENCE.md) explains the result-only fields and input-review requirements. Neither installation nor an automatically generated candidate authorizes reuse.

## Offline evidence checks

Run these from the repository root with the installed environment's Python. Use Python 3.11+ for offline research analysis, or additionally install `tomli` when using Python 3.10. These commands parse local observations and do **not** execute any recorded AI-agent command:

```sh
"$study_env/bin/python" -B -m research.sqj.strengthening.validate_traces --check
"$study_env/bin/python" -B -m research.sqj.analyze_comparison --check
"$study_env/bin/python" -B -m research.softwarex.analyze_operating_region --check
"$study_env/bin/python" -B -m research.softwarex.build_extension_evidence --check
# Windows PowerShell uses the corresponding form:
# & $studyPython -B -m research.softwarex.build_extension_evidence --check
```

To run the included unit tests, install pytest in the same environment and use a new temporary directory for each run:

```sh
"$study_env/bin/python" -m pip install pytest==9.0.2
"$study_env/bin/python" -B -m pytest tests research/sqj/strengthening/tests research/softwarex/tests -q --basetemp=/tmp/zerorun-research-tests-unique
# Windows PowerShell, with a new name on every run:
# & $studyPython -m pip install pytest==9.0.2
# & $studyPython -B -m pytest tests research/sqj/strengthening/tests research/softwarex/tests -q --basetemp="$env:TEMP/zerorun-research-tests-unique"
```

The temporary directory **must be outside every Git checkout**, on every platform. Some trust tests intentionally create incomplete `.git` fixtures; Git must not discover an ancestor repository. The first staging test attempt used a nested development-repository directory and exposed this harness constraint; its [failure report](research/softwarex/evidence/public-release-tests-1.xml) is retained separately from the corrected-location run. On Windows, use a fresh absolute path under a non-repository temporary directory. POSIX permission checks require a non-root Linux user and are explicitly skipped when the platform cannot express the tested behavior. The included tests are a selected research/runtime set, not a claim that every development-repository test is included.

The `research/softwarex/tests` directory additionally checks publication evidence bindings and archive construction. Its bounded test records, including prior failed attempts and their environment explanations, are retained under `research/softwarex/evidence` and `research/softwarex/generated`; these later publication checks are separate from the earlier 465-pass public-layout regression run.

`research/sqj/REEXECUTION.md` documents fresh container experiments separately. Those procedures acquire pinned public dependencies and execute upstream test code, require a suitable isolated laboratory, and create **new** evidence; they are not needed to inspect the recorded data. Preserve the original evidence when rerunning. Balanced short-subject replication is described in `research/sqj/strengthening/PROTOCOL.md`; any subsequently included state-rejoin experiment is a separately labeled bounded example, not a replay of the sampled AI cohort.

## Operating and AI-integration boundaries

- Whole-task reuse is result-only: cache hits do **not** replay historical stdout/stderr or generated files. If an AI client needs a fresh diagnostic transcript, execute the tests; do not substitute cached success for equivalent agent behavior.
- Reuse requires complete declared inputs, a deterministic execution contract, and the required external operator review/authorization. Generated candidates, repository files, and observation receipts are not authorization. Do not let an AI agent approve its own cache eligibility.
- Unknown, unsupported, changed, failed, or uncertain requests must remain fresh execution or refusal/bypass according to the API contract. Failure is not published as a successful reusable result.
- The [MCP tool and response reference](research/softwarex/OPERATING_GUIDE.md#the-seven-shipped-tools) distinguishes actual `HIT_REUSED` results from fresh execution and errors. In particular, `mode: "reuse"` is not evidence of a hit. Client compatibility checks are not evidence of improved AI task completion, token savings, or production reliability.
- Never apply the laboratory's disposable cache-authentication fixtures to a production repository. Public artifacts contain neither those keys nor production authority receipts.

## What the evidence establishes

The controlled experiments measure complete request costs and compare fresh outcomes under their stated contract, including failure and restoration requests. The independent inventory tests examine input-key boundaries; they are not a proof that arbitrary programs are deterministic.

The separate public AI-trace cohort contains 128 selected episodes and 122 analyzable episodes, with six exclusions retained. All 120 exact-command repeat pairs have intervening barriers. These observations motivate explicit state validation; they are **not measured cache hits, AI speedups, or demonstrated reuse eligibility**. The ten-episode parser pilot and the first rate-limited acquisition attempt remain separate and preserved.

There are no measured external users, commercial deployments, or established market-demand results in this release. Its contribution is a reusable tool and evaluation artifact with explicit limitations, not a new general caching algorithm or a claim of journal acceptance.

## Submission archive provenance

The final submission files, when present under `output/submission/`, are assembled in two stages. First, the checked code/evidence/manuscript inventory is frozen and used to build `SoftwareX_source.zip` and `ZeroRun_SoftwareX_reviewer.zip`. Each archive contains its own exact member inventory; the reviewer archive also preserves the **pre-archive** `PUBLIC_RELEASE_MANIFEST.json`. The artifact-build receipt identifies the input inventory and the resulting archive hashes.

Second, the public release is refreshed to include those completed archives and their receipt. Its newer root manifest hashes the archive bytes, so its hash intentionally differs from the earlier manifest sealed inside the reviewer archive. The reviewer archive does not contain itself, the subsequent enclosing manifest, or the later artifact receipt. This acyclic binding permits independent verification without a circular self-hash. Initial-publication and final-readiness receipts describe their own observed stage; they do not establish author approval or journal acceptance.

The author-facing cover letter, highlights, declaration checklist, and final manuscript must still be reviewed and approved by Jishan Kapoor. Artifact preparation does not submit the paper, select a publisher agreement, or authorize a payment.

## License and attribution

ZeroRun-owned code is MIT licensed; `LICENSE.txt` and `Licence.txt` are identical. Public trajectory data remains CC-BY-4.0 with its original notices. See `THIRD_PARTY_NOTICES.md` and the per-collection `source-notices/` directories. Dependencies and upstream projects retain their own licenses.

This software is provided without warranty. Report reproducible issues with the exact release commit, command, platform, and a minimal non-sensitive example. Do not include access tokens, private repository contents, or operator keys.
