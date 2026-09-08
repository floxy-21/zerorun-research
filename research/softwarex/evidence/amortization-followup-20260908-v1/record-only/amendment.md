# Prospective exploratory repeated-consumer follow-up

This is a separate workload experiment, prepared after inspecting V6. It is not
an independent subject sample, an optimization, or evidence of agent productivity.
Do not launch it while a model or another benchmark is running in the VM.

Select exactly `joshtemple__lkml-85` from the unchanged original 24-case ledger.
The rule is the greatest sum of recorded cold-producer `snapshot_prepare` over
the two V6 blocks, with case ID as deterministic tie-breaker. This selects
13,255.157 ms (7,043.957 and 6,211.200 ms), also the largest individual block.
This outcome-informed selection must remain disclosed. The earlier one-consumer
chains were 64.96% and 57.47% slower and remain unchanged.

Use the original source archive, supplied runtime and regression patches, and
full selected targets. Keep historical public engine
`ebf2884df12573d63f45813200e0675288d12096`, runtime 0.5.1, public harness
`0528905a52b74df78aa4e5a09219df34620282dd`, and the V6 Python 3.10.21 image
`127.0.0.1:19559/zerorun-compatible-v2@sha256:78039048251ecf889454509c6ef203f922d9dae9fa3b570a4ade3a9d4b552465`.
No runtime code, trust rule, admission rule, cache identity, test assertion,
timeout per command, or dependency is changed. The current 0.5.3 distribution is
a separate integration release and is not this study runtime.

Run the fixed order N=1,2,4, with two counterbalanced blocks for each N. Within
each block use fresh paired workspaces and a cold ZeroRun producer cache. The
fresh arm executes one producer plus N fresh full-target requests; the ZeroRun
arm executes one producer plus N requests for the identified validation status.
Record every consumer status without forcing a hit. These services differ:
reused identified status is not a fresh execution transcript. Require one
independently fresh full-target oracle after each arm, equal to the fresh
compatibility verdict, and unchanged source inventories throughout. Run and
record explicit fresh diagnostics once after each ZeroRun arm.

Report producer plus all consumers as the full chain. Separately report every
consumer, per-arm preparation, shared source preparation, fresh oracles, fresh
diagnostics, and the historical image-preparation cost (not newly incurred).
No missing or failed work becomes zero. Show both blocks and all N values,
including slowdowns. Do not pool this follow-up with V6, select a favorable N
after execution, or infer population confidence intervals from its six blocks.

Retain 120-second execution bounds and a 1,200-second operational guard, which
should not be interpreted as a promise of ten-minute completion. Stop later
work on a material correctness mismatch; preserve every started operation and
all six planned dispositions on errors or guard expiry. Keep a new record-only
bundle with original driver/protocol bytes, source and image bindings, clocks,
raw captures and failure receipts. Host observations must be supplied by the
operator; guest completion alone does not establish uninterrupted host timing.

Expected invocation after all model jobs finish (paths must identify the
already verified guest inputs; the output must be a new external directory):

```sh
PYTHONPATH=/path/to/public-harness python /path/to/amortization_followup_v1.py \
  --engine /path/to/historical-engine \
  --acquisition /path/to/original-acquisition \
  --v6-records /path/to/v6/record-only \
  --image-completion /path/to/image-repair-v2/completion.json \
  --protocol /path/to/AMORTIZATION_FOLLOWUP_V1.md \
  --output /path/to/new-amortization-followup-v1
```

Preparation status: protocol and driver under review; no follow-up execution or
performance outcome is claimed by this document.
