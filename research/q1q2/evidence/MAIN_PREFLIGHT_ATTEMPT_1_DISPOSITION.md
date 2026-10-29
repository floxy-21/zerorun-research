# Main preflight attempt 1: package-cache permissions

Run 40961026813 at commit cd32c19b13f0cf4c4ec8347c624bec18c61fcd58 failed during locale acquisition before any target acquisition or execution. The capability-restricted preparation container could not chmod/write Debian's existing apt partial-download directory, which is owned by the apt sandbox user. Runtime preparation and whole-attempt elapsed times remain recorded; this is an infrastructure failure, not evidence about target outcomes.

Repair: direct apt downloads to a new root-owned task-specific cache under /tmp while keeping the preparation container's dropped capabilities. No task execution isolation is weakened, no daemon configuration is changed, and no previously started timing block is repeated. Artifact 12936022572, ZIP SHA-256 9d0b7df2a1a6cb1f832597e3baea1c5ac0d5d227bb8e78a274e5dde174e0c9fb, is preserved in main-preflight-attempt-1/.
