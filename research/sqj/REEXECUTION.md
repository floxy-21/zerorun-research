# Fresh Linux re-execution, separate from offline analysis

These commands are for an isolated reviewer machine with Git, Python 3.10+,
Docker, and permission to run containers. They download public source and pinned
dependencies and execute the declared upstream tests in containers. They do not
call an AI model or create production operator authorization. Do not run the
laboratory activation procedure against a production repository.

The current paper's recorded experiment used Ubuntu 22.04, five virtual CPUs,
about 2.4 GiB RAM, and a fixed Linux/amd64 CPython-3.12.14 image. New hardware
produces new timings; do not overwrite the supplied data. Allow sufficient time:
the selected more-itertools target is substantially more expensive than the
other four, particularly under Testmon instrumentation.

Start in the extracted reviewer artifact root. The following creates a new
temporary study directory; it neither modifies this artifact nor clones the
private project history.

```bash
study_dir=$(mktemp -d -t zerorun-replay-XXXXXXXX)
mkdir "$study_dir/engine" "$study_dir/drivers" "$study_dir/workloads"
cp -R research/sqj/source-final/. "$study_dir/engine/"
cp research/sqj/producers/controlled_comparison-v3.py "$study_dir/drivers/original.py"
cp research/sqj/run_controlled_comparison.py "$study_dir/drivers/corrected.py"
cp research/sqj/producers/controlled_comparison-recovery.py "$study_dir/drivers/recovery.py"
cp research/sqj/run_frozen_campaign.py "$study_dir/drivers/run_frozen_campaign.py"
python3 -m venv "$study_dir/venv"
"$study_dir/venv/bin/python" -m pip install --require-hashes \
  -r "$study_dir/engine/ci/generalization-runtime-requirements.txt"

# The benchmark records a real Git source context. This new local snapshot has
# a new commit identity; it is not presented as the original main commit.
git -C "$study_dir/engine" init -b main
git -C "$study_dir/engine" config core.autocrlf false
git -C "$study_dir/engine" add zerorun tools ci
git -C "$study_dir/engine" -c user.name='Artifact replay' \
  -c user.email='replay@example.invalid' commit -m 'Exact archived experimental bytes'

git clone --no-checkout https://github.com/pypa/packaging.git "$study_dir/workloads/packaging"
git clone --no-checkout https://github.com/eliben/pycparser.git "$study_dir/workloads/pycparser"
git clone --no-checkout https://github.com/tartley/colorama.git "$study_dir/workloads/colorama"
git clone --no-checkout https://github.com/pallets/click.git "$study_dir/workloads/click"
git clone --no-checkout https://github.com/more-itertools/more-itertools.git "$study_dir/workloads/more-itertools-repaired"

image='docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef'
docker pull --platform linux/amd64 "$image"

"$study_dir/venv/bin/python" "$study_dir/drivers/original.py" \
  --engine "$study_dir/engine" --workloads "$study_dir/workloads" \
  --output "$study_dir/comparison-final-1"

# This deliberately reproduces the documented whole-block packaging correction.
# Keep the original block, including its incompatible Testmon configuration.
"$study_dir/venv/bin/python" "$study_dir/drivers/corrected.py" \
  --engine "$study_dir/engine" --workloads "$study_dir/workloads" \
  --output "$study_dir/comparison-packaging-corrected" \
  --only packaging --testmon-project-compat

# Retain that attempt as well; the final stock-plugin warning fix is explicit.
"$study_dir/venv/bin/python" "$study_dir/drivers/recovery.py" \
  --engine "$study_dir/engine" --workloads "$study_dir/workloads" \
  --output "$study_dir/comparison-packaging-final" \
  --only packaging --testmon-project-compat

# Reproduce the published three-arm recovery as a separately labeled block.
"$study_dir/venv/bin/python" "$study_dir/drivers/recovery.py" \
  --engine "$study_dir/engine" --workloads "$study_dir/workloads" \
  --output "$study_dir/comparison-more-whole-task" \
  --only more-itertools --without-testmon
```

The fixed driver checks every required Git object before timing and checks out
the exact frozen commits in disposable worktrees. Fresh execution disables
network access, mounts source read-only, masks repository metadata, and limits
container resources. Setup needs network access to install the hash-pinned
dependency layer and Testmon wheels. The initial Testmon run intentionally uses
`--testmon-noselect`; subsequent requests enable selection. Every request also
has an independently executed full-target outcome capture.

If a rerun has the same recorded availability pattern, its numerical comparison
can be checked from the artifact root:

```bash
python -m research.sqj.analyze_comparison \
  --input "$study_dir/comparison-final-1" \
  --archive research/sqj/source-final \
  --output "$study_dir/reanalysis"
```

The original producer's SHA-256 changes if its bytes change, not when its filename
changes. Keep its bytes intact. New results should retain their actual host and
new source-context commit; never edit receipts to impersonate the original VM.
The published-data validator deliberately verifies the recorded timeout and
configuration-failure history. On different hardware Testmon might complete
within its limit, or another operation might fail. Such a rerun is new evidence,
not a reason to manufacture the original failure or force the published-data
validator to pass. Retain and analyze that changed availability separately.

Private signing fixtures are inside the study output and must not be published.
For an allowlisted export use `research/sqj/export_public_evidence.py` with
`--results "$study_dir"`, a new `--output` archive, and explicit `--select`
directories. The importer independently checks inventory and hashes and refuses
to overwrite differing existing evidence. Do not package an entire live study
directory recursively.
