# ZeroRun research release

Software for **ZeroRun: Reproducible test-result reuse for AI coding tools**, by Jishan Kapoor, Independent researcher, Toronto, Canada. Correspondence: kapoorjishan2@gmail.com.

ZeroRun 0.5.3 provides CLI and MCP interfaces for reusing a prior successful test result under an explicitly reviewed input and runtime contract. Start with a **whole-task handoff**: validate a qualified task once, then let another coding tool check its status against the same declared state. A `HIT_REUSED` reports earlier success; it does not supply fresh stdout, stderr, or generated artifacts. Request `run_tests` with `verify: true` when you need a new execution and diagnostics.

Choose one of the three routes below. Release `softwarex-0.5.3-20260908-r3` is the target distribution; use its assets once they are available on the [release page](https://github.com/floxy-21/zerorun-research/releases/tag/softwarex-0.5.3-20260908-r3). Availability and validation are established by that snapshot's [manifest](PUBLIC_RELEASE_MANIFEST.json) and [readiness report](research/softwarex/generated/final-readiness.json), not by this README or a historical test receipt.

## 1. Install the wheel

Use Python 3.10 or later. Download `zerorun-0.5.3-py3-none-any.whl` from the release page into a directory **outside any repository checkout**, then create a fresh external environment. For example, on Linux/macOS, from the directory containing the downloaded wheel:

```sh
study_env="$(mktemp -d)/venv"
python3 -m venv "$study_env"
"$study_env/bin/python" -m pip install --no-index ./zerorun-0.5.3-py3-none-any.whl
"$study_env/bin/python" -m zerorun --help
```

The wheel has no third-party Python runtime dependencies and requires no source build. On Windows, create the external environment with `python -m venv` and use its `Scripts/python.exe`. Keep both ZeroRun and coding-client launchers outside the target checkout. Installing the CLI on Windows or macOS does not establish support for the Linux whole-task execution environment.

The [operating guide](research/softwarex/OPERATING_GUIDE.md) explains task setup, MCP integration, and the default or explicitly configured external operator trust root. The [manifest reference](research/softwarex/MANIFEST_REFERENCE.md) and [client API card](research/softwarex/CLIENT_API_CARD.md) explain qualification and result interpretation. Installation and generated configuration do not authorize reuse. An operator must review the declared inputs, dependencies, runtime and determinism; a coding model must not grant itself authority. `mode: "reuse"` alone is not evidence of a cache hit.

## 2. Run the account-free MCP example

Follow the [0.5.3 laboratory quickstart](research/softwarex/QUICKSTART_053_METADATA_V2.md) on non-root Linux/amd64 with Python 3.10+, Git and Docker. It needs **no AI account, login or model credits**. The guide creates an external installation and exercises the actual installed MCP server against a fixed synthetic fixture: refusal without authority, explicit qualification, fresh execution, reuse and fresh verification. It records the observed responses and installation identity so the result can be checked afterward.

Read its fixture and authority scope before running it. The example authorizes only its declared laboratory fixture; it does not qualify your repository or arbitrary future edits. Docker images and dependencies have separate prerequisites described in the guide. This route demonstrates executable software behavior, not a natural coding-agent experiment or a speedup guarantee.

## 3. Verify the complete evidence package offline

Use **CPython 3.12-3.14** and a clean Git checkout. This route needs no installation, Docker, AI account or model execution. Once the release is available:

```sh
git clone --config core.longpaths=true --depth 1 --branch softwarex-0.5.3-20260908-r3 https://github.com/floxy-21/zerorun-research.git zerorun-review
cd zerorun-review
git rev-parse HEAD
curl --fail --location --output output/submission/ZeroRun_SoftwareX_reviewer.zip https://github.com/floxy-21/zerorun-research/releases/download/softwarex-0.5.3-20260908-r3/ZeroRun_SoftwareX_reviewer.zip
python3 -B -m research.softwarex.verify_submission
```

Compare the commit with the release page. In Windows PowerShell, use `python` and `curl.exe`. Downloading needs network access; verification does not. Exit status `0` together with JSON `"passed": true` means the required checks passed for that exact snapshot. Missing or changed payloads fail verification. Save any redirected report outside the checkout.

**Run complete verification from the Git checkout, not an extracted reviewer ZIP.** The separate ZIP contains an earlier sealed evidence inventory and cannot contain itself or the later artifact receipt. It belongs at the exact path above. Keep this checkout pristine: do not install from source, add notes or run tests there. Even with an external environment, `pip install .` can create files in the source tree. See the [verification guide](research/softwarex/VERIFY_SUBMISSION.md) for the package boundaries and individual checks.

## Evidence, limitations and attribution

The release also provides `zerorun-softwarex.pdf`, `SoftwareX_source.zip` and `ZeroRun_SoftwareX_reviewer.zip`; the [upload guide](research/softwarex/UPLOAD_GUIDE.md) describes their journal roles. Publishing artifacts does not establish journal acceptance or complete author-controlled submission steps.

Use the manuscript and [application results](research/softwarex/APPLICATION_RESULTS.md) for measured outcomes, including overhead, unsuccessful attempts and operating limits. Historical 0.5.1 experiments and 0.5.2 receipts retain their original identities; they do not certify changed 0.5.3 integration. The [coverage audit](research/softwarex/APPLICATION_COVERAGE_AUDIT.md), [workload reproduction record](research/softwarex/FRESH_REAL_WORKLOAD_REPRODUCTION.md) and [layout correction](research/softwarex/PUBLIC_LAYOUT_CORRECTION.md) preserve qualifications and correction history. [Reproducibility instructions](research/softwarex/REPRODUCIBILITY.md) cover the complete test selection and new experiment runs; use a separate working copy because those procedures generate new evidence. Offline reconciliation does not rerun the recorded experiments.

ZeroRun-owned code is MIT licensed; [LICENSE.txt](LICENSE.txt) and [Licence.txt](Licence.txt) contain the same license. Included trajectory data and upstream projects retain their own licenses, including CC-BY-4.0 where specified. See [third-party notices](THIRD_PARTY_NOTICES.md) and the evidence collections' source notices. Report reproducible issues with the exact commit, platform and command, excluding credentials and private repository content.

The [15-row consumer and Lizard fixture audit](research/softwarex/CONSUMER_LIZARD_AUDIT.md) links recorded responses, model interpretations, unavailable protocol details and the exact upstream-backed fixture correction. The current article revision uses the unchanged, tested 0.5.3 wheel; its embedded package description still identifies the original r1 distribution of those same wheel bytes. This checkout and its evidence archives use the r3 publication identity above.

The [fresh Linux reproduction procedure](research/softwarex/FRESH_LINUX_053_REPRODUCTION.md) gives public prerequisite acquisition, an isolated source/wheel installation and actual server execution. It preserves the original metadata-capture failure and the narrowly corrected research helper; the new Linux userland shares the Windows host and is not an independent human replication.

Windows: use a short parent directory for the checkout, such as `C:/work`. The clone command enables Git long-path handling only for that repository. If an earlier deep-path checkout failed, preserve it and create a new short-path checkout; do not omit evidence files to make verification pass.
