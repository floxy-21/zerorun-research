"""One bounded API-guided treatment; root operator alone invokes model execution."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import validation as v
else:
    from . import validation as v


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codex-command", required=True)
    parser.add_argument("--zerorun-command", required=True)
    parser.add_argument("--zerorun-python-command", required=True)
    parser.add_argument("--codex-install-receipt", required=True, type=Path)
    parser.add_argument("--codex-main-integrity", required=True)
    parser.add_argument("--codex-platform-integrity", required=True)
    parser.add_argument("--prior-v1-receipt", required=True, type=Path)
    parser.add_argument("--prior-v2-receipt", required=True, type=Path)
    parser.add_argument("--prior-v3-receipt", required=True, type=Path)
    parser.add_argument("--frozen-v2-source", required=True, type=Path)
    parser.add_argument("--frozen-v3-source", required=True, type=Path)
    parser.add_argument("--approve-remote-metadata", action="store_true")
    parser.add_argument("--approve-synthetic-formative-authority", action="store_true")
    parser.add_argument("--approve-synthetic-run-tests-preauthorization", action="store_true")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args(argv)
    directory = Path(__file__).resolve().parent
    if not all((args.approve_remote_metadata, args.approve_synthetic_formative_authority, args.approve_synthetic_run_tests_preauthorization)):
        raise ValueError("all three bounded approvals are required")
    base, old = v.load_v3(args.frozen_v3_source)
    source = base.preflight(args.source_root)
    def prior_check():
        base.prior_bindings(args.prior_v1_receipt, args.prior_v2_receipt, args.frozen_v2_source)
        path = args.prior_v3_receipt.absolute()
        if path.is_symlink() or path.resolve(strict=True) != path or v.sha(path.read_bytes()) != v.PRIOR_V3:
            raise ValueError("prior v3 receipt differs")
        v.load_v3(args.frozen_v3_source)
    prior_check()
    if v.sha(args.codex_install_receipt.read_bytes()) != base.INSTALL_RECEIPT_SHA:
        raise ValueError("authenticated installer receipt differs")
    if args.output.exists() or args.output.absolute().is_relative_to(source):
        raise ValueError("new external output required")
    freeze = {"schema": "zerorun.guided-client.freeze.v1", "frozen_utc": base.timestamp(),
              "sources": v.source_bindings(directory), "source_commit": old.COMMIT, "public_manifest_sha256": old.MANIFEST_SHA256,
              "frozen_v3_sources": v.FROZEN_V3, "prior_receipts": dict(base.PRIOR_TRIALS, v3=v.PRIOR_V3),
              "scoped_tool_preauthorization": base.SCOPED_CONSENT, "stage_order": list(old.CHOICES + old.CASES),
              "maximum_model_turns": 8, "timeout_seconds": old.TIMEOUT, "output_limit_bytes": old.LIMIT}
    freeze_path = args.output.with_suffix(".freeze.json")
    if freeze_path.exists():
        previous = v.strict_json(freeze_path.read_bytes())
        if {k: val for k, val in previous.items() if k != "frozen_utc"} != {k: val for k, val in freeze.items() if k != "frozen_utc"}:
            raise ValueError("existing guided freeze differs")
        freeze = previous
    else:
        base.write_once(freeze_path, freeze)
    if args.freeze_only:
        print(v.canonical({"freeze": str(freeze_path), "sha256": v.sha(v.canonical(freeze)), "model_called": False}).decode())
        return 0
    base.write_once(args.output.with_suffix(".started.json"), {"started_utc": base.timestamp(), "freeze_sha256": v.sha(v.canonical(freeze))})
    value = {"schema": v.SCHEMA, "started_utc": base.timestamp(), "freeze": freeze,
             "freeze_sha256": v.sha(v.canonical(freeze)), "stages": [], "fresh_oracles": [], "identity": {},
             "prior_trials_reclassified": False, "runtime_modified": False, "real_repository_authorized": False,
             "treatment": "One prospectively frozen documented-API demonstration, not a causal or population comparison."}
    checkpoint = args.output.with_suffix(".events.jsonl")
    if checkpoint.exists():
        raise ValueError("guided checkpoint exists; no retry")
    def retain(row):
        value["stages"].append(row)
        with checkpoint.open("ab") as stream:
            stream.write(v.canonical(row) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    sys.path.insert(0, str(source))
    sys.path.insert(0, str(source / "src"))
    lifecycle = importlib.import_module("tools.codex_agent_lifecycle")
    for relative in base.PROTECTED:
        module = importlib.import_module(relative[:-3].replace("/", "."))
        if Path(module.__file__).resolve() != source / relative:
            raise ValueError("wrong protected helper imported")
    smoke = lifecycle.smoke
    runner = smoke._default_runner
    env, removed = smoke._sanitize_environment(dict(os.environ))
    env.pop("ZERORUN_TRUST_ROOT", None)
    git = shutil.which("git")
    if not git:
        raise ValueError("git unavailable")
    codex_lexical, codex = smoke._resolve_executable(args.codex_command, name="Codex", repository_root=source)
    _, zerorun = smoke._resolve_executable(args.zerorun_command, name="ZeroRun", repository_root=source)
    def installed():
        return lifecycle._installed_package_identity(runner, zerorun=zerorun, python_command=args.zerorun_python_command,
                                                     source_root=source, environment=env)
    def codex_identity():
        version = smoke._version(runner, codex_lexical, label="Codex", environment=env)
        return lifecycle._validated_codex_installation(codex_resolved=codex, install_receipt=args.codex_install_receipt,
            expected_source_commit=base.INSTALL_SOURCE, version=version["version"], main_integrity=args.codex_main_integrity,
            platform_integrity=args.codex_platform_integrity)
    identity = value["identity"]
    card = (directory.parent / "CLIENT_API_CARD.md").read_text(encoding="utf-8")
    original_prompt = base.prompt_for
    def prompt(name, arguments, input_response=None):
        return original_prompt(name, arguments, input_response) + "\n\nFrozen input-interface reference (not an expected result):\n" + card
    try:
        identity["source_before"] = smoke._git_snapshot(runner, git=git, root=source, environment=env)
        snapshot = identity["source_before"]
        if snapshot["head"] != old.COMMIT or snapshot["branch"] != "main" or snapshot["status"]["bytes"] or snapshot["diff_from_head"]["bytes"]:
            raise ValueError("guided source is not clean pinned main")
        identity["installed_before"] = installed()
        source_package = lifecycle._source_package_identity(source / "src")
        if identity["installed_before"]["module"]["files"] != source_package["files"] or source_package["file_count"] != 36:
            raise ValueError("guided installed runtime differs")
        identity["codex_before"] = codex_identity()
        with lifecycle.private_temporary_directory(Path(tempfile.gettempdir()), prefix="zerorun-guided-client-") as temporary:
            repository = temporary / "repository"
            trust = temporary / "external-authority"
            turns = temporary / "model-turns"
            turns.mkdir()
            explicit = dict(env, ZERORUN_TRUST_ROOT=str(trust))
            value.update(synthetic_path=str(repository), explicit_trust_path=str(trust))
            identity["fixture_before"] = lifecycle._create_synthetic_repository(runner, root=repository, runtime_image=base.IMAGE, git=git, environment=env)
            digest = v.sha((repository / ".zerorun.json").read_bytes())
            if trust.exists():
                raise ValueError("new external trust root was not empty")
            auth = runner([str(zerorun), "--manifest", str(repository / ".zerorun.json"), "--json", "authorize", "--manifest-sha256", digest],
                          cwd=temporary, environment=explicit, timeout_seconds=old.TIMEOUT, output_limit_bytes=old.LIMIT, input_bytes=None)
            value["authorization_streams"] = smoke._encoded_stream(auth)
            auth_payload = v.strict_json(auth.stdout)
            if (auth.returncode != 0 or auth.timed_out or auth.stdout_truncated or auth.stderr_truncated or
                    auth_payload != {"status": "AUTHORIZED", "manifest_sha256": digest, "pytest_profile_sha256": None, "authority_location": "external-per-user"}):
                raise ValueError("exact synthetic authorization failed")
            identity["authority_before"] = lifecycle._authority_tree_identity(trust)
            requests = v.setup_requests(str(repository))
            setup_result = runner([str(zerorun), "mcp-server"], cwd=repository, environment=explicit,
                timeout_seconds=old.TIMEOUT, output_limit_bytes=old.LIMIT, input_bytes=b"".join(v.canonical(row) + b"\n" for row in requests))
            value["setup"] = {"command": [str(zerorun), "mcp-server"], "requests": requests,
                              "streams": smoke._encoded_stream(setup_result), "model_called": False}
            analyzed = v.setup_analysis(value["setup"], str(repository), old)
            value["setup"]["analysis"] = analyzed
            captured = {"miss": analyzed["seed"]}
            shared = {"codex": codex_lexical, "zerorun": zerorun, "repository": repository, "execution_parent": turns, "environment": explicit}
            base.prompt_for = prompt
            for name in old.CHOICES:
                stage = base.capture_stage(lifecycle, runner, name=name, **shared)
                retain(stage)
                if not all(stage["judgment"][key] for key in ("protocol_pass", "tool_result_pass", "semantic_pass")):
                    break
                captured["hit" if name == "accept_prior" else "verify"] = stage["judgment"]["analysis"]["response"]
                oracle = base.direct_oracle(lifecycle, runner, repository=repository, execution_parent=turns, environment=explicit, after_stage=name)
                value["fresh_oracles"].append(oracle)
                if not oracle["passed"]:
                    break
            if len(value["fresh_oracles"]) == 2 and all(row["passed"] for row in value["fresh_oracles"]):
                for name, input_response in base.controlled_cases(captured):
                    stage = base.capture_stage(lifecycle, runner, name=name, input_response=input_response, **shared)
                    retain(stage)
                    if not stage["judgment"]["protocol_pass"]:
                        break
            base.prompt_for = original_prompt
            identity["fixture_after"] = lifecycle._synthetic_source_identity(runner, git=git, root=repository, environment=env, runtime_image=base.IMAGE)
            identity["authority_after"] = lifecycle._authority_tree_identity(trust)
        identity["temporary_fixture_cleaned"] = not temporary.exists()
        identity["source_after"] = smoke._git_snapshot(runner, git=git, root=source, environment=env)
        identity["installed_after"] = installed()
        identity["codex_after"] = codex_identity()
        base.preflight(source)
        prior_check()
        identity["public_runtime_rechecked"] = True
        if freeze["sources"] != v.source_bindings(directory):
            raise ValueError("guided sources/card changed")
        identity["sources_and_prior_trials_rechecked"] = True
    except Exception as exc:
        value["failure"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
    finally:
        base.prompt_for = original_prompt
    value["completed_utc"] = base.timestamp()
    value["summary"] = v.summarize(value, old)
    value["evidence_payload_sha256"] = v.sha(v.canonical(value))
    base.write_once(args.output, value)
    v.validate_receipt(args.output, directory=directory, frozen_v3_directory=args.frozen_v3_source)
    print(v.canonical({"receipt": str(args.output), "summary": value["summary"]}).decode())
    return 0 if value["summary"]["all_planned_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
