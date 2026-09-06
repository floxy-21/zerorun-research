from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .model import ConfigurationError
from .pytest_review import (
    DEFAULT_CANDIDATE,
    DEFAULT_PROFILE,
    DEFAULT_REVIEW,
    activate_reviewed_candidate,
    write_review_template,
)


def _rooted(root: Path, value: Path | None, default: str) -> Path:
    candidate = value or Path(default)
    candidate = candidate.expanduser() if candidate.is_absolute() else root / candidate
    lexical = Path(os.path.abspath(candidate))
    try:
        lexical.relative_to(root.resolve())
    except ValueError as exc:
        raise ConfigurationError(
            "pytest review paths must remain inside the selected repository"
        ) from exc
    return lexical


def review_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zerorun-pytest-review",
        description=(
            "Create a non-authorizing operator review record for an exact pytest candidate. "
            "The generated record must be explicitly completed before activation."
        ),
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reviewer")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    try:
        payload = write_review_template(
            _rooted(root, args.candidate, DEFAULT_CANDIDATE),
            output=_rooted(root, args.output, DEFAULT_REVIEW),
            reviewer=args.reviewer,
        )
    except ConfigurationError as exc:
        print(f"ZeroRun pytest review error: {exc}")
        return 2
    result = {
        **payload,
        "status": "REVIEW_REQUIRED",
        "reuse_activated": False,
        "output": str(_rooted(root, args.output, DEFAULT_REVIEW)),
        "next_action": (
            "operator must review closure completeness and node independence, then set all review gates and authorizes_activation to true"
        ),
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("ZeroRun pytest operator review record created")
        print(f"candidate: {result['candidate_sha256']}")
        print(f"review: {result['output']}")
        print("reuse activated: no")
        print(f"next: {result['next_action']}")
    return 0


def activate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zerorun-pytest-activate",
        description=(
            "Activate a pytest candidate only when a completed operator review record "
            "binds the exact candidate and explicitly approves both safety reviews."
        ),
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    try:
        result = activate_reviewed_candidate(
            _rooted(root, args.candidate, DEFAULT_CANDIDATE),
            review_path=_rooted(root, args.review, DEFAULT_REVIEW),
            output=_rooted(root, args.output, DEFAULT_PROFILE),
        )
    except ConfigurationError as exc:
        print(f"ZeroRun pytest activation error: {exc}")
        return 2
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("ZeroRun pytest reviewed profile activated")
        print(f"profile: {result['profile']}")
        print(f"candidate: {result['candidate_sha256']}")
        print(f"review: {result['review_record_sha256']}")
        print("reuse activated: yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(activate_main())
