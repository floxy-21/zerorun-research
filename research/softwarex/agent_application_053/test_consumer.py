"""Offline parser/qualification tests; these are not model application evidence."""
from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from research.softwarex.agent_application_053 import consumer as c
from research.softwarex.agent_application_053 import validation as v

ROOT = "/tmp/reviewed-consumer/repository"
TASK = "reviewed-tests"


def payload(status="HIT_REUSED"):
    return {"task": TASK, "status": status, "mode": "reuse", "exit_code": 0,
            "verified": status == "VERIFY_MATCH", "cache_key": "a" * 64,
            "stdout_tail": "", "stderr_tail": "", "restored_outputs": []}


def events(status="HIT_REUSED", *, verify=False, final=None, calls=True):
    records = [{"type": "thread.started", "thread_id": "unit"}, {"type": "turn.started"}]
    if calls:
        started = {"id": "call1", "type": "mcp_tool_call", "server": "zerorun", "tool": "run_tests",
                   "arguments": {"root": ROOT, "task": TASK, "verify": verify}, "status": "in_progress",
                   "error": None, "result": None}
        records.append({"type": "item.started", "item": started})
        result = payload(status)
        completed = deepcopy(started)
        completed.update(status="completed", result={"structured_content": result,
            "content": [{"type": "text", "text": v.canonical(result).decode()}], "is_error": False})
        records.append({"type": "item.completed", "item": completed})
    answer = final or {"validation_status": "success", "execution_in_this_call": status != "HIT_REUSED",
                      "diagnostics": "No complete transcript is established.", "limitations": "Bounded task only.", "next_step": "Review."}
    records += [{"type": "item.completed", "item": {"id": "final", "type": "agent_message", "text": v.canonical(answer).decode()}},
                {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 4}}]
    return records


def raw(records):
    return b"".join(v.canonical(row) + b"\n" for row in records)


def analyze(records, stage="available"):
    return v.analyze_events(raw(records), root=ROOT, task=TASK, stage=stage)


def mutate_payload(records, change):
    result = records[3]["item"]["result"]
    result["structured_content"].update(change)
    result["content"][0]["text"] = v.canonical(result["structured_content"]).decode()


def qualification():
    rows = [{"path": name, "bytes": len(data), "sha256": v.sha(data)} for name, data in
            ((".zerorun.json", b"manifest"), ("module.py", b"repaired"), ("tests/test_module.py", b"supplied test"))]
    restored = deepcopy(rows)
    restored[1].update(bytes=8, sha256=v.sha(b"original"))
    return {"schema": v.SCHEMA, "case_id": v.CASES[0], "root": ROOT, "task": TASK,
            "manifest_sha256": rows[0]["sha256"], "command": ["python", "-m", "pytest", "tests/test_module.py"],
            "runtime_image": "registry.example/test@sha256:" + "b" * 64,
            "protected_test_paths": ["tests/test_module.py"],
            "states": {"final": {"files": rows, "identity_sha256": v.identity(rows), "eligible": True},
                       "restored": {"files": restored, "identity_sha256": v.identity(restored), "eligible": True}},
            "restoration": {"changed_paths": ["module.py"], "description": "Restore the recorded issue source."},
            "inspection": {"method": "AI-assisted inspection under user-authorized laboratory scope",
                           "human_review_seconds": None, "basis": "Unit fixture only."},
            "authorization": {"scope": "selected laboratory case and the two exact recorded states only",
                              "user_authorization_reference": "Unit fixture; no actual authority created."}}


class ConsumerEventTests(unittest.TestCase):
    def test_completed_result_is_reuse_but_language_not_automatic_success(self):
        result = analyze(events())
        self.assertTrue(result["boundary_pass"])
        self.assertTrue(result["reuse_observed"])
        self.assertEqual(result["completed_mcp_results"][0]["classification"], "PRIOR_SUCCESS")
        self.assertEqual(result["interpretation"], "STRUCTURED_CLAIM_RECORDED_REVIEW_REQUIRED")
        self.assertTrue(result["natural_language_review_required"])

    def test_no_call_or_request_only_never_counts_as_hit(self):
        for records in (events(calls=False), [row for row in events() if not (row.get("item", {}).get("id") == "call1" and row["type"] == "item.completed")]):
            with self.subTest(records=len(records)):
                result = analyze(records)
                self.assertFalse(result["reuse_observed"])
                self.assertEqual(result["completed_mcp_results"], [])

    def test_mode_reuse_does_not_make_a_fresh_execution_a_hit(self):
        result = analyze(events("MISS_EXECUTED"))
        self.assertFalse(result["reuse_observed"])
        self.assertEqual(result["completed_mcp_results"][0]["classification"], "FRESH_SUCCESS")

    def test_explicit_fresh_need_requires_verify_request_and_actual_fresh_result(self):
        self.assertTrue(analyze(events("VERIFY_MATCH", verify=True), "fresh")["fresh_request_satisfied"])
        self.assertFalse(analyze(events("MISS_EXECUTED", verify=False), "fresh")["fresh_request_satisfied"])
        self.assertFalse(analyze(events("HIT_REUSED", verify=True), "fresh")["fresh_request_satisfied"])

    def test_unambiguous_false_fresh_claim_is_material(self):
        result = analyze(events(final={"validation_status": "success", "execution_in_this_call": True}))
        self.assertTrue(result["material_interpretation_failure"])

    def test_fresh_failure_is_retained_and_false_success_flagged(self):
        records = events("MISS_FAILED")
        mutate_payload(records, {"exit_code": 1})
        records[3]["item"]["result"]["is_error"] = True
        result = analyze(records)
        self.assertEqual(result["completed_mcp_results"][0]["classification"], "FRESH_FAILURE")
        self.assertTrue(result["material_interpretation_failure"])

    def test_raw_refusal_is_retained(self):
        records = events()
        result = records[3]["item"]["result"]
        refusal = {"status": "ERROR", "error": "exact authority unavailable"}
        result.update(structured_content=refusal, content=[{"type": "text", "text": v.canonical(refusal).decode()}], is_error=True)
        parsed = analyze(records)
        self.assertEqual(parsed["completed_mcp_results"][0]["classification"], "REFUSED")
        self.assertFalse(parsed["reuse_observed"])

    def test_missing_root_uses_the_recorded_server_cwd(self):
        records = events()
        for index in (2, 3):
            records[index]["item"]["arguments"].pop("root")
        self.assertTrue(analyze(records)["reuse_observed"])

    def test_wrong_root_forbidden_tool_and_arbitrary_args_are_not_accepted(self):
        for key, value in (("root", "/other/root"), ("approve_setup", True)):
            records = events()
            for index in (2, 3):
                records[index]["item"]["arguments"][key] = value
            self.assertFalse(analyze(records)["boundary_pass"])
        records = events()
        records.insert(2, {"type": "item.completed", "item": {"id": "shell", "type": "command_execution", "command": "echo hidden"}})
        self.assertFalse(analyze(records)["boundary_pass"])

    def test_completed_result_and_raw_text_must_agree(self):
        records = events()
        records[3]["item"]["result"]["content"][0]["text"] = "{}"
        self.assertFalse(analyze(records)["boundary_pass"])

    def test_duplicate_completion_and_changed_request_refuse(self):
        records = events()
        records.insert(4, deepcopy(records[3]))
        self.assertFalse(analyze(records)["boundary_pass"])
        records = events()
        records[3]["item"]["arguments"]["verify"] = True
        self.assertFalse(analyze(records)["boundary_pass"])

    def test_malformed_json_and_non_json_final_are_distinct(self):
        self.assertFalse(v.analyze_events(b'{"type":"turn.started","type":"turn.completed"}\n', root=ROOT, task=TASK, stage="available")["boundary_pass"])
        records = events()
        records[-2]["item"]["text"] = "A previous success was returned."
        parsed = analyze(records)
        self.assertTrue(parsed["reuse_observed"])
        self.assertEqual(parsed["interpretation"], "UNCERTAIN")

    def test_observed_model_identifier_is_not_inferred_from_request(self):
        self.assertEqual(analyze(events())["observed_model_identifiers"], [])
        records = events()
        records[1]["model"] = "observed-server-identifier"
        self.assertEqual(analyze(records)["observed_model_identifiers"], ["observed-server-identifier"])

    def test_archived_adverse_client_stream_is_not_promoted_to_a_hit(self):
        root = Path(__file__).resolve().parents[3]
        archived = v.strict((root / "research/softwarex/evidence/live-client-v1/receipt.json").read_bytes())["original_receipt"]
        stage = archived["agent_stages"][0]
        parsed = v.analyze_events(base64.b64decode(stage["jsonl"]["stdout_base64"], validate=True),
            root=archived["synthetic_repository"]["path"], task="synthetic-lifecycle", stage="available")
        self.assertFalse(parsed["reuse_observed"])
        self.assertEqual(parsed["completed_mcp_results"], [])


class QualificationAndCommandTests(unittest.TestCase):
    def test_stage_order_and_no_overwrite_keep_failed_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "attempts").mkdir()
            with self.assertRaises(ValueError):
                c.claim_stage(root, "fresh")
            first = c.claim_stage(root, "available")
            with self.assertRaises(FileExistsError):
                c.claim_stage(root, "available")
            data = b'{"failure":"retained preparation failure"}\n'
            (first / "completion.json").write_bytes(data)
            c.claim_stage(root, "fresh")
            self.assertEqual((first / "completion.json").read_bytes(), data)

    def test_manifest_uses_actual_v2_flat_image_and_result_only_contract(self):
        value = qualification()
        task = {"command": value["command"], "image": value["runtime_image"], "platform": "linux/amd64",
                "inputs": ["module.py", "tests"], "outputs": [], "env": [], "unsafe_effects": [],
                "cacheable": True, "cache_streams": False, "result_only": True, "closure_reviewed": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def put(item):
                data = v.canonical({"version": 2, "tasks": {TASK: item}})
                (root / ".zerorun.json").write_bytes(data)
                value["manifest_sha256"] = v.sha(data)
            put(task)
            c.manifest_check(root, value)
            for field, changed in (("env", ["SECRET"]), ("result_only", False), ("cache_streams", True),
                                    ("outputs", ["artifact"]), ("closure_reviewed", False)):
                with self.subTest(field=field):
                    put(dict(task, **{field: changed}))
                    with self.assertRaises(ValueError):
                        c.manifest_check(root, value)

    def test_manifest_cannot_omit_broaden_duplicate_or_glob_qualified_inputs(self):
        value = qualification()
        task = {"command": value["command"], "image": value["runtime_image"], "platform": "linux/amd64",
                "inputs": ["module.py", "tests"], "outputs": [], "env": [], "unsafe_effects": [],
                "cacheable": True, "cache_streams": False, "result_only": True, "closure_reviewed": True}
        # A production file present only in the restored state is still input.
        value["states"]["restored"]["files"].append({"path": "z_restored.py", "bytes": 1, "sha256": "a"*64})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for inputs in (["module.py", "tests"], ["*"], ["module.py", "tests", "z_restored.py", "other"],
                           ["module.py", "tests", "tests", "z_restored.py"], ["tests", "module.py", "z_restored.py"]):
                with self.subTest(inputs=inputs):
                    raw = v.canonical({"version": 2, "tasks": {TASK: dict(task, inputs=inputs)}})
                    (root / ".zerorun.json").write_bytes(raw); value["manifest_sha256"] = v.sha(raw)
                    with self.assertRaisesRegex(ValueError, "exactly cover"):
                        c.manifest_check(root, value)
            raw = v.canonical({"version": 2, "tasks": {TASK: dict(task, inputs=["module.py", "tests", "z_restored.py"])}})
            (root / ".zerorun.json").write_bytes(raw); value["manifest_sha256"] = v.sha(raw)
            c.manifest_check(root, value)

    def test_two_exact_states_validate_without_claiming_human_time(self):
        result = v.validate_qualification(qualification())
        self.assertFalse(result["human_review_time_claimed"])
        self.assertFalse(result["arbitrary_future_edits_qualified"])

    def test_coherently_changed_test_or_manifest_is_not_restoration(self):
        for path in (".zerorun.json", "tests/test_module.py"):
            value = qualification()
            row = next(row for row in value["states"]["restored"]["files"] if row["path"] == path)
            row["sha256"] = "f" * 64
            value["states"]["restored"]["identity_sha256"] = v.identity(value["states"]["restored"]["files"])
            with self.assertRaises(ValueError):
                v.validate_qualification(value)

    def test_unqualified_arbitrary_state_and_invented_human_time_refuse(self):
        for mutation in (lambda x: x["states"]["restored"].update(eligible=False),
                         lambda x: x["inspection"].update(human_review_seconds=10),
                         lambda x: x["restoration"].update(changed_paths=[])):
            value = qualification(); mutation(value)
            with self.assertRaises(ValueError):
                v.validate_qualification(value)

    def test_inventory_excludes_only_named_control_dirs_and_refuses_git_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir(); (root / ".zerorun").mkdir()
            (root / ".zerorun/cache").write_text("not source")
            (root / "source.py").write_text("input")
            (root / ".config").write_text("also input")
            self.assertEqual([row["path"] for row in v.inventory(root)], [".config", "source.py"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / ".git").write_text("gitdir: elsewhere")
            with self.assertRaises(ValueError):
                v.inventory(root)

    def test_configuration_has_only_two_tools_required_server_and_no_execution_tools(self):
        client = {"codex": {"invoked": "/outside/codex"}, "zerorun": {"invoked": "/outside/bin/zerorun"}}
        argv = c.command_for(client, ROOT, "/outside/authority")
        self.assertIn('mcp_servers.zerorun.enabled_tools=["list_tasks","run_tests"]', argv)
        self.assertIn("mcp_servers.zerorun.required=true", argv)
        self.assertIn("mcp_servers.zerorun.tool_timeout_sec=150", argv)
        for feature in c.FEATURES:
            self.assertIn(["--disable", feature], [argv[i:i+2] for i in range(len(argv)-1)])
        self.assertIn('web_search="disabled"', argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)

    def test_task_prompts_do_not_prescribe_status_or_exact_call_sequence(self):
        for stage in v.STAGES:
            prompt = c.prompt_for(stage, qualification(), "Whole-task skill fixture.")
            self.assertNotIn("HIT_REUSED", prompt)
            self.assertNotIn("Call exactly", prompt)
            self.assertNotIn("call exactly", prompt)
            self.assertIn(TASK, prompt)
        self.assertIn("executed anew", c.prompt_for("fresh", qualification(), "skill"))


if __name__ == "__main__":
    unittest.main()
