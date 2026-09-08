"""Actual captured failed-client representation and tamper controls; no model calls."""
from copy import deepcopy
from pathlib import Path
import unittest
from . import consumer_event_audit_v1 as a
from . import validation as v

FIXTURE_SHA = "819790ccfa2682b3d63fd7116e2efc20c57b7c129ffb1078903046453953750d"


class AdditiveClientFailureAuditTests(unittest.TestCase):
    def setUp(self):
        self.raw = (Path(__file__).parent / "fixtures/restored-failed-client-v1.log").read_bytes()
        self.assertEqual(v.sha(self.raw), FIXTURE_SHA)
        self.rows = [v.strict(line) for line in self.raw.splitlines()]
        self.item = next(row["item"] for row in self.rows if row["type"] == "item.completed"
                         and row.get("item", {}).get("type") == "mcp_tool_call")
        self.root, self.task = self.item["arguments"]["root"], self.item["arguments"]["task"]

    def encode(self):
        return b"".join(v.canonical(row) + b"\n" for row in self.rows)

    def check(self, raw=None):
        return a.audit(self.raw if raw is None else raw, self.root, self.task, "restored")

    def payload(self, **changes):
        result = self.item["result"]
        result["structured_content"].update(changes)
        result["content"][0]["text"] = v.canonical(result["structured_content"]).decode()

    def test_actual_captured_stream_is_additive_fresh_failure_without_wire_flag(self):
        original = v.analyze_events(self.raw, root=self.root, task=self.task, stage="restored")
        self.assertTrue(original["boundary_pass"])
        self.assertEqual(original["completed_mcp_results"], [])
        before = deepcopy(original)
        checked = self.check()
        self.assertEqual(checked["additional_fresh_failures"], 1)
        self.assertEqual(checked["additional_results"][0]["classification"], "FRESH_FAILURE")
        self.assertIsNone(checked["additional_results"][0]["wire_is_error"])
        self.assertFalse(checked["wire_error_flag_inferred"])
        self.assertFalse(checked["original_analysis_modified"])
        self.assertTrue(checked["fresh_request_satisfied_additionally"])
        self.assertFalse(checked["reuse_observed_additionally"])
        self.assertTrue(checked["natural_language_review_required"])
        self.assertEqual(original, before)
        self.assertEqual(v.analyze_events(self.raw, root=self.root, task=self.task, stage="restored"), before)

    def test_text_structured_mismatch_refuses(self):
        self.item["result"]["content"][0]["text"] = "{}"
        with self.assertRaises(ValueError): self.check(self.encode())

    def test_tool_error_or_missing_result_is_never_fresh_evidence(self):
        for change in ({"error": {"message": "transport failed"}}, {"result": None}):
            old = deepcopy(self.item); self.item.update(change)
            with self.assertRaises(ValueError): self.check(self.encode())
            self.item.clear(); self.item.update(old)

    def test_changed_status_exit_verified_outputs_or_key_refuses(self):
        original = deepcopy(self.item["result"])
        for change in ({"status": "HIT_REUSED"}, {"status": "ERROR"}, {"exit_code": 0},
                       {"exit_code": True}, {"exit_code": -1}, {"verified": True},
                       {"restored_outputs": ["artifact"]}, {"cache_key": None}, {"mode": "observation"}):
            with self.subTest(change=change):
                self.item["result"] = deepcopy(original); self.payload(**change)
                with self.assertRaises(ValueError): self.check(self.encode())

    def test_wire_flag_not_silently_synthesized_or_accepted(self):
        for key in ("isError", "is_error"):
            self.item["result"][key] = True
            with self.assertRaises(ValueError): self.check(self.encode())
            del self.item["result"][key]

    def test_tail_type_and_unicode_boundary(self):
        original = deepcopy(self.item["result"])
        self.payload(stdout_tail="\ufffd" * 4000)
        self.assertEqual(self.check(self.encode())["additional_fresh_failures"], 1)
        for value in ("x" * 4001, None, 4):
            self.item["result"] = deepcopy(original); self.payload(stdout_tail=value)
            with self.assertRaises(ValueError): self.check(self.encode())

    def test_request_only_duplicate_malformed_or_forbidden_boundary_refuses(self):
        original = deepcopy(self.rows)
        completion = next(i for i, row in enumerate(self.rows) if row.get("item") is self.item)
        # No completed result remains no additive evidence, even with a request.
        self.rows.pop(completion)
        self.assertEqual(self.check(self.encode())["additional_fresh_failures"], 0)
        self.rows = deepcopy(original); self.rows.insert(completion, deepcopy(self.rows[completion]))
        with self.assertRaises(ValueError): self.check(self.encode())
        with self.assertRaises(ValueError): self.check(b'{"type":"turn.started","type":"turn.completed"}\n')
        self.rows = deepcopy(original)
        self.rows.insert(2, {"type": "item.completed", "item": {"id": "shell", "type": "command_execution"}})
        with self.assertRaises(ValueError): self.check(self.encode())

    def test_root_task_and_request_drift_refuse(self):
        for root, task in (("/other", self.task), (self.root, "other")):
            with self.assertRaises(ValueError): a.audit(self.raw, root, task, "restored")
        self.item["arguments"]["verify"] = False
        with self.assertRaises(ValueError): self.check(self.encode())

    def test_final_language_alone_is_not_validation(self):
        self.rows = [row for row in self.rows if row.get("item", {}).get("type") != "mcp_tool_call"]
        checked = self.check(self.encode())
        self.assertEqual(checked["additional_fresh_failures"], 0)
        self.assertFalse(checked["fresh_request_satisfied_additionally"])
        self.assertTrue(checked["natural_language_review_required"])


if __name__ == "__main__": unittest.main()
