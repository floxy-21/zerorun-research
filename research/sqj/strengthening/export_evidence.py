"""Export only the two named extension studies using the existing verifier format."""
from research.sqj import export_public_evidence as export


def main():
    # These are study-output names, never caller-supplied arbitrary directory roots.
    export.ALLOWED_ROOTS = {"short-randomized-replication-v1", "agent-state-rejoin-v1"}
    export.EXCLUDED = export.EXCLUDED | {
        "workspace", ".zerorun", ".zerorun-env", "__pycache__", ".git", ".pytest_cache"
    }
    export.main()


if __name__ == "__main__":
    main()
