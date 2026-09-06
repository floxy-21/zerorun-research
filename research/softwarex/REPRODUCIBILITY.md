# Reproduce the SoftwareX research release

The manuscript is **ZeroRun: Reproducible test-result reuse for AI coding tools**, by Jishan Kapoor. There are three separate workflows below. Reading the recorded evidence does not require Docker, private GitHub access, an AI subscription, or model API credits.

## 1. Install and inspect the submitted software

Clone the public `floxy-21/zerorun-research` repository at the exact commit given in the manuscript's metadata. Use a fresh Python environment. The package has no third-party runtime Python dependencies; its source build uses pinned setuptools. The release's root `README.md` supplies Linux and Windows commands. Do not accidentally substitute a globally installed ZeroRun for this version.

On Linux, from the release root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m zerorun --version
.venv/bin/python -m zerorun --help
```

On Windows replace `.venv/bin/python` with `.venv\Scripts\python.exe`. CLI inspection is cross-platform; the demonstrated whole-task result-reuse mode requires Linux/amd64 and Docker. An unsupported host is not evidence that Linux reuse works there.

The public release uses `src/zerorun` for packaging. The frozen original experiment source is separately included under `research/sqj/source-final`, and exact Git source bytes under `source-ci-final`. The binding manifest records physical newline differences. Do not silently replace the frozen experiment source with an installed version when reproducing timings.

## 2. Check recorded evidence without executing agent commands

Run from the release root using its environment:

```sh
.venv/bin/python -m research.sqj.strengthening.validate_traces --check
.venv/bin/python -m research.sqj.analyze_comparison --check
.venv/bin/python -m research.sqj.strengthening.analyze_replication \
  --directory research/sqj/strengthening/evidence/short-randomized-replication-v1 \
  --output research/sqj/strengthening/evidence/replication-analysis-v1.json --check
.venv/bin/python -m research.sqj.strengthening.analyze_state_rejoin \
  --directory research/sqj/strengthening/evidence/agent-state-rejoin-v1 \
  --replication-directory research/sqj/strengthening/evidence/short-randomized-replication-v1 \
  --output research/sqj/strengthening/evidence/state-rejoin-analysis-v1.json --check
.venv/bin/python -m research.softwarex.build_paper --check
```

These commands verify raw-result consistency, source identities, denominators, original failures, restored source, and generated article inputs. They fail on missing or modified evidence. They do not execute shell strings or code from the downloaded AI trajectories. The original publication snapshot and the new extension are retained separately, not pooled into a new favorable dataset.

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

The small django-environ state-restoration case has its own source archive and `research/sqj/strengthening/STATE_REJOIN.md`. Its original recorded environment is **not** reproduced: the controlled case uses the existing Python 3.12.14 / pytest 9.1.1 locked runtime. The intermediate 15-test check is extra counterfactual instrumentation, not an observed agent action. A successful case is a result-only mechanism demonstration, not an autonomous-agent benchmark, historical-output replay, or TVCache comparison.

Never export an entire live study directory. The extension exporter explicitly allows only the named studies and excludes private cache keys, workspaces, engine state, and installed dependencies:

```sh
"$study_dir/venv/bin/python" -m research.sqj.strengthening.export_evidence \
  --results "$study_dir" --output "$study_dir/extension-export.tar.gz" \
  --select short-randomized-replication-v1
```

The existing `research.sqj.import_public_evidence` verifies the archive inventory and every file hash before importing; it refuses to overwrite differing evidence. Fresh re-execution has a new Git context and new timestamps. Keep these truthful instead of editing them to imitate the original run.

## Build the article

`research/softwarex/build_paper.py` reads reconciled evidence and produces `paper/main.tex`, `paper/references.bib`, and `generated/paper-evidence.json`. A final build refuses incomplete replication data or an unverified public source pointer. Its `--preview` mode writes visibly incomplete layout material only under `tmp/pdfs/softwarex-layout`.

The final manuscript uses Elsevier's `elsarticle` preprint class and numeric bibliography style. The source bundle is flat for Editorial Manager: `main.tex`, `references.bib`, `main.bbl`, `elsarticle.cls`, and `elsarticle-num.bst`, plus the license/source notice. The architecture figure is editable LaTeX, not an external raster image. Compile using a standard LaTeX/BibTeX installation or Tectonic. The final PDF was rendered and inspected page by page; mechanical checks alone are not visual QA.

## What is not a reproduction target

The release does not establish universal speedup, a passed 5x/50% product gate, equivalent autonomous-agent decisions, increased model accuracy, commercial demand, or a journal acceptance probability. It provides inspectable software, bounded examples, and the raw evidence needed to evaluate those narrower claims independently.
