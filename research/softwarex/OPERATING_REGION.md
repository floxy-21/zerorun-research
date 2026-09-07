# Existing-data operating-region analysis: frozen descriptive protocol

Frozen on 6 September 2026 at 23:46 UTC, before implementing this extension.
The underlying timings and preliminary crossover calculations were already
inspected. This is **post-hoc descriptive analysis**, not preregistration, a new
experiment, a new caching algorithm, or an acceptance prediction. It uses the
two-direct-means equation already documented in `research/sqj/COST_MODEL_NOTE.md`.
This protocol will not be rewritten to fit the results.

## Inputs and inclusion rules

Use only the 24 completed planned blocks (four subjects, six blocks each, seven
requests per block) selected by the existing independently reconciled original
and recovery analysis. Preserve every completed block and its full request
timings, including adverse and influential observations. Never select by speed.
The original Packaging block-four interruption and subsequent recovery are not
an uninterrupted run; incomplete and additional observations remain separate.

The fixed source-and-analysis anchors are:

| Relative path | SHA256 |
| --- | --- |
| `research/sqj/strengthening/evidence/replication-analysis-v1.json` | `287c3f79beb90ac12913a9b6002e8c02c5bdd8fcda2bbf3644b0118661f5ac0b` |
| `research/sqj/strengthening/evidence/short-randomized-replication-v1/protocol.json` | `6bcadb632a509bf12ba49449f069585a5836c795e761e66f875da4c24d7081ec` |
| `research/sqj/strengthening/evidence/short-randomized-recovery-v1/recovery-protocol.json` | `7eea714d987ae1923c5c526b49a9363f0c37d2ea2fca3c0ab86b66dda1f08eae` |
| `research/sqj/strengthening/analyze_replication.py` | `70a36cc5c77d3e600a0e5ad151af86c3dbf3ebfae5b17004c5c995c0e6e294a4` |
| `research/sqj/strengthening/analyze_recovered_replication.py` | `b51e5fd3c33459a23cff57c41031bc32b37b1dbc52dc3469f8a99217f95426c9` |

Recompute the independent reconciliation and require exact equality with its
saved result before analysis. Bind every consulted raw JSON receipt, the frozen
protocol, and the new analyzer to the output with file sizes and SHA256 values.
All 168 selected observations must have the expected status and fresh agreement;
all original/recovery selections and all 24 block orders must reconcile. Do not
pool the earlier five-library comparison or the purposive state-restoration
case into this analysis. Do not execute recorded commands, tests, or containers.

## Descriptive crossover

The fixed hit-category indices are 1, 2, 5, and 6. Non-hit indices are 0, 3,
and 4: one successful seed and two failing requests per block. Every subject
therefore has 24 hit-category and 18 non-hit-category observations. The imposed
hit fraction is 4/7 (57.14%); it is **not an observed real-agent hit frequency**.

Let `D_h, F_h` be arithmetic means of complete direct and optimized invocation
time in the hit category, and `D_m, F_m` the corresponding non-hit means. For a
hypothetical hit fraction `p`, preserving the observed mixture within each
category, define:

`C_D(p) = p D_h + (1-p) D_m`

`C_F(p) = p F_h + (1-p) F_m`

`C_F(p)-C_D(p) = (F_m-D_m) - p[(F_m-D_m)+(D_h-F_h)]`.

When the slope denominator is nonzero, the equality point is
`p* = (F_m-D_m) / [(F_m-D_m)+(D_h-F_h)]`. Report its sign and whether it lies in
`[0,1]`; do not clamp it or invent an interior crossover. A zero denominator
means either a constant advantage/disadvantage or equality everywhere. A
negative denominator reverses which side of the crossover favors reuse.

Report category counts, means, complete cost totals, the observed-mixture cost
ratio, and crossover for every subject and every one of its six blocks. Report
the all-block range without calling it a confidence interval. Numerically
reconstruct the original full-sequence totals/ratios from the category means.
Do not search for a favorable subset or optimize a production decision rule.

An additional deployment/review cost difference `A` amortized over `N` requests
adds `A/N` to the difference above. If its denominator is nonzero, the equality
point becomes `(F_m-D_m + A/N) / [(F_m-D_m)+(D_h-F_h)]`. No value for `A` or `N`
is measured here. Equal common setup allocations cancel from the crossover;
Packaging's missing original setup total remains unavailable. Extra interrupted
request costs and the existing all-recorded-attempt ratio are retained as a
separate sensitivity; missing work is never imputed or assigned to a category.

## Narrow latency attribution

For optimized `HIT_REUSED` observations only, sum the two individually timed
fingerprint intervals, initial runtime inspection, and remaining named exclusive
intervals (environment, lock acquisition, authenticated lookup, event append).
The exact frozen implementation times these sequentially. Report the residual
against complete outer invocation time. Require nonnegative components and no
component sum exceeding the outer clock beyond documented sub-millisecond
rounding tolerance. Snapshot preparation/cleanup and Docker execution must be
absent or zero on this path. **Never add the inclusive `readonly_hit_path`
aggregate** to its components. Do not partition the more complex miss path.

For direct calls only, group the existing nonoverlapping Docker subprocess
intervals into `start`, other control calls, and outer residual. `start` includes
attached execution and Docker overhead; it is not pure test computation.
Report both block and subject totals/fractions, retaining all observations.
This attribution is observational, not a causal estimate of recoverable savings.

## Limits and reproduction

Changing the successful/failing non-hit mixture, target runtime, input size,
hardware, filesystem load, or admission behavior can change the crossover.
These four seen convenience subjects on one shared-host VM do not support
population inference, real-agent speedup, generalized scaling, CPU/energy
savings, or commercial demand. No model was invoked. The scientific claim is a
reproducible, explicitly conditional cost calculation useful to integrators.

After creation, reproduce the saved analysis without modifying it:

```sh
python -B -m research.softwarex.analyze_operating_region --check
```

The new analyzer's tests exercise artificial arithmetic, schema, boundary,
tamper, and disjoint-timing cases separately from the recorded experiment.
The generated result is `research/softwarex/generated/operating-region-v1.json`.
Results belong in that generated file; they do not change this frozen protocol.
