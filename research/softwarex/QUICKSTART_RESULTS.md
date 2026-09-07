# Internal clean-install laboratory reproduction

## Recorded result

The first account-free quickstart reproduction passed on 7 September 2026 UTC.
The read-only `validate_installation_receipt` and `validate_saved_receipt`
functions independently rechecked the archived source/install identities,
bounded command transcripts, server responses, fixed request order, and receipt
hashes. These are **internal automated reproduction results**, not observations
from external developers or a human usability study.

| Check | Recorded evidence |
| --- | --- |
| Clean public source | `ebf2884df12573d63f45813200e0675288d12096`; unchanged before/after installation and execution |
| Fresh external installation | New virtual environment; seven recorded commands; 36 runtime modules and five packaging files copied byte-for-byte into a private build directory |
| Installed runtime | ZeroRun 0.5.1; all 36 installed runtime files match the frozen public core |
| Missing-authority control | Actual STDIO doctor refused the task as `UNTRUSTED` and observation-only |
| Explicit isolated authority | Actual STDIO doctor reported readiness for the built-in synthetic task |
| Result lifecycle | Actual `MISS_EXECUTED`, `HIT_REUSED`, and `VERIFY_MATCH`; one shared positive cache-key identity |
| Postflight | Fixture inputs, source, installed package, and external authority unchanged; temporary fixture/authority and private build copy removed |

The source runtime inventory identity is
`0bb214d1c9b33a2e3b7a3aa81e8f2469024cb6ddaab46c3fa41a9272502a57d5`.
Execution used Linux/amd64, CPython 3.10.12, Git 2.34.1, Docker 29.1.3,
and the already-present fixed CPython image documented in the
[laboratory quickstart](QUICKSTART_LAB.md). Neither the installer nor the check
called a model, contacted Codex, or authorized a caller-supplied repository.

Installation ran from `01:17:48` to `01:18:08` UTC and recorded **19.847712 s**.
The subsequent laboratory check ran from `01:18:08` to `01:18:21` UTC and
recorded **12.459911 s**, including 43 prerequisite, identity, authorization,
Git, and server commands. Its five actual STDIO requests are not 43 independent
functional test cases. These single-run timings exclude cloning, prior Docker
setup/image acquisition, and researcher preparation. They are not performance
comparisons or estimates of novice setup time. No setup intervention was entered
in the optional operator-report field; that does not establish unassisted use.

## First adapter run versus final public-guide reproduction

This first run installed the public code from the immutable commit above, but
the **new checker and quickstart document were supplied in an external adapter
directory**. That earlier public commit did not yet contain the new quickstart.
The checker's exact producer and document hashes are archived in the receipts.
The pinned source was not edited and was not used as a build workspace.

Consequently, this run verifies the new installation/check procedure against
the existing public runtime; it is **not yet evidence of following the final
published in-clone command literally**. That separate reproduction remains
pending until a public code commit containing the frozen quickstart files is
available. It must use a new clone and new external installation, retain its own
receipts, and preserve this first adapter result. Do not overwrite this record
or silently pool the two installations into a single run.

The live Codex study uses a separate installation and separate model-backed
protocol. Sharing the same verified runtime bytes does not make this new
installation a successful live Codex run. Model interpretation, coding-task
completion, realistic reuse frequency, and end-to-end AI workflow benefit need
their own evidence.

## Archived records and checks

- [Installation receipt](evidence/quickstart-lab-v1/install.json), SHA-256
  `da68d008eef5432ac163caa40b647dbf3e0e7449426dd4ba71694f682afb190c`.
- [Laboratory receipt](evidence/quickstart-lab-v1/check.json), SHA-256
  `4445f4bb2575ddd1a97c595dbf56f84fc191df7f913a730af73ee41ac91bee05`.

The publication's direct unit tests for this checker passed **36 tests** during
development from a fresh working directory outside Git. They cover receipt
tampering, command/output mismatch, false model/user claims, missing authority
approval, refusal preservation, runtime/source drift, installation failure,
fixed request denominators, and output overwrite protection. Unit-test success
is separate from the seven installation commands and five live STDIO requests.
The consolidated publication test receipt reports the final packaged test run.

## Reviewer interpretation

This addition makes the result-only contract inspectable through a short,
account-free runnable example and demonstrates a fresh install of the public
runtime. It does not establish widespread adoption, independent human ease of
use, a new caching algorithm, or a journal acceptance probability. Its value is
a concrete first-use path with explicit prerequisites, narrowly scoped
permission, retained adverse outcomes, and directly checked installation and
execution identities.
