# Reproduce the SoftwareX research release

The manuscript is **ZeroRun: Reproducible test-result reuse for AI coding tools**, by Jishan Kapoor. There are three separate workflows below. Reading the recorded evidence does not require Docker, private GitHub access, an AI subscription, or model API credits.

Start with [complete offline submission verification](VERIFY_SUBMISSION.md) using CPython 3.12–3.14, before installation, testing, or local notes change the checkout. It needs no package installation. Use a clean Git checkout for that command; the extracted reviewer ZIP contains the earlier sealed inventory and original README, so it cannot satisfy complete final-snapshot verification. The individual checks below remain available for inspecting that extracted evidence.

## 1. Install and inspect the submitted software

Clone the public `floxy-21/zerorun-research` repository at submission tag `softwarex-0.5.3-20260908-r1`; compare its commit with the versioned GitHub Release page. The earlier manuscript metadata pointer identifies its code/evidence stage, rather than the later completed submission. Use a fresh external Python environment. The package has no third-party runtime Python dependencies; the included wheel avoids a source build and network installation. The release's root `README.md` supplies Linux and Windows commands. Do not accidentally substitute a globally installed ZeroRun for this version.

On Linux, from the release root:

```sh
study_env="$(mktemp -d)/venv"
python3 -m venv "$study_env"
"$study_env/bin/python" -m pip install --no-index output/packages/zerorun-softwarex/zerorun-0.5.3-py3-none-any.whl
"$study_env/bin/python" -m zerorun --version
"$study_env/bin/python" -m zerorun --help
```

Keep the environment outside the source checkout. If building from source with setuptools, use a separate working copy: local source installation can create build and `.egg-info` files even when its virtual environment is external. For Windows PowerShell commands, use the root README's external-environment example. CLI inspection is cross-platform; the demonstrated whole-task result-reuse mode requires Linux/amd64 and Docker. An unsupported host is not evidence that Linux reuse works there. The operating workflow and exact manifest contract are documented in `OPERATING_GUIDE.md` and `MANIFEST_REFERENCE.md`. A new account-free server demonstration uses [QUICKSTART_053.md](QUICKSTART_053.md).

The public release uses `src/zerorun` for packaging. The frozen original experiment source is separately included under `research/sqj/source-final`, and exact Git source bytes under `source-ci-final`. The binding manifest records physical newline differences. Do not silently replace the frozen experiment source with an installed version when reproducing timings.

## 2. Check recorded evidence without executing agent commands

Run from the completed submission snapshot or extracted reviewer archive using its environment. Canonical timing-analysis and manuscript reproduction require **CPython 3.12–3.14**, independently of the runtime and account-free quickstart's Python 3.10+ support. Older Python float aggregation is not byte-identical to the archived analysis; adding `tomli` does not fix that difference. The [portability record](evidence/analysis-portability-v1/README.md) explains the separately versioned check and retained earlier outputs. The manuscript's C2 commit pins the code and raw evidence; the later submission snapshot adds the final manuscript and its public-pointer receipt. The raw-evidence validators run at the pinned code/evidence commit; `build_paper --check` additionally needs those later manuscript files.

```sh
"$study_env/bin/python" -B -m research.sqj.strengthening.validate_traces --check
"$study_env/bin/python" -B -m research.sqj.analyze_comparison --check
"$study_env/bin/python" -B -m research.softwarex.analysis_reproduction --check
"$study_env/bin/python" -B -m research.sqj.strengthening.analyze_state_rejoin \
  --directory research/sqj/strengthening/evidence/agent-state-rejoin-v3 \
  --replication-directory research/sqj/strengthening/evidence/short-randomized-replication-v1 \
  --output research/sqj/strengthening/evidence/state-rejoin-analysis-v1.json --check
"$study_env/bin/python" -B -m research.softwarex.analyze_operating_region --check
"$study_env/bin/python" -B -m research.softwarex.build_extension_evidence --check
"$study_env/bin/python" -B -m research.softwarex.build_application_evidence --check
"$study_env/bin/python" -B -m research.softwarex.build_paper --check
```

These individual diagnostics verify raw-result consistency, source identities, denominators, original failures, restored source, and generated article inputs. They do not replace the complete verifier, which also checks the current runtime, 0.5.3 quickstart, handoff evidence, and archives. They fail on missing or modified evidence. Use `-B` to avoid adding bytecode files to the exact release inventory. No shell strings or code from the downloaded AI trajectories are executed. The original publication snapshot and the new extension are retained separately, not pooled into a new favorable dataset.

### Pinned Linux analysis environment

If the host's default Python is 3.10 or 3.11, keep it for the supported product
quickstart and use this separate Linux/amd64 route for canonical analysis. From
the completed release root, the following function uses CPython 3.12.14 from
the experiment's digest-pinned image, with a read-only source mount, no network,
and no account or operator credentials. It runs with the host user's UID/GID so
the capability-dropped container can read that user's private extraction
directory without broadening directory permissions. No package installation is required
inside this analysis container. Acquire the public image once with
`docker pull` if it is not already present; image acquisition is not an offline
check or part of the reported quickstart timings.

```sh
analysis_image='docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef'
analysis_root="$(pwd -P)"
analysis_python() {
  docker run --rm --platform linux/amd64 --pull=never --network none --read-only --cap-drop ALL \
    --user "$(id -u):$(id -g)" \
    --security-opt no-new-privileges --cpus 2 --memory 2g --pids-limit 128 \
    --mount "type=bind,src=$analysis_root,dst=/artifact,readonly" \
    -w /artifact -e PYTHONPATH=/artifact/src -e PYTHONDONTWRITEBYTECODE=1 \
    "$analysis_image" python -B "$@"
}
analysis_python -m research.sqj.strengthening.validate_traces --check
analysis_python -m research.sqj.analyze_comparison --check
analysis_python -m research.softwarex.analysis_reproduction --check
analysis_python -m research.sqj.strengthening.analyze_state_rejoin \
  --directory research/sqj/strengthening/evidence/agent-state-rejoin-v3 \
  --replication-directory research/sqj/strengthening/evidence/short-randomized-replication-v1 \
  --output research/sqj/strengthening/evidence/state-rejoin-analysis-v1.json --check
analysis_python -m research.softwarex.analyze_operating_region --check
analysis_python -m research.softwarex.build_extension_evidence --check
analysis_python -m research.softwarex.build_application_evidence --check
analysis_python -m research.softwarex.build_paper --check
```

The last command requires the completed submission snapshot, not the earlier
code-and-evidence pin. It validates article source; LaTeX compilation is a
separate step below. This optional Docker route does not turn the pure-Python
recorded-evidence checks into a requirement for Docker when a supported local
CPython environment is available.

The frozen historical recovery analyzer remains unchanged. Its original `--check` compares a filesystem-discovery list byte-for-byte, so use the versioned `analysis_reproduction --check` above for portable verification. It re-executes that original analyzer and requires exact equality of every value after ordering only the declared zero-byte incident inventory; it does not change numerical equality, ordered experiment sequences, counts, hashes, or any raw observation. The original saved analysis retains its own bytes and order.

The main AI-trace sample has 128 selected rows and 122 valid episodes. Its six exclusions, the separate ten-row pilot, and the original 96 HTTP-429 retrieval failures are all retained. Dataset API snapshots are identified by downloaded bytes and observed revision headers, not falsely described as immutable revision-specific API URLs. Raw source notices retain CC-BY-4.0 attribution.

The independent inventory checks are in `research/sqj/strengthening/tests/test_inventory_oracle.py`. Linux evidence is `strengthening/evidence/strengthening-linux-2.xml`: 38 inventory cases and 16 driver cases pass. The earlier three failed directory-permission fixture cases are preserved in `inventory-linux-1.xml`; they are not the final passing receipt. Windows skips remain skips.

The clean public-release tests additionally exercise the installed/source-layout distribution. Use a fresh test directory **outside every Git checkout**; the tests intentionally construct empty Git metadata, and placing those fixtures inside an ancestor checkout changes their meaning. The retained initial nested-checkout failure and corrected run are identified in `generated/public-release-tests.json`. The root README gives the tested commands and separates primary cases, platform skips, and subtests.

## 3. Generate new Linux execution evidence

New execution requires a non-root isolated Linux/amd64 laboratory with Git and Docker. It downloads public workload source and hash-locked dependency wheels, then executes reviewed pytest targets in network-disabled containers. It does not call an AI model or create production operator authorization. Expect different timings on different hardware.

For the original five-library experiment, follow `research/sqj/REEXECUTION.md`. Preserve all new failures and receipts; do not overwrite the supplied data or manufacture the original timeout on different hardware. The expensive more-itertools/Testmon workload is not required to inspect this release.

For the balanced four-short-subject extension, prepare the isolated `study_dir/engine` and `study_dir/workloads` exactly as described there. From the public release root, run the following with the prepared environment (not a Python process that already imported a different ZeroRun):

```sh
"$study_dir/venv/bin/python" -m research.sqj.strengthening.randomized_replication \
  --engine "$study_dir/engine" --workloads "$study_dir/workloads" \
  --output "$study_dir/short-randomized-replication-v1"
```

The driver freezes four subjects, six order-balanced blocks each, seven request states per block, and complete invocation clocks before execution. It stops the affected workload on a material disagreement and retains the failure. Do not run a regression workload concurrently inside the timed VM. Setup is kept outside request clocks and reported separately, not erased.

The supplied run suffered a VirtualBox host assertion after 21 complete blocks and part of Packaging block four. `run_replication_recovery.py` records a separate post-interruption amendment and restarts only preselected unfinished blocks four through six. Its initial preflight failed while reading a zero-byte crash remnant; that failed log and original recovery helper are retained. The corrected planner preserves empty records as unavailable, not passing, and the independent `analyze_recovered_replication.py` reconciles the original and new attempts without modifying either. Completed-block ratios, additional interrupted-attempt costs, and missing invocation records remain separate. Packaging's original environment-layer setup total was never flushed, so its setup-inclusive ratio is unavailable. This is not uninterrupted fulfillment of the original no-retry protocol; a new reproduction should not manufacture the same host crash.

The small django-environ state-restoration case has its own source archive and `research/sqj/strengthening/STATE_REJOIN.md`. Its original recorded environment is **not** reproduced: the controlled case uses the existing Python 3.12.14 / pytest 9.1.1 locked runtime. The intermediate 15-test check is extra counterfactual instrumentation, not an observed agent action. A successful case is a result-only mechanism demonstration, not an autonomous-agent benchmark, historical-output replay, or TVCache comparison. The post-interruption wrapper binds the original prospective protocol and unchanged runtime source, without pretending the original campaign completed.

All three state-case attempts are retained separately. `agent-state-rejoin-v1` refused a nine-fractional-digit timestamp during preflight, before invoking any test. The subsequent interruption receipt uses Python-3.10-compatible microsecond precision; it does not overwrite the original receipt. `agent-state-rejoin-v2` then encountered the existing baseline guard requiring ordinary Git metadata in the reconstructed fixture. The final case directory is `agent-state-rejoin-v3`, produced by the separately versioned `state_rejoin_v2.py` and `run_recovered_state_rejoin_v2.py`.

The fixture correction initializes a new ordinary local Git directory with templates disabled, no network access, no inherited Git configuration, and a checked canonical root. It does not reconstruct historical commits or change workload source, expected outcomes, cache eligibility, or the runtime's safety guard. The pre-existing container contract masks Git metadata; the corrected runner explicitly excludes this fixture metadata from source-identity comparisons alongside runtime bookkeeping. The retained `case/git-fixture.json` records these checks, and the independent analyzer requires this exact correction rather than accepting any arbitrary exclusion. Earlier failures are not described as successful requests.

Never export an entire live study directory. The extension exporter explicitly allows only the named studies and excludes private cache keys, workspaces, engine state, and installed dependencies:

```sh
"$study_dir/venv/bin/python" -m research.sqj.strengthening.export_recovered_evidence \
  --results "$study_dir" --output "$study_dir/extension-export.tar.gz" \
  --select short-randomized-replication-v1 short-randomized-recovery-v1 \
    agent-state-rejoin-v1 agent-state-rejoin-v2 agent-state-rejoin-v3
```

The existing `research.sqj.import_public_evidence` verifies the archive inventory and every file hash before importing; it refuses to overwrite differing evidence. Fresh re-execution has a new Git context and new timestamps. Keep these truthful instead of editing them to imitate the original run.

## Historical V1 client extension and configuration diagnostic

`generated/extension-evidence-v1.json` independently reconciles the scripted
consumer, installed-server refusal/readiness checks, actual Codex receipt,
separate configuration diagnostic, and operating-region calculation. It binds
raw requests and responses, unchanged source identities, and retained failures.
The offline `--check` commands require no model credentials or Docker.

The recorded Codex trial stopped after its first doctor call: the MCP process
did not find matching external authority, and an extra commentary message also
violated the frozen exact-message harness. No execution/reuse/verification turn
followed and the model trial was not retried. Its complete receipt remains at
`evidence/live-client-v1/receipt.json`.

The separately frozen non-model diagnostic records a fresh synthetic fixture,
a missing-variable control, and an explicit external-trust-path intervention.
All five planned exchanges passed; it did not invoke Codex or demonstrate a
corrected autonomous-agent run. See `NON_MODEL_DIAGNOSTIC_PROTOCOL.md`,
`evidence/live-client-v1/non-model-diagnostic.json`, and the operating guide's
configuration/reproduction instructions. Do not supply a production repository
or silently authorize a generated manifest. An independent reproduction uses
new timestamps and output paths, never rewrites the archived evidence.

`OPERATING_REGION.md` freezes the post-hoc calculation and its interpretation:
the 57.14% hit fraction is imposed by constructed sequences, not an observed AI
workflow frequency. Subject and block crossovers preserve within-category
composition; deployment/review cost and application-level benefits are unmeasured.

## Later client application and public first-use evidence

[Application results](APPLICATION_RESULTS.md) gives the complete attempt ledger, including V1, V2, V3, and the separately frozen guided treatment. The [V2 protocol](live_client_v2/PROTOCOL.md) led to one passing doctor turn followed by a client-side approval refusal. Under the separately approved [V3 protocol](live_client_v3/PROTOCOL.md), four actual model lifecycle turns and two separate fresh oracles passed; the fifth model turn supplied an unsupported argument and the trial stopped. Neither full plan is relabeled as successful.

The [guided protocol](guided_client_v1/PROTOCOL.md) used the exact frozen [API card](CLIENT_API_CARD.md), two non-model setup tool calls plus discovery, two actual model-selected `run_tests` calls, two separate fresh container oracles, and six no-tool model interpretation cases. All its planned checks passed. The two controlled adverse interpretation inputs remain synthetic, and the fresh seed remains non-model setup; these are not eight real coding tasks. Its raw [receipt](evidence/guided-client-v1/receipt.json), prospective freeze, and unmodified event log are retained alongside all earlier trials.

V1 and its diagnostic use source `681907860dc2ab9df70034f82a0025463d1fdec4`. V2, V3, and the guided treatment instead pin `ebf2884df12573d63f45813200e0675288d12096`, with its own manifest and unchanged 36-file runtime inventory. The [operating guide](OPERATING_GUIDE.md#inspect-or-reproduce-the-bounded-integration-experiments) keeps their reproduction instructions separate. Later public artifact commits do not rewrite those experimental identities. V3/guided tool preauthorization was invocation-local, explicitly operator-approved for the original isolated fixture, and did not change global approval settings, the read-only sandbox, unrelated-tool restrictions, real-repository authority, or runtime source.

The [account-free public quickstart](QUICKSTART_LAB.md) was also replayed literally from clean public commit `860675c041c5190dcbae0892d64c8ba82b257bb8` without intervention: seven recorded installation commands passed in 18.645573 seconds, then five actual STDIO stages passed in 11.340818 seconds. Those clocks exclude cloning, Docker setup, and researcher preparation. The [installation receipt](evidence/quickstart-public-v1/install.json) and [server receipt](evidence/quickstart-public-v1/check.json) bind all 36 runtime files. This installation is distinct from the model experiment's installed environment; both contain the same frozen runtime. It is an internal reproduction, not five independent developers or a user study.

`python -B -m research.softwarex.build_application_evidence --check` recomputes [application-evidence-v1.json](generated/application-evidence-v1.json) from raw records and frozen validators. It checks the earlier adverse trials, guided denominators, source/receipt identities, and both laboratory and literal-public-guide installation records. No model is invoked. Successful fixed cases do not establish population accuracy, causal improvement from documentation, autonomous workflow speedup, or commercial adoption.

## Build the article

`research/softwarex/build_paper.py` reads reconciled evidence and produces `paper/main.tex`, `paper/references.bib`, and `generated/paper-evidence.json`. A final build refuses incomplete replication data or an unverified public source pointer. Its `--preview` mode writes visibly incomplete layout material only under `tmp/pdfs/softwarex-layout`.

The final manuscript uses Elsevier's `elsarticle` preprint class and numeric bibliography style. The source bundle is flat for Editorial Manager: `main.tex`, `references.bib`, `main.bbl`, `elsarticle.cls`, and `elsarticle-num.bst`, plus the license/source notice. The architecture figure is editable LaTeX, not an external raster image. Compile using a standard LaTeX/BibTeX installation or Tectonic. The final PDF was rendered and inspected page by page; mechanical checks alone are not visual QA.

## What is not a reproduction target

The release does not establish universal speedup, a passed 5x/50% product gate, equivalent autonomous-agent decisions, increased model accuracy, commercial demand, or a journal acceptance probability. It provides inspectable software, bounded examples, and the raw evidence needed to evaluate those narrower claims independently.
