# Try the public ZeroRun laboratory example

This account-free example checks a clean installation through the **actual MCP
STDIO server**. It requires no Codex login, model credits, historical evidence
archive, or private repository. It creates only the original two-file synthetic
fixture in a private temporary directory; it never accepts your project as a
target. This is a small laboratory demonstration, not an AI-agent performance
benchmark, an external developer study, or production authorization.

## Prerequisites and installation

Use a non-root **Linux/amd64** account with Git, Python 3.10+, Python's `venv`
support, and permission to use your own Docker daemon. Windows/macOS installation
does not establish supported whole-task execution. Allow about 10 minutes once
prerequisites are present; network downloads and Docker setup vary and are not
included in the checker's recorded time.

Choose a new directory outside every existing project. These commands use a
fresh temporary parent. The checker below will create the new installation
**outside the source checkout** and archive its actual installation commands:

```sh
lab_dir=$(mktemp -d -t zerorun-lab-XXXXXXXX)
git clone https://github.com/floxy-21/zerorun-research.git "$lab_dir/source"
git -C "$lab_dir/source" rev-parse HEAD
```

Keep the printed commit. For exact paper reproduction, check out the
manuscript's C2 commit before installing. The checker records the
actual commit and refuses changes in the public source checkout. Do not use an
editable install or put the executable inside a target project.

The demonstration uses this specific official CPython image. Inspect the image
and its provenance under your own Docker policy; **pulling it is a separate
operator action**, never an automatic fallback from the checker:

```sh
docker pull --platform linux/amd64 docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef
```

The test itself only inspects the already-present image. It does not pull, build,
or replace a runtime. This image is for the self-contained fixture only, not a
pytest environment or a recommendation for your application.

## Review the fixture-only permission, then run once

The fixture reads `fixture.txt` and compares it with a fixed string using the
standard-library program in `tools/codex_agent_lifecycle.py` (`FIXTURE_PROGRAM`
and `FIXTURE_DATA`). Its reviewed inputs are exactly these two files; it needs
no network, host environment forwarding, or generated outputs. The runner
creates its own Git repository and isolated external authority directory.

The explicit flag below permits **only this built-in fixture** to receive
temporary exact-manifest authority. It does not approve a real project or an
agent-created task. Omit the flag unless you agree to this precise operation.

```sh
python3 -I -B "$lab_dir/source/research/softwarex/quickstart_check.py" \
  --source-root "$lab_dir/source" \
  --create-tools-env "$lab_dir/tools" \
  --installation-output "$lab_dir/installation-receipt.json" \
  --output "$lab_dir/quickstart-receipt.json" \
  --approve-synthetic-formative-authority
```

The installation step creates a **new** virtual environment, copies exactly the
36 source modules and five packaging files into a private build directory,
installs that byte-checked copy with isolated pip configuration, and records
the command outputs and elapsed time. The private build copy is then removed;
the public clone is not a build workspace. The installation receipt includes
before/after source identity. It does not measure clone time or claim an external
developer completed the instructions.

The new environment's Python then checks all 36 installed runtime files against the source and
records five actual server requests in this fixed order:

| Request | Expected observation |
| --- | --- |
| Doctor without the external authority-path setting | Refusal: `UNTRUSTED`, observation-only |
| Doctor with the explicitly supplied isolated path | Authorized task ready |
| First execution | `MISS_EXECUTED`: fresh successful execution |
| Unchanged second request | `HIT_REUSED`: previous successful status, **not fresh output** |
| Verification | `VERIFY_MATCH`: a fresh execution agrees with the saved result |

One cache key must bind the four positive results. Every server request has a
120-second deadline and a 2-MiB limit per output stream. The receipt retains
commands, output streams, per-step/checker elapsed times, source/package hashes,
and any failure. No retry or output overwrite is automatic. The runner checks
that fixture, authority, source, and installed files remain unchanged, then
removes only its private fixture and authority. Your clone, installation, and
receipt remain available under the printed `lab_dir`.

If you had to fix an installation or prerequisite problem, record that fact
with a repeated `--intervention "description without secrets"` argument and
keep all earlier receipts. This field is an operator report, not automatically
verified evidence. Check-stage timing excludes clone and installation; the
separate installation receipt records installation time. Neither receipt
measures user effort, usability satisfaction, or saved agent time.

## If it stops

- **Missing image or Docker permission:** fix the actual prerequisite outside
  the checker; do not substitute another digest or elevate it to root.
- **Helper/source/install mismatch:** use a clean public clone and a normal new
  external installation. Do not change expected hashes to clear the check.
- **Existing output file:** preserve it. A deliberate new attempt needs a new
  filename and an explanation, including failed attempts in reported results.
- **Unexpected status or verification failure:** retain the receipt and inspect
  its raw results. A refusal, miss, timeout, or error is never a cache hit.

## Next: connect a coding client

The [operating guide](OPERATING_GUIDE.md#6-connect-an-mcp-client-or-codex) explains
the separate Codex/MCP configuration and how to distinguish a previous success
from fresh test diagnostics. Completing this account-free example does **not**
prove that Codex forwarded the authority path or interpreted a response correctly.
The live-client experiments are separate evidence.

For your own repository, start at the operating guide's review step and obtain
an actual operator decision about input completeness, determinism, and exact
manifest authority. Never copy the laboratory authority or approval fields into
a project. A user must review and authorize a real configuration; an AI agent
must not authorize itself. Support: public GitHub issues or
`kapoorjishan2@gmail.com`; never attach credentials or authority keys.
