"""Export original, explicitly recovered, and separately bound case receipts."""
from research.sqj import export_public_evidence as export


def main():
    export.ALLOWED_ROOTS = {"short-randomized-replication-v1", "short-randomized-recovery-v1", "agent-state-rejoin-v1", "agent-state-rejoin-v2", "agent-state-rejoin-v3"}
    export.EXCLUDED = export.EXCLUDED | {"workspace", ".zerorun", ".zerorun-env", "__pycache__", ".git", ".pytest_cache"}
    export.main()


if __name__ == "__main__":
    main()
