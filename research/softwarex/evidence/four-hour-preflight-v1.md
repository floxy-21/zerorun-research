# Four-hour extension: environment preflight

The user approved the strengthening plan before this extension began at
2026-09-07 01:00:55 UTC. This record describes setup, not an experiment outcome.

- The original final package was preserved in a separate local backup and in
  Git history before new work. Original live-client-v1 records remain unchanged.
- VirtualBox reported the named ZeroRun VM powered off. The operator-approved
  test VM was started headlessly; its existing loopback SSH forwarding was used.
- A fresh, unauthenticated public clone was created at
  `/home/floxy/zerorun-four-hour-20260907/source`. Its HEAD was verified as
  `ebf2884df12573d63f45813200e0675288d12096`.
- Docker inspection confirmed that the existing Python image has ID
  `sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef`
  and architecture `amd64`. No new image was acquired in this preflight.
- A direct Codex `--version` probe used the VM's default old Node runtime and
  stopped with a JavaScript syntax error. It made no model call. The subsequent
  experiment must explicitly use the already installed, authenticated Node
  22.14.0 path recorded in the original installer receipt; this is an invocation
  environment correction, not a new Codex installation or a model retry.
- The original Codex installer receipt still has SHA-256
  `52a9bfbbef880d817cfa48479da1b65389e272946d765aad4863d8f1ad290858`.
- Before any new model work, the temporary VM Codex authentication file was
  confirmed absent. Any later account use and cleanup must be recorded separately.
- Windows `doctor` reported the existing whole-task manifest INVALID because
  hermetic reuse requires Linux/amd64. Publication-helper unit tests therefore
  run directly, outside ZeroRun, with temporary fixtures outside Git checkouts.

This preflight does not authorize a real repository, attest to model behavior,
establish performance, or imply a successful corrected live-client experiment.
