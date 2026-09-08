"""Actual-response and tamper controls for the source-indexed audit."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from research.softwarex import build_consumer_lizard_audit as audit


class ConsumerAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = audit.read(audit.ROOT, audit.app.OUTPUT)
        cls.first = cls.application["consumer"]["cases"][0]

    def raw(self, stage="available"):
        attempt = next(a for a in self.first["stages"] if a["stage"] == stage)
        path = audit.ROOT / audit.app.CONSUMERS / attempt["raw_path"] / "model.stdout.log"
        return path.read_bytes()

    def parse(self, raw, stage="available"):
        return audit.literal_consumer_row(raw, self.first["case_id"], stage,
                                          self.first["seed"]["root"], self.first["seed"]["task"])

    def events(self, stage="available"):
        return [json.loads(line) for line in self.raw(stage).splitlines()]

    @staticmethod
    def encoded(events):
        return ("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n").encode()

    @staticmethod
    def result_event(events):
        return next(e for e in events if e.get("type") == "item.completed"
                    and e.get("item", {}).get("type") == "mcp_tool_call")

    def test_all_fifteen_actual_messages_have_exact_locators_and_thirteen_extra_assertions(self):
        rows = audit.consumer_rows(audit.ROOT, self.application)
        self.assertEqual(15, len(rows))
        self.assertEqual(13, sum(bool(r["extra_wire_assertions"]) for r in rows))
        self.assertTrue(all(r["freshness_claim_matches_recorded_status"] for r in rows))
        self.assertTrue(all(r["outcome_claim_matches_recorded_status"] for r in rows))
        for row in rows:
            with self.subTest(case=row["case_id"], stage=row["stage"]):
                lines = (audit.ROOT / row["response"]["path"]).read_bytes().splitlines()
                message = json.loads(lines[row["final_message_locator"]["jsonl_line"] - 1])["item"]["text"]
                claim = json.loads(message)
                self.assertEqual(row["model_execution_in_this_call"], claim["execution_in_this_call"])
                self.assertEqual(row["model_validation_status"], claim["validation_status"])
                self.assertIn(row["diagnostics_excerpt"], claim["diagnostics"])
                for detail in row["extra_wire_assertions"]:
                    self.assertEqual(detail["excerpt"], message[detail["decoded_final_message_char_start"]:detail["decoded_final_message_char_end"]])
                self.assertFalse(row["wire_error_flag_captured"])

    def test_actual_failed_client_result_is_indexed_as_fresh_failure(self):
        row = self.parse(self.raw("restored"), "restored")
        self.assertEqual(("MISS_FAILED", 1, "failed"), (row["observed_status"], row["observed_exit_code"], row["client_item_status"]))
        self.assertEqual("UNVERIFIABLE_WIRE_FIELD_NOT_CAPTURED", row["extra_wire_assertions"][0]["support"])

    def test_contradictory_model_claim_is_retained_as_disagreement_not_rewritten(self):
        events = self.events()
        final = [e for e in events if e.get("item", {}).get("type") == "agent_message"][-1]
        claim = json.loads(final["item"]["text"])
        claim.update(execution_in_this_call=True, validation_status="failure")
        final["item"]["text"] = json.dumps(claim)
        row = self.parse(self.encoded(events))
        self.assertTrue(row["model_execution_in_this_call"])
        self.assertEqual("failure", row["model_validation_status"])
        self.assertFalse(row["freshness_claim_matches_recorded_status"])
        self.assertFalse(row["outcome_claim_matches_recorded_status"])

    def test_wire_flag_absence_cannot_be_changed_to_captured_false(self):
        events = self.events()
        self.result_event(events)["item"]["result"]["isError"] = False
        with self.assertRaisesRegex(ValueError, "capture shape changed"):
            self.parse(self.encoded(events))

    def test_mismatched_structured_and_text_results_fail_closed(self):
        events = self.events()
        self.result_event(events)["item"]["result"]["structured_content"]["exit_code"] = 1
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.parse(self.encoded(events))

    def test_missing_or_duplicate_actual_call_fails_closed(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                events = self.events(); event = self.result_event(events)
                if duplicate:
                    events.insert(5, copy.deepcopy(event))
                else:
                    events.remove(event)
                with self.assertRaisesRegex(ValueError, "one actual completed"):
                    self.parse(self.encoded(events))

    def test_wrong_recorded_root_and_truncated_turn_fail_closed(self):
        events = self.events(); self.result_event(events)["item"]["arguments"]["root"] += "-other"
        with self.assertRaisesRegex(ValueError, "root/task differs"):
            self.parse(self.encoded(events))
        with self.assertRaisesRegex(ValueError, "completed model turn"):
            self.parse(self.encoded(self.events()[:-1]))

    def test_modified_capture_does_not_reuse_old_review_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for case in self.application["consumer"]["cases"]:
                for attempt in case["stages"]:
                    relative = Path(audit.app.CONSUMERS) / attempt["raw_path"] / "model.stdout.log"
                    path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
                    raw = (audit.ROOT / relative).read_bytes()
                    path.write_bytes(raw.replace(b"No tests executed", b"No tests freshly executed", 1)
                                     if case == self.first and attempt["stage"] == "available" else raw)
            with self.assertRaisesRegex(ValueError, "differs from reconciled"):
                audit.consumer_rows(root, self.application)
            target = root / audit.app.CONSUMERS / self.first["stages"][0]["raw_path"] / "model.stdout.log"
            target.unlink()
            with self.assertRaises((ValueError, FileNotFoundError)):
                audit.consumer_rows(root, self.application)

    def test_primary_six_case_counts_do_not_relabel_assisted_sqlglot_fix(self):
        rows = audit.derive_case_rows(audit.ROOT, self.application)
        self.assertEqual([46, 114, 99, 21, 18, 49], [r["target_nodes"] for r in rows])
        sql = next(r for r in rows if r["case_id"] == "tobymao__sqlglot-3182")
        self.assertEqual((97, 2, 0, False), (sql["final"]["passed"], sql["final"]["failed"], sql["model_consumer_stages"], sql["primary_consumer_eligible"]))

    def test_missing_or_duplicate_per_node_outcomes_refused(self):
        value = {"nodeids": ["a", "a"], "outcomes": [{"call": "passed"}] * 2, "exit_code": 0}
        with self.assertRaisesRegex(ValueError, "missing/duplicate"):
            audit.capture_counts(value)


class GeneratedArtifactTests(unittest.TestCase):
    def test_json_and_markdown_both_bound_and_individually_tamper_sensitive(self):
        value = audit.read(audit.ROOT, audit.JSON_PATH)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (audit.JSON_PATH, audit.MARKDOWN_PATH):
                path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((audit.ROOT / relative).read_bytes())
            audit.check_outputs(root, value)
            for relative in (audit.JSON_PATH, audit.MARKDOWN_PATH):
                path = root / relative; original = path.read_bytes(); path.write_bytes(original + b"changed")
                with self.assertRaises(ValueError):
                    audit.check_outputs(root, value)
                path.write_bytes(original)
            (root / audit.MARKDOWN_PATH).unlink()
            with self.assertRaises((ValueError, FileNotFoundError)):
                audit.check_outputs(root, value)

    def test_pinned_lizard_intervention_and_unfavorable_original_remain_visible(self):
        value = audit.read(audit.ROOT, audit.JSON_PATH)["lizard_correction"]
        self.assertEqual((2, 3), (value["before_expected"], value["after_expected"]))
        self.assertEqual([23, 24], [r["completed"] for r in value["cohorts"]])
        original = value["fresh_observations"][0]
        self.assertEqual(("V5", 86, 1, 1), (original["cohort"], original["passed"], original["failed"], original["exit_code"]))
        self.assertTrue(value["strict_correction_check"]["exact_single_assertion_change_verified"])
        self.assertEqual("67d87968e9fecd459c9a9a1dcb01cf6ceac5721d", value["upstream"]["commit"])


if __name__ == "__main__":
    unittest.main()
