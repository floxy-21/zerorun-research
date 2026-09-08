# Install ZeroRun 0.5.3 and exercise its actual MCP server

This account-free laboratory route installs the public source into a new external
virtual environment and runs the actual STDIO server. It needs no model account,
private repository, historical VM, or derived registry image. It authorizes only
the reviewed built-in two-file fixture. It does not authorize your project.

Use a non-root Linux/amd64 account with Git, Python 3.10 or newer, Python venv
support, and access to a Docker daemon you control. The source clone, tools
environment, configuration/home and result files must be new and separate.
Canonical offline publication analysis has its own CPython 3.12-3.14 requirement.

## Obtain public inputs

```sh
lab_dir=$(mktemp -d -t zerorun-public-XXXXXXXX)
git -c credential.helper= clone https://github.com/floxy-21/zerorun-research.git "$lab_dir/source"
git -C "$lab_dir/source" rev-parse HEAD
docker pull --platform linux/amd64 docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef
```

Keep the printed public commit and pull log. Image acquisition is an explicit
operator action. The checker only inspects the pinned image; it never silently
pulls, substitutes or rebuilds one. The image is sufficient for this standard
library fixture and is not a pytest environment for arbitrary projects.

The exact 0.5.3 runtime inventory remains pinned to product revision
`e5194d340a09bccb667d3021a6a4a9a9a054123b`, with inventory SHA256
`42f1f43433872bc80dd5f44a87505fef4d3e08b974cd5415514a74093952f7a3`.
The published wheel has SHA256
`dbac896587365b010b6c1c2c5913e1f75e79da99cfdb00c407e67f3442deddb9`.
Use an ordinary external installation, not an editable install.

## Review the fixture and run

The exact fixture program reads `fixture.txt` as UTF-8, compares it with the
literal `zerorun synthetic lifecycle fixture v1\n`, and exits 73 on inequality.
It has no network access, forwarded environment, generated outputs or clocks.
The standard demonstration keeps the expected data unchanged. Its reviewed
inputs are exactly `fixture.py` and `fixture.txt`; the frozen manifest binds their
declared paths, command and public container digest. The external authority is
temporary and restricted to this manifest. This inspection is a fixture-specific
operator decision, not a proof that an arbitrary manifest is complete.

If you approve that precise laboratory operation, run:

```sh
python3 -I -B "$lab_dir/source/research/softwarex/quickstart_053_metadata_v2.py" \
  --source-root "$lab_dir/source" \
  --create-tools-env "$lab_dir/tools" \
  --installation-output "$lab_dir/install.json" \
  --output "$lab_dir/check.json" \
  --approve-synthetic-formative-authority
```

Five actual server requests must be recorded in order: missing-authority doctor
refusal (`UNTRUSTED`), authorized readiness, `MISS_EXECUTED`, `HIT_REUSED`, and
`VERIFY_MATCH`. A hit identifies earlier success; it does not execute the test
or provide a fresh diagnostic transcript. Explicit verification executes fresh.
The initialization, requests and complete response JSON—including wire
`isError`, matching text and structured content—are retained in the receipt.
The checker must retain one cache key across its positive states and unchanged
source, installation, fixture and authority identities. It removes only its
private fixture/authority/build staging; the public clone, installation and
receipts remain.

```sh
python3 -I -B "$lab_dir/source/research/softwarex/quickstart_053_metadata_v2.py" \
  --source-root "$lab_dir/source" \
  --installation-receipt "$lab_dir/install.json" \
  --check "$lab_dir/check.json"
```

The second command validates retained evidence without Docker execution,
installation, authority creation or a model call. Preserve failures and use new
paths for deliberate attempts; record any intervention with `--intervention`.
Installation/check intervals are separate; neither includes operator effort or
establishes saved development time.

## Why this versioned correction exists

The expanded final public repository exceeded the original checker's 2-MiB
`git ls-files -s -z` metadata limit. An actual fresh-environment attempt preserved
that pre-MCP refusal after successful source installation. The original
`quickstart_053.py`, guide and receipts remain unchanged historical evidence.

This version gives only the five exact source-checkout Git metadata commands an
8-MiB bound. It retains complete streams and independently recomputes their
before/after hashes. Changed, missing, failed or truncated metadata is refused.
Every actual MCP stream and all other commands retain the original 2-MiB bound.
No runtime, input-eligibility, trust or cache rule changes. Its installation and
check receipts use new schemas and bind this helper and guide's exact bytes.

## Scope of a fresh Linux demonstration

A separate attempt uses a newly imported official Alpine Linux 3.24.1 x86_64
root filesystem with a new non-root account, configuration/home and Docker data
store. Its public rootfs URL is
`https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-minirootfs-3.24.1-x86_64.tar.gz`;
the publisher SHA256 is
`41f73e3cf5fa919b8aa5ca6b30dc48f0da2720776d7423e2a7748211456fe081`.
The retained preparation driver records the exact WSL import, package commands,
daemon launch and public-image pull. No prior VirtualBox image or loopback
registry is used. WSL still shares an existing Windows host and kernel; this is
author-side conformance reproduction, not an independent human trial or clean
physical hardware. The retained first daemon lifecycle refusal and original
Git-metadata refusal must remain visible alongside any corrected result.

The first corrective attempt runs this versioned helper outside an unchanged
anonymous clone of release r1, revision
`13a3ceae84913a1ddbed412bdad9cd6fbf7e2e54`. Its wrapper is a recorded intervention,
not a claim that the correction was already shipped in r1. The later delivery
publishes the helper and these instructions; receipts state the exact code they
actually executed. See `evidence/public-fresh-linux-053-v1/` for available records
and their verification outcome. No result is implied merely by this guide.

For your own repository use [the operating guide](OPERATING_GUIDE.md): actual
review and external exact-manifest authority are required. Do not copy the lab
authority into a project, and do not let a coding agent authorize itself.
