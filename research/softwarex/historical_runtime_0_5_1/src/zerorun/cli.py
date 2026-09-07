from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import sys
from pathlib import Path

from . import __version__
from .api import run_task
from .codex import install_codex
from .fingerprint import task_fingerprint
from .hermetic import hermetic_environment, inspect_runtime, task_fingerprint_v2
from .hermetic_store import _load_result_entry
from .manifest import find_manifest, load_manifest
from .mcp import serve_stdio
from .model import ConfigurationError
from .pilot import build_pilot_report
from .pytest_profile import DEFAULT_PYTEST_PROFILE, load_pytest_profile
from .pytest_qualify import validate_repository_file_path, validate_unlinked_file_path
from .runner import observe, read_events, safety_reason
from .store import Store
from .trust import authorize_manifest, authorize_pytest_profile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zerorun",
        description="Conservative deterministic test-result reuse for AI coding agents and CI",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--manifest", type=Path, help="path to .zerorun.json")
    parser.add_argument("--json", action="store_true", help="print machine-readable result JSON")
    sub = parser.add_subparsers(dest="action", required=True)

    init = sub.add_parser("init", help="install agent integration for the current repository")
    init.add_argument("--codex", action="store_true", help="install the ZeroRun Codex skill and MCP registration")
    init.add_argument("--root", type=Path, default=Path.cwd(), help="repository root; defaults to the current directory")
    init.add_argument(
        "--authorize-manifest-sha256",
        help="authorize reuse only for this exact manifest SHA-256",
    )
    init.add_argument(
        "--authorize-pytest-profile-sha256",
        help="also authorize the exact default pytest profile SHA-256",
    )

    authorize = sub.add_parser(
        "authorize",
        help="store external per-user authority for exact reviewed configuration",
    )
    authorize.add_argument(
        "--manifest-sha256",
        required=True,
        help="exact SHA-256 of the reviewed .zerorun.json bytes",
    )
    authorize.add_argument(
        "--pytest-profile-sha256",
        help="optional exact SHA-256 of the reviewed .zerorun-pytest.json bytes",
    )

    sub.add_parser("mcp-server", help="run the ZeroRun MCP server over stdio")

    run = sub.add_parser("run", help="run or safely replay a configured task")
    run.add_argument("task")
    run.add_argument("--force", action="store_true", help="execute even if a cache entry exists")
    run.add_argument("--verify", action="store_true", help="execute a cache hit and compare it with cached evidence")

    explain = sub.add_parser("explain", help="explain whether a configured task is reusable")
    explain.add_argument("task")

    sub.add_parser("list", help="list configured tasks")
    sub.add_parser("stats", help="summarize local execution and reuse events")
    sub.add_parser("doctor", help="validate the manifest and runtime contract")

    pilot_export = sub.add_parser(
        "pilot-export",
        help="write a privacy-preserving aggregate pilot metrics report",
    )
    pilot_export.add_argument(
        "--output",
        type=Path,
        default=Path("zerorun-pilot-report.json"),
        help="report path relative to the repository root unless absolute",
    )

    observe_parser = sub.add_parser("observe", help="measure an unconfigured command without caching it")
    observe_parser.add_argument("command", nargs=argparse.REMAINDER)

    cache = sub.add_parser("cache", help="manage the local cache")
    cache_sub = cache.add_subparsers(dest="cache_action", required=True)
    clear = cache_sub.add_parser("clear", help="clear cached entries")
    clear.add_argument("--yes", action="store_true", help="confirm deletion of the project-local cache")
    return parser


def _manifest(args: argparse.Namespace):
    return load_manifest(find_manifest(explicit=args.manifest))


def _task(manifest, name: str):
    if name not in manifest.tasks:
        raise ConfigurationError(f"unknown task {name!r}; available: {', '.join(sorted(manifest.tasks))}")
    return manifest.tasks[name]


def _task_key(manifest, task):
    if manifest.version == 2:
        runtime = inspect_runtime(task, repository_root=manifest.root)
        _, env_fp = hermetic_environment(task)
        return task_fingerprint_v2(manifest, task, runtime, environment_fingerprint=env_fp)
    return task_fingerprint(manifest, task)


def _authorize_exact_configuration(
    manifest,
    *,
    expected_manifest_sha256: str,
    expected_profile_sha256: str | None,
) -> dict[str, object]:
    actual_manifest = authorize_manifest(
        manifest,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    actual_profile: str | None = None
    if expected_profile_sha256 is not None:
        profile = load_pytest_profile(
            manifest.root / DEFAULT_PYTEST_PROFILE,
            manifest,
        )
        if profile.profile_sha256 != expected_profile_sha256:
            raise ConfigurationError(
                "pytest profile authorization digest mismatch; exact digest is "
                + profile.profile_sha256
            )
        authorize_pytest_profile(manifest, profile.profile_sha256)
        actual_profile = profile.profile_sha256
    return {
        "status": "AUTHORIZED",
        "manifest_sha256": actual_manifest,
        "pytest_profile_sha256": actual_profile,
        "authority_location": "external-per-user",
    }


def _print_result(result, as_json: bool) -> None:
    data = result.as_dict()
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    key = data["cache_key"][:12] if data["cache_key"] else "-"
    print(
        f"ZeroRun: {data['status']} | task={data['task']} | "
        f"wall={data['wall_ms']:.1f}ms | saved={data['saved_ms']:.1f}ms | key={key}",
        file=sys.stderr,
    )
    if data["reason"]:
        print(f"Reason: {data['reason']}", file=sys.stderr)


def _pilot_output_path(root: Path, supplied: Path) -> Path:
    if supplied.is_absolute():
        output = validate_unlinked_file_path(supplied, field="pilot export output")
    else:
        output = validate_repository_file_path(
            root,
            root / supplied,
            field="pilot export output",
        )
    if not output.parent.is_dir():
        raise ConfigurationError(
            "pilot export output parent must be an existing real directory"
        )
    if output.exists() and not output.is_file():
        raise ConfigurationError("pilot export output must be a regular file")
    return output


def _publish_pilot_report(output: Path, report: dict) -> None:
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = output.with_name(
        f".{output.name}.zerorun-{secrets.token_hex(12)}.tmp"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except OSError as exc:
        raise ConfigurationError(f"could not publish pilot report safely: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        if args.action == "mcp-server":
            return serve_stdio()

        if args.action == "init":
            if not args.codex:
                raise ConfigurationError("init currently requires --codex")
            if (
                args.authorize_pytest_profile_sha256 is not None
                and args.authorize_manifest_sha256 is None
            ):
                raise ConfigurationError(
                    "--authorize-pytest-profile-sha256 requires "
                    "--authorize-manifest-sha256"
                )
            if args.authorize_manifest_sha256 is not None:
                root = args.root.expanduser().resolve(strict=True)
                manifest = load_manifest(root / ".zerorun.json")
                _authorize_exact_configuration(
                    manifest,
                    expected_manifest_sha256=args.authorize_manifest_sha256,
                    expected_profile_sha256=args.authorize_pytest_profile_sha256,
                )
            try:
                data = install_codex(args.root)
            except ValueError as exc:
                raise ConfigurationError(str(exc)) from exc
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                print("ZeroRun Codex integration")
                print(f"skill: {data['skill']['path']}")
                print(f"MCP registered: {'yes' if data['mcp'].get('registered') else 'no'}")
                print(f"manifest: {'ready' if data['manifest_present'] else 'missing'}")
                print(f"ready: {'yes' if data['ready'] else 'no'}")
                print(f"next: {data['next_action']}")
            return 0 if data["ready"] else 2

        if args.action == "authorize":
            manifest = _manifest(args)
            data = _authorize_exact_configuration(
                manifest,
                expected_manifest_sha256=args.manifest_sha256,
                expected_profile_sha256=args.pytest_profile_sha256,
            )
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                print("ZeroRun external user authority stored")
                print(f"manifest SHA-256: {data['manifest_sha256']}")
                if data["pytest_profile_sha256"] is not None:
                    print(
                        "pytest profile SHA-256: "
                        f"{data['pytest_profile_sha256']}"
                    )
            return 0

        if args.action == "observe":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            store = Store(Path.cwd().resolve())
            result = observe(command)
            store.append_event(result.as_dict())
            _print_result(result, args.json)
            return result.exit_code

        manifest = _manifest(args)
        store = Store(manifest.root)

        if args.action == "run":
            if args.force and args.verify:
                raise ConfigurationError("--force and --verify are mutually exclusive")
            task = _task(manifest, args.task)
            # In JSON mode stdout is the machine-readable result channel. Route
            # the underlying task's stdout/stderr to stderr so a noisy test
            # cannot corrupt the JSON document emitted by _print_result().
            task_stdout = sys.stderr.buffer if args.json else None
            task_stderr = sys.stderr.buffer if args.json else None
            result = run_task(
                manifest,
                task,
                force=args.force,
                verify=args.verify,
                stdout=task_stdout,
                stderr=task_stderr,
            )
            _print_result(result, args.json)
            return result.exit_code

        if args.action == "list":
            rows = []
            for task in manifest.tasks.values():
                rows.append(
                    {
                        "task": task.name,
                        "cacheable": task.cacheable,
                        "unsafe_effects": list(task.unsafe_effects),
                        "command": list(task.command),
                        "result_only": task.result_only,
                        "image": task.image,
                        "platform": task.platform,
                    }
                )
            if args.json:
                print(json.dumps(rows, indent=2, sort_keys=True))
            else:
                for row in rows:
                    policy = "cacheable" if row["cacheable"] and not row["unsafe_effects"] else "execute-only"
                    print(f"{row['task']:<20} {policy:<12} {' '.join(row['command'])}")
            return 0

        if args.action == "explain":
            task = _task(manifest, args.task)
            reason = safety_reason(task)
            key = None
            cache_state = "BYPASS"
            if reason is None:
                key, fingerprint = _task_key(manifest, task)
                if manifest.version == 2:
                    cached = _load_result_entry(store, task, key, fingerprint)
                else:
                    cached = store.validated_metadata(
                        manifest, task, key, fingerprint
                    )
                cache_state = "HIT" if cached is not None else "MISS"
                reason = (
                    "all declared dependencies and signed result provenance are unchanged"
                    if cache_state == "HIT"
                    else "no matching externally authenticated cache entry"
                )
            data = {"task": task.name, "decision": cache_state, "reason": reason, "cache_key": key}
            print(json.dumps(data, indent=2, sort_keys=True) if args.json else f"{cache_state}: {reason}")
            return 0

        if args.action == "stats":
            events = read_events(store)
            counts: dict[str, int] = {}
            for event in events:
                status = str(event.get("status", "UNKNOWN"))
                counts[status] = counts.get(status, 0) + 1
            saved_ms = round(sum(float(event.get("saved_ms", 0) or 0) for event in events), 3)
            data = {
                "events": len(events),
                "counts": counts,
                "saved_ms": saved_ms,
                "saved_seconds": round(saved_ms / 1000.0, 3),
                "execution_ms": round(sum(float(event.get("execution_ms", 0) or 0) for event in events), 3),
            }
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                print(f"events: {data['events']}")
                print(f"verified time saved: {data['saved_ms']:.1f}ms ({data['saved_seconds']:.3f}s)")
                for status, count in sorted(counts.items()):
                    print(f"{status:<28} {count}")
            return 0

        if args.action == "pilot-export":
            report = build_pilot_report(read_events(store))
            output = _pilot_output_path(manifest.root, args.output)
            _publish_pilot_report(output, report)
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(f"pilot report written: {output}")
                print("aggregate only: no source code, commands, environment values, repository paths, or cache keys exported")
            return 0

        if args.action == "doctor":
            checks = []
            for task in manifest.tasks.values():
                reason = safety_reason(task)
                key = None
                try:
                    key, _ = _task_key(manifest, task) if reason is None else (None, None)
                    status = "CACHEABLE" if reason is None else "EXECUTE_ONLY"
                except ConfigurationError as exc:
                    status = "INVALID"
                    reason = str(exc)
                checks.append({"task": task.name, "status": status, "reason": reason, "cache_key": key})
            data = {
                "manifest": str(manifest.path),
                "platform": platform.platform(),
                "contract": (
                    "pinned OCI digest; Linux/amd64; network-disabled read-only checkout; reviewed source closure; result-only reuse; per-action-key lock"
                    if manifest.version == 2 else
                    "explicit dependencies; strict child environment; isolated workspace; fresh regular-file outputs; transactional publication; local cache only"
                ),
                "tasks": checks,
            }
            print(json.dumps(data, indent=2, sort_keys=True) if args.json else json.dumps(data, indent=2))
            return 1 if any(check["status"] == "INVALID" for check in checks) else 0

        if args.action == "cache" and args.cache_action == "clear":
            if not args.yes:
                raise ConfigurationError("cache clear requires --yes")
            count = store.clear_cache()
            print(f"cleared {count} cache entr{'y' if count == 1 else 'ies'}")
            return 0

        parser.error("unsupported action")
        return 2
    except ConfigurationError as exc:
        print(f"ZeroRun configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
