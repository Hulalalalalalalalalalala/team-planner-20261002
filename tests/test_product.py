import json
from datetime import date, timedelta
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from team_planner import TeamPlanner

class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = TeamPlanner(self.root)

    def test_roster_and_reopen(self):
        self.app.add_member("A", "Alice")
        self.app.create_activity("T", "Training", "2026-10-15", 2)
        self.app.enroll("T", "A")
        self.assertEqual(TeamPlanner(self.root).roster("T")["members"], [{"member_id": "A", "name": "Alice"}])

    def test_capacity_duplicate_and_unknown_leave_state_unchanged(self):
        self.app.add_member("A", "Alice")
        self.app.add_member("B", "Bob")
        self.app.create_activity("T", "Training", "2026-10-15", 1)
        self.app.enroll("T", "A")
        before = self.app.path.read_bytes()
        for member in ["A", "B", "UNKNOWN"]:
            with self.assertRaises(ValueError):
                self.app.enroll("T", member)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_bad_activity_does_not_create_file(self):
        with self.assertRaises(ValueError):
            self.app.create_activity("T", "Training", "2026-02-30", 2)
        with self.assertRaises(ValueError):
            self.app.create_activity("T", "Training", "2026-10-15", True)
        self.assertEqual(self.app.activities(), [])

    def test_cli_demo_and_invalid_action(self):
        result = subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "demo"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["members"], [{"member_id":"M-001", "name":"小陈"}])
        failed = subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "not-an-action"], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)

    def _ready(self):
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-001", "新成员产品介绍", "2026-10-15", 2)
        self.app.create_activity("A-002", "二", "2026-10-10", 5)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-001")

    def test_record_completion_and_query_sorted(self):
        self._ready()
        record = self.app.record_completion("A-001", "M-001", "2026-10-16")
        self.assertEqual(record, {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16", "title": "新成员产品介绍", "on": "2026-10-15"})
        self.app.record_completion("A-002", "M-001", "2026-10-16")
        # Equal completion dates order by activity date, then activity_id.
        self.assertEqual([r["activity_id"] for r in TeamPlanner(self.root).completions("M-001")], ["A-002", "A-001"])
        # Completion does not cancel enrollment or change order.
        self.assertEqual(self.app.roster("A-001")["members"], [{"member_id": "M-001", "name": "小陈"}])
        self.assertEqual(self.app.completions("M-002"), [])

    def test_completion_equal_date_and_legacy_file(self):
        self._ready()
        self.assertEqual(self.app.record_completion("A-001", "M-001", "2026-10-15")["completed_on"], "2026-10-15")
        # A historical data.json without completion records means zero completions.
        legacy = self.root / "legacy"
        TeamPlanner(legacy).add_member("M-002", "小林")
        self.assertEqual(TeamPlanner(legacy).completions("M-002"), [])

    def test_completion_rejections_leave_state_unchanged(self):
        self._ready()
        before = self.app.path.read_bytes()
        for kwargs in [
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-14"},
            {"activity_id": "A-001", "member_id": "M-002", "completed_on": "2026-10-16"},
            {"activity_id": "A-001", "member_id": "GHOST", "completed_on": "2026-10-16"},
            {"activity_id": "GHOST", "member_id": "M-001", "completed_on": "2026-10-16"},
            {"activity_id": "  ", "member_id": "M-001", "completed_on": "2026-10-16"},
            {"activity_id": "A-001", "member_id": 9, "completed_on": "2026-10-16"},
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-02-30"},
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": " 2026-10-16"},
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": 10},
        ]:
            with self.assertRaises(ValueError):
                self.app.record_completion(**kwargs)
        self.assertEqual(before, self.app.path.read_bytes())
        self.app.record_completion("A-001", "M-001", "2026-10-16")
        before = self.app.path.read_bytes()
        # An existing completion can neither be overwritten nor replaced.
        with self.assertRaises(ValueError):
            self.app.record_completion("A-001", "M-001", "2026-10-20")
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.completions("GHOST")

    def test_invalid_completion_does_not_create_file(self):
        fresh = TeamPlanner(self.root / "empty")
        with self.assertRaises(ValueError):
            fresh.record_completion("A", "B", "nope")
        self.assertFalse((self.root / "empty" / "data.json").exists())

    def test_cli_complete_and_completions(self):
        self._ready()
        def run(action, payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), action, name], text=True, capture_output=True)
        early = run("complete", {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-14"})
        self.assertEqual(early.returncode, 2)
        self.assertIn("error", json.loads(early.stderr))
        ok = run("complete", {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["completed_on"], "2026-10-16")
        duplicate = run("complete", {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-17"})
        self.assertEqual(duplicate.returncode, 2)
        query = run("completions", {"member_id": "M-001"})
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertEqual(len(json.loads(query.stdout)), 1)
        unknown = run("completions", {"member_id": "GHOST"})
        self.assertEqual(unknown.returncode, 2)

    def _batch_ready(self):
        # Two fictional trainings on 2026-10-15: A-001 holds 小陈, A-002 holds
        # 小周 and 小林. 小陈 also has the finished A-003 whose seat stays.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "新成员产品介绍", "2026-10-15", 2)
        self.app.create_activity("A-002", "安全规范", "2026-10-15", 2)
        self.app.create_activity("A-003", "团队协作", "2026-10-15", 2)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-003")
        self.app.enroll("A-002", "M-002")
        self.app.enroll("A-003", "M-001")
        self.app.record_completion("A-003", "M-001", "2026-10-15")

    def test_record_completions_cross_activity_and_member(self):
        self._batch_ready()
        # Identifiers are trimmed and the entries may span activities and
        # members; a future date is accepted regardless of the current date.
        result = self.app.record_completions([
            {"activity_id": " A-001 ", "member_id": "M-001", "completed_on": "2026-10-16"},
            {"activity_id": "A-002", "member_id": " M-003 ", "completed_on": "2026-10-15"},
        ])
        self.assertEqual(result, [
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16", "title": "新成员产品介绍", "on": "2026-10-15"},
            {"activity_id": "A-002", "member_id": "M-003", "completed_on": "2026-10-15", "title": "安全规范", "on": "2026-10-15"},
        ])
        reopened = TeamPlanner(self.root)
        # Sorted by completed_on, then on and activity_id.
        self.assertEqual([r["activity_id"] for r in reopened.completions("M-001")], ["A-003", "A-001"])
        self.assertEqual([r["activity_id"] for r in reopened.completions("M-003")], ["A-002"])
        self.assertEqual(reopened.completions("M-002"), [])
        # Completion keeps the enrollment, capacity and participant order;
        # schedules and exports pick up the new records.
        self.assertEqual([m["member_id"] for m in reopened.roster("A-001")["members"]], ["M-001"])
        self.assertEqual([m["member_id"] for m in reopened.roster("A-002")["members"]], ["M-003", "M-002"])
        self.assertEqual({r["activity_id"]: r["status"] for r in reopened.member_schedule("M-001")},
                         {"A-001": "completed", "A-003": "completed"})
        self.assertEqual(reopened.export_schedule(status="completed")["row_count"], 3)

    def test_record_completions_other_members_record_does_not_block(self):
        self._batch_ready()
        self.app.record_completions([
            {"activity_id": "A-002", "member_id": "M-003", "completed_on": "2026-10-15"},
        ])
        # 小林's own record for A-002 is accepted even though 小周 already has one.
        result = self.app.record_completions([
            {"activity_id": "A-002", "member_id": "M-002", "completed_on": "2026-10-16"},
        ])
        self.assertEqual(result[0]["member_id"], "M-002")
        self.assertEqual([r["member_id"] for r in self.app.completions("M-003")], ["M-003"])

    def test_record_completions_rejections_are_atomic(self):
        self._batch_ready()
        before = self.app.path.read_bytes()
        for records in [
            None,
            [],
            "x",
            3,
            {},
            [42],
            [None],
            [[]],
            [{"activity_id": "A-001", "member_id": "M-001"}],
            [{"activity_id": "A-001", "completed_on": "2026-10-15"}],
            [{"member_id": "M-001", "completed_on": "2026-10-15"}],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-15", "extra": 1}],
            [{"activity_id": "   ", "member_id": "M-001", "completed_on": "2026-10-15"}],
            [{"activity_id": 9, "member_id": "M-001", "completed_on": "2026-10-15"}],
            [{"activity_id": "A-001", "member_id": None, "completed_on": "2026-10-15"}],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-02-30"}],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": " 2026-10-15"}],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-15 "}, ],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": 10}],
            [{"activity_id": "GHOST", "member_id": "M-001", "completed_on": "2026-10-15"}],
            [{"activity_id": "A-001", "member_id": "GHOST", "completed_on": "2026-10-15"}],
            [{"activity_id": "A-001", "member_id": "M-002", "completed_on": "2026-10-15"}],  # not enrolled
            [{"activity_id": "A-003", "member_id": "M-001", "completed_on": "2026-10-15"}],  # already recorded
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-14"}],  # before activity date
            # Duplicate activity/member pair after trimming: same date is no merge.
            [{"activity_id": " A-001 ", "member_id": "M-001", "completed_on": "2026-10-15"},
             {"activity_id": "A-001", "member_id": " M-001 ", "completed_on": "2026-10-15"}],
            # The last entry fails on an existing own completion.
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-15"},
             {"activity_id": "A-003", "member_id": "M-001", "completed_on": "2026-10-15"}],
            # The first entry fails on enrollment; a valid later entry cannot save it.
            [{"activity_id": "A-001", "member_id": "M-002", "completed_on": "2026-10-15"},
             {"activity_id": "A-002", "member_id": "M-003", "completed_on": "2026-10-15"}],
        ]:
            with self.assertRaises(ValueError):
                self.app.record_completions(records)
            self.assertEqual(before, self.app.path.read_bytes())
        # Nothing from any rejected group survived.
        self.assertEqual([r["activity_id"] for r in self.app.completions("M-001")], ["A-003"])
        self.assertEqual(self.app.completions("M-002"), [])
        self.assertEqual(self.app.completions("M-003"), [])

    def test_invalid_record_completions_does_not_create_file(self):
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        for records in [
            [],
            "x",
            [{"activity_id": "A", "member_id": "B", "completed_on": "nope"}],
            [{"activity_id": "A", "member_id": "B"}],
        ]:
            with self.assertRaises(ValueError):
                fresh.record_completions(records)
        # A well-formed group against a missing history fails the business
        # rules and must not create the directory or data.json either.
        with self.assertRaises(ValueError):
            fresh.record_completions([{"activity_id": "A", "member_id": "B", "completed_on": "2026-10-15"}])
        self.assertFalse(empty.exists())
        self.assertFalse((empty / "data.json").exists())

    def test_record_completions_legacy_file_and_extra_fields_preserved(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.create_activity("A-001", "旧活动", "2026-10-15", 2)
        other.enroll("A-001", "M-001")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        raw["note"] = "imported from the old planner"
        raw["members"]["M-001"]["team"] = "平台"
        raw["activities"]["A-001"]["location"] = "一号会议室"
        raw["completions"] = {"A-001": {}}
        (legacy / "data.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        result = TeamPlanner(legacy).record_completions([
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-15"},
        ])
        self.assertEqual(result[0]["completed_on"], "2026-10-15")
        saved = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["note"], "imported from the old planner")
        self.assertEqual(saved["members"]["M-001"]["team"], "平台")
        self.assertEqual(saved["activities"]["A-001"]["location"], "一号会议室")
        self.assertEqual(saved["activities"]["A-001"]["participants"], ["M-001"])
        self.assertEqual(saved["completions"]["A-001"], {"M-001": "2026-10-15"})

    def test_cli_complete_batch_object_and_partial_array(self):
        self._batch_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "complete-batch", name], text=True, capture_output=True)
        ok = run({"records": [
            {"activity_id": " A-001 ", "member_id": "M-001", "completed_on": "2026-10-15"},
            {"activity_id": "A-002", "member_id": " M-003 ", "completed_on": "2026-10-15"},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([r["activity_id"] for r in json.loads(ok.stdout)], ["A-001", "A-002"])
        duplicate = run({"records": [
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16"},
        ]})
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("error", json.loads(duplicate.stderr))
        self.assertEqual(duplicate.stdout, "")
        bad_shape = run({"records": []})
        self.assertEqual(bad_shape.returncode, 2)
        self.assertIn("error", json.loads(bad_shape.stderr))
        # A top-level array is a sequence of independent batches: the first
        # batch succeeds and stays, the second fails, processing stops, stdout
        # stays empty and the error goes to stderr.
        partial = run([
            {"records": [{"activity_id": "A-002", "member_id": "M-002", "completed_on": "2026-10-15"}]},
            {"records": [{"activity_id": "A-003", "member_id": "M-001", "completed_on": "2026-10-15"}]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        reopened = TeamPlanner(self.root)
        self.assertEqual([r["activity_id"] for r in reopened.completions("M-002")], ["A-002"])
        self.assertEqual(sorted(r["activity_id"] for r in reopened.completions("M-001")), ["A-001", "A-003"])
        self.assertEqual(reopened.completions("M-003")[0]["completed_on"], "2026-10-15")

    def _correct_ready(self):
        # Fictional fixed dates: on 2026-10-15 A-001 holds 小陈 (finished on
        # the 16th) and A-002 holds 小周 (finished on the day) and 小林
        # (pending); A-003 on the 20th holds 小陈 (finished on the day).
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "新成员产品介绍", "2026-10-15", 2)
        self.app.create_activity("A-002", "安全规范", "2026-10-15", 2)
        self.app.create_activity("A-003", "团队协作", "2026-10-20", 2)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-003")
        self.app.enroll("A-002", "M-002")
        self.app.enroll("A-003", "M-001")
        self.app.record_completion("A-001", "M-001", "2026-10-16")
        self.app.record_completion("A-002", "M-003", "2026-10-15")
        self.app.record_completion("A-003", "M-001", "2026-10-20")

    def test_correct_completions_cross_activity_change_and_revoke(self):
        self._correct_ready()
        # Identifiers are trimmed; one entry moves a date and another revokes
        # (null) across activities. Dates never depend on the current date.
        result = self.app.correct_completions([
            {"activity_id": " A-001 ", "member_id": "M-001", "completed_on": "2026-10-18"},
            {"activity_id": "A-002", "member_id": " M-003 ", "completed_on": None},
        ])
        self.assertEqual(result, [
            {"activity_id": "A-001", "member_id": "M-001", "title": "新成员产品介绍", "on": "2026-10-15", "previous_completed_on": "2026-10-16", "completed_on": "2026-10-18"},
            {"activity_id": "A-002", "member_id": "M-003", "title": "安全规范", "on": "2026-10-15", "previous_completed_on": "2026-10-15", "completed_on": None},
        ])
        reopened = TeamPlanner(self.root)
        self.assertEqual([(r["activity_id"], r["completed_on"]) for r in reopened.completions("M-001")],
                         [("A-001", "2026-10-18"), ("A-003", "2026-10-20")])
        self.assertEqual(reopened.completions("M-003"), [])
        # schedule and export immediately reflect the new date and the
        # unfinished status.
        rows = {r["activity_id"]: r for r in reopened.member_schedule("M-003")}
        self.assertEqual(rows["A-002"]["status"], "pending")
        self.assertIsNone(rows["A-002"]["completed_on"])
        self.assertEqual(reopened.export_schedule(status="completed")["row_count"], 2)
        self.assertEqual(reopened.export_schedule(status="pending")["row_count"], 2)
        # Revocation neither cancels the enrollment nor frees the seat, and
        # participant order is untouched.
        self.assertEqual([m["member_id"] for m in reopened.roster("A-002")["members"]], ["M-003", "M-002"])
        # After revoking, the record can be registered again; it blocks
        # rescheduling like any completion, and revoking it once more restores
        # the original rules for reschedule and transfer.
        reopened.record_completion("A-002", "M-003", "2026-10-16")
        self.assertEqual(reopened.completions("M-003")[0]["completed_on"], "2026-10-16")
        with self.assertRaises(ValueError):
            reopened.reschedule_activity("A-002", "2026-10-22")
        reopened.correct_completions([{"activity_id": "A-002", "member_id": "M-003", "completed_on": None}])
        reopened.reschedule_activity("A-002", "2026-10-22")
        reopened.transfer_enrollment("A-002", "A-003", "M-003")
        self.assertEqual([m["member_id"] for m in reopened.roster("A-003")["members"]], ["M-001", "M-003"])
        # Other members' records are untouched by all of this.
        self.assertEqual([(r["activity_id"], r["completed_on"]) for r in reopened.completions("M-001")],
                         [("A-001", "2026-10-18"), ("A-003", "2026-10-20")])

    def test_correct_completions_same_date_succeeds_without_rewrite(self):
        self._correct_ready()
        before = self.app.path.read_bytes()
        result = self.app.correct_completions([
            {"activity_id": " A-001 ", "member_id": "M-001", "completed_on": "2026-10-16"},
        ])
        self.assertEqual(result[0]["previous_completed_on"], "2026-10-16")
        self.assertEqual(result[0]["completed_on"], "2026-10-16")
        # Equal old and new date is a success but changes nothing.
        self.assertEqual(before, self.app.path.read_bytes())
        # A mixed group still reports the no-op entry while applying the real
        # change in the other entry.
        mixed = self.app.correct_completions([
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16"},
            {"activity_id": "A-003", "member_id": "M-001", "completed_on": "2026-10-21"},
        ])
        self.assertEqual([(r["previous_completed_on"], r["completed_on"]) for r in mixed],
                         [("2026-10-16", "2026-10-16"), ("2026-10-20", "2026-10-21")])
        saved = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["completions"]["A-001"]["M-001"], "2026-10-16")
        self.assertEqual(saved["completions"]["A-003"]["M-001"], "2026-10-21")

    def test_correct_completions_rejections_are_atomic(self):
        self._correct_ready()
        before = self.app.path.read_bytes()
        for records in [
            None,
            [],
            "x",
            3,
            {},
            [42],
            [None],
            [[]],
            [{"activity_id": "A-001", "member_id": "M-001"}],
            [{"activity_id": "A-001", "completed_on": "2026-10-17"}],
            [{"member_id": "M-001", "completed_on": "2026-10-17"}],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-17", "extra": 1}],
            [{"activity_id": "   ", "member_id": "M-001", "completed_on": "2026-10-17"}],
            [{"activity_id": 9, "member_id": "M-001", "completed_on": "2026-10-17"}],
            [{"activity_id": "A-001", "member_id": None, "completed_on": "2026-10-17"}],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-02-30"}],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": " 2026-10-17"}],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-17 "}],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": 10}],
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": True}],
            [{"activity_id": "GHOST", "member_id": "M-001", "completed_on": "2026-10-17"}],
            [{"activity_id": "A-001", "member_id": "GHOST", "completed_on": None}],
            [{"activity_id": "A-001", "member_id": "M-002", "completed_on": "2026-10-15"}],  # not enrolled
            [{"activity_id": "A-002", "member_id": "M-002", "completed_on": "2026-10-15"}],  # enrolled, no record
            [{"activity_id": "A-002", "member_id": "M-002", "completed_on": None}],         # nothing to revoke
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-14"}],  # before activity date
            [{"activity_id": "A-003", "member_id": "M-001", "completed_on": "2026-10-19"}],
            # Duplicate activity/member pair after trimming: same date is no merge.
            [{"activity_id": " A-001 ", "member_id": "M-001", "completed_on": "2026-10-17"},
             {"activity_id": "A-001", "member_id": " M-001 ", "completed_on": "2026-10-17"}],
            # The last entry has no own record; the first (valid) change is
            # rejected with the whole group.
            [{"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-17"},
             {"activity_id": "A-002", "member_id": "M-002", "completed_on": "2026-10-15"}],
            # The first entry fails; a valid later entry cannot save it.
            [{"activity_id": "A-002", "member_id": "M-002", "completed_on": None},
             {"activity_id": "A-003", "member_id": "M-001", "completed_on": None}],
        ]:
            with self.assertRaises(ValueError):
                self.app.correct_completions(records)
            self.assertEqual(before, self.app.path.read_bytes())
        # Nothing from any rejected group survived.
        self.assertEqual([(r["activity_id"], r["completed_on"]) for r in self.app.completions("M-001")],
                         [("A-001", "2026-10-16"), ("A-003", "2026-10-20")])
        self.assertEqual([(r["activity_id"], r["completed_on"]) for r in self.app.completions("M-003")],
                         [("A-002", "2026-10-15")])
        self.assertEqual(self.app.completions("M-002"), [])

    def test_invalid_correct_completions_does_not_create_file(self):
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        for records in [
            [],
            "x",
            [42],
            [{"activity_id": "A", "member_id": "B"}],
            [{"activity_id": "A", "member_id": "B", "completed_on": "nope"}],
            [{"activity_id": "A", "member_id": "B", "completed_on": None}],
        ]:
            with self.assertRaises(ValueError):
                fresh.correct_completions(records)
        # A well-formed group against a missing history fails the business
        # rules and must not create the directory or data.json either.
        with self.assertRaises(ValueError):
            fresh.correct_completions([{"activity_id": "A", "member_id": "B", "completed_on": "2026-10-15"}])
        self.assertFalse(empty.exists())
        self.assertFalse((empty / "data.json").exists())

    def test_correct_completions_legacy_and_extra_fields_preserved(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.create_activity("A-001", "旧活动", "2026-10-15", 2)
        other.enroll("A-001", "M-001")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        # An old file without completions holds nothing correctable and stays
        # byte-identical.
        before = (legacy / "data.json").read_bytes()
        with self.assertRaises(ValueError):
            TeamPlanner(legacy).correct_completions([
                {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16"},
            ])
        self.assertEqual((legacy / "data.json").read_bytes(), before)
        # A historical document carrying extra fields and another member's
        # record: only the selected date changes, everything else survives.
        other.add_member("M-002", "小林")
        other.enroll("A-001", "M-002")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        raw["note"] = "imported from the old planner"
        raw["members"]["M-001"]["team"] = "平台"
        raw["activities"]["A-001"]["location"] = "一号会议室"
        raw["completions"] = {"A-001": {"M-001": "2026-10-15", "M-002": "2026-10-17"}}
        (legacy / "data.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        result = TeamPlanner(legacy).correct_completions([
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-18"},
        ])
        self.assertEqual(result[0]["previous_completed_on"], "2026-10-15")
        self.assertEqual(result[0]["completed_on"], "2026-10-18")
        saved = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["note"], "imported from the old planner")
        self.assertEqual(saved["members"]["M-001"]["team"], "平台")
        self.assertEqual(saved["activities"]["A-001"]["location"], "一号会议室")
        self.assertEqual(saved["activities"]["A-001"]["participants"], ["M-001", "M-002"])
        self.assertEqual(saved["completions"]["A-001"], {"M-001": "2026-10-18", "M-002": "2026-10-17"})
        # Revoking 小陈 removes only his record; 小林's record and the inner
        # completion object both remain.
        TeamPlanner(legacy).correct_completions([
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": None},
        ])
        saved = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["completions"]["A-001"], {"M-002": "2026-10-17"})
        self.assertEqual([m["member_id"] for m in TeamPlanner(legacy).roster("A-001")["members"]], ["M-001", "M-002"])

    def test_cli_correct_completions_object_and_partial_array(self):
        self._correct_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "correct-completions", name], text=True, capture_output=True)
        ok = run({"records": [
            {"activity_id": " A-001 ", "member_id": "M-001", "completed_on": "2026-10-18"},
            {"activity_id": "A-002", "member_id": " M-003 ", "completed_on": None},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([(r["activity_id"], r["previous_completed_on"], r["completed_on"]) for r in json.loads(ok.stdout)],
                         [("A-001", "2026-10-16", "2026-10-18"), ("A-002", "2026-10-15", None)])
        # 小林 has no own record in A-002: the call fails with error JSON.
        missing = run({"records": [{"activity_id": "A-002", "member_id": "M-002", "completed_on": None}]})
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stderr))
        self.assertEqual(missing.stdout, "")
        bad_shape = run({"records": []})
        self.assertEqual(bad_shape.returncode, 2)
        self.assertIn("error", json.loads(bad_shape.stderr))
        # Top-level array: independent batches. The first batch succeeds
        # (小陈's A-003 date moves); the second fails on an unknown member,
        # processing stops with empty stdout and the error on stderr, while the
        # first batch's change stays.
        partial = run([
            {"records": [{"activity_id": "A-003", "member_id": "M-001", "completed_on": "2026-10-21"}]},
            {"records": [{"activity_id": "A-001", "member_id": "GHOST", "completed_on": None}]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        reopened = TeamPlanner(self.root)
        self.assertEqual([(r["activity_id"], r["completed_on"]) for r in reopened.completions("M-001")],
                         [("A-001", "2026-10-18"), ("A-003", "2026-10-21")])
        self.assertEqual(reopened.completions("M-003"), [])

    def _transfer_ready(self):
        # A-001 on the 15th is full with 小陈 and 小林; A-002 on the 16th
        # still has free seats and 小周 is already enrolled.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.add_member("M-004", "小吴")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 2)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 3)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-001", "M-002")
        self.app.enroll("A-002", "M-003")

    def test_transfer_success_returns_full_target_and_frees_seat(self):
        self._transfer_ready()
        result = self.app.transfer_enrollment("A-001", "A-002", "M-001")
        self.assertEqual(result, {"activity_id": "A-002", "title": "十六号培训", "on": "2026-10-16", "capacity": 3, "participants": ["M-003", "M-001"]})
        # The source frees exactly one seat; 小陈 is gone and 小林 stays.
        source = self.app.roster("A-001")
        self.assertEqual([m["member_id"] for m in source["members"]], ["M-002"])
        self.app.enroll("A-001", "M-004")
        self.assertEqual([m["member_id"] for m in self.app.roster("A-001")["members"]], ["M-002", "M-004"])
        # Titles, dates, capacity and the member profile are untouched and the
        # result survives reopening the same root.
        reopened = TeamPlanner(self.root)
        self.assertEqual(reopened.roster("A-002")["members"], [{"member_id": "M-003", "name": "小周"}, {"member_id": "M-001", "name": "小陈"}])
        self.assertEqual(reopened.activities()[0]["capacity"], 2)
        self.assertEqual(reopened.roster("A-002")["title"], "十六号培训")

    def test_transfer_keeps_relative_participant_order(self):
        self._transfer_ready()
        self.app.enroll("A-002", "M-004")
        self.app.transfer_enrollment("A-001", "A-002", "M-001")
        # Removing the first source member keeps the other's position; the
        # transferred member is appended to the end of the target list.
        self.assertEqual(self.app.roster("A-001")["members"], [{"member_id": "M-002", "name": "小林"}])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-002")["members"]], ["M-003", "M-004", "M-001"])

    def test_transfer_identifiers_are_trimmed(self):
        self._transfer_ready()
        result = TeamPlanner(self.root).transfer_enrollment("  A-001 ", " A-002  ", "  M-001 ")
        self.assertEqual(result["participants"], ["M-003", "M-001"])

    def test_transfer_completion_on_source_blocks_but_other_members_do_not(self):
        self._transfer_ready()
        # 小林's completion for the source does not block 小陈's transfer.
        self.app.record_completion("A-001", "M-002", "2026-10-15")
        result = self.app.transfer_enrollment("A-001", "A-002", "M-001")
        self.assertEqual(result["participants"], ["M-003", "M-001"])
        # 小林's completion record is neither deleted nor moved.
        self.assertEqual([r["member_id"] for r in self.app.completions("M-002")], ["M-002"])
        # A member whose own source training is finished cannot transfer.
        self.app.create_activity("A-003", "十七号培训", "2026-10-17", 3)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.transfer_enrollment("A-001", "A-003", "M-002")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_transfer_third_activity_on_target_date_conflicts(self):
        self._transfer_ready()
        self.app.create_activity("A-003", "十六号另一场", "2026-10-16", 5)
        self.app.enroll("A-003", "M-001")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.transfer_enrollment("A-001", "A-002", "M-001")
        self.assertEqual(before, self.app.path.read_bytes())
        # A completed enrollment in the third activity counts as a conflict too.
        self.app.record_completion("A-003", "M-001", "2026-10-16")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.transfer_enrollment("A-001", "A-002", "M-001")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_transfer_rejections_leave_state_unchanged(self):
        self._transfer_ready()
        self.app.enroll("A-002", "M-004")
        self.app.enroll("A-002", "M-002")
        before = self.app.path.read_bytes()
        for kwargs in [
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-002"},  # already in target
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-003"},  # not in source
            {"source_activity_id": "A-001", "target_activity_id": "A-001", "member_id": "M-001"},  # same activity
            {"source_activity_id": "GHOST", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-001", "target_activity_id": "GHOST", "member_id": "M-001"},
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "GHOST"},
            {"source_activity_id": "  ", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-001", "target_activity_id": 7, "member_id": "M-001"},
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": None},
        ]:
            with self.assertRaises(ValueError):
                self.app.transfer_enrollment(**kwargs)
        self.assertEqual(before, self.app.path.read_bytes())
        # The target is now full (capacity 3): the transfer is refused and no
        # seat is freed on the source.
        with self.assertRaises(ValueError):
            self.app.transfer_enrollment("A-001", "A-002", "M-001")
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.enroll("A-001", "M-004")

    def test_invalid_transfer_does_not_create_file(self):
        fresh = TeamPlanner(self.root / "empty")
        with self.assertRaises(ValueError):
            fresh.transfer_enrollment(" ", "B", "C")
        with self.assertRaises(ValueError):
            fresh.transfer_enrollment("A", "B", "C")
        self.assertFalse((self.root / "empty" / "data.json").exists())

    def test_transfer_legacy_file_without_completions(self):
        # A historical data.json without a completions field behaves as having
        # no completion records and still supports a transfer.
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.create_activity("A-001", "旧活动", "2026-10-15", 2)
        other.create_activity("A-002", "新活动", "2026-10-16", 2)
        other.enroll("A-001", "M-001")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        result = TeamPlanner(legacy).transfer_enrollment("A-001", "A-002", "M-001")
        self.assertEqual(result["participants"], ["M-001"])

    def test_cli_transfer_success_failure_and_partial_array(self):
        self._transfer_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "transfer", name], text=True, capture_output=True)
        ok = run({"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["participants"], ["M-003", "M-001"])
        bad = run({"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        # Array input: set up 小吴 enrolled in the source, move 小林 first
        # (succeeds), then repeat 小林's move (fails). The first success stays.
        self.app.enroll("A-001", "M-004")
        partial = run([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-002"},
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-002"},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual([m["member_id"] for m in TeamPlanner(self.root).roster("A-001")["members"]], ["M-004"])
        self.assertEqual(TeamPlanner(self.root).roster("A-002")["members"][-1], {"member_id": "M-002", "name": "小林"})

    def _transfer_batch_ready(self):
        # 小陈 alone fills A-001 on the 15th; 小林 alone fills A-002 on the 16th.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 1)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 1)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-002")

    def test_transfer_batch_swap_between_full_activities(self):
        self._transfer_batch_ready()
        result = self.app.transfer_enrollments([
            {"source_activity_id": " A-001 ", "target_activity_id": "A-002", "member_id": " M-001 "},
            {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002"},
        ])
        self.assertEqual(result, [
            {"activity_id": "A-002", "title": "十六号培训", "on": "2026-10-16", "capacity": 1, "participants": ["M-001"]},
            {"activity_id": "A-001", "title": "十五号培训", "on": "2026-10-15", "capacity": 1, "participants": ["M-002"]},
        ])
        # The swap survives reopening and shows up in schedules and exports.
        reopened = TeamPlanner(self.root)
        self.assertEqual([m["member_id"] for m in reopened.roster("A-001")["members"]], ["M-002"])
        self.assertEqual([m["member_id"] for m in reopened.roster("A-002")["members"]], ["M-001"])
        self.assertEqual([r["activity_id"] for r in reopened.member_schedule("M-001")], ["A-002"])
        self.assertEqual(reopened.export_schedule()["row_count"], 2)

    def test_transfer_batch_rotation_between_full_activities(self):
        # A three-member rotation among full activities takes effect at once.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 1)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 1)
        self.app.create_activity("A-003", "十七号培训", "2026-10-17", 1)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-002")
        self.app.enroll("A-003", "M-003")
        result = self.app.transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-002", "target_activity_id": "A-003", "member_id": "M-002"},
            {"source_activity_id": "A-003", "target_activity_id": "A-001", "member_id": "M-003"},
        ])
        self.assertEqual([a["participants"] for a in result], [["M-001"], ["M-002"], ["M-003"]])

    def test_transfer_batch_shared_target_reflects_final_roster(self):
        # 小陈 and 小林 move into the same target: both result entries show the
        # final roster, movers are appended in input order, and the members who
        # stay keep their relative order.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.add_member("M-004", "小吴")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 3)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 3)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-001", "M-004")
        self.app.enroll("A-001", "M-002")
        self.app.enroll("A-002", "M-003")
        result = self.app.transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-002"},
        ])
        self.assertEqual([a["participants"] for a in result], [["M-003", "M-001", "M-002"], ["M-003", "M-001", "M-002"]])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-001")["members"]], ["M-004"])

    def test_transfer_batch_own_completion_blocks_group_atomically(self):
        self._transfer_batch_ready()
        # 小陈's own completion for the source rejects the whole swap.
        self.app.record_completion("A-001", "M-001", "2026-10-15")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.transfer_enrollments([
                {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002"},
                {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([r["activity_id"] for r in self.app.completions("M-001")], ["A-001"])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-002")["members"]], ["M-002"])

    def test_transfer_batch_other_members_completions_do_not_block(self):
        # 小周's completion for the source neither blocks 小陈's move nor is
        # deleted or migrated.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 3)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 3)
        self.app.enroll("A-001", "M-003")
        self.app.enroll("A-001", "M-001")
        self.app.record_completion("A-001", "M-003", "2026-10-15")
        result = self.app.transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
        ])
        self.assertEqual(result[0]["participants"], ["M-001"])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-001")["members"]], ["M-003"])
        self.assertEqual(self.app.completions("M-003")[0]["activity_id"], "A-001")

    def test_transfer_batch_third_activity_on_target_date_blocks_group(self):
        self._transfer_batch_ready()
        self.app.create_activity("A-003", "十六号另一场", "2026-10-16", 2)
        self.app.enroll("A-003", "M-001")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.transfer_enrollments([
                {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
                {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        # A completed enrollment in the third activity counts as a conflict too.
        self.app.record_completion("A-003", "M-001", "2026-10-16")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.transfer_enrollments([
                {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_transfer_batch_unrelated_conflicts_do_not_block(self):
        self._transfer_batch_ready()
        # 小陈 also holds a seat in another activity on the SOURCE date; that
        # historical conflict is unrelated to the target date and both the
        # swap and the extra enrollment survive.
        self.app.create_activity("A-003", "十五号另一场", "2026-10-15", 2)
        self.app.enroll("A-003", "M-001")
        result = self.app.transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002"},
        ])
        self.assertEqual([a["activity_id"] for a in result], ["A-002", "A-001"])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-003")["members"]], ["M-001"])

    def test_transfer_batch_final_capacity_overflow_is_atomic(self):
        # Two movers into a target that frees no seat: the final roster would
        # exceed capacity, so the whole group is rejected.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 2)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 2)
        self.app.create_activity("A-003", "十七号培训", "2026-10-17", 2)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-003")
        self.app.enroll("A-003", "M-002")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.transfer_enrollments([
                {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
                {"source_activity_id": "A-003", "target_activity_id": "A-002", "member_id": "M-002"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_transfer_batch_rejections_leave_bytes_unchanged(self):
        self._transfer_batch_ready()
        self.app.create_activity("A-003", "十七号培训", "2026-10-17", 2)
        self.app.enroll("A-003", "M-001")
        before = self.app.path.read_bytes()
        for changes in [
            None,
            [],
            {},
            "x",
            3,
            [42],
            [{"source_activity_id": "A-001", "target_activity_id": "A-002"}],
            [{"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001", "extra": 1}],
            [{"source_activity_id": "  ", "target_activity_id": "A-002", "member_id": "M-001"}],
            [{"source_activity_id": "A-001", "target_activity_id": 7, "member_id": "M-001"}],
            [{"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": None}],
            [{"source_activity_id": "GHOST", "target_activity_id": "A-002", "member_id": "M-001"}],
            [{"source_activity_id": "A-001", "target_activity_id": "GHOST", "member_id": "M-001"}],
            [{"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "GHOST"}],
            [{"source_activity_id": "A-001", "target_activity_id": "A-001", "member_id": "M-001"}],  # same activity
            [{"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-001"}],  # not in source
            [{"source_activity_id": "A-001", "target_activity_id": "A-003", "member_id": "M-001"}],  # already in target
            [{"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
             {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": " M-001 "}],  # duplicate member
        ]:
            with self.assertRaises(ValueError):
                self.app.transfer_enrollments(changes)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_transfer_batch_invalid_does_not_create_file(self):
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        for changes in [[], [{"source_activity_id": "A", "target_activity_id": "B", "member_id": "M"}]]:
            with self.assertRaises(ValueError):
                fresh.transfer_enrollments(changes)
        self.assertFalse(empty.exists())
        self.assertFalse((empty / "data.json").exists())

    def test_transfer_batch_legacy_file_without_completions(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.add_member("M-002", "小林")
        other.create_activity("A-001", "旧活动一", "2026-10-15", 1)
        other.create_activity("A-002", "旧活动二", "2026-10-16", 1)
        other.enroll("A-001", "M-001")
        other.enroll("A-002", "M-002")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        result = TeamPlanner(legacy).transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002"},
        ])
        self.assertEqual([a["participants"] for a in result], [["M-001"], ["M-002"]])

    def test_cli_transfer_batch_success_failure_and_partial_array(self):
        self._transfer_batch_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "transfer-batch", name], text=True, capture_output=True)
        ok = run({"changes": [
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002"},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([a["participants"] for a in json.loads(ok.stdout)], [["M-001"], ["M-002"]])
        # 小陈 is no longer enrolled in A-001: the move fails and stdout stays empty.
        bad = run({"changes": [{"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"}]})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        # Top-level array: independent batches; the first success (swapping
        # back) stays after the later failure, and nothing is printed.
        partial = run([
            {"changes": [
                {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-001"},
                {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-002"},
            ]},
            {"changes": [{"source_activity_id": "A-001", "target_activity_id": "GHOST", "member_id": "M-001"}]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual([m["member_id"] for m in TeamPlanner(self.root).roster("A-001")["members"]], ["M-001"])
        self.assertEqual([m["member_id"] for m in TeamPlanner(self.root).roster("A-002")["members"]], ["M-002"])

    def test_preview_transfer_same_day_full_swap_is_feasible(self):
        # The headline scenario: two full trainings on 2026-10-15 swap seats;
        # with no completion record and no third-activity conflict the preview
        # is feasible. Identifiers are trimmed and entries keep input order.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-001", "十五号甲", "2026-10-15", 1)
        self.app.create_activity("A-002", "十五号乙", "2026-10-15", 1)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-002")
        before = self.app.path.read_bytes()
        result = self.app.preview_transfer_enrollments([
            {"source_activity_id": " A-001 ", "target_activity_id": "A-002", "member_id": " M-001 "},
            {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002"},
        ])
        self.assertEqual(result, {
            "can_transfer": True,
            "changes": [
                {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001", "has_completion": False, "remaining_seats": 0, "conflict_activity_ids": []},
                {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002", "has_completion": False, "remaining_seats": 0, "conflict_activity_ids": []},
            ],
        })
        # A preview never rewrites the file; the rosters stay as they were.
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([m["member_id"] for m in self.app.roster("A-001")["members"]], ["M-001"])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-002")["members"]], ["M-002"])

    def test_preview_transfer_reports_completion_overflow_and_conflict_together(self):
        # One entry carries all three obstacles at once: 小陈's own completion
        # for the source, a full target that frees no seat (negative remaining
        # seats), and a completed-capable third activity on the target date.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 2)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 1)
        self.app.create_activity("A-003", "十六号另一场", "2026-10-16", 2)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-003")
        self.app.enroll("A-003", "M-001")
        self.app.record_completion("A-001", "M-001", "2026-10-15")
        before = self.app.path.read_bytes()
        result = self.app.preview_transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
        ])
        self.assertFalse(result["can_transfer"])
        self.assertEqual(result["changes"], [
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001",
             "has_completion": True, "remaining_seats": -1, "conflict_activity_ids": ["A-003"]},
        ])
        self.assertEqual(before, self.app.path.read_bytes())
        # Other members' completion records are not reported on this entry.
        self.app.record_completion("A-002", "M-003", "2026-10-16")
        result = self.app.preview_transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
        ])
        self.assertTrue(result["changes"][0]["has_completion"])

    def test_preview_transfer_shared_target_seats_are_consistent(self):
        # Two movers share one target: every entry naming that target reports
        # the same final seat count (negative when overflowing), and an entry
        # whose own source is finished still counts toward the headcount.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.add_member("M-004", "小吴")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 3)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 2)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-001", "M-004")
        self.app.enroll("A-001", "M-002")
        self.app.enroll("A-002", "M-003")
        self.app.record_completion("A-001", "M-001", "2026-10-15")
        result = self.app.preview_transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-002"},
        ])
        self.assertEqual([c["remaining_seats"] for c in result["changes"]], [-1, -1])
        self.assertEqual([c["has_completion"] for c in result["changes"]], [True, False])
        self.assertFalse(result["can_transfer"])

    def test_preview_transfer_conflict_ids_sorted_completed_counts(self):
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 4)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 4)
        self.app.create_activity("A-003", "十六号一场", "2026-10-16", 4)
        self.app.create_activity("A-004", "十六号已完成", "2026-10-16", 4)
        self.app.create_activity("A-005", "十六号他人场", "2026-10-16", 4)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-003", "M-001")
        self.app.enroll("A-003", "M-003")
        self.app.enroll("A-004", "M-001")
        self.app.enroll("A-005", "M-002")
        self.app.record_completion("A-004", "M-001", "2026-10-16")
        result = self.app.preview_transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
        ])
        self.assertFalse(result["can_transfer"])
        # The completed A-004 still counts, ids are sorted, and A-005 (only
        # 小林 enrolled) is no conflict for 小陈.
        self.assertEqual(result["changes"][0]["conflict_activity_ids"], ["A-003", "A-004"])
        self.assertFalse(result["changes"][0]["has_completion"])

    def test_preview_transfer_unrelated_source_date_conflict_not_listed(self):
        self._transfer_batch_ready()
        # 小陈 keeps a seat in another activity on the SOURCE date (the 15th);
        # it is unrelated to the target date and must not block the swap.
        self.app.create_activity("A-003", "十五号另一场", "2026-10-15", 2)
        self.app.enroll("A-003", "M-001")
        before = self.app.path.read_bytes()
        result = self.app.preview_transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002"},
        ])
        self.assertTrue(result["can_transfer"])
        self.assertEqual([c["conflict_activity_ids"] for c in result["changes"]], [[], []])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([m["member_id"] for m in self.app.roster("A-003")["members"]], ["M-001"])

    def test_preview_transfer_invalid_never_creates_or_rewrites(self):
        self._transfer_batch_ready()
        self.app.create_activity("A-003", "十七号培训", "2026-10-17", 2)
        self.app.enroll("A-003", "M-001")
        before = self.app.path.read_bytes()
        for changes in [
            None,
            [],
            {},
            "x",
            3,
            [42],
            [{"source_activity_id": "A-001", "target_activity_id": "A-002"}],
            [{"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001", "extra": 1}],
            [{"source_activity_id": "  ", "target_activity_id": "A-002", "member_id": "M-001"}],
            [{"source_activity_id": "A-001", "target_activity_id": 7, "member_id": "M-001"}],
            [{"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": None}],
            [{"source_activity_id": "GHOST", "target_activity_id": "A-002", "member_id": "M-001"}],
            [{"source_activity_id": "A-001", "target_activity_id": "GHOST", "member_id": "M-001"}],
            [{"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "GHOST"}],
            [{"source_activity_id": "A-001", "target_activity_id": "A-001", "member_id": "M-001"}],  # same activity
            [{"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-001"}],  # not in source
            [{"source_activity_id": "A-001", "target_activity_id": "A-003", "member_id": "M-001"}],  # already in target
            [{"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
             {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": " M-001 "}],  # duplicate member
        ]:
            with self.assertRaises(ValueError):
                self.app.preview_transfer_enrollments(changes)
        self.assertEqual(before, self.app.path.read_bytes())
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        for changes in [
            [],
            [{"source_activity_id": "A", "target_activity_id": "B", "member_id": "M"}],  # unknown activity
        ]:
            with self.assertRaises(ValueError):
                fresh.preview_transfer_enrollments(changes)
        self.assertFalse(empty.exists())
        self.assertFalse((empty / "data.json").exists())

    def test_preview_transfer_legacy_file_without_completions(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.add_member("M-002", "小林")
        other.create_activity("A-001", "旧活动一", "2026-10-15", 1)
        other.create_activity("A-002", "旧活动二", "2026-10-16", 1)
        other.enroll("A-001", "M-001")
        other.enroll("A-002", "M-002")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        before = (legacy / "data.json").read_bytes()
        result = TeamPlanner(legacy).preview_transfer_enrollments([
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002"},
        ])
        self.assertTrue(result["can_transfer"])
        self.assertTrue(all(not c["has_completion"] for c in result["changes"]))
        self.assertEqual(before, (legacy / "data.json").read_bytes())

    def test_cli_preview_transfer_success_failure_and_no_writes(self):
        self._transfer_batch_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "preview-transfer", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"changes": [
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-002"},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        preview = json.loads(ok.stdout)
        self.assertTrue(preview["can_transfer"])
        self.assertEqual([(c["source_activity_id"], c["target_activity_id"], c["member_id"]) for c in preview["changes"]],
                         [("A-001", "A-002", "M-001"), ("A-002", "A-001", "M-002")])
        # An unknown activity is an input error: exit 2, error JSON on stderr,
        # nothing on stdout.
        bad = run({"changes": [{"source_activity_id": "A-001", "target_activity_id": "GHOST", "member_id": "M-001"}]})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        # Top-level array: independent previews; a later failure still stops the
        # command with empty stdout, and nothing is ever written in any case.
        partial = run([
            {"changes": [{"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"}]},
            {"changes": [{"source_activity_id": "A-001", "target_activity_id": "GHOST", "member_id": "M-001"}]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([m["member_id"] for m in TeamPlanner(self.root).roster("A-001")["members"]], ["M-001"])
        self.assertEqual([m["member_id"] for m in TeamPlanner(self.root).roster("A-002")["members"]], ["M-002"])

    def _schedule_ready(self):
        # 小陈 is enrolled in A-001/A-002 on the 15th and A-003 on the 20th;
        # 小林 shares A-001 and alone occupies A-005 on the 20th.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-001", "十五号培训一", "2026-10-15", 4)
        self.app.create_activity("A-002", "十五号培训二", "2026-10-15", 4)
        self.app.create_activity("A-003", "二十号培训", "2026-10-20", 4)
        self.app.create_activity("A-005", "二十号他人培训", "2026-10-20", 4)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-001")
        self.app.enroll("A-003", "M-001")
        self.app.enroll("A-001", "M-002")
        self.app.enroll("A-005", "M-002")
        self.app.record_completion("A-001", "M-001", "2026-10-16")

    def test_member_schedule_entries_sorted_with_status(self):
        self._schedule_ready()
        rows = self.app.member_schedule("M-001")
        self.assertEqual([r["activity_id"] for r in rows], ["A-001", "A-002", "A-003"])
        self.assertEqual(rows[0], {"activity_id": "A-001", "title": "十五号培训一", "on": "2026-10-15", "status": "completed", "completed_on": "2026-10-16", "conflict_activity_ids": ["A-002"]})
        self.assertEqual(rows[1], {"activity_id": "A-002", "title": "十五号培训二", "on": "2026-10-15", "status": "pending", "completed_on": None, "conflict_activity_ids": ["A-001"]})
        # A-005 runs on the same day but 小陈 never enrolled, so it is no conflict.
        self.assertEqual(rows[2], {"activity_id": "A-003", "title": "二十号培训", "on": "2026-10-20", "status": "pending", "completed_on": None, "conflict_activity_ids": []})
        # A member with no enrollments gets an empty schedule, and trimming applies.
        self.app.add_member("M-003", "小周")
        self.assertEqual(self.app.member_schedule("  M-003 "), [])

    def test_member_schedule_conflicts_ignore_status_filter(self):
        self._schedule_ready()
        pending = self.app.member_schedule("M-001", status="pending")
        self.assertEqual([r["activity_id"] for r in pending], ["A-002", "A-003"])
        # The completed A-001 is hidden by the filter but still conflicts with A-002.
        self.assertEqual(pending[0]["conflict_activity_ids"], ["A-001"])
        completed = self.app.member_schedule("M-001", status="completed")
        self.assertEqual([r["activity_id"] for r in completed], ["A-001"])
        # The pending A-002 is hidden by the filter but still conflicts with A-001.
        self.assertEqual(completed[0]["conflict_activity_ids"], ["A-002"])

    def test_member_schedule_date_range_inclusive(self):
        self._schedule_ready()
        self.assertEqual([r["activity_id"] for r in self.app.member_schedule("M-001", from_on="2026-10-20")], ["A-003"])
        self.assertEqual([r["activity_id"] for r in self.app.member_schedule("M-001", to_on="2026-10-15")], ["A-001", "A-002"])
        self.assertEqual([r["activity_id"] for r in self.app.member_schedule("M-001", from_on="2026-10-15", to_on="2026-10-15")], ["A-001", "A-002"])
        self.assertEqual([r["activity_id"] for r in self.app.member_schedule("M-001", from_on=None, to_on=None)], ["A-001", "A-002", "A-003"])
        self.assertEqual(self.app.member_schedule("M-001", from_on="2026-10-21"), [])

    def test_member_schedule_conflict_ids_sorted_with_three_same_day(self):
        self.app.add_member("M-001", "小陈")
        for activity_id, title in [("A-003", "三"), ("A-001", "一"), ("A-002", "二")]:
            self.app.create_activity(activity_id, title, "2026-11-01", 4)
            self.app.enroll(activity_id, "M-001")
        rows = self.app.member_schedule("M-001")
        self.assertEqual([r["activity_id"] for r in rows], ["A-001", "A-002", "A-003"])
        self.assertEqual(rows[0]["conflict_activity_ids"], ["A-002", "A-003"])
        self.assertEqual(rows[1]["conflict_activity_ids"], ["A-001", "A-003"])
        self.assertEqual(rows[2]["conflict_activity_ids"], ["A-001", "A-002"])

    def test_member_schedule_rejections_never_modify_state(self):
        self._schedule_ready()
        before = self.app.path.read_bytes()
        for kwargs in [
            {"member_id": "GHOST"},
            {"member_id": 9},
            {"member_id": None},
            {"member_id": "   "},
            {"member_id": "M-001", "from_on": 10},
            {"member_id": "M-001", "to_on": "2026-02-30"},
            {"member_id": "M-001", "from_on": " 2026-10-15"},
            {"member_id": "M-001", "from_on": "2026/10/15"},
            {"member_id": "M-001", "from_on": "2026-13-01"},
            {"member_id": "M-001", "from_on": "2026-10-20", "to_on": "2026-10-15"},
            {"member_id": "M-001", "status": "done"},
            {"member_id": "M-001", "status": None},
            {"member_id": "M-001", "status": "PENDING"},
        ]:
            with self.assertRaises(ValueError):
                self.app.member_schedule(**kwargs)
        self.assertEqual(before, self.app.path.read_bytes())
        # A successful query does not rewrite the file either.
        self.app.member_schedule("M-001", from_on="2026-10-01", to_on="2026-12-31", status="all")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_member_schedule_unknown_member_does_not_create_empty_dir(self):
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.member_schedule("GHOST")
        self.assertFalse(empty.exists())
        self.assertFalse((empty / "data.json").exists())

    def test_member_schedule_legacy_file_without_completions_is_pending(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.create_activity("A-001", "旧活动", "2026-10-15", 2)
        other.enroll("A-001", "M-001")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        row = TeamPlanner(legacy).member_schedule("M-001")[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["completed_on"])

    def test_member_schedule_reflects_transfer_reschedule_and_merge(self):
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-001", "一号", "2026-11-01", 4)
        self.app.create_activity("A-002", "二号", "2026-11-02", 4)
        self.app.create_activity("A-003", "三号", "2026-11-03", 4)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-003", "M-002")
        # A transfer moves the seat to the new date.
        self.app.transfer_enrollment("A-001", "A-002", "M-001")
        self.assertEqual([r["activity_id"] for r in self.app.member_schedule("M-001")], ["A-002"])
        # A reschedule moves the date as well.
        self.app.reschedule_activity("A-002", "2026-11-05")
        self.assertEqual(self.app.member_schedule("M-001")[0]["on"], "2026-11-05")
        # After a merge the target keeps both seats (ordered by date) and the
        # deleted member is unknown.
        self.app.merge_member("M-002", "M-001")
        self.assertEqual([r["activity_id"] for r in self.app.member_schedule("M-001")], ["A-003", "A-002"])
        with self.assertRaises(ValueError):
            self.app.member_schedule("M-002")

    def test_cli_schedule_object_array_and_failure(self):
        self._schedule_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "schedule", name], text=True, capture_output=True)
        ok = run({"member_id": "M-001", "status": "pending"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([r["activity_id"] for r in json.loads(ok.stdout)], ["A-002", "A-003"])
        self.app.add_member("M-003", "小周")
        batch = run([{"member_id": "M-001"}, {"member_id": "M-003"}])
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual([r["activity_id"] for r in values[0]], ["A-001", "A-002", "A-003"])
        self.assertEqual(values[1], [])
        bad = run({"member_id": "GHOST"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        # A failing array produces no partial output on stdout.
        partial = run([{"member_id": "M-001"}, {"member_id": "GHOST"}])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")

    def _cancel_ready(self):
        # On 2026-10-15 小陈 is in three activities, two of them shared:
        # A-001 is full with 小陈 and 小林, A-002 also has 小周, A-004 only
        # 小陈. A-003 on the 20th is 小陈's alone.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "十五号培训一", "2026-10-15", 2)
        self.app.create_activity("A-002", "十五号培训二", "2026-10-15", 3)
        self.app.create_activity("A-003", "二十号培训", "2026-10-20", 2)
        self.app.create_activity("A-004", "十五号培训三", "2026-10-15", 2)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-001", "M-002")
        self.app.enroll("A-002", "M-001")
        self.app.enroll("A-002", "M-003")
        self.app.enroll("A-003", "M-001")
        self.app.enroll("A-004", "M-001")

    def test_cancel_batch_returns_activities_in_input_order(self):
        self._cancel_ready()
        result = self.app.cancel_enrollments("M-001", [" A-002 ", "A-001"])
        self.assertEqual(result, [
            {"activity_id": "A-002", "title": "十五号培训二", "on": "2026-10-15", "capacity": 3, "participants": ["M-003"]},
            {"activity_id": "A-001", "title": "十五号培训一", "on": "2026-10-15", "capacity": 2, "participants": ["M-002"]},
        ])
        # Only 小陈 is removed; everyone else keeps their relative position and
        # titles, dates and capacities are unchanged.
        self.assertEqual([m["member_id"] for m in self.app.roster("A-001")["members"]], ["M-002"])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-002")["members"]], ["M-003"])
        listing = {a["activity_id"]: a for a in self.app.activities()}
        self.assertEqual(listing["A-001"]["participants"], ["M-002"])
        self.assertEqual(listing["A-002"]["participants"], ["M-003"])
        self.assertEqual(listing["A-001"]["capacity"], 2)
        # The canceled activities leave the schedule; remaining same-day
        # enrollments keep fewer (here: no) conflicts.
        rows = self.app.member_schedule("M-001")
        self.assertEqual([(r["activity_id"], r["conflict_activity_ids"]) for r in rows], [("A-004", []), ("A-003", [])])
        # The change survives reopening the same root.
        reopened = TeamPlanner(self.root)
        self.assertEqual(reopened.roster("A-001")["members"], [{"member_id": "M-002", "name": "小林"}])
        self.assertEqual([r["activity_id"] for r in reopened.member_schedule("M-001")], ["A-004", "A-003"])

    def test_cancel_frees_full_seat_for_enroll_and_transfer(self):
        self._cancel_ready()
        self.app.cancel_enrollments("M-001", ["A-001", "A-002", "A-004"])
        # A-001 was full: the freed seat can immediately be taken.
        self.app.enroll("A-001", "M-003")
        self.assertEqual([m["member_id"] for m in self.app.roster("A-001")["members"]], ["M-002", "M-003"])
        # A transfer can claim a freed seat as well.
        self.app.create_activity("A-005", "二十一号培训", "2026-10-21", 2)
        self.app.enroll("A-005", "M-001")
        self.app.transfer_enrollment("A-005", "A-002", "M-001")
        self.assertEqual([m["member_id"] for m in self.app.roster("A-002")["members"]], ["M-003", "M-001"])

    def test_cancel_completion_rules(self):
        self._cancel_ready()
        # Another member's completion record does not block 小陈's cancellation
        # and the record itself is preserved.
        self.app.record_completion("A-001", "M-002", "2026-10-15")
        self.app.cancel_enrollments("M-001", ["A-001"])
        self.assertEqual([r["member_id"] for r in self.app.completions("M-002")], ["M-002"])
        # 小陈's own completion in a non-selected activity does not matter.
        self.app.record_completion("A-003", "M-001", "2026-10-20")
        self.app.cancel_enrollments("M-001", ["A-002"])
        self.assertEqual([r["activity_id"] for r in self.app.completions("M-001")], ["A-003"])
        # A past activity without a completion record can still be canceled;
        # the operation does not depend on the current date.
        self.app.create_activity("A-OLD", "九月培训", "2026-09-01", 2)
        self.app.enroll("A-OLD", "M-001")
        self.assertEqual(self.app.cancel_enrollments("M-001", ["A-OLD"])[0]["participants"], [])

    def test_cancel_own_completion_is_atomic_rejection(self):
        self._cancel_ready()
        self.app.record_completion("A-002", "M-001", "2026-10-15")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.cancel_enrollments("M-001", ["A-001", "A-002"])
        # Neither roster is touched and the completion record stays.
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([m["member_id"] for m in self.app.roster("A-001")["members"]], ["M-001", "M-002"])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-002")["members"]], ["M-001", "M-003"])
        self.assertEqual([r["activity_id"] for r in self.app.completions("M-001")], ["A-002"])

    def test_cancel_rejections_leave_state_unchanged(self):
        self._cancel_ready()
        before = self.app.path.read_bytes()
        for kwargs in [
            {"member_id": "GHOST", "activity_ids": ["A-001"]},
            {"member_id": " GHOST ", "activity_ids": ["A-001"]},
            {"member_id": "M-001", "activity_ids": ["GHOST"]},
            {"member_id": "M-001", "activity_ids": ["A-001", "GHOST"]},
            {"member_id": "M-002", "activity_ids": ["A-002"]},  # not enrolled
            {"member_id": 9, "activity_ids": ["A-001"]},
            {"member_id": None, "activity_ids": ["A-001"]},
            {"member_id": "   ", "activity_ids": ["A-001"]},
            {"member_id": "M-001", "activity_ids": "A-001"},
            {"member_id": "M-001", "activity_ids": None},
            {"member_id": "M-001", "activity_ids": []},
            {"member_id": "M-001", "activity_ids": ["A-001", 7]},
            {"member_id": "M-001", "activity_ids": ["   "]},
            {"member_id": "M-001", "activity_ids": ["A-001", " A-001 "]},  # duplicates after trim
        ]:
            with self.assertRaises(ValueError):
                self.app.cancel_enrollments(**kwargs)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_invalid_cancel_does_not_create_file(self):
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        for kwargs in [
            {"member_id": "M-001", "activity_ids": []},
            {"member_id": "M-001", "activity_ids": ["A-001"]},
            {"member_id": "GHOST", "activity_ids": ["A-001"]},
        ]:
            with self.assertRaises(ValueError):
                fresh.cancel_enrollments(**kwargs)
        self.assertFalse(empty.exists())
        self.assertFalse((empty / "data.json").exists())

    def test_cancel_legacy_file_without_completions(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.create_activity("A-001", "旧活动一", "2026-10-15", 2)
        other.create_activity("A-002", "旧活动二", "2026-10-16", 2)
        other.enroll("A-001", "M-001")
        other.enroll("A-002", "M-001")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        result = TeamPlanner(legacy).cancel_enrollments("M-001", ["A-002", "A-001"])
        self.assertEqual([a["activity_id"] for a in result], ["A-002", "A-001"])
        self.assertTrue(all(a["participants"] == [] for a in result))

    def test_cli_cancel_object_array_and_partial_failure(self):
        self._cancel_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "cancel", name], text=True, capture_output=True)
        ok = run({"member_id": "M-001", "activity_ids": [" A-002 ", "A-001"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([a["activity_id"] for a in json.loads(ok.stdout)], ["A-002", "A-001"])
        repeat = run({"member_id": "M-001", "activity_ids": ["A-001"]})
        self.assertEqual(repeat.returncode, 2)
        self.assertIn("error", json.loads(repeat.stderr))
        # An all-success array outputs an array of per-call result arrays.
        self.app.enroll("A-002", "M-001")
        batch = run([
            {"member_id": "M-001", "activity_ids": ["A-002"]},
            {"member_id": "M-002", "activity_ids": ["A-001"]},
        ])
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual([call[0]["activity_id"] for call in values], ["A-002", "A-001"])
        self.assertEqual([m["member_id"] for m in TeamPlanner(self.root).roster("A-001")["members"]], [])
        # A failing array stops after the first failure: its success is kept,
        # stdout stays empty and the error goes to stderr.
        self.app.enroll("A-001", "M-001")
        partial = run([
            {"member_id": "M-001", "activity_ids": ["A-001"]},
            {"member_id": "M-001", "activity_ids": ["GHOST"]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual([m["member_id"] for m in TeamPlanner(self.root).roster("A-001")["members"]], [])

    def _reschedule_batch_ready(self):
        # 小陈 is enrolled in A-001 (15th) and A-002 (16th); 小林 alone holds
        # A-003 on the 20th.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 4)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 4)
        self.app.create_activity("A-003", "二十号培训", "2026-10-20", 4)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-001")
        self.app.enroll("A-003", "M-002")

    def test_reschedule_batch_swap_dates_returns_input_order(self):
        self._reschedule_batch_ready()
        result = self.app.reschedule_activities([
            {"activity_id": " A-002 ", "on": "2026-10-15"},
            {"activity_id": "A-001", "on": "2026-10-16"},
        ])
        self.assertEqual(result, [
            {"activity_id": "A-002", "title": "十六号培训", "on": "2026-10-15", "capacity": 4, "participants": ["M-001"]},
            {"activity_id": "A-001", "title": "十五号培训", "on": "2026-10-16", "capacity": 4, "participants": ["M-001"]},
        ])
        # The swap survives reopening and shows up in list, rosters and schedules.
        reopened = TeamPlanner(self.root)
        by_id = {a["activity_id"]: a for a in reopened.activities()}
        self.assertEqual(by_id["A-001"]["on"], "2026-10-16")
        self.assertEqual(by_id["A-002"]["on"], "2026-10-15")
        self.assertEqual(reopened.roster("A-002")["on"], "2026-10-15")
        self.assertEqual([(r["activity_id"], r["on"], r["conflict_activity_ids"]) for r in reopened.member_schedule("M-001")],
                         [("A-002", "2026-10-15", []), ("A-001", "2026-10-16", [])])

    def test_reschedule_batch_other_members_and_group_external_dates(self):
        self._reschedule_batch_ready()
        # Moving onto the 20th, where only 小林 is enrolled in another activity,
        # is allowed; the swap exchanges the two shared activities at once.
        result = self.app.reschedule_activities([
            {"activity_id": "A-001", "on": "2026-10-20"},
            {"activity_id": "A-002", "on": "2026-10-15"},
        ])
        self.assertEqual([a["on"] for a in result], ["2026-10-20", "2026-10-15"])
        # A-003 keeps its own date and participants.
        self.assertEqual(self.app.roster("A-003")["members"], [{"member_id": "M-002", "name": "小林"}])

    def test_reschedule_batch_final_same_day_conflict_is_atomic(self):
        self._reschedule_batch_ready()
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reschedule_activities([
                {"activity_id": "A-001", "on": "2026-10-16"},
                {"activity_id": "A-002", "on": "2026-10-16"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.roster("A-001")["on"], "2026-10-15")
        self.assertEqual(self.app.roster("A-002")["on"], "2026-10-16")

    def test_reschedule_batch_completed_enrollment_outside_group_counts(self):
        self._reschedule_batch_ready()
        # A completed enrollment of the same member on the final date blocks too.
        self.app.create_activity("A-004", "十六号已完成", "2026-10-16", 4)
        self.app.enroll("A-004", "M-001")
        self.app.record_completion("A-004", "M-001", "2026-10-16")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reschedule_activities([
                {"activity_id": "A-001", "on": "2026-10-16"},
                {"activity_id": "A-002", "on": "2026-10-15"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_reschedule_batch_completion_rules_per_activity(self):
        self._reschedule_batch_ready()
        # 小林's completion for A-003 does not block 小陈's move onto the 20th.
        self.app.record_completion("A-003", "M-002", "2026-10-20")
        self.app.reschedule_activities([{"activity_id": "A-001", "on": "2026-10-20"}])
        # A moving activity carrying any completion record is rejected.
        self.app.create_activity("A-005", "二十一号", "2026-10-21", 4)
        self.app.enroll("A-005", "M-002")
        self.app.record_completion("A-005", "M-002", "2026-10-21")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reschedule_activities([{"activity_id": "A-005", "on": "2026-10-22"}])
        # Even with an unchanged sibling in the same call the batch fails.
        with self.assertRaises(ValueError):
            self.app.reschedule_activities([
                {"activity_id": "A-002", "on": "2026-10-15"},
                {"activity_id": "A-005", "on": "2026-10-22"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_reschedule_batch_all_unchanged_is_noop(self):
        self._reschedule_batch_ready()
        # A-001 and A-002 already share a participant on different dates; add a
        # completion record to a selected unchanged activity.
        self.app.record_completion("A-002", "M-001", "2026-10-16")
        before = self.app.path.read_bytes()
        result = self.app.reschedule_activities([
            {"activity_id": "A-002", "on": "2026-10-16"},
            {"activity_id": "A-001", "on": "2026-10-15"},
        ])
        self.assertEqual([a["activity_id"] for a in result], ["A-002", "A-001"])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_reschedule_batch_rejections_leave_bytes_unchanged(self):
        self._reschedule_batch_ready()
        before = self.app.path.read_bytes()
        for changes in [
            None,
            [],
            {},
            "x",
            3,
            [42],
            [{"activity_id": "A-001"}],
            [{"on": "2026-10-15"}],
            [{"activity_id": "A-001", "on": "2026-10-15", "extra": 1}],
            [{"activity_id": "   ", "on": "2026-10-15"}],
            [{"activity_id": 9, "on": "2026-10-15"}],
            [{"activity_id": "A-001", "on": "2026-02-30"}],
            [{"activity_id": "A-001", "on": " 2026-10-15"}],
            [{"activity_id": "A-001", "on": "2026-10-15 "}, ],
            [{"activity_id": "A-001", "on": 10}],
            [{"activity_id": "A-001", "on": "2026/10/15"}],
            [{"activity_id": "GHOST", "on": "2026-10-15"}],
            [{"activity_id": "A-001", "on": "2026-10-16"},
             {"activity_id": " A-001 ", "on": "2026-10-17"}],
        ]:
            with self.assertRaises(ValueError):
                self.app.reschedule_activities(changes)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_reschedule_batch_invalid_does_not_create_file(self):
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        for changes in [[], [{"activity_id": "A", "on": "2026-10-15"}]]:
            with self.assertRaises(ValueError):
                fresh.reschedule_activities(changes)
        self.assertFalse(empty.exists())
        self.assertFalse((empty / "data.json").exists())

    def test_reschedule_batch_legacy_file_without_completions(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.create_activity("A-001", "旧活动一", "2026-10-15", 2)
        other.create_activity("A-002", "旧活动二", "2026-10-16", 2)
        other.enroll("A-001", "M-001")
        other.enroll("A-002", "M-001")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        result = TeamPlanner(legacy).reschedule_activities([
            {"activity_id": "A-001", "on": "2026-10-16"},
            {"activity_id": "A-002", "on": "2026-10-15"},
        ])
        self.assertEqual([a["on"] for a in result], ["2026-10-16", "2026-10-15"])

    def test_cli_reschedule_batch_success_failure_and_partial_array(self):
        self._reschedule_batch_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "reschedule-batch", name], text=True, capture_output=True)
        ok = run({"changes": [
            {"activity_id": "A-001", "on": "2026-10-16"},
            {"activity_id": "A-002", "on": "2026-10-15"},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([a["on"] for a in json.loads(ok.stdout)], ["2026-10-16", "2026-10-15"])
        bad = run({"changes": [
            {"activity_id": "A-001", "on": "2026-10-15"},
            {"activity_id": "A-002", "on": "2026-10-15"},
        ]})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        # Top-level array: independent batches, the first success stays after a
        # later failure, and nothing is printed on stdout.
        partial = run([
            {"changes": [{"activity_id": "A-001", "on": "2026-10-17"}]},
            {"changes": [{"activity_id": "GHOST", "on": "2026-10-18"}]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(TeamPlanner(self.root).roster("A-001")["on"], "2026-10-17")
        self.assertEqual(TeamPlanner(self.root).roster("A-002")["on"], "2026-10-15")

    def test_preview_reschedule_clear_group_returns_input_order(self):
        self._reschedule_batch_ready()
        before = self.app.path.read_bytes()
        result = self.app.preview_reschedule_activities([
            {"activity_id": " A-002 ", "on": "2026-10-15"},
            {"activity_id": "A-001", "on": "2026-10-16"},
        ])
        self.assertEqual(result, {
            "can_reschedule": True,
            "activities": [
                {"activity_id": "A-002", "previous_on": "2026-10-16", "on": "2026-10-15", "completion_member_ids": [], "conflicts": []},
                {"activity_id": "A-001", "previous_on": "2026-10-15", "on": "2026-10-16", "completion_member_ids": [], "conflicts": []},
            ],
        })
        # A preview never rewrites the file, and the dates stay as they were.
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.roster("A-001")["on"], "2026-10-15")
        self.assertEqual(self.app.roster("A-002")["on"], "2026-10-16")

    def test_preview_reschedule_reports_completions_and_conflicts(self):
        self._reschedule_batch_ready()
        # 小陈 also holds a completed enrollment on the 20th and a pending one
        # on the 21st; 小周 only enrolls in the 21st activity.
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-004", "二十号已完成", "2026-10-20", 4)
        self.app.create_activity("A-005", "二十一号培训", "2026-10-21", 4)
        self.app.enroll("A-004", "M-001")
        self.app.record_completion("A-004", "M-001", "2026-10-20")
        self.app.enroll("A-005", "M-001")
        self.app.enroll("A-005", "M-003")
        # Moving A-001 onto the 21st conflicts only with 小陈's own A-005;
        # 小周's enrollment in A-005 does not count.
        result = self.app.preview_reschedule_activities([
            {"activity_id": "A-001", "on": "2026-10-21"},
            {"activity_id": "A-004", "on": "2026-10-22"},
        ])
        self.assertFalse(result["can_reschedule"])
        self.assertEqual(result["activities"], [
            {"activity_id": "A-001", "previous_on": "2026-10-15", "on": "2026-10-21", "completion_member_ids": [],
             "conflicts": [{"member_id": "M-001", "activity_ids": ["A-005"]}]},
            {"activity_id": "A-004", "previous_on": "2026-10-20", "on": "2026-10-22", "completion_member_ids": ["M-001"], "conflicts": []},
        ])
        # The completed enrollment on the 20th still counts when moving onto it.
        result = self.app.preview_reschedule_activities([{"activity_id": "A-001", "on": "2026-10-20"}])
        self.assertEqual(result["activities"][0]["conflicts"], [{"member_id": "M-001", "activity_ids": ["A-004"]}])
        self.assertFalse(result["can_reschedule"])

    def test_preview_reschedule_conflict_members_and_activities_sorted(self):
        self._reschedule_batch_ready()
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-004", "二十一号甲", "2026-10-21", 4)
        self.app.create_activity("A-005", "二十一号乙", "2026-10-21", 4)
        self.app.enroll("A-004", "M-001")
        self.app.enroll("A-005", "M-001")
        self.app.enroll("A-005", "M-003")
        self.app.enroll("A-004", "M-003")
        self.app.enroll("A-001", "M-003")
        result = self.app.preview_reschedule_activities([{"activity_id": "A-001", "on": "2026-10-21"}])
        self.assertEqual(result["activities"][0]["conflicts"], [
            {"member_id": "M-001", "activity_ids": ["A-004", "A-005"]},
            {"member_id": "M-003", "activity_ids": ["A-004", "A-005"]},
        ])

    def test_preview_reschedule_unchanged_entries_report_no_obstacles(self):
        self._reschedule_batch_ready()
        # A-002 carries a completion record and A-001 conflicts with it on the
        # 16th after the move; the unchanged A-002 entry stays clear.
        self.app.record_completion("A-002", "M-001", "2026-10-16")
        before = self.app.path.read_bytes()
        result = self.app.preview_reschedule_activities([
            {"activity_id": "A-002", "on": "2026-10-16"},
            {"activity_id": "A-001", "on": "2026-10-16"},
        ])
        self.assertEqual(result["activities"][0], {
            "activity_id": "A-002", "previous_on": "2026-10-16", "on": "2026-10-16", "completion_member_ids": [], "conflicts": [],
        })
        self.assertEqual(result["activities"][1]["conflicts"], [{"member_id": "M-001", "activity_ids": ["A-002"]}])
        self.assertFalse(result["can_reschedule"])
        self.assertEqual(before, self.app.path.read_bytes())
        # All dates unchanged: the whole group is clear and nothing is written.
        result = self.app.preview_reschedule_activities([
            {"activity_id": "A-002", "on": "2026-10-16"},
            {"activity_id": "A-001", "on": "2026-10-15"},
        ])
        self.assertTrue(result["can_reschedule"])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_preview_reschedule_swap_and_group_dates(self):
        self._reschedule_batch_ready()
        # The swap is feasible: each activity's conflict check uses the final
        # dates of the whole group, so the two shared activities do not collide.
        result = self.app.preview_reschedule_activities([
            {"activity_id": "A-001", "on": "2026-10-16"},
            {"activity_id": "A-002", "on": "2026-10-15"},
        ])
        self.assertTrue(result["can_reschedule"])
        # Moving both onto the same final date conflicts through the group;
        # A-002 keeps its own date, so only the moving entry is checked.
        result = self.app.preview_reschedule_activities([
            {"activity_id": "A-001", "on": "2026-10-16"},
            {"activity_id": "A-002", "on": "2026-10-16"},
        ])
        self.assertFalse(result["can_reschedule"])
        self.assertEqual(result["activities"][0]["conflicts"], [{"member_id": "M-001", "activity_ids": ["A-002"]}])
        self.assertEqual(result["activities"][1]["conflicts"], [])
        # When both actually move onto the 17th, each side reports the other.
        result = self.app.preview_reschedule_activities([
            {"activity_id": "A-001", "on": "2026-10-17"},
            {"activity_id": "A-002", "on": "2026-10-17"},
        ])
        self.assertFalse(result["can_reschedule"])
        self.assertEqual(result["activities"][0]["conflicts"], [{"member_id": "M-001", "activity_ids": ["A-002"]}])
        self.assertEqual(result["activities"][1]["conflicts"], [{"member_id": "M-001", "activity_ids": ["A-001"]}])

    def test_preview_reschedule_invalid_never_creates_or_rewrites(self):
        self._reschedule_batch_ready()
        before = self.app.path.read_bytes()
        for changes in [
            None,
            [],
            {},
            "x",
            3,
            [42],
            [{"activity_id": "A-001"}],
            [{"on": "2026-10-15"}],
            [{"activity_id": "A-001", "on": "2026-10-15", "extra": 1}],
            [{"activity_id": "   ", "on": "2026-10-15"}],
            [{"activity_id": 9, "on": "2026-10-15"}],
            [{"activity_id": "A-001", "on": "2026-02-30"}],
            [{"activity_id": "A-001", "on": " 2026-10-15"}],
            [{"activity_id": "A-001", "on": "2026-10-15 "}],
            [{"activity_id": "A-001", "on": 10}],
            [{"activity_id": "A-001", "on": "2026/10/15"}],
            [{"activity_id": "GHOST", "on": "2026-10-15"}],
            [{"activity_id": "A-001", "on": "2026-10-16"},
             {"activity_id": " A-001 ", "on": "2026-10-17"}],
        ]:
            with self.assertRaises(ValueError):
                self.app.preview_reschedule_activities(changes)
        self.assertEqual(before, self.app.path.read_bytes())
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        for changes in [[], [{"activity_id": "A", "on": "2026-10-15"}]]:
            with self.assertRaises(ValueError):
                fresh.preview_reschedule_activities(changes)
        self.assertFalse(empty.exists())
        self.assertFalse((empty / "data.json").exists())

    def test_preview_reschedule_legacy_file_without_completions(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.create_activity("A-001", "旧活动一", "2026-10-15", 2)
        other.create_activity("A-002", "旧活动二", "2026-10-16", 2)
        other.enroll("A-001", "M-001")
        other.enroll("A-002", "M-001")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        before = (legacy / "data.json").read_bytes()
        result = TeamPlanner(legacy).preview_reschedule_activities([
            {"activity_id": "A-001", "on": "2026-10-16"},
            {"activity_id": "A-002", "on": "2026-10-15"},
        ])
        self.assertTrue(result["can_reschedule"])
        self.assertEqual(before, (legacy / "data.json").read_bytes())

    def test_cli_preview_reschedule_success_failure_and_no_writes(self):
        self._reschedule_batch_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "preview-reschedule", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"changes": [
            {"activity_id": "A-001", "on": "2026-10-16"},
            {"activity_id": "A-002", "on": "2026-10-15"},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        preview = json.loads(ok.stdout)
        self.assertTrue(preview["can_reschedule"])
        self.assertEqual([a["activity_id"] for a in preview["activities"]], ["A-001", "A-002"])
        blocked = run({"changes": [{"activity_id": "A-001", "on": "2026-10-16"}]})
        self.assertEqual(blocked.returncode, 0, blocked.stderr)
        self.assertFalse(json.loads(blocked.stdout)["can_reschedule"])
        bad = run({"changes": [{"activity_id": "GHOST", "on": "2026-10-15"}]})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        # Top-level array: independent previews, a later failure still stops
        # the command, and nothing is ever written in any case.
        partial = run([
            {"changes": [{"activity_id": "A-001", "on": "2026-10-17"}]},
            {"changes": [{"activity_id": "GHOST", "on": "2026-10-18"}]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(TeamPlanner(self.root).roster("A-001")["on"], "2026-10-15")

    def test_export_schedule_rows_sorted_with_conflicts(self):
        self._schedule_ready()
        result = self.app.export_schedule()
        self.assertEqual(result["row_count"], 5)
        lines = result["csv"].split("\r\n")
        self.assertEqual(lines[0], "member_id,name,activity_id,title,on,status,completed_on,conflict_activity_ids")
        self.assertEqual(lines[-1], "")
        rows = lines[1:-1]
        self.assertEqual(len(rows), 5)
        # Sorted by on, activity_id, member_id; the completed enrollment stays.
        self.assertEqual(rows[0], 'M-001,小陈,A-001,十五号培训一,2026-10-15,completed,2026-10-16,"[""A-002""]"')
        self.assertEqual(rows[1], 'M-002,小林,A-001,十五号培训一,2026-10-15,pending,,[]')
        self.assertEqual(rows[2], 'M-001,小陈,A-002,十五号培训二,2026-10-15,pending,,"[""A-001""]"')
        self.assertEqual(rows[3], 'M-001,小陈,A-003,二十号培训,2026-10-20,pending,,[]')
        # A-005 has only 小林, so 小陈's A-003 has no conflict and vice versa.
        self.assertEqual(rows[4], 'M-002,小林,A-005,二十号他人培训,2026-10-20,pending,,[]')
        # CRLF everywhere, no BOM, no lone LF.
        self.assertNotIn("\n", result["csv"].replace("\r\n", ""))
        self.assertFalse(result["csv"].startswith("﻿"))

    def test_export_schedule_filters_and_conflicts_ignore_status(self):
        self._schedule_ready()
        pending = self.app.export_schedule(status="pending")
        self.assertEqual(pending["row_count"], 4)
        rows = pending["csv"].split("\r\n")[1:-1]
        self.assertNotIn("completed", ",".join(r.split(",")[5] for r in rows))
        # The completed A-001 is hidden but still conflicts with 小陈's A-002.
        self.assertIn('"[""A-001""]"', rows[1])
        completed = self.app.export_schedule(status="completed")
        self.assertEqual(completed["row_count"], 1)
        self.assertIn('"[""A-002""]"', completed["csv"])
        # Inclusive date range on the activity date.
        self.assertEqual(self.app.export_schedule(from_on="2026-10-15", to_on="2026-10-15")["row_count"], 3)
        self.assertEqual(self.app.export_schedule(from_on="2026-10-16")["row_count"], 2)
        self.assertEqual(self.app.export_schedule(to_on="2026-10-14")["row_count"], 0)
        empty = self.app.export_schedule(from_on="2026-11-01")
        self.assertEqual(empty["row_count"], 0)
        self.assertEqual(empty["csv"], "member_id,name,activity_id,title,on,status,completed_on,conflict_activity_ids\r\n")

    def test_export_schedule_quoting_and_cancellation(self):
        self.app.add_member("M-001", '小"陈')
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-001", "含,逗号", "2026-10-15", 4)
        self.app.create_activity("A-002", "含\n换行", "2026-10-15", 4)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-001")
        self.app.enroll("A-001", "M-002")
        result = self.app.export_schedule()
        rows = result["csv"].split("\r\n")[1:-1]
        self.assertEqual(rows[0], 'M-001,"小""陈",A-001,"含,逗号",2026-10-15,pending,,"[""A-002""]"')
        self.assertEqual(rows[1], 'M-002,小林,A-001,"含,逗号",2026-10-15,pending,,[]')
        self.assertEqual(rows[2], 'M-001,"小""陈",A-002,"含\n换行",2026-10-15,pending,,"[""A-001""]"')
        # A canceled enrollment leaves no row; an activity without
        # participants and a member without enrollments produce none either.
        self.app.cancel_enrollments("M-002", ["A-001"])
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-003", "无人报名", "2026-10-16", 2)
        result = self.app.export_schedule()
        self.assertEqual(result["row_count"], 2)
        self.assertNotIn("M-002", result["csv"])
        self.assertNotIn("M-003", result["csv"])
        self.assertNotIn("A-003", result["csv"])

    def test_export_schedule_rejections_never_modify_state(self):
        self._schedule_ready()
        before = self.app.path.read_bytes()
        for kwargs in [
            {"from_on": 10},
            {"to_on": "2026-02-30"},
            {"from_on": " 2026-10-15"},
            {"from_on": "2026/10/15"},
            {"from_on": "2026-13-01"},
            {"from_on": "2026-10-20", "to_on": "2026-10-15"},
            {"status": "done"},
            {"status": None},
            {"status": "PENDING"},
        ]:
            with self.assertRaises(ValueError):
                self.app.export_schedule(**kwargs)
        self.assertEqual(before, self.app.path.read_bytes())
        # A successful export does not rewrite the file either.
        self.app.export_schedule(from_on="2026-10-01", to_on="2026-12-31", status="all")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_export_schedule_does_not_create_file_and_legacy_pending(self):
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        result = fresh.export_schedule()
        self.assertEqual(result["row_count"], 0)
        self.assertEqual(result["csv"], "member_id,name,activity_id,title,on,status,completed_on,conflict_activity_ids\r\n")
        self.assertFalse(empty.exists())
        with self.assertRaises(ValueError):
            fresh.export_schedule(status="done")
        self.assertFalse(empty.exists())
        # A historical data.json without a completions field exports as pending.
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.create_activity("A-001", "旧活动", "2026-10-15", 2)
        other.enroll("A-001", "M-001")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        row = TeamPlanner(legacy).export_schedule()["csv"].split("\r\n")[1]
        self.assertEqual(row, "M-001,小陈,A-001,旧活动,2026-10-15,pending,,[]")

    def test_export_schedule_broken_history_is_rejected(self):
        self._schedule_ready()
        raw = json.loads(self.app.path.read_text(encoding="utf-8"))
        raw["activities"]["A-001"]["capacity"] = 0
        self.app.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.export_schedule(to_on="2026-10-20")

    def test_cli_export_schedule_object_array_and_failure(self):
        self._schedule_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "export-schedule", name], text=True, capture_output=True)
        ok = run({"status": "pending"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(value["row_count"], 4)
        self.assertTrue(value["csv"].startswith("member_id,name,activity_id,title,on,status,completed_on,conflict_activity_ids\r\n"))
        batch = run([{"from_on": "2026-10-15", "to_on": "2026-10-15"}, {}])
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual([v["row_count"] for v in values], [3, 5])
        bad = run({"status": "done"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        partial = run([{}, {"from_on": "2026-10-20", "to_on": "2026-10-15"}])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")

    def _enroll_batch_ready(self):
        # Fictional fixed dates: on 2026-10-15 A-001 has one free seat (小周
        # holds the other) and 小陈's finished A-004 still occupies its seat;
        # A-002 and A-005 run on the 16th, A-003 on the 17th, all with room.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.add_member("M-004", "小吴")
        self.app.create_activity("A-001", "十五号培训", "2026-10-15", 2)
        self.app.create_activity("A-002", "十六号培训", "2026-10-16", 2)
        self.app.create_activity("A-003", "十七号培训", "2026-10-17", 2)
        self.app.create_activity("A-004", "十五号另一场", "2026-10-15", 2)
        self.app.create_activity("A-005", "十六号另一场", "2026-10-16", 2)
        self.app.enroll("A-001", "M-003")
        self.app.enroll("A-004", "M-001")
        self.app.record_completion("A-004", "M-001", "2026-10-15")

    def test_enroll_batch_cross_member_and_activity(self):
        self._enroll_batch_ready()
        # Identifiers are trimmed; one member may join several activities on
        # different dates and one activity may receive several members. An
        # activity named twice shows the final roster in both entries.
        result = self.app.enroll_batch([
            {"activity_id": " A-002 ", "member_id": "M-001"},
            {"activity_id": "A-002", "member_id": " M-002 "},
            {"activity_id": "A-003", "member_id": "M-001"},
        ])
        self.assertEqual(result, [
            {"activity_id": "A-002", "title": "十六号培训", "on": "2026-10-16", "capacity": 2, "participants": ["M-001", "M-002"]},
            {"activity_id": "A-002", "title": "十六号培训", "on": "2026-10-16", "capacity": 2, "participants": ["M-001", "M-002"]},
            {"activity_id": "A-003", "title": "十七号培训", "on": "2026-10-17", "capacity": 2, "participants": ["M-001"]},
        ])
        # The new enrollments survive reopening and show up in rosters,
        # schedules and candidate queries; the completion record is untouched.
        reopened = TeamPlanner(self.root)
        self.assertEqual([m["member_id"] for m in reopened.roster("A-002")["members"]], ["M-001", "M-002"])
        self.assertEqual([m["member_id"] for m in reopened.roster("A-003")["members"]], ["M-001"])
        self.assertEqual([r["activity_id"] for r in reopened.member_schedule("M-001")], ["A-004", "A-002", "A-003"])
        self.assertEqual([r["activity_id"] for r in reopened.completions("M-001")], ["A-004"])
        self.assertEqual([o["activity_id"] for o in reopened.enrollment_options("M-001")], ["A-001", "A-005"])
        self.assertEqual(reopened.enrollment_options("M-001", available_only=True), [])

    def test_enroll_batch_last_seat_rejects_whole_group(self):
        self._enroll_batch_ready()
        # A-001 has exactly one free seat and neither 小林 nor 小吴 has a
        # same-day engagement: both cannot take it, so nothing is enrolled.
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.enroll_batch([
                {"activity_id": "A-001", "member_id": "M-002"},
                {"activity_id": "A-001", "member_id": "M-004"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([m["member_id"] for m in self.app.roster("A-001")["members"]], ["M-003"])
        # The completed enrollment in A-004 keeps its seat: two additions to
        # a capacity-2 activity overflow as well.
        with self.assertRaises(ValueError):
            self.app.enroll_batch([
                {"activity_id": "A-004", "member_id": "M-002"},
                {"activity_id": "A-004", "member_id": "M-004"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        # Picking different dates with free seats and no own same-day
        # conflict succeeds for both.
        result = self.app.enroll_batch([
            {"activity_id": "A-002", "member_id": "M-002"},
            {"activity_id": "A-003", "member_id": "M-004"},
        ])
        self.assertEqual([a["participants"] for a in result], [["M-002"], ["M-004"]])

    def test_enroll_batch_same_day_conflicts(self):
        self._enroll_batch_ready()
        before = self.app.path.read_bytes()
        # 小陈's completed A-004 still occupies the 15th: joining A-001 on the
        # same date conflicts even though one seat is free.
        with self.assertRaises(ValueError):
            self.app.enroll_batch([{"activity_id": "A-001", "member_id": "M-001"}])
        # Two entries of the group on the same date conflict with each other.
        with self.assertRaises(ValueError):
            self.app.enroll_batch([
                {"activity_id": "A-002", "member_id": "M-002"},
                {"activity_id": "A-005", "member_id": "M-002"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.roster("A-002")["members"], [])
        # Other members' enrollments are no conflict: 小林 takes the last
        # seat of A-001 next to 小周.
        result = self.app.enroll_batch([{"activity_id": "A-001", "member_id": "M-002"}])
        self.assertEqual(result[0]["participants"], ["M-003", "M-002"])

    def test_enroll_batch_unrelated_historical_conflict_does_not_block(self):
        self._enroll_batch_ready()
        # The single enroll still allows same-day enrollments: 小陈 picks up
        # a second seat on the 15th. That historical conflict sits on a date
        # the group does not touch, so joining A-002 on the 16th is fine.
        self.app.enroll("A-001", "M-001")
        result = self.app.enroll_batch([{"activity_id": "A-002", "member_id": "M-001"}])
        self.assertEqual(result[0]["participants"], ["M-001"])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-001")["members"]], ["M-003", "M-001"])

    def test_enroll_batch_rejections_leave_bytes_unchanged(self):
        self._enroll_batch_ready()
        before = self.app.path.read_bytes()
        for records in [
            None,
            [],
            "x",
            3,
            {},
            [42],
            [None],
            [[]],
            [{"activity_id": "A-002"}],
            [{"member_id": "M-001"}],
            [{"activity_id": "A-002", "member_id": "M-001", "extra": 1}],
            [{"activity_id": "   ", "member_id": "M-001"}],
            [{"activity_id": 9, "member_id": "M-001"}],
            [{"activity_id": "A-002", "member_id": None}],
            [{"activity_id": "A-002", "member_id": "  "}],
            [{"activity_id": "GHOST", "member_id": "M-001"}],
            [{"activity_id": "A-002", "member_id": "GHOST"}],
            [{"activity_id": "A-001", "member_id": "M-003"}],  # already enrolled
            [{"activity_id": "A-004", "member_id": "M-001"}],  # completed stays enrolled
            # Duplicate activity/member pair after trimming.
            [{"activity_id": " A-002 ", "member_id": "M-002"},
             {"activity_id": "A-002", "member_id": " M-002 "}],
            # The last entry fails; a valid earlier entry cannot save it.
            [{"activity_id": "A-002", "member_id": "M-002"},
             {"activity_id": "A-001", "member_id": "M-003"}],
        ]:
            with self.assertRaises(ValueError):
                self.app.enroll_batch(records)
            self.assertEqual(before, self.app.path.read_bytes())
        # Nothing from any rejected group survived.
        self.assertEqual([m["member_id"] for m in self.app.roster("A-002")["members"]], [])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-001")["members"]], ["M-003"])

    def test_enroll_batch_invalid_does_not_create_file(self):
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        for records in [
            [],
            "x",
            [{"activity_id": "A", "member_id": 9}],
            [{"activity_id": "A"}],
        ]:
            with self.assertRaises(ValueError):
                fresh.enroll_batch(records)
        # A well-formed group against a missing history fails the business
        # rules and must not create the directory or data.json either.
        with self.assertRaises(ValueError):
            fresh.enroll_batch([{"activity_id": "A", "member_id": "M"}])
        self.assertFalse(empty.exists())
        self.assertFalse((empty / "data.json").exists())

    def test_enroll_batch_legacy_file_and_extra_fields_preserved(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.add_member("M-002", "小林")
        other.create_activity("A-001", "旧活动", "2026-10-15", 3)
        other.enroll("A-001", "M-001")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        raw["note"] = "imported from the old planner"
        raw["members"]["M-001"]["team"] = "平台"
        raw["activities"]["A-001"]["location"] = "一号会议室"
        (legacy / "data.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        result = TeamPlanner(legacy).enroll_batch([{"activity_id": "A-001", "member_id": "M-002"}])
        self.assertEqual(result[0]["participants"], ["M-001", "M-002"])
        saved = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["note"], "imported from the old planner")
        self.assertEqual(saved["members"]["M-001"]["team"], "平台")
        self.assertEqual(saved["activities"]["A-001"]["location"], "一号会议室")
        self.assertEqual(saved["activities"]["A-001"]["participants"], ["M-001", "M-002"])
        self.assertNotIn("completions", saved)

    def test_enroll_batch_broken_history_is_rejected(self):
        self._enroll_batch_ready()
        raw = json.loads(self.app.path.read_text(encoding="utf-8"))
        raw["activities"]["A-002"]["capacity"] = 0
        self.app.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.enroll_batch([{"activity_id": "A-003", "member_id": "M-002"}])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_cli_enroll_batch_success_failure_and_partial_array(self):
        self._enroll_batch_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "enroll-batch", name], text=True, capture_output=True)
        ok = run({"records": [
            {"activity_id": " A-002 ", "member_id": "M-001"},
            {"activity_id": "A-002", "member_id": " M-002 "},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([a["participants"] for a in json.loads(ok.stdout)], [["M-001", "M-002"], ["M-001", "M-002"]])
        # 小陈 is already enrolled in A-002: the call fails with error JSON.
        bad = run({"records": [{"activity_id": "A-002", "member_id": "M-001"}]})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        bad_shape = run({"records": []})
        self.assertEqual(bad_shape.returncode, 2)
        self.assertIn("error", json.loads(bad_shape.stderr))
        # Top-level array: independent batches; the first success (小陈 joins
        # A-003) stays after the later failure, stdout stays empty and the
        # error goes to stderr.
        partial = run([
            {"records": [{"activity_id": "A-003", "member_id": "M-001"}]},
            {"records": [{"activity_id": "A-003", "member_id": "M-001"}]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        reopened = TeamPlanner(self.root)
        self.assertEqual([m["member_id"] for m in reopened.roster("A-003")["members"]], ["M-001"])
        self.assertEqual([m["member_id"] for m in reopened.roster("A-002")["members"]], ["M-001", "M-002"])

    def test_preview_enroll_feasible_group_then_commits(self):
        self._enroll_batch_ready()
        before = self.app.path.read_bytes()
        # Identifiers are trimmed; one member may join several activities on
        # different dates and one activity may receive several members.
        records = [
            {"activity_id": " A-002 ", "member_id": "M-001"},
            {"activity_id": "A-002", "member_id": " M-002 "},
            {"activity_id": "A-003", "member_id": "M-001"},
        ]
        result = self.app.preview_enrollments(records)
        self.assertEqual(result, {"can_enroll": True, "records": [
            {"activity_id": "A-002", "member_id": "M-001", "remaining_seats": 0, "conflict_activity_ids": []},
            {"activity_id": "A-002", "member_id": "M-002", "remaining_seats": 0, "conflict_activity_ids": []},
            {"activity_id": "A-003", "member_id": "M-001", "remaining_seats": 1, "conflict_activity_ids": []},
        ]})
        # The preview is read-only: the file bytes are untouched and no
        # enrollment happened.
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.roster("A-002")["members"], [])
        # With the data unchanged the feasible preview commits as-is.
        enrolled = self.app.enroll_batch(records)
        self.assertEqual([a["participants"] for a in enrolled], [["M-001", "M-002"], ["M-001", "M-002"], ["M-001"]])

    def test_preview_enroll_last_seat_overflow_is_shared_and_negative(self):
        self._enroll_batch_ready()
        # A-001 has exactly one free seat: 小林 and 小吴 cannot both take it,
        # so both entries show the same negative count.
        result = self.app.preview_enrollments([
            {"activity_id": "A-001", "member_id": "M-002"},
            {"activity_id": "A-001", "member_id": "M-004"},
        ])
        self.assertEqual(result, {"can_enroll": False, "records": [
            {"activity_id": "A-001", "member_id": "M-002", "remaining_seats": -1, "conflict_activity_ids": []},
            {"activity_id": "A-001", "member_id": "M-004", "remaining_seats": -1, "conflict_activity_ids": []},
        ]})
        # The completed enrollment in A-004 keeps its seat: two additions to
        # a capacity-2 activity overflow as well.
        result = self.app.preview_enrollments([
            {"activity_id": "A-004", "member_id": "M-002"},
            {"activity_id": "A-004", "member_id": "M-004"},
        ])
        self.assertEqual([r["remaining_seats"] for r in result["records"]], [-1, -1])
        self.assertFalse(result["can_enroll"])
        self.assertEqual(self.app.roster("A-001")["members"], [{"member_id": "M-003", "name": "小周"}])

    def test_preview_enroll_same_day_conflicts_listed_both_ways(self):
        self._enroll_batch_ready()
        # Two entries of the group on the same date list each other.
        result = self.app.preview_enrollments([
            {"activity_id": "A-002", "member_id": "M-002"},
            {"activity_id": "A-005", "member_id": "M-002"},
        ])
        self.assertEqual(result, {"can_enroll": False, "records": [
            {"activity_id": "A-002", "member_id": "M-002", "remaining_seats": 1, "conflict_activity_ids": ["A-005"]},
            {"activity_id": "A-005", "member_id": "M-002", "remaining_seats": 1, "conflict_activity_ids": ["A-002"]},
        ]})
        # 小陈's completed A-004 still occupies the 15th: joining A-001 on the
        # same date conflicts even though one seat is free.
        result = self.app.preview_enrollments([{"activity_id": "A-001", "member_id": "M-001"}])
        self.assertEqual(result, {"can_enroll": False, "records": [
            {"activity_id": "A-001", "member_id": "M-001", "remaining_seats": 0, "conflict_activity_ids": ["A-004"]},
        ]})
        # Other members' enrollments are no conflict: 小林 next to 小周 in
        # A-001 is feasible.
        result = self.app.preview_enrollments([{"activity_id": "A-001", "member_id": "M-002"}])
        self.assertEqual(result, {"can_enroll": True, "records": [
            {"activity_id": "A-001", "member_id": "M-002", "remaining_seats": 0, "conflict_activity_ids": []},
        ]})

    def test_preview_enroll_overflow_and_conflict_reported_together(self):
        self._enroll_batch_ready()
        # The single enroll still allows same-day enrollments: 小林 picks up
        # the last seat of A-001 on the 15th.
        self.app.enroll("A-001", "M-002")
        before = self.app.path.read_bytes()
        # Both additions to the completed A-004 overflow; 小林's entry also
        # conflicts with his A-001 seat. Both obstacles are reported, and the
        # conflict-free 小吴 entry still shows its own negative count.
        result = self.app.preview_enrollments([
            {"activity_id": "A-004", "member_id": "M-002"},
            {"activity_id": "A-004", "member_id": "M-004"},
        ])
        self.assertEqual(result, {"can_enroll": False, "records": [
            {"activity_id": "A-004", "member_id": "M-002", "remaining_seats": -1, "conflict_activity_ids": ["A-001"]},
            {"activity_id": "A-004", "member_id": "M-004", "remaining_seats": -1, "conflict_activity_ids": []},
        ]})
        self.assertEqual(before, self.app.path.read_bytes())

    def test_preview_enroll_unrelated_historical_conflict_not_listed(self):
        self._enroll_batch_ready()
        # 小陈 picks up a second seat on the 15th via the single enroll; that
        # historical conflict sits on a date the group does not touch, so
        # joining A-002 on the 16th shows no conflict.
        self.app.enroll("A-001", "M-001")
        result = self.app.preview_enrollments([{"activity_id": "A-002", "member_id": "M-001"}])
        self.assertEqual(result, {"can_enroll": True, "records": [
            {"activity_id": "A-002", "member_id": "M-001", "remaining_seats": 1, "conflict_activity_ids": []},
        ]})

    def test_preview_enroll_invalid_never_creates_or_rewrites(self):
        self._enroll_batch_ready()
        before = self.app.path.read_bytes()
        for records in [
            None,
            [],
            "x",
            3,
            {},
            [42],
            [None],
            [[]],
            [{"activity_id": "A-002"}],
            [{"member_id": "M-001"}],
            [{"activity_id": "A-002", "member_id": "M-001", "extra": 1}],
            [{"activity_id": "   ", "member_id": "M-001"}],
            [{"activity_id": 9, "member_id": "M-001"}],
            [{"activity_id": "A-002", "member_id": None}],
            [{"activity_id": "A-002", "member_id": "  "}],
            [{"activity_id": "GHOST", "member_id": "M-001"}],
            [{"activity_id": "A-002", "member_id": "GHOST"}],
            [{"activity_id": "A-001", "member_id": "M-003"}],  # already enrolled
            [{"activity_id": "A-004", "member_id": "M-001"}],  # completed stays enrolled
            # Duplicate activity/member pair after trimming.
            [{"activity_id": " A-002 ", "member_id": "M-002"},
             {"activity_id": "A-002", "member_id": " M-002 "}],
            # The last entry fails; a valid earlier entry cannot save it.
            [{"activity_id": "A-002", "member_id": "M-002"},
             {"activity_id": "A-001", "member_id": "M-003"}],
        ]:
            with self.assertRaises(ValueError):
                self.app.preview_enrollments(records)
            self.assertEqual(before, self.app.path.read_bytes())
        # A missing history fails the business rules without creating the
        # directory or data.json.
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.preview_enrollments([{"activity_id": "A", "member_id": "M"}])
        self.assertFalse(empty.exists())
        self.assertFalse((empty / "data.json").exists())

    def test_preview_enroll_legacy_file_without_completions(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-001", "小陈")
        other.add_member("M-002", "小林")
        other.create_activity("A-001", "旧活动", "2026-10-15", 2)
        other.enroll("A-001", "M-001")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        before = (legacy / "data.json").read_bytes()
        result = TeamPlanner(legacy).preview_enrollments([{"activity_id": "A-001", "member_id": "M-002"}])
        self.assertEqual(result, {"can_enroll": True, "records": [
            {"activity_id": "A-001", "member_id": "M-002", "remaining_seats": 0, "conflict_activity_ids": []},
        ]})
        self.assertEqual(before, (legacy / "data.json").read_bytes())
        self.assertNotIn("completions", json.loads((legacy / "data.json").read_text(encoding="utf-8")))

    def test_cli_preview_enroll_success_failure_and_no_writes(self):
        self._enroll_batch_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "preview-enroll", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"records": [
            {"activity_id": " A-002 ", "member_id": "M-001"},
            {"activity_id": "A-002", "member_id": " M-002 "},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), {"can_enroll": True, "records": [
            {"activity_id": "A-002", "member_id": "M-001", "remaining_seats": 0, "conflict_activity_ids": []},
            {"activity_id": "A-002", "member_id": "M-002", "remaining_seats": 0, "conflict_activity_ids": []},
        ]})
        # A business obstacle is a successful call with can_enroll false.
        blocked = run({"records": [
            {"activity_id": "A-001", "member_id": "M-002"},
            {"activity_id": "A-001", "member_id": "M-004"},
        ]})
        self.assertEqual(blocked.returncode, 0, blocked.stderr)
        self.assertEqual(json.loads(blocked.stdout)["can_enroll"], False)
        # An invalid group fails with error JSON and empty stdout.
        bad = run({"records": [{"activity_id": "A-002", "member_id": "GHOST"}]})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        # Top-level array: independent previews; a later failure empties
        # stdout and the error goes to stderr.
        partial = run([
            {"records": [{"activity_id": "A-003", "member_id": "M-001"}]},
            {"records": []},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        # Nothing was ever written.
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.roster("A-002")["members"], [])

    def _split_ready(self):
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "新成员产品介绍", "2026-10-15", 3)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-001", "M-002")
        self.app.enroll("A-001", "M-003")

    def test_split_activity_moves_members_in_input_order(self):
        self._split_ready()
        # 小林、小陈依次分到同日新场：新名单按输入顺序，原场保留小周。
        result = self.app.split_activity("A-001", "A-101", "进阶培训", "2026-10-15", 2, ["M-002", "M-001"])
        self.assertEqual(set(result), {"source_activity", "new_activity"})
        self.assertEqual(result["new_activity"], {"activity_id": "A-101", "title": "进阶培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-002", "M-001"]})
        self.assertEqual(result["source_activity"]["participants"], ["M-003"])
        reopened = TeamPlanner(self.root)
        self.assertEqual([m["member_id"] for m in reopened.roster("A-101")["members"]], ["M-002", "M-001"])
        self.assertEqual([m["member_id"] for m in reopened.roster("A-001")["members"]], ["M-003"])
        # 查询与导出随即反映新名单。
        self.assertEqual([r["activity_id"] for r in reopened.member_schedule("M-002")], ["A-101"])
        exported = reopened.export_schedule()
        self.assertEqual(exported["row_count"], 3)
        self.assertIn("M-002,小林,A-101", exported["csv"])

    def test_split_activity_keeps_relative_order_and_empty_source(self):
        self._split_ready()
        result = self.app.split_activity("A-001", "A-101", "进阶培训", "2026-10-16", 2, ["M-002"])
        self.assertEqual(result["source_activity"]["participants"], ["M-001", "M-003"])
        # 全部移出后空名单的原场仍保留。
        result = self.app.split_activity("A-001", "A-102", "补训", "2026-10-17", 5, ["M-001", "M-003"])
        self.assertEqual(result["source_activity"]["participants"], [])
        self.assertEqual(result["new_activity"]["participants"], ["M-001", "M-003"])
        self.assertEqual([a["activity_id"] for a in TeamPlanner(self.root).activities()], ["A-001", "A-101", "A-102"])

    def test_split_activity_preserves_fields_and_completions(self):
        self._split_ready()
        # 他人的原场完成记录不阻止分场，记录与额外字段全部保留。
        self.app.record_completion("A-001", "M-003", "2026-10-15")
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data["activities"]["A-001"]["location"] = "一号会议室"
        data["note"] = "imported"
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.split_activity(" A-001 ", " A-101 ", " 进阶培训 ", "2026-10-15", 2, [" M-001 ", "M-002"])
        self.assertEqual(result["source_activity"]["location"], "一号会议室")
        self.assertEqual(result["source_activity"]["participants"], ["M-003"])
        self.assertEqual(set(result["new_activity"]), {"activity_id", "title", "on", "capacity", "participants"})
        stored = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["note"], "imported")
        self.assertEqual(stored["completions"], {"A-001": {"M-003": "2026-10-15"}})
        self.assertNotIn("A-101", stored["completions"])
        self.assertEqual([r["activity_id"] for r in self.app.completions("M-003")], ["A-001"])

    def test_split_activity_rejections_leave_bytes_unchanged(self):
        self._split_ready()
        self.app.create_activity("A-002", "安全规范", "2026-10-20", 2)
        self.app.enroll("A-002", "M-001")
        self.app.add_member("M-004", "小李")
        before = self.app.path.read_bytes()
        for kwargs in [
            # 标识、标题、成员标识去空白后须非空。
            {"source_activity_id": "  ", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": ["M-001"]},
            {"source_activity_id": "A-001", "activity_id": " ", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": ["M-001"]},
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "", "on": "2026-10-15", "capacity": 2, "member_ids": ["M-001"]},
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": ["  "]},
            # member_ids 只接受非空数组且规范化后无重复。
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": []},
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": "M-001"},
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": ["M-001", " M-001 "]},
            # 日期只接受无首尾空白的真实 YYYY-MM-DD。
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": " 2026-10-15", "capacity": 2, "member_ids": ["M-001"]},
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-02-30", "capacity": 2, "member_ids": ["M-001"]},
            # 容量须为非布尔正整数且不少于转移人数。
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": True, "member_ids": ["M-001"]},
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 0, "member_ids": ["M-001"]},
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 1, "member_ids": ["M-001", "M-002"]},
            # 原场与成员须存在，新场标识不得已存在。
            {"source_activity_id": "GHOST", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": ["M-001"]},
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": ["GHOST"]},
            {"source_activity_id": "A-001", "activity_id": "A-002", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": ["M-002"]},
            {"source_activity_id": "A-001", "activity_id": "A-001", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": ["M-002"]},
            # 所选成员均须已报名原场（M-004 存在但未报名）。
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 2, "member_ids": ["M-004"]},
            # 所选成员在新日期仍报名第三场活动（M-001 在 A-002 是 2026-10-20）。
            {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-20", "capacity": 2, "member_ids": ["M-001"]},
        ]:
            with self.assertRaises(ValueError, msg=kwargs):
                self.app.split_activity(**kwargs)
        self.assertEqual(before, self.app.path.read_bytes())
        # 本人已有原场完成记录则整次拒绝；第三场已完成报名也算冲突。
        self.app.record_completion("A-001", "M-002", "2026-10-15")
        self.app.record_completion("A-002", "M-001", "2026-10-20")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.split_activity("A-001", "A-101", "进阶", "2026-10-15", 2, ["M-002"])
        with self.assertRaises(ValueError):
            self.app.split_activity("A-001", "A-101", "进阶", "2026-10-20", 2, ["M-001"])
        self.assertEqual(before, self.app.path.read_bytes())
        # 他人的完成记录不阻止分场：移出小陈后小林记录仍在原场。
        result = self.app.split_activity("A-001", "A-101", "进阶", "2026-10-15", 2, ["M-001"])
        self.assertEqual(result["source_activity"]["participants"], ["M-002", "M-003"])

    def test_split_activity_invalid_does_not_create_file(self):
        with self.assertRaises(ValueError):
            self.app.split_activity("A-001", "A-101", "进阶", "2026-10-15", 2, ["M-001"])
        self.assertFalse(self.app.path.exists())
        with self.assertRaises(ValueError):
            self.app.split_activity("A-001", "A-101", "进阶", "2026-02-30", 2, ["M-001"])
        self.assertFalse(self.app.path.exists())

    def test_split_activity_legacy_file_without_completions(self):
        legacy = self.root / "legacy"
        app = TeamPlanner(legacy)
        app.add_member("M-001", "小陈")
        app.create_activity("A-001", "新成员产品介绍", "2026-10-15", 2)
        app.enroll("A-001", "M-001")
        data = json.loads(app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = TeamPlanner(legacy).split_activity("A-001", "A-101", "进阶", "2026-10-15", 1, ["M-001"])
        self.assertEqual(result["new_activity"]["participants"], ["M-001"])
        self.assertNotIn("completions", json.loads(app.path.read_text(encoding="utf-8")))

    def test_cli_split_activity_success_failure_and_partial_array(self):
        self._split_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "split-activity", name], text=True, capture_output=True)
        ok = run({"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶培训", "on": "2026-10-15", "capacity": 2, "member_ids": ["M-002", "M-001"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(value["new_activity"]["participants"], ["M-002", "M-001"])
        self.assertEqual(value["source_activity"]["participants"], ["M-003"])
        # 新场标识已存在：失败时标准输出为空，标准错误输出含 error 的 JSON。
        bad = run({"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 1, "member_ids": ["M-003"]})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        # 顶层数组逐项执行：第一组分场保留，第二组失败后标准输出为空。
        partial = run([
            {"source_activity_id": "A-001", "activity_id": "A-102", "title": "补训", "on": "2026-10-16", "capacity": 1, "member_ids": ["M-003"]},
            {"source_activity_id": "A-001", "activity_id": "A-102", "title": "补训", "on": "2026-10-16", "capacity": 1, "member_ids": ["M-003"]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        reopened = TeamPlanner(self.root)
        self.assertEqual([m["member_id"] for m in reopened.roster("A-102")["members"]], ["M-003"])
        self.assertEqual(reopened.roster("A-001")["members"], [])

    def _merge_ready(self):
        # 2026-10-15: target A-001 (capacity 3) holds 小陈; sources A-002 and
        # A-003 carry overlapping rosters and different titles. Individual
        # enrolls allow the pre-merge same-day enrollments the merge reconciles.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "目标培训", "2026-10-15", 3)
        self.app.create_activity("A-002", "来源一", "2026-10-15", 5)
        self.app.create_activity("A-003", "来源二", "2026-10-15", 5)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-002")
        self.app.enroll("A-002", "M-001")
        self.app.enroll("A-003", "M-003")
        self.app.enroll("A-003", "M-002")

    def test_merge_activities_example_order_and_dedup(self):
        self._merge_ready()
        # 目标容量 3 且已有小陈，两个来源依次含小林、小陈和小周、小林：
        # 名单保留目标原顺序，再按来源顺序追加未出现的成员，每人只占一席。
        result = self.app.merge_activities(" A-001 ", [" A-002 ", "A-003"])
        self.assertEqual(result, {"activity_id": "A-001", "title": "目标培训", "on": "2026-10-15", "capacity": 3, "participants": ["M-001", "M-002", "M-003"]})
        reopened = TeamPlanner(self.root)
        self.assertEqual([a["activity_id"] for a in reopened.activities()], ["A-001"])
        self.assertEqual([m["member_id"] for m in reopened.roster("A-001")["members"]], ["M-001", "M-002", "M-003"])
        # 旧来源标识随后按未知活动处理。
        for source_id in ("A-002", "A-003"):
            with self.assertRaises(ValueError):
                reopened.roster(source_id)

    def test_merge_activities_keeps_fields_completions_and_queries(self):
        self._merge_ready()
        # 完成记录随来源并入目标；没有记录的小陈仍为未完成。
        self.app.record_completion("A-002", "M-002", "2026-10-16")
        self.app.record_completion("A-003", "M-003", "2026-10-15")
        # 空名单来源也可合并。
        self.app.create_activity("A-004", "空来源", "2026-10-15", 2)
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data["activities"]["A-001"]["location"] = "一号会议室"
        data["activities"]["A-002"]["location"] = "二号会议室"
        data["note"] = "imported"
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.merge_activities("A-001", ["A-002", "A-003", "A-004"])
        # 目标保留标识、标题、日期、容量与额外字段，来源额外字段随活动删除。
        self.assertEqual(result["location"], "一号会议室")
        self.assertEqual(result["participants"], ["M-001", "M-002", "M-003"])
        stored = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["note"], "imported")
        self.assertEqual(set(stored["members"]), {"M-001", "M-002", "M-003"})
        self.assertEqual(set(stored["activities"]), {"A-001"})
        self.assertEqual(stored["activities"]["A-001"]["location"], "一号会议室")
        # 来源的完成记录入口删除并并入目标，日期保持原值。
        self.assertEqual(set(stored["completions"]), {"A-001"})
        self.assertEqual(stored["completions"]["A-001"], {"M-002": "2026-10-16", "M-003": "2026-10-15"})
        # 名单、日程、完成记录查询和导出立即反映合并结果。
        reopened = TeamPlanner(self.root)
        self.assertEqual([r["activity_id"] for r in reopened.completions("M-002")], ["A-001"])
        schedule = {r["activity_id"]: r for r in reopened.member_schedule("M-001")}
        self.assertEqual(schedule["A-001"]["status"], "pending")
        schedule2 = {r["activity_id"]: r for r in reopened.member_schedule("M-002")}
        self.assertEqual(schedule2["A-001"]["status"], "completed")
        self.assertEqual(schedule2["A-001"]["completed_on"], "2026-10-16")
        exported = reopened.export_schedule()
        self.assertEqual(exported["row_count"], 3)
        self.assertNotIn("A-002", exported["csv"])
        self.assertIn("A-001", exported["csv"])

    def test_merge_activities_completion_date_rules(self):
        self.app.add_member("M-001", "小陈")
        self.app.create_activity("A-001", "目标", "2026-10-15", 5)
        self.app.create_activity("A-002", "来源", "2026-10-15", 5)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-001")
        # 两处都有记录且日期相同：合为一条，日期不改写。
        self.app.record_completion("A-001", "M-001", "2026-10-16")
        self.app.record_completion("A-002", "M-001", "2026-10-16")
        self.app.merge_activities("A-001", ["A-002"])
        self.assertEqual(self.app.completions("M-001"), [
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16", "title": "目标", "on": "2026-10-15"},
        ])
        # 日期不同则整体拒绝，不选择或改写日期，来源活动与记录保留。
        self.app.create_activity("A-010", "目标二", "2026-10-17", 5)
        self.app.create_activity("A-011", "来源二", "2026-10-17", 5)
        self.app.enroll("A-010", "M-001")
        self.app.enroll("A-011", "M-001")
        self.app.record_completion("A-010", "M-001", "2026-10-17")
        self.app.record_completion("A-011", "M-001", "2026-10-18")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.merge_activities("A-010", ["A-011"])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([m["member_id"] for m in self.app.roster("A-011")["members"]], ["M-001"])
        self.assertEqual(self.app.completions("M-001")[-1]["activity_id"], "A-011")

    def test_merge_activities_same_day_conflict_rules(self):
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "目标", "2026-10-15", 5)
        self.app.create_activity("A-002", "来源", "2026-10-15", 5)
        self.app.create_activity("A-003", "第三场", "2026-10-15", 5)
        self.app.create_activity("A-004", "他日", "2026-10-16", 5)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-003", "M-001")  # 目标原有成员的历史冲突，不阻止合并
        self.app.enroll("A-002", "M-002")
        self.app.enroll("A-003", "M-002")  # 转入成员仍占第三场，阻止合并
        self.app.enroll("A-002", "M-003")
        self.app.enroll("A-004", "M-003")  # 其他日期不阻止合并
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.merge_activities("A-001", ["A-002"])
        self.assertEqual(before, self.app.path.read_bytes())
        # 第三场已完成报名仍算冲突。
        self.app.record_completion("A-003", "M-002", "2026-10-15")
        with self.assertRaises(ValueError):
            self.app.merge_activities("A-001", ["A-002"])
        # 转入成员放弃第三场后，目标原有成员的历史冲突不再阻止操作。
        self.app.correct_completions([{"activity_id": "A-003", "member_id": "M-002", "completed_on": None}])
        self.app.cancel_enrollments("M-002", ["A-003"])
        result = self.app.merge_activities(" A-001 ", ["A-002"])
        self.assertEqual(result["participants"], ["M-001", "M-002", "M-003"])
        rows = {r["activity_id"]: r for r in self.app.member_schedule("M-001")}
        self.assertEqual(rows["A-001"]["conflict_activity_ids"], ["A-003"])
        rows3 = {r["activity_id"]: r for r in self.app.member_schedule("M-003")}
        self.assertEqual(set(rows3), {"A-001", "A-004"})

    def test_merge_activities_rejections_leave_bytes_unchanged(self):
        self._merge_ready()
        before = self.app.path.read_bytes()
        for target, sources in [
            ("A-001", []),                          # 来源必须是非空数组
            ("A-001", "A-002"),
            ("A-001", None),
            ("A-001", ["A-001"]),                  # 目标不得出现在来源中
            ("A-001", ["A-002", " A-002 "]),       # 规范化后来源重复
            ("  ", ["A-002"]),                     # 空白目标标识
            (9, ["A-002"]),
            ("A-001", ["  "]),                     # 空白来源标识
            ("A-001", [9]),
            ("GHOST", ["A-002"]),                  # 未知目标
            ("A-001", ["GHOST"]),                  # 未知来源
        ]:
            with self.assertRaises(ValueError, msg=(target, sources)):
                self.app.merge_activities(target, sources)
        self.assertEqual(before, self.app.path.read_bytes())
        # 来源与目标日期不同则拒绝。
        self.app.create_activity("A-009", "他日", "2026-10-14", 5)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.merge_activities("A-001", ["A-009"])
        self.assertEqual(before, self.app.path.read_bytes())
        # 最终去重人数超过目标容量则整体拒绝，来源全部保留。
        self.app.add_member("M-004", "小李")
        self.app.create_activity("A-005", "超员来源", "2026-10-15", 5)
        self.app.enroll("A-005", "M-004")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.merge_activities("A-001", ["A-002", "A-003", "A-005"])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([m["member_id"] for m in self.app.roster("A-005")["members"]], ["M-004"])

    def test_merge_activities_invalid_does_not_create_file(self):
        fresh = TeamPlanner(self.root / "empty")
        with self.assertRaises(ValueError):
            fresh.merge_activities("A-001", ["A-002"])
        self.assertFalse((self.root / "empty").exists())

    def test_merge_activities_legacy_file_without_completions(self):
        legacy = self.root / "legacy"
        app = TeamPlanner(legacy)
        app.add_member("M-001", "小陈")
        app.add_member("M-002", "小林")
        app.create_activity("A-001", "目标", "2026-10-15", 2)
        app.create_activity("A-002", "来源", "2026-10-15", 2)
        app.enroll("A-001", "M-001")
        app.enroll("A-002", "M-002")
        data = json.loads(app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = TeamPlanner(legacy).merge_activities("A-001", ["A-002"])
        self.assertEqual(result["participants"], ["M-001", "M-002"])
        stored = json.loads(app.path.read_text(encoding="utf-8"))
        self.assertNotIn("completions", stored)
        self.assertEqual(TeamPlanner(legacy).completions("M-002"), [])

    def test_cli_merge_activities_success_failure_and_partial_array(self):
        self._merge_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "merge-activities", name], text=True, capture_output=True)
        ok = run({"target_activity_id": " A-001 ", "source_activity_ids": [" A-002 ", "A-003"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["participants"], ["M-001", "M-002", "M-003"])
        # 来源已删除：失败时标准输出为空，标准错误输出含 error 的 JSON。
        bad = run({"target_activity_id": "A-001", "source_activity_ids": ["A-002"]})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        # 顶层数组逐项执行：第一组合并保留，第二组引用已删来源后失败，标准输出为空。
        self.app.create_activity("A-010", "补训一", "2026-10-16", 2)
        self.app.create_activity("A-011", "补训二", "2026-10-16", 2)
        partial = run([
            {"target_activity_id": "A-010", "source_activity_ids": ["A-011"]},
            {"target_activity_id": "A-010", "source_activity_ids": ["A-011"]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        reopened = TeamPlanner(self.root)
        self.assertEqual([a["activity_id"] for a in reopened.activities()], ["A-001", "A-010"])

    def _requirements_ready(self):
        # 三个成员、五场活动：入门两场（10-10/10-11）、进阶两场（10-20/10-21，
        # 相对截止日仍是未来活动）、同日补训两场（10-12）。
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "入门一", "2026-10-10", 3)
        self.app.create_activity("A-002", "入门二", "2026-10-11", 3)
        self.app.create_activity("A-003", "进阶一", "2026-10-20", 3)
        self.app.create_activity("A-004", "进阶二", "2026-10-21", 3)
        self.app.create_activity("A-005", "补训甲", "2026-10-12", 3)
        self.app.create_activity("A-006", "补训乙", "2026-10-12", 3)
        # 小陈：入门一已完成（10-15），进阶一已报名且完成日在截止日之后。
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-003", "M-001")
        self.app.record_completion("A-001", "M-001", "2026-10-15")
        self.app.record_completion("A-003", "M-001", "2026-10-20")
        # 小林只报名入门二，无完成记录。
        self.app.enroll("A-002", "M-002")
        # 小周报名补训两场，两场同日完成，按活动标识选首条。
        self.app.enroll("A-005", "M-003")
        self.app.enroll("A-006", "M-003")
        self.app.record_completion("A-005", "M-003", "2026-10-12")
        self.app.record_completion("A-006", "M-003", "2026-10-12")

    def test_training_requirements_statuses_counts_and_order(self):
        self._requirements_ready()
        result = self.app.training_requirements([
            {"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"]},
            {"group_id": "G-ADV", "activity_ids": ["A-003", "A-004"]},
            {"group_id": "G-EXTRA", "activity_ids": ["A-005", "A-006"]},
        ], "2026-10-16")
        self.assertEqual(result["as_of"], "2026-10-16")
        self.assertEqual(result["required_count"], 3)
        self.assertEqual([m["member_id"] for m in result["members"]], ["M-001", "M-002", "M-003"])
        chen, lin, zhou = result["members"]
        self.assertEqual(chen, {
            "member_id": "M-001", "name": "小陈", "completed_count": 1, "remaining_count": 2,
            "groups": [
                {"group_id": "G-ENTRY", "status": "completed", "activity_id": "A-001", "completed_on": "2026-10-15"},
                # 未来活动仍算要求；晚于截止日的记录不抵扣，但本人已报名，故为 pending。
                {"group_id": "G-ADV", "status": "pending", "activity_id": None, "completed_on": None},
                {"group_id": "G-EXTRA", "status": "not_enrolled", "activity_id": None, "completed_on": None},
            ],
        })
        self.assertEqual(lin, {
            "member_id": "M-002", "name": "小林", "completed_count": 0, "remaining_count": 3,
            "groups": [
                {"group_id": "G-ENTRY", "status": "pending", "activity_id": None, "completed_on": None},
                {"group_id": "G-ADV", "status": "not_enrolled", "activity_id": None, "completed_on": None},
                {"group_id": "G-EXTRA", "status": "not_enrolled", "activity_id": None, "completed_on": None},
            ],
        })
        # 同日完成日期、同日活动日期时按活动标识升序选首条。
        self.assertEqual(zhou["groups"][2], {"group_id": "G-EXTRA", "status": "completed", "activity_id": "A-005", "completed_on": "2026-10-12"})
        self.assertEqual(zhou["completed_count"], 1)
        self.assertEqual(zhou["remaining_count"], 2)

    def test_training_requirements_first_qualifying_record_tie_breaks(self):
        self._requirements_ready()
        # 小林在入门两场都有合格记录：完成日期相同则取活动日期较早者。
        self.app.enroll("A-001", "M-002")
        self.app.record_completion("A-001", "M-002", "2026-10-15")
        self.app.record_completion("A-002", "M-002", "2026-10-15")
        result = self.app.training_requirements(
            [{"group_id": " G-ENTRY ", "activity_ids": [" A-001 ", "A-002"]}], "2026-10-16",
            member_ids=["M-002"],
        )
        group = result["members"][0]["groups"][0]
        self.assertEqual(group, {"group_id": "G-ENTRY", "status": "completed", "activity_id": "A-001", "completed_on": "2026-10-15"})
        # 不同完成日期：较晚活动日期但完成日期更早的记录胜出。
        self.app.correct_completions([{"activity_id": "A-001", "member_id": "M-002", "completed_on": "2026-10-16"}])
        result = self.app.training_requirements(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"]}], "2026-10-16", member_ids=["M-002"],
        )
        group = result["members"][0]["groups"][0]
        self.assertEqual(group, {"group_id": "G-ENTRY", "status": "completed", "activity_id": "A-002", "completed_on": "2026-10-15"})
        # 截止日当天计完成；再早一天则两条记录都过期，已报名故为 pending。
        result = self.app.training_requirements(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"]}], "2026-10-14", member_ids=["M-002"],
        )
        group = result["members"][0]["groups"][0]
        self.assertEqual(group, {"group_id": "G-ENTRY", "status": "pending", "activity_id": None, "completed_on": None})

    def test_training_requirements_member_selection_semantics(self):
        self._requirements_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"]}]
        # 省略或 null：全员按标识升序。
        self.assertEqual([m["member_id"] for m in self.app.training_requirements(groups, "2026-10-16")["members"]], ["M-001", "M-002", "M-003"])
        self.assertEqual([m["member_id"] for m in self.app.training_requirements(groups, "2026-10-16", None)["members"]], ["M-001", "M-002", "M-003"])
        # 显式给出：输入顺序，带空白先规范化。
        result = self.app.training_requirements(groups, "2026-10-16", [" M-003 ", "M-001"])
        self.assertEqual([m["member_id"] for m in result["members"]], ["M-003", "M-001"])
        # 空数组：无人。
        empty = self.app.training_requirements(groups, "2026-10-16", [])
        self.assertEqual(empty, {"as_of": "2026-10-16", "required_count": 1, "members": []})

    def test_training_requirements_rejections(self):
        self._requirements_ready()
        valid_groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"]}]
        for groups in [None, [], "x", [{}], [{"group_id": "G"}], [{"activity_ids": ["A-001"]}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "extra": 1}],
                      [{"group_id": "  ", "activity_ids": ["A-001"]}],
                      [{"group_id": 9, "activity_ids": ["A-001"]}],
                      [{"group_id": "G", "activity_ids": None}],
                      [{"group_id": "G", "activity_ids": []}],
                      [{"group_id": "G", "activity_ids": ["A-001", " A-001 "]}],
                      [{"group_id": "G1", "activity_ids": ["A-001"]}, {"group_id": "G1", "activity_ids": ["A-002"]}],
                      [{"group_id": "G1", "activity_ids": ["A-001"]}, {"group_id": "G2", "activity_ids": ["A-001"]}],
                      [{"group_id": "G1", "activity_ids": ["A-001"]}, {"group_id": "G2", "activity_ids": ["GHOST"]}]]:
            with self.assertRaises(ValueError, msg=groups):
                self.app.training_requirements(groups, "2026-10-16")
        for as_of in ["2026-02-30", " 2026-10-16", "2026/10/16", 20261016, None]:
            with self.assertRaises(ValueError, msg=as_of):
                self.app.training_requirements(valid_groups, as_of)
        for member_ids in ["M-001", [" "], ["M-001", " M-001 "], ["M-001", "GHOST"]]:
            with self.assertRaises(ValueError, msg=member_ids):
                self.app.training_requirements(valid_groups, "2026-10-16", member_ids)

    def test_training_requirements_other_members_and_unenrolled_records(self):
        self._requirements_ready()
        # 他人完成记录不影响本人：小林在入门二仍为 pending。
        result = self.app.training_requirements(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"]}], "2026-10-16", member_ids=["M-002", "M-001"],
        )
        by_member = {m["member_id"]: m for m in result["members"]}
        self.assertEqual(by_member["M-002"]["groups"][0]["status"], "pending")
        self.assertEqual(by_member["M-001"]["groups"][0]["status"], "completed")

    def test_training_requirements_never_writes_and_legacy_data(self):
        self._requirements_ready()
        before = self.app.path.read_bytes()
        self.app.training_requirements([{"group_id": "G", "activity_ids": ["A-001", "A-002"]}], "2026-10-16")
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.training_requirements([{"group_id": "G", "activity_ids": ["GHOST"]}], "2026-10-16")
        self.assertEqual(before, self.app.path.read_bytes())
        # 旧数据缺少 completions：按无记录处理，按报名给 pending/not_enrolled。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.training_requirements(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"]},
             {"group_id": "G-ADV", "activity_ids": ["A-003", "A-004"]}],
            "2026-10-16", member_ids=["M-001", "M-002"],
        )
        self.assertEqual([g["status"] for g in result["members"][0]["groups"]], ["pending", "pending"])
        self.assertEqual([g["status"] for g in result["members"][1]["groups"]], ["pending", "not_enrolled"])
        # 查询不补写 completions 字段。
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        # 空目录上的失败查询不创建目录或文件。
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.training_requirements([{"group_id": "G", "activity_ids": ["A-001"]}], "2026-10-16")
        self.assertFalse(empty.exists())

    def test_training_requirements_broken_history_and_os_error(self):
        self._requirements_ready()
        broken = json.loads(self.app.path.read_text(encoding="utf-8"))
        broken["members"]["M-002"]["name"] = "   "
        self.app.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.training_requirements([{"group_id": "G", "activity_ids": ["A-001"]}], "2026-10-16")
        # data.json 是目录属于操作系统错误，保留 OSError。
        self.app.path.unlink()
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.training_requirements([{"group_id": "G", "activity_ids": ["A-001"]}], "2026-10-16")

    def test_cli_training_requirements_success_failure_and_partial_array(self):
        self._requirements_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "training-requirements", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": [{"group_id": " G-ENTRY ", "activity_ids": [" A-001 ", "A-002"]}], "as_of": "2026-10-16", "member_ids": ["M-001"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(value["required_count"], 1)
        self.assertEqual(value["members"][0]["groups"], [
            {"group_id": "G-ENTRY", "status": "completed", "activity_id": "A-001", "completed_on": "2026-10-15"},
        ])
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["GHOST"]}], "as_of": "2026-10-16"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        # 顶层数组：两次独立查询；第二个失败时标准输出为空。
        partial = run([
            {"groups": [{"group_id": "G", "activity_ids": ["A-001"]}], "as_of": "2026-10-16", "member_ids": []},
            {"groups": [{"group_id": "G", "activity_ids": ["GHOST"]}], "as_of": "2026-10-16"},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def _validity_ready(self):
        # 与 _requirements_ready 相同的基础数据，供复训有效期核对使用。
        self._requirements_ready()

    def test_training_validity_statuses_counts_and_order(self):
        self._validity_ready()
        result = self.app.training_validity([
            {"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3},
            {"group_id": "G-ADV", "activity_ids": ["A-003", "A-004"], "valid_days": 5},
            {"group_id": "G-EXTRA", "activity_ids": ["A-005", "A-006"], "valid_days": 30},
        ], "2026-10-16")
        self.assertEqual(result["as_of"], "2026-10-16")
        self.assertEqual(result["required_count"], 3)
        self.assertEqual([m["member_id"] for m in result["members"]], ["M-001", "M-002", "M-003"])
        chen, lin, zhou = result["members"]
        # 小陈 10-15 完成入门一，距 10-16 一天，三天有效：valid；进阶场的记录在
        # 截止日之后，不参与选取，本人已报名故 pending。
        self.assertEqual(chen, {
            "member_id": "M-001", "name": "小陈", "valid_count": 1, "remaining_count": 2,
            "groups": [
                {"group_id": "G-ENTRY", "status": "valid", "activity_id": "A-001", "completed_on": "2026-10-15"},
                {"group_id": "G-ADV", "status": "pending", "activity_id": None, "completed_on": None},
                {"group_id": "G-EXTRA", "status": "not_enrolled", "activity_id": None, "completed_on": None},
            ],
        })
        self.assertEqual(lin, {
            "member_id": "M-002", "name": "小林", "valid_count": 0, "remaining_count": 3,
            "groups": [
                {"group_id": "G-ENTRY", "status": "pending", "activity_id": None, "completed_on": None},
                {"group_id": "G-ADV", "status": "not_enrolled", "activity_id": None, "completed_on": None},
                {"group_id": "G-EXTRA", "status": "not_enrolled", "activity_id": None, "completed_on": None},
            ],
        })
        # 小周两场同日完成：完成日期相同按活动日期、活动标识升序取首条。
        self.assertEqual(zhou["groups"][2], {"group_id": "G-EXTRA", "status": "valid", "activity_id": "A-005", "completed_on": "2026-10-12"})
        self.assertEqual(zhou["valid_count"], 1)
        self.assertEqual(zhou["remaining_count"], 2)

    def test_training_validity_boundary_days_example(self):
        self._validity_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        # 小陈 2026-10-15 完成、有效三天：10-17 仍有效，10-18 过期。
        valid = self.app.training_validity(groups, "2026-10-17", member_ids=["M-001"])
        self.assertEqual(valid["members"][0]["groups"][0], {"group_id": "G-ENTRY", "status": "valid", "activity_id": "A-001", "completed_on": "2026-10-15"})
        expired = self.app.training_validity(groups, "2026-10-18", member_ids=["M-001"])
        self.assertEqual(expired["members"][0]["groups"][0], {"group_id": "G-ENTRY", "status": "expired", "activity_id": "A-001", "completed_on": "2026-10-15"})
        self.assertEqual(expired["members"][0]["valid_count"], 0)
        self.assertEqual(expired["members"][0]["remaining_count"], 1)
        # 完成日当天为第 0 天，一天有效期同样 valid。
        same_day = self.app.training_validity(groups, "2026-10-15", member_ids=["M-001"])
        self.assertEqual(same_day["members"][0]["groups"][0]["status"], "valid")

    def test_training_validity_latest_record_wins_and_future_not_masking(self):
        self._validity_ready()
        # 小林在入门两场都完成：较晚的完成日期胜出，即使其活动日期更早。
        self.app.enroll("A-001", "M-002")
        self.app.record_completion("A-001", "M-002", "2026-10-16")
        self.app.record_completion("A-002", "M-002", "2026-10-15")
        result = self.app.training_validity(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}], "2026-10-16", member_ids=["M-002"],
        )
        self.assertEqual(result["members"][0]["groups"][0], {"group_id": "G-ENTRY", "status": "valid", "activity_id": "A-001", "completed_on": "2026-10-16"})
        # 小陈的入门一 10-15 完成（三天有效）；再在入门二登记一条未来完成记录。
        # 截至 10-18 旧记录已过期，未来记录不参与选取也不掩盖过期状态。
        self.app.enroll("A-002", "M-001")
        self.app.record_completion("A-002", "M-001", "2026-10-25")
        result = self.app.training_validity(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}], "2026-10-18", member_ids=["M-001"],
        )
        self.assertEqual(result["members"][0]["groups"][0], {"group_id": "G-ENTRY", "status": "expired", "activity_id": "A-001", "completed_on": "2026-10-15"})
        # 未来记录在更晚的截止日成为最新记录并重新判定有效期。
        result = self.app.training_validity(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}], "2026-10-25", member_ids=["M-001"],
        )
        self.assertEqual(result["members"][0]["groups"][0], {"group_id": "G-ENTRY", "status": "valid", "activity_id": "A-002", "completed_on": "2026-10-25"})

    def test_training_validity_member_selection_semantics(self):
        self._validity_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        self.assertEqual([m["member_id"] for m in self.app.training_validity(groups, "2026-10-16")["members"]], ["M-001", "M-002", "M-003"])
        self.assertEqual([m["member_id"] for m in self.app.training_validity(groups, "2026-10-16", None)["members"]], ["M-001", "M-002", "M-003"])
        result = self.app.training_validity(groups, "2026-10-16", [" M-003 ", "M-001"])
        self.assertEqual([m["member_id"] for m in result["members"]], ["M-003", "M-001"])
        empty = self.app.training_validity(groups, "2026-10-16", [])
        self.assertEqual(empty, {"as_of": "2026-10-16", "required_count": 1, "members": []})

    def test_training_validity_rejections(self):
        self._validity_ready()
        valid_groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        for groups in [None, [], "x", [{}],
                      [{"group_id": "G", "activity_ids": ["A-001"]}],
                      [{"group_id": "G", "valid_days": 3}],
                      [{"activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "extra": 1}],
                      [{"group_id": "  ", "activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": 9, "activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": None, "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": [], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001", " A-001 "], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": True}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": False}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 0}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": -1}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": "3"}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 1.0}],
                      [{"group_id": "G1", "activity_ids": ["A-001"], "valid_days": 3},
                       {"group_id": "G1", "activity_ids": ["A-002"], "valid_days": 3}],
                      [{"group_id": "G1", "activity_ids": ["A-001"], "valid_days": 3},
                       {"group_id": "G2", "activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G1", "activity_ids": ["GHOST"], "valid_days": 3}]]:
            with self.assertRaises(ValueError, msg=groups):
                self.app.training_validity(groups, "2026-10-16")
        for as_of in ["2026-02-30", " 2026-10-16", "2026/10/16", 20261016, None]:
            with self.assertRaises(ValueError, msg=as_of):
                self.app.training_validity(valid_groups, as_of)
        for member_ids in ["M-001", [" "], ["M-001", " M-001 "], ["M-001", "GHOST"]]:
            with self.assertRaises(ValueError, msg=member_ids):
                self.app.training_validity(valid_groups, "2026-10-16", member_ids)

    def test_training_validity_never_writes_and_legacy_data(self):
        self._validity_ready()
        before = self.app.path.read_bytes()
        self.app.training_validity([{"group_id": "G", "activity_ids": ["A-001", "A-002"], "valid_days": 3}], "2026-10-16")
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.training_validity([{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}], "2026-10-16")
        self.assertEqual(before, self.app.path.read_bytes())
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.training_validity(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3},
             {"group_id": "G-ADV", "activity_ids": ["A-003", "A-004"], "valid_days": 3}],
            "2026-10-16", member_ids=["M-001", "M-002"],
        )
        self.assertEqual([g["status"] for g in result["members"][0]["groups"]], ["pending", "pending"])
        self.assertEqual([g["status"] for g in result["members"][1]["groups"]], ["pending", "not_enrolled"])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        empty = self.root / "empty-validity"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.training_validity([{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-16")
        self.assertFalse(empty.exists())

    def test_training_validity_broken_history_and_os_error(self):
        self._validity_ready()
        broken = json.loads(self.app.path.read_text(encoding="utf-8"))
        broken["members"]["M-002"]["name"] = "   "
        self.app.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.training_validity([{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-16")
        self.app.path.unlink()
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.training_validity([{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-16")

    def test_cli_training_validity_success_failure_and_partial_array(self):
        self._validity_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "training-validity", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": [{"group_id": " G-ENTRY ", "activity_ids": [" A-001 ", "A-002"], "valid_days": 3}], "as_of": "2026-10-17", "member_ids": ["M-001"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(value["required_count"], 1)
        self.assertEqual(value["members"][0]["groups"], [
            {"group_id": "G-ENTRY", "status": "valid", "activity_id": "A-001", "completed_on": "2026-10-15"},
        ])
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}], "as_of": "2026-10-16"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        partial = run([
            {"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "as_of": "2026-10-16", "member_ids": []},
            {"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}], "as_of": "2026-10-16"},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def _timeline_ready(self):
        # 与 _validity_ready 相同的基础数据，供有效期区间查询使用。
        self._validity_ready()

    def test_training_timeline_periods_cover_range_and_merge(self):
        self._timeline_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        result = self.app.training_validity_timeline(groups, "2026-10-14", "2026-10-19")
        self.assertEqual(result["from_on"], "2026-10-14")
        self.assertEqual(result["to_on"], "2026-10-19")
        self.assertEqual([m["member_id"] for m in result["members"]], ["M-001", "M-002", "M-003"])
        chen, lin, zhou = result["members"]
        self.assertEqual(chen["name"], "小陈")
        # 小陈 2026-10-15 完成且有效三天：一日 pending、三日 valid、两日 expired。
        self.assertEqual(chen["groups"], [{
            "group_id": "G-ENTRY",
            "periods": [
                {"from_on": "2026-10-14", "to_on": "2026-10-14", "status": "pending", "activity_id": None, "completed_on": None},
                {"from_on": "2026-10-15", "to_on": "2026-10-17", "status": "valid", "activity_id": "A-001", "completed_on": "2026-10-15"},
                {"from_on": "2026-10-18", "to_on": "2026-10-19", "status": "expired", "activity_id": "A-001", "completed_on": "2026-10-15"},
            ],
        }])
        # 小林已报名无记录：整段一个 pending 区间；小周未报名：整段 not_enrolled。
        self.assertEqual(lin["groups"][0]["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-19", "status": "pending", "activity_id": None, "completed_on": None},
        ])
        self.assertEqual(zhou["groups"][0]["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-19", "status": "not_enrolled", "activity_id": None, "completed_on": None},
        ])

    def test_training_timeline_matches_daily_validity_and_covers_closed_range(self):
        self._timeline_ready()
        groups = [
            {"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3},
            {"group_id": "G-ADV", "activity_ids": ["A-003", "A-004"], "valid_days": 5},
            {"group_id": "G-EXTRA", "activity_ids": ["A-005", "A-006"], "valid_days": 30},
        ]
        result = self.app.training_validity_timeline(groups, "2026-10-10", "2026-10-26")
        # 每个区间闭区间相接、完整覆盖查询范围且无重叠，按起日升序。
        for member in result["members"]:
            for group in member["groups"]:
                periods = group["periods"]
                self.assertEqual(periods[0]["from_on"], "2026-10-10")
                self.assertEqual(periods[-1]["to_on"], "2026-10-26")
                for earlier, later in zip(periods, periods[1:]):
                    self.assertEqual(
                        date.fromisoformat(earlier["to_on"]) + timedelta(days=1),
                        date.fromisoformat(later["from_on"]),
                    )
                    self.assertLess(earlier["to_on"], later["from_on"])
                # 相邻区间的（状态、活动、完成日）三元组绝不相同：合并是最长的。
                for earlier, later in zip(periods, periods[1:]):
                    self.assertNotEqual(
                        (earlier["status"], earlier["activity_id"], earlier["completed_on"]),
                        (later["status"], later["activity_id"], later["completed_on"]),
                    )
        # 每一天的三项与当日 training-validity 的对应组结果完全一致。
        day = date(2026, 10, 10)
        while day <= date(2026, 10, 26):
            daily = self.app.training_validity(groups, day.isoformat())
            for member, daily_member in zip(result["members"], daily["members"]):
                for group, daily_group in zip(member["groups"], daily_member["groups"]):
                    period = next(p for p in group["periods"] if p["from_on"] <= day.isoformat() <= p["to_on"])
                    self.assertEqual(
                        (period["status"], period["activity_id"], period["completed_on"]),
                        (daily_group["status"], daily_group["activity_id"], daily_group["completed_on"]),
                    )
            day += timedelta(days=1)

    def test_training_timeline_record_change_splits_and_future_not_masking(self):
        self._timeline_ready()
        # 小陈入门一 10-15 完成（三天有效），再在入门二登记 10-18 的完成记录：
        # 10-18 起选取依据改变，状态同为 valid 也必须分段；10-18 之前旧记录不被遮盖。
        self.app.enroll("A-002", "M-001")
        self.app.record_completion("A-002", "M-001", "2026-10-18")
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        result = self.app.training_validity_timeline(groups, "2026-10-14", "2026-10-22", ["M-001"])
        self.assertEqual(result["members"][0]["groups"][0]["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-14", "status": "pending", "activity_id": None, "completed_on": None},
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "status": "valid", "activity_id": "A-001", "completed_on": "2026-10-15"},
            {"from_on": "2026-10-18", "to_on": "2026-10-20", "status": "valid", "activity_id": "A-002", "completed_on": "2026-10-18"},
            {"from_on": "2026-10-21", "to_on": "2026-10-22", "status": "expired", "activity_id": "A-002", "completed_on": "2026-10-18"},
        ])
        # 完成记录在查询范围之后：整段按旧记录判定，未来记录不参与也不遮盖。
        result = self.app.training_validity_timeline(groups, "2026-10-14", "2026-10-17", ["M-001"])
        self.assertEqual(result["members"][0]["groups"][0]["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-14", "status": "pending", "activity_id": None, "completed_on": None},
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "status": "valid", "activity_id": "A-001", "completed_on": "2026-10-15"},
        ])
        # 小周两场同日完成：完成日期相同按活动日期、活动标识升序取首条。
        extra = [{"group_id": "G-EXTRA", "activity_ids": ["A-005", "A-006"], "valid_days": 30}]
        result = self.app.training_validity_timeline(extra, "2026-10-11", "2026-10-12", ["M-003"])
        self.assertEqual(result["members"][0]["groups"][0]["periods"], [
            {"from_on": "2026-10-11", "to_on": "2026-10-11", "status": "pending", "activity_id": None, "completed_on": None},
            {"from_on": "2026-10-12", "to_on": "2026-10-12", "status": "valid", "activity_id": "A-005", "completed_on": "2026-10-12"},
        ])

    def test_training_timeline_member_selection_and_single_day(self):
        self._timeline_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        result = self.app.training_validity_timeline(groups, "2026-10-16", "2026-10-17", [" M-003 ", "M-001"])
        self.assertEqual([m["member_id"] for m in result["members"]], ["M-003", "M-001"])
        # 成员选择为空数组时 members 为空，起止日期原样返回。
        empty = self.app.training_validity_timeline(groups, "2026-10-16", "2026-10-17", [])
        self.assertEqual(empty, {"from_on": "2026-10-16", "to_on": "2026-10-17", "members": []})
        # 起止相等表示查询一天：单个一日区间。
        single = self.app.training_validity_timeline(groups, "2026-10-18", "2026-10-18", ["M-001"])
        self.assertEqual(single["members"][0]["groups"][0]["periods"], [
            {"from_on": "2026-10-18", "to_on": "2026-10-18", "status": "expired", "activity_id": "A-001", "completed_on": "2026-10-15"},
        ])

    def test_training_timeline_validity_beyond_date_max(self):
        self._timeline_ready()
        # 有效期越过 9999-12-31：只按查询范围给出状态，不报溢出错误。
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001"], "valid_days": 10 ** 12}]
        result = self.app.training_validity_timeline(groups, "2026-10-14", "2026-10-20", ["M-001"])
        self.assertEqual(result["members"][0]["groups"][0]["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-14", "status": "pending", "activity_id": None, "completed_on": None},
            {"from_on": "2026-10-15", "to_on": "2026-10-20", "status": "valid", "activity_id": "A-001", "completed_on": "2026-10-15"},
        ])

    def test_training_timeline_rejections(self):
        self._timeline_ready()
        valid_groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        for groups in [None, [], "x", [{}],
                      [{"group_id": "G", "activity_ids": ["A-001"]}],
                      [{"group_id": "G", "valid_days": 3}],
                      [{"activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "extra": 1}],
                      [{"group_id": "  ", "activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": None, "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": [], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001", " A-001 "], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": True}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 0}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": "3"}],
                      [{"group_id": "G1", "activity_ids": ["A-001"], "valid_days": 3},
                       {"group_id": "G1", "activity_ids": ["A-002"], "valid_days": 3}],
                      [{"group_id": "G1", "activity_ids": ["A-001"], "valid_days": 3},
                       {"group_id": "G2", "activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G1", "activity_ids": ["GHOST"], "valid_days": 3}]]:
            with self.assertRaises(ValueError, msg=groups):
                self.app.training_validity_timeline(groups, "2026-10-14", "2026-10-19")
        for from_on, to_on in [("2026-02-30", "2026-10-19"), (" 2026-10-14", "2026-10-19"),
                               ("2026-10-14", "2026/10/19"), (20261014, "2026-10-19"),
                               ("2026-10-14", None), ("2026-10-20", "2026-10-19")]:
            with self.assertRaises(ValueError, msg=(from_on, to_on)):
                self.app.training_validity_timeline(valid_groups, from_on, to_on)
        for member_ids in ["M-001", [" "], ["M-001", " M-001 "], ["M-001", "GHOST"]]:
            with self.assertRaises(ValueError, msg=member_ids):
                self.app.training_validity_timeline(valid_groups, "2026-10-14", "2026-10-19", member_ids)

    def test_training_timeline_never_writes_and_legacy_data(self):
        self._timeline_ready()
        before = self.app.path.read_bytes()
        self.app.training_validity_timeline(
            [{"group_id": "G", "activity_ids": ["A-001", "A-002"], "valid_days": 3}], "2026-10-14", "2026-10-19")
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.training_validity_timeline(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}], "2026-10-14", "2026-10-19")
        self.assertEqual(before, self.app.path.read_bytes())
        # 旧数据缺少 completions：按无记录处理，查询不补写该字段。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.training_validity_timeline(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}],
            "2026-10-14", "2026-10-19", member_ids=["M-001", "M-003"])
        self.assertEqual([p["status"] for p in result["members"][0]["groups"][0]["periods"]], ["pending"])
        self.assertEqual([p["status"] for p in result["members"][1]["groups"][0]["periods"]], ["not_enrolled"])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        # 空目录上的失败查询不创建目录或文件。
        empty = self.root / "empty-timeline"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.training_validity_timeline(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-14", "2026-10-19")
        self.assertFalse(empty.exists())

    def test_training_timeline_broken_history_and_os_error(self):
        self._timeline_ready()
        broken = json.loads(self.app.path.read_text(encoding="utf-8"))
        broken["members"]["M-002"]["name"] = "   "
        self.app.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.training_validity_timeline(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-14", "2026-10-19")
        self.app.path.unlink()
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.training_validity_timeline(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-14", "2026-10-19")

    def test_cli_training_timeline_success_failure_and_partial_array(self):
        self._timeline_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "training-timeline", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": [{"group_id": " G-ENTRY ", "activity_ids": [" A-001 ", "A-002"], "valid_days": 3}],
                  "from_on": "2026-10-14", "to_on": "2026-10-19", "member_ids": ["M-001"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(value["from_on"], "2026-10-14")
        self.assertEqual(value["to_on"], "2026-10-19")
        self.assertEqual(value["members"][0]["groups"][0]["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-14", "status": "pending", "activity_id": None, "completed_on": None},
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "status": "valid", "activity_id": "A-001", "completed_on": "2026-10-15"},
            {"from_on": "2026-10-18", "to_on": "2026-10-19", "status": "expired", "activity_id": "A-001", "completed_on": "2026-10-15"},
        ])
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                   "from_on": "2026-10-14", "to_on": "2026-10-19"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        partial = run([
            {"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
             "from_on": "2026-10-14", "to_on": "2026-10-19", "member_ids": []},
            {"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
             "from_on": "2026-10-14", "to_on": "2026-10-19"},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def _coverage_ready(self):
        # 与 _timeline_ready 相同的基础数据，供培训合格人数核对使用。
        self._timeline_ready()

    def test_training_coverage_example_and_all_groups_required(self):
        self._coverage_ready()
        # 需求示例：只选小陈、期望两人，他唯一的组 2026-10-15 完成且有效三天、
        # 无其他记录：15 至 17 日合格 1 人、欠缺 1 人，18 日起合格 0 人、欠缺 2 人。
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        result = self.app.training_coverage(groups, "2026-10-15", "2026-10-18", 2, ["M-001"])
        self.assertEqual(result, {
            "from_on": "2026-10-15", "to_on": "2026-10-18", "minimum_count": 2,
            "periods": [
                {"from_on": "2026-10-15", "to_on": "2026-10-17",
                 "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 1},
                {"from_on": "2026-10-18", "to_on": "2026-10-18",
                 "valid_member_ids": [], "valid_count": 0, "shortage_count": 2},
            ],
        })
        # 只有当天所有组都 valid 才算合格：小陈进阶组记录在 10-20，10-15 至 10-17
        # 入门组有效但进阶组尚无合格记录，整段无人合格。
        both = self.app.training_coverage(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3},
             {"group_id": "G-ADV", "activity_ids": ["A-003", "A-004"], "valid_days": 5}],
            "2026-10-15", "2026-10-19", 1, ["M-001"])
        self.assertEqual(both["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-19",
             "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
        ])
        # 入门组有效期放宽到十天：小陈 15 至 24 日入门有效、20 至 24 日进阶有效，
        # 仅两组同时有效的 20 至 24 日合格；进阶记录在完成日前不提前生效。
        both = self.app.training_coverage(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 10},
             {"group_id": "G-ADV", "activity_ids": ["A-003", "A-004"], "valid_days": 5}],
            "2026-10-14", "2026-10-26", 1, ["M-001"])
        self.assertEqual(both["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-19",
             "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
            {"from_on": "2026-10-20", "to_on": "2026-10-24",
             "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-25", "to_on": "2026-10-26",
             "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
        ])

    def test_training_coverage_personnel_change_splits_basis_change_does_not(self):
        self._coverage_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        # 小林 10-18 完成入门二：人数同为 1 但人员替换，必须分段。
        self.app.record_completion("A-002", "M-002", "2026-10-18")
        result = self.app.training_coverage(groups, "2026-10-15", "2026-10-20", 1)
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-17",
             "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-18", "to_on": "2026-10-20",
             "valid_member_ids": ["M-002"], "valid_count": 1, "shortage_count": 0},
        ])
        # 小陈 10-18 在入门二再完成一场：完成依据改变但合格人员不变，不分段。
        self.app.enroll("A-002", "M-001")
        self.app.record_completion("A-002", "M-001", "2026-10-18")
        result = self.app.training_coverage(groups, "2026-10-15", "2026-10-20", 1, ["M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-20",
             "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
        ])

    def test_training_coverage_matches_daily_validity_and_covers_closed_range(self):
        self._coverage_ready()
        groups = [
            {"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3},
            {"group_id": "G-ADV", "activity_ids": ["A-003", "A-004"], "valid_days": 5},
            {"group_id": "G-EXTRA", "activity_ids": ["A-005", "A-006"], "valid_days": 30},
        ]
        result = self.app.training_coverage(groups, "2026-10-10", "2026-10-26", 2)
        self.assertEqual(result["from_on"], "2026-10-10")
        self.assertEqual(result["to_on"], "2026-10-26")
        self.assertEqual(result["minimum_count"], 2)
        periods = result["periods"]
        # 区间闭区间相接、完整覆盖查询范围且无重叠，按起日升序；相邻区间
        # 合格名单绝不相同：合并是最长的。
        self.assertEqual(periods[0]["from_on"], "2026-10-10")
        self.assertEqual(periods[-1]["to_on"], "2026-10-26")
        for earlier, later in zip(periods, periods[1:]):
            self.assertEqual(
                date.fromisoformat(earlier["to_on"]) + timedelta(days=1),
                date.fromisoformat(later["from_on"]),
            )
            self.assertNotEqual(earlier["valid_member_ids"], later["valid_member_ids"])
        for period in periods:
            self.assertEqual(period["valid_count"], len(period["valid_member_ids"]))
            self.assertEqual(period["shortage_count"], max(0, 2 - period["valid_count"]))
        # 每一天的合格名单与当日 training-validity 全部组均为 valid 的成员一致。
        day = date(2026, 10, 10)
        while day <= date(2026, 10, 26):
            daily = self.app.training_validity(groups, day.isoformat())
            expected = [
                member["member_id"] for member in daily["members"]
                if all(group["status"] == "valid" for group in member["groups"])
            ]
            period = next(p for p in periods if p["from_on"] <= day.isoformat() <= p["to_on"])
            self.assertEqual(period["valid_member_ids"], expected)
            day += timedelta(days=1)

    def test_training_coverage_member_selection_order_empty_and_single_day(self):
        self._coverage_ready()
        # 小林 10-16 完成入门二：16 至 17 日小陈、小林同时合格，名单按成员选择
        # 顺序排列（标识带首尾空白先去除），不按标识排序。
        self.app.record_completion("A-002", "M-002", "2026-10-16")
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        result = self.app.training_coverage(groups, "2026-10-16", "2026-10-17", 3, [" M-002 ", "M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-16", "to_on": "2026-10-17",
             "valid_member_ids": ["M-002", "M-001"], "valid_count": 2, "shortage_count": 1},
        ])
        # 成员选择为空数组：仍返回一个覆盖全范围的区间，人数为零且欠缺数等于
        # 期望人数；全部引用仍然校验。
        empty = self.app.training_coverage(groups, "2026-10-16", "2026-10-17", 3, [])
        self.assertEqual(empty, {
            "from_on": "2026-10-16", "to_on": "2026-10-17", "minimum_count": 3,
            "periods": [
                {"from_on": "2026-10-16", "to_on": "2026-10-17",
                 "valid_member_ids": [], "valid_count": 0, "shortage_count": 3},
            ],
        })
        with self.assertRaises(ValueError):
            self.app.training_coverage(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                "2026-10-16", "2026-10-17", 1, [])
        # 起止相等表示查询一天：单个一日区间。
        single = self.app.training_coverage(groups, "2026-10-15", "2026-10-15", 1, ["M-001"])
        self.assertEqual(single["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-15",
             "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
        ])

    def test_training_coverage_validity_beyond_date_max(self):
        self._coverage_ready()
        # 有效期越过 9999-12-31：只按查询范围给出人数，不报溢出错误。
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001"], "valid_days": 10 ** 12}]
        result = self.app.training_coverage(groups, "2026-10-14", "2026-10-20", 1, ["M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-14",
             "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
            {"from_on": "2026-10-15", "to_on": "2026-10-20",
             "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
        ])
        # 查询范围止于 9999-12-31 本身也不报溢出错误。
        edge = self.app.training_coverage(groups, "9999-12-31", "9999-12-31", 1, ["M-001"])
        self.assertEqual(edge["periods"], [
            {"from_on": "9999-12-31", "to_on": "9999-12-31",
             "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
        ])

    def test_training_coverage_rejections(self):
        self._coverage_ready()
        valid_groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        for groups in [None, [], "x", [{}],
                      [{"group_id": "G", "activity_ids": ["A-001"]}],
                      [{"group_id": "G", "valid_days": 3}],
                      [{"activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "extra": 1}],
                      [{"group_id": "  ", "activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": None, "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": [], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001", " A-001 "], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": True}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 0}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": "3"}],
                      [{"group_id": "G1", "activity_ids": ["A-001"], "valid_days": 3},
                       {"group_id": "G1", "activity_ids": ["A-002"], "valid_days": 3}],
                      [{"group_id": "G1", "activity_ids": ["A-001"], "valid_days": 3},
                       {"group_id": "G2", "activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G1", "activity_ids": ["GHOST"], "valid_days": 3}]]:
            with self.assertRaises(ValueError, msg=groups):
                self.app.training_coverage(groups, "2026-10-14", "2026-10-19", 1)
        for from_on, to_on in [("2026-02-30", "2026-10-19"), (" 2026-10-14", "2026-10-19"),
                               ("2026-10-14", "2026/10/19"), (20261014, "2026-10-19"),
                               ("2026-10-14", None), ("2026-10-20", "2026-10-19")]:
            with self.assertRaises(ValueError, msg=(from_on, to_on)):
                self.app.training_coverage(valid_groups, from_on, to_on, 1)
        # 期望人数必须是非布尔的正整数，允许超过选中成员数。
        for minimum_count in [None, True, 0, -1, "2", 2.5]:
            with self.assertRaises(ValueError, msg=minimum_count):
                self.app.training_coverage(valid_groups, "2026-10-14", "2026-10-19", minimum_count)
        for member_ids in ["M-001", [" "], ["M-001", " M-001 "], ["M-001", "GHOST"]]:
            with self.assertRaises(ValueError, msg=member_ids):
                self.app.training_coverage(valid_groups, "2026-10-14", "2026-10-19", 1, member_ids)

    def test_training_coverage_never_writes_and_legacy_data(self):
        self._coverage_ready()
        before = self.app.path.read_bytes()
        self.app.training_coverage(
            [{"group_id": "G", "activity_ids": ["A-001", "A-002"], "valid_days": 3}],
            "2026-10-14", "2026-10-19", 2)
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.training_coverage(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                "2026-10-14", "2026-10-19", 2)
        self.assertEqual(before, self.app.path.read_bytes())
        # 旧数据缺少 completions：按无记录处理，查询不补写该字段。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.training_coverage(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}],
            "2026-10-14", "2026-10-19", 2)
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-19",
             "valid_member_ids": [], "valid_count": 0, "shortage_count": 2},
        ])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        # 空目录上的失败查询不创建目录或文件。
        empty = self.root / "empty-coverage"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.training_coverage(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
                "2026-10-14", "2026-10-19", 1)
        self.assertFalse(empty.exists())

    def test_training_coverage_broken_history_and_os_error(self):
        self._coverage_ready()
        broken = json.loads(self.app.path.read_text(encoding="utf-8"))
        broken["members"]["M-002"]["name"] = "   "
        self.app.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.training_coverage(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
                "2026-10-14", "2026-10-19", 1)
        self.app.path.unlink()
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.training_coverage(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
                "2026-10-14", "2026-10-19", 1)

    def test_cli_training_coverage_success_failure_and_partial_array(self):
        self._coverage_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "training-coverage", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": [{"group_id": " G-ENTRY ", "activity_ids": [" A-001 ", "A-002"], "valid_days": 3}],
                  "from_on": "2026-10-15", "to_on": "2026-10-18", "minimum_count": 2, "member_ids": ["M-001"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(value, {
            "from_on": "2026-10-15", "to_on": "2026-10-18", "minimum_count": 2,
            "periods": [
                {"from_on": "2026-10-15", "to_on": "2026-10-17",
                 "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 1},
                {"from_on": "2026-10-18", "to_on": "2026-10-18",
                 "valid_member_ids": [], "valid_count": 0, "shortage_count": 2},
            ],
        })
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                   "from_on": "2026-10-14", "to_on": "2026-10-19", "minimum_count": 1})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        missing = run({"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
                       "from_on": "2026-10-14", "to_on": "2026-10-19"})
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stderr))
        self.assertEqual(missing.stdout, "")
        partial = run([
            {"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
             "from_on": "2026-10-14", "to_on": "2026-10-19", "minimum_count": 1, "member_ids": []},
            {"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
             "from_on": "2026-10-14", "to_on": "2026-10-19", "minimum_count": 1},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def _renewal_ready(self):
        # 小陈 2026-10-15 在组内 A-001 完成、有效三天（10-18 到期）；组内另有
        # 10-17（窗口前）、10-19 满员且与本人两场同日活动冲突的 A-002、10-20 尚
        # 余一席的 A-003、10-21（窗口后）的场次。组外 A-100 是小陈当天已报名
        # （未完成）的场次，A-101 是当天已完成的场次，两者都只应出现在冲突里。
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "入门一", "2026-10-15", 2)
        self.app.create_activity("A-002", "十九号满员", "2026-10-19", 1)
        self.app.create_activity("A-003", "二十号可选", "2026-10-20", 2)
        self.app.create_activity("A-005", "窗口前", "2026-10-17", 2)
        self.app.create_activity("A-006", "窗口后", "2026-10-21", 2)
        self.app.create_activity("A-007", "廿二号", "2026-10-22", 2)
        self.app.create_activity("A-100", "组外十九号", "2026-10-19", 2)
        self.app.create_activity("A-101", "组外已完成", "2026-10-19", 2)
        self.app.enroll("A-001", "M-001")
        self.app.record_completion("A-001", "M-001", "2026-10-15")
        self.app.enroll("A-002", "M-003")
        self.app.enroll("A-003", "M-002")
        self.app.enroll("A-100", "M-001")
        self.app.enroll("A-101", "M-001")
        self.app.record_completion("A-101", "M-001", "2026-10-19")

    def _renewal_groups(self, valid_days=3):
        return [{"group_id": "G-ENTRY",
                 "activity_ids": ["A-001", "A-002", "A-003", "A-005", "A-006", "A-007"],
                 "valid_days": valid_days}]

    def test_renewal_options_expired_example_full_conflict_and_available(self):
        self._renewal_ready()
        result = self.app.training_renewal_options(self._renewal_groups(), "2026-10-18", "2026-10-20", ["M-001"])
        self.assertEqual(result, {"as_of": "2026-10-18", "through_on": "2026-10-20", "alerts": [
            {
                "member_id": "M-001", "name": "小陈", "group_id": "G-ENTRY", "status": "expired",
                "activity_id": "A-001", "completed_on": "2026-10-15",
                "expires_on": "2026-10-18", "days_remaining": 0,
                # 19 日场次满员（小周占去唯一席位）且与本人组外未完成的 A-100、
                # 已完成的 A-101 同日冲突：两类阻碍同时展示；20 日场次余一席且
                # 本人当天无任何报名，available 为 true。
                "candidates": [
                    {"activity_id": "A-002", "title": "十九号满员", "on": "2026-10-19", "capacity": 1,
                     "remaining_seats": 0, "available": False, "conflict_activity_ids": ["A-100", "A-101"]},
                    {"activity_id": "A-003", "title": "二十号可选", "on": "2026-10-20", "capacity": 2,
                     "remaining_seats": 1, "available": True, "conflict_activity_ids": []},
                ],
            },
        ]})

    def test_renewal_options_keep_alert_fields_and_order_and_empty_candidates(self):
        self._renewal_ready()
        groups = self._renewal_groups()
        alerts_only = self.app.training_alerts(groups, "2026-10-18", "2026-10-20")
        with_options = self.app.training_renewal_options(groups, "2026-10-18", "2026-10-20")
        self.assertEqual((with_options["as_of"], with_options["through_on"]),
                         (alerts_only["as_of"], alerts_only["through_on"]))
        # Outer order and every original alert field are preserved exactly.
        self.assertEqual(len(with_options["alerts"]), len(alerts_only["alerts"]))
        for enriched, plain in zip(with_options["alerts"], alerts_only["alerts"]):
            self.assertTrue(enriched["candidates"] == [] or isinstance(enriched["candidates"], list))
            self.assertEqual({k: v for k, v in enriched.items() if k != "candidates"}, plain)
        order = [(a["member_id"], a["group_id"], a["status"]) for a in with_options["alerts"]]
        # 小陈 expired；小林已报名 A-003 为 pending；小周已报名满员的 A-002 为 pending。
        self.assertEqual(order, [("M-001", "G-ENTRY", "expired"),
                                 ("M-002", "G-ENTRY", "pending"),
                                 ("M-003", "G-ENTRY", "pending")])
        lin, zhou = with_options["alerts"][1], with_options["alerts"][2]
        self.assertEqual(lin["activity_id"], None)
        self.assertEqual(lin["expires_on"], None)
        # 小林本人 19 日无报名：满员只来自小周的席位，冲突数组为空，仍不可报名。
        self.assertEqual(lin["candidates"], [
            {"activity_id": "A-002", "title": "十九号满员", "on": "2026-10-19", "capacity": 1,
             "remaining_seats": 0, "available": False, "conflict_activity_ids": []},
        ])
        # 小周已报名的 A-002 被排除；同一个余席对小周也独立显示为可报名，不预留席位。
        self.assertEqual(zhou["candidates"], [
            {"activity_id": "A-003", "title": "二十号可选", "on": "2026-10-20", "capacity": 2,
             "remaining_seats": 1, "available": True, "conflict_activity_ids": []},
        ])

    def test_renewal_options_candidates_stay_inside_group(self):
        self._renewal_ready()
        # 第二个组只含小陈组外的两场 19 日活动（一场待完成、一场已完成）：候选
        # 只能来自本组，G-ENTRY 的场次绝不泄漏；本人已报名场次全部排除，故候选
        # 为空数组，但 pending 提醒保留。
        groups = self._renewal_groups() + [
            {"group_id": "G-OTHER", "activity_ids": ["A-100", "A-101"], "valid_days": 30},
        ]
        result = self.app.training_renewal_options(groups, "2026-10-18", "2026-10-20", ["M-001"])
        self.assertEqual([(a["group_id"], a["status"]) for a in result["alerts"]],
                         [("G-ENTRY", "expired"), ("G-OTHER", "pending")])
        self.assertEqual(result["alerts"][1]["candidates"], [])

    def test_renewal_options_expiring_requires_date_before_expiry(self):
        self.app.add_member("M-004", "小吴")
        self.app.create_activity("B-001", "上周培训", "2026-10-16", 2)
        self.app.create_activity("B-002", "二十号复训", "2026-10-20", 2)
        self.app.create_activity("B-003", "到期日复训", "2026-10-21", 2)
        self.app.create_activity("B-004", "到期后复训", "2026-10-22", 2)
        self.app.enroll("B-001", "M-004")
        self.app.record_completion("B-001", "M-004", "2026-10-16")
        groups = [{"group_id": "G-B", "activity_ids": ["B-001", "B-002", "B-003", "B-004"], "valid_days": 5}]
        # 10-16 完成、有效五天：10-21 到期；10-18 查至 10-25 为 expiring。
        result = self.app.training_renewal_options(groups, "2026-10-18", "2026-10-25", ["M-004"])
        alert = result["alerts"][0]
        self.assertEqual(alert["status"], "expiring")
        self.assertEqual(alert["expires_on"], "2026-10-21")
        self.assertEqual(alert["days_remaining"], 3)
        # expiring 的候选日期必须严格早于到期日：到期日当天 10-21 与之后的场次
        # 即使落在查询窗口内也排除，窗口内 10-20 的场次保留。
        self.assertEqual([c["activity_id"] for c in alert["candidates"]], ["B-002"])

    def test_renewal_options_expired_has_no_expiry_upper_bound(self):
        self._renewal_ready()
        # expired 状态不加到期日限制：查至 10-25 时到期日之后的 10-21、10-22
        # 组内场次同样作为候选，按日期与标识升序。
        result = self.app.training_renewal_options(self._renewal_groups(), "2026-10-18", "2026-10-25", ["M-001"])
        alert = result["alerts"][0]
        self.assertEqual(alert["status"], "expired")
        self.assertEqual([c["activity_id"] for c in alert["candidates"]],
                         ["A-002", "A-003", "A-006", "A-007"])
        for candidate in alert["candidates"]:
            self.assertEqual(candidate["available"], candidate["remaining_seats"] > 0)

    def test_renewal_options_valid_group_produces_no_alert_and_empty_selection_validates(self):
        self._renewal_ready()
        groups = self._renewal_groups(valid_days=30)
        # 有效期 30 天：小陈 10-15 完成后在整个窗口内仍然有效，无提醒；小林、小周
        # 无合格记录仍各有一条提醒。
        result = self.app.training_renewal_options(groups, "2026-10-18", "2026-10-20", ["M-001"])
        self.assertEqual(result, {"as_of": "2026-10-18", "through_on": "2026-10-20", "alerts": []})
        # 成员选择为空数组时 alerts 为空，但全部输入引用仍须校验。
        empty = self.app.training_renewal_options(groups, "2026-10-18", "2026-10-20", [])
        self.assertEqual(empty["alerts"], [])
        with self.assertRaises(ValueError):
            self.app.training_renewal_options(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}], "2026-10-18", "2026-10-20", [])

    def test_renewal_options_rejections_match_alerts(self):
        self._renewal_ready()
        valid_groups = self._renewal_groups()
        for groups in [None, [], "x", [{}],
                      [{"group_id": "G", "activity_ids": ["A-001"]}],
                      [{"group_id": "G", "valid_days": 3}],
                      [{"activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "extra": 1}],
                      [{"group_id": "  ", "activity_ids": ["A-001"], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": None, "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": [], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001", " A-001 "], "valid_days": 3}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": True}],
                      [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 0}],
                      [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                      valid_groups + [{"group_id": "G2", "activity_ids": ["A-001"], "valid_days": 3}]]:
            with self.assertRaises(ValueError, msg=groups):
                self.app.training_renewal_options(groups, "2026-10-18", "2026-10-20")
        for as_of, through_on in [("2026-02-30", "2026-10-20"), (" 2026-10-18", "2026-10-20"),
                                  ("2026-10-21", "2026-10-20"), ("2026-10-18", None)]:
            with self.assertRaises(ValueError, msg=(as_of, through_on)):
                self.app.training_renewal_options(valid_groups, as_of, through_on)
        for member_ids in ["M-001", [" "], ["M-001", " M-001 "], ["M-001", "GHOST"]]:
            with self.assertRaises(ValueError, msg=member_ids):
                self.app.training_renewal_options(valid_groups, "2026-10-18", "2026-10-20", member_ids)

    def test_renewal_options_never_writes_legacy_and_broken_history(self):
        self._renewal_ready()
        before = self.app.path.read_bytes()
        self.app.training_renewal_options(self._renewal_groups(), "2026-10-18", "2026-10-20")
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.training_renewal_options(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}], "2026-10-18", "2026-10-20")
        self.assertEqual(before, self.app.path.read_bytes())
        # Legacy data without completions: every enrollment is pending and the
        # groups are still evaluated for candidates.
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        legacy = self.app.training_renewal_options(self._renewal_groups(), "2026-10-18", "2026-10-20")
        self.assertEqual([a["status"] for a in legacy["alerts"]], ["pending", "pending", "pending"])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        # Broken history is rejected wholesale and a read OSError stays an OSError.
        broken = json.loads(self.app.path.read_text(encoding="utf-8"))
        broken["members"]["M-002"]["name"] = "   "
        self.app.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.training_renewal_options(self._renewal_groups(), "2026-10-18", "2026-10-20")
        self.app.path.unlink()
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.training_renewal_options(self._renewal_groups(), "2026-10-18", "2026-10-20")
        # A query against a missing directory neither creates it nor writes a file.
        empty = self.root / "empty-renewal"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.training_renewal_options(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-18", "2026-10-20")
        self.assertFalse(empty.exists())

    def test_cli_renewal_options_success_failure_and_partial_array(self):
        self._renewal_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "renewal-options", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": self._renewal_groups(), "as_of": "2026-10-18", "through_on": "2026-10-20", "member_ids": ["M-001"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(value["as_of"], "2026-10-18")
        self.assertEqual(value["alerts"][0]["status"], "expired")
        self.assertEqual([c["activity_id"] for c in value["alerts"][0]["candidates"]], ["A-002", "A-003"])
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                   "as_of": "2026-10-18", "through_on": "2026-10-20"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        partial = run([
            {"groups": self._renewal_groups(), "as_of": "2026-10-18", "through_on": "2026-10-20", "member_ids": []},
            {"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
             "as_of": "2026-10-18", "through_on": "2026-10-20"},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

if __name__ == "__main__":
    unittest.main()
