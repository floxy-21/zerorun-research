# Fresh dependency image and real-workload reproduction

Status after sealed-record verification on 7 September 2026: **author-side public-source recipe build and real-workload reruns completed, including a separate v3 build with Docker build-cache reuse disabled**. This procedure was fixed before execution. Its completed attempt, measured outcomes and environment limits are recorded below; earlier image-build and handoff receipts remain separate.

This is a fresh reproduction of the controlled reference-patch handoff experiment. It rebuilds dependencies from the public recipe and runs both frozen SQLGlot pilot cases, including an independent fresh oracle and both counterbalanced paired blocks. It makes no model calls and is not a new autonomous coding-agent study. It uses the study's historical 0.5.1 runtime; it does not substitute for regression verification of a subsequently revised product release.

## Requirements and fixed identities

Use a non-root Linux/amd64 account with Python 3.10+, Python's `venv` support, Git, an operator-controlled Docker daemon, adequate free disk space, and permission for the stated source/dependency downloads and isolated containers. Keep other benchmarks out of the timed VM. The loopback registry uses port 19519; establish that this port is unused before proceeding. A complete new attempt uses new directories and retains every failure. Do not retry until a desired timing appears.

The public repository is `https://github.com/floxy-21/zerorun-research.git`.

| Component | Exact identity |
| --- | --- |
| Published study harness and case inputs | `0528905a52b74df78aa4e5a09219df34620282dd` |
| Harness public manifest SHA-256 | `229ce2c029555dc8e132a36beca4d24bfae524ded68e6fce1d1801a3f7b2dcb0` |
| Historical public engine checkout | `ebf2884df12573d63f45813200e0675288d12096` |
| Engine public manifest SHA-256 | `f7a01e6a16f32022ce686a4a213e073c83a8f77de82df0c9df357c9c31ab6564` |
| Historical runtime core | `86f42289c2f59a74b1f642f0b20d1a26b3d55e57` |
| Exact 36-module runtime-row digest | `01352a1e63f0a6eaad4a7d3daa8b03181def064c364e791a3ab0b0449266dd41` |
| Benchmark helper SHA-256 | `96588c65ee674597e4c459651345b9e89ac92cbdc0c85baaa3de4f57f6a44359` |
| Dependency lock SHA-256 | `a04f815c62754114a0f0a4b7db15d02c920e7c7d006d813ac9955be00482c02c` |
| Unchanged two-case pilot ledger SHA-256 | `2017a0414475b7a0bc19da04cc824c5d49c23b7713e6940865c5e87985756b59` |

The separate engine checkout matters. `handoff_v1.run.load_engine` requires the exact historical runtime and helper, so passing the current 0.5.3 package root fails. The earlier `6819078` public checkout also has different physical helper newline bytes and does not satisfy this experiment's helper digest. Use the verified `ebf2884` engine above; do not patch the runtime, rewrite the expected digest, or normalize the helper to make admission pass.

## 1. Obtain clean, separately pinned public checkouts

Run in Bash. The created laboratory is outside both source checkouts. The host runner uses the standard library and explicitly imports the bound historical engine; installing the current ZeroRun package into it is unnecessary.

```bash
set -euo pipefail
repro_lab=$(mktemp -d -t zerorun-real-rebuild-XXXXXXXX)
export repro_lab
repo_url=https://github.com/floxy-21/zerorun-research.git
harness_commit=0528905a52b74df78aa4e5a09219df34620282dd
engine_commit=ebf2884df12573d63f45813200e0675288d12096

mkdir "$repro_lab/preparation" "$repro_lab/records"
git -c init.templateDir= init "$repro_lab/harness"
git -C "$repro_lab/harness" config core.autocrlf false
git -C "$repro_lab/harness" remote add origin "$repo_url"
git -C "$repro_lab/harness" fetch --depth=1 origin "$harness_commit"
git -C "$repro_lab/harness" checkout --detach "$harness_commit"
git -c init.templateDir= init "$repro_lab/engine"
git -C "$repro_lab/engine" config core.autocrlf false
git -C "$repro_lab/engine" remote add origin "$repo_url"
git -C "$repro_lab/engine" fetch --depth=1 origin "$engine_commit"
git -C "$repro_lab/engine" checkout --detach "$engine_commit"
python3 -m venv "$repro_lab/runner"
cd "$repro_lab/harness"

"$repro_lab/runner/bin/python" -B - <<'PY'
import hashlib, json, os, subprocess
from pathlib import Path
from research.softwarex.handoff_v1 import run as h

lab = Path(os.environ['repro_lab'])
source, engine = lab / 'harness', lab / 'engine'
expected = {
    source: ('0528905a52b74df78aa4e5a09219df34620282dd',
             '229ce2c029555dc8e132a36beca4d24bfae524ded68e6fce1d1801a3f7b2dcb0'),
    engine: ('ebf2884df12573d63f45813200e0675288d12096',
             'f7a01e6a16f32022ce686a4a213e073c83a8f77de82df0c9df357c9c31ab6564'),
}
checkouts = []
for root, (commit, manifest_sha) in expected.items():
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
    status = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'])
    manifest = (root / 'PUBLIC_RELEASE_MANIFEST.json').read_bytes()
    assert actual == commit and not status
    assert hashlib.sha256(manifest).hexdigest() == manifest_sha
    checkouts.append({'commit': actual, 'manifest_sha256': manifest_sha, 'clean': True})
ledger_path = source / 'research/softwarex/evidence/handoff-acquisition-recovery-v1/pilot.json'
ledger_raw = ledger_path.read_bytes()
assert hashlib.sha256(ledger_raw).hexdigest() == '2017a0414475b7a0bc19da04cc824c5d49c23b7713e6940865c5e87985756b59'
ledger = h.strict(ledger_raw)
cases = h.validate_ledger(ledger)
assert [c['case_id'] for c in cases] == ['tobymao__sqlglot-3425', 'tobymao__sqlglot-3756']
for case in cases:
    h.bound(ledger_path.parent, case['metadata'])
    h.bound(ledger_path.parent, case['source_archive'], h.MAX_ARCHIVE_BYTES)
binding = h.load_engine(engine)[-1]
assert binding['requirements']['sha256'] == 'a04f815c62754114a0f0a4b7db15d02c920e7c7d006d813ac9955be00482c02c'
h.save(lab / 'preparation/source-check.json', {
    'checkouts': checkouts, 'engine_binding': binding,
    'pilot_ledger_sha256': h.sha(ledger_raw),
    'case_ids': [c['case_id'] for c in cases],
    'new_execution_started': False,
})
print('Public sources, historical engine, and both frozen case inputs verified.')
PY
```

The ledger already contains public benchmark metadata and exact base-commit source archives, with their licenses and recorded acquisition identities. This route does not depend on downloading the old author's local derived image or re-querying mutable benchmark metadata. The source checker validates the shipped input bytes before execution; it does not yet claim oracle agreement.

## 2. Acquire prerequisites explicitly and build a new image

The two acquisition operations below are deliberate preparation, separate from the helper's image-build clock. Their logs are retained even if preparation fails. No registry credentials or payment are required by this procedure. The builder subsequently uses an isolated loopback registry and removes only the exact container it created; the private registry store remains local.

```bash
base_image=docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef
registry_image=docker.io/library/registry@sha256:46faa9a1ae6813194b53921a370f2f4f8c5e1aae228a89bceafef5847a6a3278
date -u +%FT%TZ > "$repro_lab/preparation/acquisition-started.txt"
docker pull --platform linux/amd64 "$base_image" \
  > "$repro_lab/preparation/base-pull.log" 2>&1
docker pull --platform linux/amd64 "$registry_image" \
  > "$repro_lab/preparation/registry-pull.log" 2>&1
date -u +%FT%TZ > "$repro_lab/preparation/acquisition-completed.txt"
docker version > "$repro_lab/preparation/docker-version.log" 2>&1
uname -a > "$repro_lab/preparation/host.log"

"$repro_lab/runner/bin/python" -B -m research.softwarex.handoff_image_v2.build_image \
  --engine "$repro_lab/engine" \
  --output "$repro_lab/handoff-image-build-fresh-v1" \
  --registry-image "$registry_image" --port 19519 \
  --approve-loopback-registry \
  > "$repro_lab/records/image-build.log" 2>&1

"$repro_lab/runner/bin/python" -B -m research.softwarex.handoff_image_v2.validate \
  "$repro_lab/handoff-image-build-fresh-v1" \
  > "$repro_lab/records/image-reconciliation.json"
```

The dependency helper acquires the exact hash-locked pytest/pretend closure; network access during this preparation is distinct from the subsequent `docker build --network=none`. The Dockerfile is embedded in the frozen builder and written into the local build context. The record binds that Dockerfile, the dependency lock, each copied dependency file, actual image IDs/RepoDigests, build commands, import probe, in-image file checks, cleanup, and setup duration.

A rebuilt image may have a different digest. The runner must use the new build receipt and the image it actually attests; no digest is invented or replaced by a configuration ID. There is no bit-identical rebuild or public-registry distribution claim. `validate_image`'s existing `independent_image_rebuild: false` is a per-build scope flag: it is not silently changed to certify this new sequence. A separate reproduction statement must bind the new source-check, build and rerun records.

## 3. Run the unchanged real-case pilot on the new image

This invocation fixes a 600-second campaign budget and the existing symmetric 120-second per-execution cap before outcomes. It preserves both pilot identities and all attempted operations. It is not the 24-case main cohort and must not be pooled into that cohort. There is no extra warmup, consumer, case substitution, reference-answer change, or retry-to-pass step.

```bash
"$repro_lab/runner/bin/python" -B -m research.softwarex.handoff_image_v2.run \
  --ledger "$repro_lab/harness/research/softwarex/evidence/handoff-acquisition-recovery-v1/pilot.json" \
  --engine "$repro_lab/engine" \
  --image-build "$repro_lab/handoff-image-build-fresh-v1" \
  --output "$repro_lab/handoff-pilot-fresh-image-v1" \
  --execution-seconds 120 --budget-seconds 600 --execute-reviewed-lab \
  > "$repro_lab/records/pilot-run.log" 2>&1

"$repro_lab/runner/bin/python" -B -m research.softwarex.handoff_image_v2.validate \
  "$repro_lab/handoff-pilot-fresh-image-v1" \
  --image-build "$repro_lab/handoff-image-build-fresh-v1" \
  --acquisition-base "$repro_lab/harness/research/softwarex/evidence/handoff-acquisition-recovery-v1" \
  > "$repro_lab/records/pilot-reconciliation.json"
```

For each supported repaired issue state, the helper first executes a compatibility oracle. Each of its two counterbalanced blocks then runs a cold producer plus one consumer in each arm, separate fresh diagnostics, and independent fresh outcome captures. The validator checks node identities/outcomes, source and runtime bindings, block order, hit/producer keys, every complete cost, and the full case disposition ledger. A successful validator return can reconcile a partial campaign; inspect completed cases and blocks rather than equating reconciliation with full completion. A material correctness discrepancy must remain a stop.

Report producer-plus-consumer time, consumer waiting, per-arm setup-inclusive time, diagnostics and oracle instrumentation separately. Add common image setup and prerequisite acquisition as separately reported costs; do not hide them or charge them only to the fresh arm. Slower total execution remains a valid reproduction outcome even if consumer waiting is lower.

## 4. Export records without private workspaces or keys

These existing exporters independently validate before and after their allowlisted copies. The new destination directories must not exist.

```bash
mkdir "$repro_lab/export"
"$repro_lab/runner/bin/python" -B -m research.softwarex.handoff_image_v2.export \
  "$repro_lab/handoff-image-build-fresh-v1" \
  "$repro_lab/export/handoff-image-build-fresh-v1"
"$repro_lab/runner/bin/python" -B -m research.softwarex.handoff_image_v2.export \
  "$repro_lab/handoff-pilot-fresh-image-v1" \
  "$repro_lab/export/handoff-pilot-fresh-image-v1" \
  --image-build "$repro_lab/export/handoff-image-build-fresh-v1"
"$repro_lab/runner/bin/python" -B -m research.softwarex.handoff_image_v2.validate \
  "$repro_lab/export/handoff-pilot-fresh-image-v1" \
  --image-build "$repro_lab/export/handoff-image-build-fresh-v1" \
  --acquisition-base "$repro_lab/harness/research/softwarex/evidence/handoff-acquisition-recovery-v1" \
  > "$repro_lab/records/exported-pilot-reconciliation.json"
```

The image exporter deliberately excludes `context`, `registry-store`, dependency workspaces and private cache authentication. It retains recipe hashes, the dependency inventory, provenance, command results and `EXPORT_MANIFEST.json`. Publish the separate source-check and preparation logs only after reviewing their named files; never recursively publish the entire laboratory. A failed image build is not accepted by the success exporter: preserve the original failed directory and raw commands separately instead of changing `passed` or overwriting the attempt.

## Publication integration and verified execution

The sealed attempt is retained at `evidence/application-revision-20260907-v2/record-only/`, with separately named `handoff-main-repeat-v1`, `handoff-image-build-fresh-v1` and `handoff-pilot-fresh-image-v1` exports. `verify_application_revision.py` independently checks the wrapper records, all 1,244 manifest-bound files, command outputs, clean source inventories and workload reconciliation. It confirmed execution from anonymous public checkouts of harness `0528905a52b74df78aa4e5a09219df34620282dd` and historical engine `ebf2884df12573d63f45813200e0675288d12096`, with exact clean source state before and after. The record manifest SHA-256 is `04e29e0062a6f0b1e3c6b175bba73f6ad450be14e9b48b593d2df8e6c068631e`.

The aggregate binds this fresh image and pilot separately from historical v2 runs. It also retains the earlier failed preflight at `evidence/application-revision-preflight-v1/record-only/`, the old failed image build, interrupted pilot, earlier clean repeat and partial main cohort. New records do not replace or reassign any old image binding.

The new build receipt requested `127.0.0.1:19519/zerorun-handoff-v2@sha256:155eee3ae061fd7ae8546e728f04705d368fccf79f8dd206059d201d478882c9`. Its observed image ID is `sha256:155eee3ae061fd7ae8546e728f04705d368fccf79f8dd206059d201d478882c9`, and configuration SHA-256 is `4d7707dcf5aaa18eb988f21f955339e67a1408f07b5712487288587a7a9dd5d2`. This is the **same observed image digest as the earlier build**, with both old port 19509 and new port 19519 repository digests present in the existing Docker daemon. The new build receipt and registry route establish a functional recipe build and rerun; the retained Docker build log contains five "Using cache" lines, establishing that existing build layers were reused. They do not prove a cold, bit-identical image rebuild or a different image identity.

Builder setup took **26.396 s**, including **11.312 s** for the locked dependency preparation. The wrapper measured **26.995 s** for its complete build command; these overlapping clocks must not be added. The dependency inventory contains 511 installed files. Explicit pulls of the pinned public Python base and registry image succeeded, but both reported that the image was already up to date. Their respective 1.097 s and 0.692 s wrapper clocks are separate prerequisites, not cold network-download measurements. Source checkout and runner preparation are also outside the builder setup clock. The recipe used its new loopback registry and does not require access to the old derived-image registry; the derived image is not claimed to be publicly pullable.

Both frozen SQLGlot pilot cases completed, yielding **2 of 2 cases, 4 paired blocks and 4 reused successes**, with fresh-oracle agreement and no material correctness stop. All complete paired costs are retained:

| Fresh-image pilot measure | Fresh execution | ZeroRun |
| --- | ---: | ---: |
| Producer plus consumer | 55.272 s | 61.101 s |
| Consumer waiting | 20.060 s | 4.857 s |
| Per-arm setup-inclusive producer plus consumer | 56.060 s | 61.908 s |

The full paired sequence was **10.546% slower**, while consumer waiting was **75.786% lower**. Both completed cases were slower over the full sequence: SQLGlot 3425 by 11.355% and SQLGlot 3756 by 9.675%. Common image preparation is additional and excluded from both arms above; diagnostics and fresh-oracle instrumentation retain their separate raw clocks. This is successful functional reproduction with unfavorable complete-sequence timing, not evidence of general acceleration.

The separately recorded main repeat retained all **24 preselected cases across 8 repositories**: **15 completed across 7 repositories**, with **30 paired blocks**, **9 incomplete or unsupported cases**, and no unrun cases. Complete paired chain totals were 240.692 s fresh versus 235.872 s ZeroRun; consumer totals were 113.499 s versus 24.359 s, and per-arm setup-inclusive chain totals were 242.796 s versus 237.787 s. Four of the 15 completed cases were slower: lizard 174 and lkml 85, 97 and 87. Incomplete cases and seven failed operations remain in the ledger and are not silently converted to successes or included in complete-pair ratios.

That main repeat was interrupted by host disk exhaustion and a VirtualBox pause observed at 08:07:57 UTC. The retained `evidence/application-revision-20260907-v2/host-interruption.json` records subsequent recovery using external-drive VM storage, clock uncertainty and the absence of uninterrupted timing certification. Its approximate 2.0% lower complete-pair aggregate is therefore not presented as a clean performance replication. Raw timestamps were preserved.

The fresh-image pilot followed on that resumed, existing author-side VM with external-drive storage and cached public images. This closes the functional public-source recipe-to-workload check in that environment; it is not an independent external laboratory replication, a fresh operating-system installation, a cold public-base acquisition test or proof of byte-identical rebuilds. It makes no additional model calls and does not establish natural reuse prevalence, end-to-end agent speedup, broad applicability or any acceptance probability.

## Separate v3 build without Docker build-cache reuse

The later sealed v3 attempt is retained at `evidence/application-revision-20260907-v3/record-only/`, with sibling `host-observation/` records. Its `provenance/build_image_no_cache_v3.py` adapter adds only `--no-cache` to the frozen builder's Docker build command, retaining `--pull=false` and `--network=none`. It verifies the frozen builder hash before invocation and records the original and actual arguments. The actual build log contains no `Using cache` steps. Public harness `0528905a52b74df78aa4e5a09219df34620282dd`, historical engine `ebf2884df12573d63f45813200e0675288d12096`, the original ledger, new image and fresh oracles were independently reconciled against their retained files.

The observed new reference is `127.0.0.1:19529/zerorun-handoff-v2@sha256:4684c5606a3cc79f9755bdde2ea364543a2fd2db2019e298ea70b411dae88c09`. Its configuration SHA-256 remains `4d7707dcf5aaa18eb988f21f955339e67a1408f07b5712487288587a7a9dd5d2`; the image digest differs from the cached v2 image. Image preparation took **66.070 s**, including **16.050 s** for locked dependency preparation. These are overlapping clocks, and common image preparation is additional to the paired workload ratios. The public base and registry images were already present; `--no-cache` disables derived-image build-step reuse, not reuse of pinned base layers. The same existing VM and external-drive storage were used. This establishes neither a clean operating-system installation, independent human replication nor a bit-identical image guarantee.

The main campaign kept the original 24 selected cases and fixed 1,200-second budget. It completed **10 cases across five repositories, 20 paired blocks and 20 reused successes**, with fresh-oracle agreement. **Six cases were incomplete or unsupported and eight were not run because the budget expired.** All 24 dispositions are retained; the campaign did not attempt or complete all 24 cases. No material correctness stop occurred. The wrapper's main command took 1,226.553 s because active work finished after the 1,200-second campaign boundary; this is a separate wall clock, not the sum or ratio of complete paired chains.

| v3 complete-case measure | Fresh execution | ZeroRun |
| --- | ---: | ---: |
| Producer plus consumer | 281.329 s | 280.985 s |
| Consumer waiting | 135.856 s | 28.353 s |
| Per-arm setup-inclusive producer plus consumer | 283.951 s | 283.812 s |

Complete producer-plus-consumer totals were near break-even (**0.122% lower**), while consumer waiting was **79.130% lower**. Per-arm setup-inclusive totals were 0.049% lower. Five of ten completed cases were slower: SQLGlot 3182 and 2658, lizard 174 and 241, and lkml 85. The median completed case was 1.707% slower. Image preparation, acquisition, failed or incomplete work, diagnostics and oracle instrumentation are not hidden in these complete-case ratios; their separate records remain available. No general speedup or causal improvement over earlier cohorts is inferred.

Independent host checks reconciled **132 samples with zero sample errors**, full run coverage, a maximum sampling gap of **16.737447 s**, and no qualification reasons. These are sampled continuity checks, not continuous proof of an idle or uncontended host. The earlier v2 pause remains attached to that separate historical campaign. The current aggregate exposes v3 as `controlled_runs.v3_main_clean` and binds its image, no-cache invocation, source and host records under `clean_public_source_reproduction`.

To repeat the no-cache build, use fresh laboratory directories and the same public harness/engine preparation above. Copy the published `record-only/provenance/build_image_no_cache_v3.py` adapter into an external adapter directory and verify its published hash before running it. Invoke it with `--harness`, `--engine`, `--output`, `--registry-image`, `--port 19529` and `--approve-loopback-registry`, using the pinned registry image above and an unused loopback port. Then run the unchanged `research.softwarex.handoff_image_v2.run` helper against `main.json`, the actual new `--image-build` directory and a new output directory, with `--execution-seconds 120 --budget-seconds 1200 --execute-reviewed-lab`. Validate and export with the matching image directory as in the earlier commands. Preserve every new disposition and failure; do not substitute an older image receipt or retry to select a favorable result.
