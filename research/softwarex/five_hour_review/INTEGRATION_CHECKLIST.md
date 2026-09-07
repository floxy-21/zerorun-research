# Versioned release and evidence integration

This is an operator checklist, not a declaration that the new studies passed.
Historical receipts, raw data, source snapshots and failed trials remain intact.
No acceptance probability is derived from completing this checklist.

## Exact runtime separation

| Use | Version and source | Required check |
| --- | --- | --- |
| Previously recorded benchmarks, installation and model trials | 0.5.1, `86f42289c2f59a74b1f642f0b20d1a26b3d55e57` | Original receipts and historical hashes remain authoritative. |
| New controlled issue-state handoffs | The same exact 0.5.1 runtime, with the study's disclosed symmetric timeout setting | Use the old public snapshot; the historical commit label alone is insufficient. The runner binds all 36 exact runtime files. |
| Current distribution and discoverable MCP documentation | 0.5.2, `d02b12e43ece3526566ec69c7516579f8d2fc9dd` | Exactly `__init__.py` and `mcp.py` differ. The first changes only the version; the second changes only `run_tests` discovery descriptions. Fresh current-version installation and regression evidence is required. |

`build_public_release.py --current-core d02b12e43ece3526566ec69c7516579f8d2fc9dd`
selects the new release explicitly. Omitting this argument retains historical
0.5.1 behavior. A current stage contains 0.5.2 under `src/zerorun` and the exact
old 36 modules under `research/softwarex/historical_runtime_0_5_1/src/zerorun`.
The manifest distinguishes both commits and versions. Its retained
`frozen_core_commit` denotes historical evidence, not the root runtime version.

The original 0.5.1 installation/test checker reads only that exact historical
shadow for old runtime rows. It does not waive any old hash failure or extend
the earlier, specifically named test-amendment exception. The original 0.5.1
wheel and JUnit reports remain byte-identical.

## Small, ordered integration sequence

1. Finish new protocol/harness and publication Python edits before final tests.
   Add every new published source to the explicit public allowlist. Add nested
   study unit-test modules to the publication test command: inventory membership
   alone does not mean pytest executed them. Keep acquired upstream archives,
   licenses and explicit export manifests, and exclude caches, credentials,
   authorization files, private history and generated workspaces.
2. On the VM, create the new public stage with the explicit current-core argument.
   Build its wheel as `output/packages/zerorun-softwarex/zerorun-0.5.2-py3-none-any.whl`.
   Preserve the wheel in the private publication inputs as well as the stage so
   a subsequent refresh includes the same bytes. Building a new wheel does not
   update or replace any historical wheel.
3. From the exact staged helper, execute the following with actual absolute paths:

   ```sh
   python -B -m research.softwarex.five_hour_review.current_runtime \
     --release /absolute/new-public-stage \
     --output /absolute/private-root/research/softwarex/evidence/current-runtime-0.5.2-v3
   ```

   The output directory must be new; its parent must already exist. The calling
   Python needs pytest. The producer creates an external venv, installs the wheel
   with isolated Python and `--no-index --no-deps`, compares all installed runtime bytes, checks the
   CLI and runs the complete published `tests/` directory against current source.
   The regression check uses the caller's pytest, separately from the fresh
   installed-wheel smoke. `PYTHONPATH` is removed from subprocess environments;
   only the regression launcher sets its exact source path for itself and its
   child test processes. The retained v2 installation smoke passed but four
   child-process regressions could not import the source package before this
   regression-only propagation was added. The retained v1
   attempt exposed source metadata to pip and failed the isolated import smoke;
   it is not successful installation evidence. No forced reinstall is used.
   The producer does not invoke a model, Docker benchmark or an
   authorization change. Retain unsuccessful attempts under distinct paths.
4. Copy `receipt.json` and `tests.xml` unchanged to the matching private/public
   evidence directory. Validate with `--check /absolute/receipt.json` instead of
   `--output`. The checker recomputes source inventories, permitted versioned
   differences, wheel bytes, captured installation results, command scope and
   JUnit counts. This step certifies no current-version speed improvement.
5. Reconcile all new study records before writing numerical claims. Report every
   selected disposition, version, oracle outcome and unsuccessful pilot. Keep
   natural agent issue work separate from controlled reference-patch handoffs
   and from result-interpretation tasks. Include cold seed cost in the primary
   paired treatment, and label setup, diagnostics and independent oracle costs.
6. Freeze publication source, then run the full direct publication test producer
   into `research/softwarex/evidence/publication-five-hour-final-v1`, the path
   currently selected by `build_readiness.EXTENSION_TESTS`. If a fix is necessary,
   preserve that attempt, choose a new final path before testing, and rerun the
   affected scope plus the final aggregate. Never edit the selected source after
   its successful final receipt and claim the old receipt still covers it.
7. Refresh the current public stage. Check manifest/source bindings and canonical
   analysis reproduction. Pin the code/evidence revision used by the manuscript.
   Regenerate manuscript tables and summaries from reconciled data, compile the
   PDF, inspect every page, and record the actual journal-scope word count.
8. Refresh and inspect the pre-archive public inventory. Build source/reviewer
   archives from that checked state, verify their entries and hashes, and then
   refresh the enclosing public manifest with those completed archive bytes.
   Do not embed an archive in itself or rewrite the earlier manifest sealed
   inside it. Regenerate readiness after PDF/archive/source inputs are final.
9. Commit and publish only the checked payload. Any later documentation change
   requires the corresponding enclosing manifest/ZIP/readiness refresh, not a
   new historical experiment. A Python change requires a new matching test receipt.

## Remaining author and submission actions

Full corresponding-author postal address, publisher declarations-tool Word
output, factual contributor roles and final author approval remain author
formalities. No tool here submits the manuscript, accepts journal charges or
claims a particular acceptance probability. The paper must describe AI
assistance and preserve the unsuccessful earlier model trials.
