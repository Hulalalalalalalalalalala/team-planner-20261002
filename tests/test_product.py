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
        # 与 _timeline_ready 相同的基础数据，供日期范围人数核对使用。
        self._timeline_ready()

    def test_training_coverage_periods_counts_and_shortage(self):
        self._coverage_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        # 只选小陈、期望两人：15 至 17 日合格 1 人、欠缺 1 人，其余日期合格
        # 0 人、欠缺 2 人；范围包含两端。
        result = self.app.training_coverage(groups, "2026-10-14", "2026-10-19", 2, ["M-001"])
        self.assertEqual(result, {
            "from_on": "2026-10-14",
            "to_on": "2026-10-19",
            "minimum_count": 2,
            "periods": [
                {"from_on": "2026-10-14", "to_on": "2026-10-14", "valid_member_ids": [], "valid_count": 0, "shortage_count": 2},
                {"from_on": "2026-10-15", "to_on": "2026-10-17", "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 1},
                {"from_on": "2026-10-18", "to_on": "2026-10-19", "valid_member_ids": [], "valid_count": 0, "shortage_count": 2},
            ],
        })
        # 起止相等表示查询一天。
        single = self.app.training_coverage(groups, "2026-10-16", "2026-10-16", 1, ["M-001"])
        self.assertEqual(single["periods"], [
            {"from_on": "2026-10-16", "to_on": "2026-10-16", "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
        ])

    def test_training_coverage_all_groups_must_be_valid(self):
        self._coverage_ready()
        # 小陈入门 10-15 完成（有效十天至 10-24），进阶 10-20 完成（有效五天
        # 至 10-24）：只有两组同日都有效的 20 至 24 日才计为合格。
        groups = [
            {"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 10},
            {"group_id": "G-ADV", "activity_ids": ["A-003", "A-004"], "valid_days": 5},
        ]
        result = self.app.training_coverage(groups, "2026-10-14", "2026-10-26", 1, ["M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-19", "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
            {"from_on": "2026-10-20", "to_on": "2026-10-24", "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-25", "to_on": "2026-10-26", "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
        ])

    def test_training_coverage_record_change_does_not_split(self):
        self._coverage_ready()
        # 小陈再在入门二登记 10-18 的完成记录：完成依据改变但合格人员不变，
        # 15 至 20 日合为一段。
        self.app.enroll("A-002", "M-001")
        self.app.record_completion("A-002", "M-001", "2026-10-18")
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        result = self.app.training_coverage(groups, "2026-10-14", "2026-10-22", 1, ["M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-14", "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
            {"from_on": "2026-10-15", "to_on": "2026-10-20", "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-21", "to_on": "2026-10-22", "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
        ])

    def test_training_coverage_personnel_swap_splits(self):
        self._coverage_ready()
        # 小林 10-18 完成入门二：15 至 17 日小陈合格、18 至 20 日换成小林，
        # 人数相同但人员替换，必须分段；标识按成员选择顺序排列。
        self.app.record_completion("A-002", "M-002", "2026-10-18")
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        result = self.app.training_coverage(groups, "2026-10-14", "2026-10-21", 1, ["M-002", "M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-14", "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-18", "to_on": "2026-10-20", "valid_member_ids": ["M-002"], "valid_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-21", "to_on": "2026-10-21", "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
        ])
        # 两人同时合格的日期各计一次，期望人数可以超过选中成员数。
        self.app.enroll("A-002", "M-001")
        self.app.record_completion("A-002", "M-001", "2026-10-18")
        both = [{"group_id": "G-ENTRY", "activity_ids": ["A-002"], "valid_days": 3}]
        result = self.app.training_coverage(both, "2026-10-18", "2026-10-20", 5, ["M-002", "M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-18", "to_on": "2026-10-20", "valid_member_ids": ["M-002", "M-001"], "valid_count": 2, "shortage_count": 3},
        ])

    def test_training_coverage_member_selection_and_empty(self):
        self._coverage_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        # 省略或 null 选择全员（按成员标识升序）；只有小陈在范围内合格。
        expected = [
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 1},
        ]
        self.assertEqual(self.app.training_coverage(groups, "2026-10-15", "2026-10-17", 2)["periods"], expected)
        self.assertEqual(self.app.training_coverage(groups, "2026-10-15", "2026-10-17", 2, None)["periods"], expected)
        # 空数组表示无人：仍返回一个覆盖全范围的区间，人数为零且欠缺数等于
        # 期望人数。
        empty = self.app.training_coverage(groups, "2026-10-14", "2026-10-19", 2, [])
        self.assertEqual(empty, {
            "from_on": "2026-10-14",
            "to_on": "2026-10-19",
            "minimum_count": 2,
            "periods": [
                {"from_on": "2026-10-14", "to_on": "2026-10-19", "valid_member_ids": [], "valid_count": 0, "shortage_count": 2},
            ],
        })

    def test_training_coverage_validity_beyond_date_max(self):
        self._coverage_ready()
        # 有效期越过 9999-12-31：只按查询范围给出人数，不报溢出错误。
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001"], "valid_days": 10 ** 12}]
        result = self.app.training_coverage(groups, "2026-10-14", "2026-10-20", 1, ["M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-14", "valid_member_ids": [], "valid_count": 0, "shortage_count": 1},
            {"from_on": "2026-10-15", "to_on": "2026-10-20", "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 0},
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
                self.app.training_coverage(groups, "2026-10-14", "2026-10-19", 2)
        for from_on, to_on in [("2026-02-30", "2026-10-19"), (" 2026-10-14", "2026-10-19"),
                               ("2026-10-14", "2026/10/19"), (20261014, "2026-10-19"),
                               ("2026-10-14", None), ("2026-10-20", "2026-10-19")]:
            with self.assertRaises(ValueError, msg=(from_on, to_on)):
                self.app.training_coverage(valid_groups, from_on, to_on, 2)
        # 期望人数必须是非布尔正整数，允许超过选中成员数。
        for minimum_count in [None, True, 0, -1, "2", 2.5]:
            with self.assertRaises(ValueError, msg=minimum_count):
                self.app.training_coverage(valid_groups, "2026-10-14", "2026-10-19", minimum_count)
        with self.assertRaises(TypeError):
            self.app.training_coverage(valid_groups, "2026-10-14", "2026-10-19")
        for member_ids in ["M-001", [" "], ["M-001", " M-001 "], ["M-001", "GHOST"]]:
            with self.assertRaises(ValueError, msg=member_ids):
                self.app.training_coverage(valid_groups, "2026-10-14", "2026-10-19", 2, member_ids)
        # 无人入选时仍校验全部引用。
        with self.assertRaises(ValueError):
            self.app.training_coverage([{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                                       "2026-10-14", "2026-10-19", 2, [])

    def test_training_coverage_never_writes_and_legacy_data(self):
        self._coverage_ready()
        before = self.app.path.read_bytes()
        self.app.training_coverage(
            [{"group_id": "G", "activity_ids": ["A-001", "A-002"], "valid_days": 3}], "2026-10-14", "2026-10-19", 2)
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.training_coverage(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}], "2026-10-14", "2026-10-19", 2)
        self.assertEqual(before, self.app.path.read_bytes())
        # 旧数据缺少 completions：按无记录处理，查询不补写该字段。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.training_coverage(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}],
            "2026-10-14", "2026-10-19", 2)
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-19", "valid_member_ids": [], "valid_count": 0, "shortage_count": 2},
        ])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        # 空目录上的失败查询不创建目录或文件。
        empty = self.root / "empty-coverage"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.training_coverage(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-14", "2026-10-19", 2)
        self.assertFalse(empty.exists())

    def test_training_coverage_broken_history_and_os_error(self):
        self._coverage_ready()
        broken = json.loads(self.app.path.read_text(encoding="utf-8"))
        broken["members"]["M-002"]["name"] = "   "
        self.app.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.training_coverage(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-14", "2026-10-19", 2)
        self.app.path.unlink()
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.training_coverage(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-14", "2026-10-19", 2)

    def test_training_coverage_matches_daily_validity(self):
        self._coverage_ready()
        groups = [
            {"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3},
            {"group_id": "G-ADV", "activity_ids": ["A-003", "A-004"], "valid_days": 5},
            {"group_id": "G-EXTRA", "activity_ids": ["A-005", "A-006"], "valid_days": 30},
        ]
        result = self.app.training_coverage(groups, "2026-10-10", "2026-10-26", 2)
        # 区间闭区间相接、完整覆盖查询范围且无重叠，按起日升序。
        periods = result["periods"]
        self.assertEqual(periods[0]["from_on"], "2026-10-10")
        self.assertEqual(periods[-1]["to_on"], "2026-10-26")
        for earlier, later in zip(periods, periods[1:]):
            self.assertEqual(
                date.fromisoformat(earlier["to_on"]) + timedelta(days=1),
                date.fromisoformat(later["from_on"]),
            )
            self.assertNotEqual(earlier["valid_member_ids"], later["valid_member_ids"])
        # 每一天的合格名单与当日 training-validity 逐组判定完全一致。
        day = date(2026, 10, 10)
        while day <= date(2026, 10, 26):
            daily = self.app.training_validity(groups, day.isoformat())
            expected = [
                member["member_id"]
                for member in daily["members"]
                if all(group["status"] == "valid" for group in member["groups"])
            ]
            period = next(p for p in periods if p["from_on"] <= day.isoformat() <= p["to_on"])
            self.assertEqual(period["valid_member_ids"], expected)
            self.assertEqual(period["valid_count"], len(expected))
            self.assertEqual(period["shortage_count"], max(0, 2 - len(expected)))
            day += timedelta(days=1)

    def test_cli_training_coverage_success_failure_and_partial_array(self):
        self._coverage_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "training-coverage", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": [{"group_id": " G-ENTRY ", "activity_ids": [" A-001 ", "A-002"], "valid_days": 3}],
                  "from_on": "2026-10-14", "to_on": "2026-10-19", "minimum_count": 2, "member_ids": ["M-001"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(value["minimum_count"], 2)
        self.assertEqual(value["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-14", "valid_member_ids": [], "valid_count": 0, "shortage_count": 2},
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "valid_member_ids": ["M-001"], "valid_count": 1, "shortage_count": 1},
            {"from_on": "2026-10-18", "to_on": "2026-10-19", "valid_member_ids": [], "valid_count": 0, "shortage_count": 2},
        ])
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                   "from_on": "2026-10-14", "to_on": "2026-10-19", "minimum_count": 2})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        partial = run([
            {"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
             "from_on": "2026-10-14", "to_on": "2026-10-19", "minimum_count": 2, "member_ids": []},
            {"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
             "from_on": "2026-10-14", "to_on": "2026-10-19", "minimum_count": 2},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def _absence_ready(self):
        # 与 _coverage_ready 相同的基础数据，供拟请假预览使用。
        self._coverage_ready()

    def test_preview_absence_coverage_example(self):
        self._absence_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        # 小陈 2026-10-15 完成全部组且有效三天，只在 16 日请假、期望一人：
        # 15、17 日可出勤一人，16、18 日欠缺一人，18 日合格人数也为零。
        result = self.app.preview_absence_coverage(
            groups, "2026-10-15", "2026-10-18", 1,
            [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16"}], ["M-001"])
        self.assertEqual(result, {
            "from_on": "2026-10-15",
            "to_on": "2026-10-18",
            "minimum_count": 1,
            "periods": [
                {"from_on": "2026-10-15", "to_on": "2026-10-15", "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
                {"from_on": "2026-10-16", "to_on": "2026-10-16", "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": ["M-001"], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
                {"from_on": "2026-10-17", "to_on": "2026-10-17", "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
                {"from_on": "2026-10-18", "to_on": "2026-10-18", "valid_member_ids": [], "valid_count": 0,
                 "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
            ],
        })

    def test_preview_absence_coverage_union_and_out_of_range(self):
        self._absence_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        # 重复、重叠、相邻的请假按日期并集处理，不重复扣人；范围外的日期及
        # 未入选的已知成员不影响人数。
        absences = [
            {"member_id": " M-001 ", "from_on": "2026-10-16", "to_on": "2026-10-17"},
            {"member_id": "M-001", "from_on": "2026-10-17", "to_on": "2026-10-18"},
            {"member_id": "M-001", "from_on": "2026-10-19", "to_on": "2026-10-19"},
            {"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16"},
            {"member_id": "M-001", "from_on": "2026-11-01", "to_on": "2026-11-02"},
            {"member_id": "M-002", "from_on": "2026-10-15", "to_on": "2026-10-17"},
        ]
        result = self.app.preview_absence_coverage(groups, "2026-10-15", "2026-10-20", 1, absences, ["M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-15", "valid_member_ids": ["M-001"], "valid_count": 1,
             "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-16", "to_on": "2026-10-17", "valid_member_ids": ["M-001"], "valid_count": 1,
             "absent_member_ids": ["M-001"], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
            # 18、19 日小陈在请假但已不合格：请假名单只收录合格者中的请假者，
            # 与 20 日合为一段。
            {"from_on": "2026-10-18", "to_on": "2026-10-20", "valid_member_ids": [], "valid_count": 0,
             "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
        ])

    def test_preview_absence_coverage_personnel_swap_and_empty_absences(self):
        self._absence_ready()
        # 小林 10-18 完成入门二：18 至 20 日换成小林合格，人数相同仍分段；
        # 小林 19 日请假只影响他自己的区间。
        self.app.record_completion("A-002", "M-002", "2026-10-18")
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        result = self.app.preview_absence_coverage(
            groups, "2026-10-15", "2026-10-20", 1,
            [{"member_id": "M-002", "from_on": "2026-10-19", "to_on": "2026-10-19"}], ["M-002", "M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "valid_member_ids": ["M-001"], "valid_count": 1,
             "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-18", "to_on": "2026-10-18", "valid_member_ids": ["M-002"], "valid_count": 1,
             "absent_member_ids": [], "available_member_ids": ["M-002"], "available_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-19", "to_on": "2026-10-19", "valid_member_ids": ["M-002"], "valid_count": 1,
             "absent_member_ids": ["M-002"], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
            {"from_on": "2026-10-20", "to_on": "2026-10-20", "valid_member_ids": ["M-002"], "valid_count": 1,
             "absent_member_ids": [], "available_member_ids": ["M-002"], "available_count": 1, "shortage_count": 0},
        ])
        # 空请假数组与 training-coverage 的名单和人数一致，仅多出请假相关字段。
        plain = self.app.training_coverage(groups, "2026-10-15", "2026-10-20", 1, ["M-002", "M-001"])
        preview = self.app.preview_absence_coverage(groups, "2026-10-15", "2026-10-20", 1, [], ["M-002", "M-001"])
        self.assertEqual(
            [{k: v for k, v in period.items() if k in ("from_on", "to_on", "valid_member_ids", "valid_count", "shortage_count")}
             for period in preview["periods"]],
            plain["periods"])
        for period in preview["periods"]:
            self.assertEqual(period["absent_member_ids"], [])
            self.assertEqual(period["available_member_ids"], period["valid_member_ids"])
            self.assertEqual(period["available_count"], period["valid_count"])

    def test_preview_absence_coverage_member_selection_and_empty(self):
        self._absence_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        # 省略或 null 选择全员（按成员标识升序）；只有小陈在范围内合格，未合格
        # 成员的请假不出现在请假名单中。
        absences = [{"member_id": "M-002", "from_on": "2026-10-15", "to_on": "2026-10-17"}]
        expected = [
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "valid_member_ids": ["M-001"], "valid_count": 1,
             "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 1},
        ]
        self.assertEqual(self.app.preview_absence_coverage(groups, "2026-10-15", "2026-10-17", 2, absences)["periods"], expected)
        self.assertEqual(self.app.preview_absence_coverage(groups, "2026-10-15", "2026-10-17", 2, absences, None)["periods"], expected)
        # 空数组表示无人：仍返回一个覆盖全范围的区间，人数为零且欠缺数等于
        # 期望人数；未入选成员的请假仍须校验但不影响结果。
        empty = self.app.preview_absence_coverage(groups, "2026-10-14", "2026-10-19", 2, absences, [])
        self.assertEqual(empty, {
            "from_on": "2026-10-14",
            "to_on": "2026-10-19",
            "minimum_count": 2,
            "periods": [
                {"from_on": "2026-10-14", "to_on": "2026-10-19", "valid_member_ids": [], "valid_count": 0,
                 "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 2},
            ],
        })

    def test_preview_absence_coverage_rejections(self):
        self._absence_ready()
        valid_groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        valid_absences = [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16"}]
        # 缺少 absences 或显式 null、非数组均被拒绝；空数组合法。
        with self.assertRaises(ValueError):
            self.app.preview_absence_coverage(valid_groups, "2026-10-14", "2026-10-19", 2)
        for absences in [None, "x", 1, {}]:
            with self.assertRaises(ValueError, msg=absences):
                self.app.preview_absence_coverage(valid_groups, "2026-10-14", "2026-10-19", 2, absences)
        # 条目字段不符、标识非法、日期非法或逆序、未知成员均被拒绝。
        for absences in [[{}],
                         [{"member_id": "M-001", "from_on": "2026-10-16"}],
                         [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16", "extra": 1}],
                         [{"member_id": " ", "from_on": "2026-10-16", "to_on": "2026-10-16"}],
                         [{"member_id": 123, "from_on": "2026-10-16", "to_on": "2026-10-16"}],
                         [{"member_id": "GHOST", "from_on": "2026-10-16", "to_on": "2026-10-16"}],
                         [{"member_id": "M-001", "from_on": "2026-02-30", "to_on": "2026-10-16"}],
                         [{"member_id": "M-001", "from_on": " 2026-10-16", "to_on": "2026-10-16"}],
                         [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": None}],
                         [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": 20261016}],
                         [{"member_id": "M-001", "from_on": "2026-10-17", "to_on": "2026-10-16"}]]:
            with self.assertRaises(ValueError, msg=absences):
                self.app.preview_absence_coverage(valid_groups, "2026-10-14", "2026-10-19", 2, absences)
        # 无人入选或请假在范围外时仍校验全部输入：未知活动、未知成员、非法日期。
        with self.assertRaises(ValueError):
            self.app.preview_absence_coverage(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                "2026-10-14", "2026-10-19", 2, valid_absences, [])
        with self.assertRaises(ValueError):
            self.app.preview_absence_coverage(valid_groups, "2026-10-14", "2026-10-19", 2,
                                              [{"member_id": "GHOST", "from_on": "2026-11-01", "to_on": "2026-11-02"}], [])
        with self.assertRaises(ValueError):
            self.app.preview_absence_coverage(valid_groups, "2026-10-14", "2026-10-19", 2,
                                              [{"member_id": "M-001", "from_on": "2026-11-02", "to_on": "2026-11-01"}], [])
        # 沿用 training-coverage 的既有校验：分组、范围日期、期望人数、成员选择。
        with self.assertRaises(ValueError):
            self.app.preview_absence_coverage([], "2026-10-14", "2026-10-19", 2, valid_absences)
        with self.assertRaises(ValueError):
            self.app.preview_absence_coverage(valid_groups, "2026-10-20", "2026-10-19", 2, valid_absences)
        with self.assertRaises(ValueError):
            self.app.preview_absence_coverage(valid_groups, "2026-10-14", "2026-10-19", True, valid_absences)
        with self.assertRaises(ValueError):
            self.app.preview_absence_coverage(valid_groups, "2026-10-14", "2026-10-19", 2, valid_absences, ["M-001", " M-001 "])

    def test_preview_absence_coverage_never_writes_and_legacy_data(self):
        self._absence_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        absences = [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16"}]
        before = self.app.path.read_bytes()
        self.app.preview_absence_coverage(groups, "2026-10-14", "2026-10-19", 2, absences)
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.preview_absence_coverage(groups, "2026-10-14", "2026-10-19", 2,
                                              [{"member_id": "GHOST", "from_on": "2026-10-16", "to_on": "2026-10-16"}])
        self.assertEqual(before, self.app.path.read_bytes())
        # 旧数据缺少 completions：按无记录处理，查询不补写该字段。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.preview_absence_coverage(groups, "2026-10-14", "2026-10-19", 2, absences)
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-19", "valid_member_ids": [], "valid_count": 0,
             "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 2},
        ])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        # 空目录上的失败查询不创建目录或文件。
        empty = self.root / "empty-absence"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.preview_absence_coverage(groups, "2026-10-14", "2026-10-19", 2, absences)
        self.assertFalse(empty.exists())

    def test_preview_absence_coverage_broken_history_and_os_error(self):
        self._absence_ready()
        groups = [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}]
        absences = [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16"}]
        broken = json.loads(self.app.path.read_text(encoding="utf-8"))
        broken["members"]["M-002"]["name"] = "   "
        self.app.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.preview_absence_coverage(groups, "2026-10-14", "2026-10-19", 2, absences)
        self.app.path.unlink()
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.preview_absence_coverage(groups, "2026-10-14", "2026-10-19", 2, absences)

    def test_cli_preview_absence_coverage_success_failure_and_partial_array(self):
        self._absence_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "preview-absence-coverage", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": [{"group_id": " G-ENTRY ", "activity_ids": [" A-001 ", "A-002"], "valid_days": 3}],
                  "from_on": "2026-10-15", "to_on": "2026-10-18", "minimum_count": 1,
                  "absences": [{"member_id": " M-001 ", "from_on": "2026-10-16", "to_on": "2026-10-16"}],
                  "member_ids": ["M-001"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(value["minimum_count"], 1)
        self.assertEqual(value["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-15", "valid_member_ids": ["M-001"], "valid_count": 1,
             "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-16", "to_on": "2026-10-16", "valid_member_ids": ["M-001"], "valid_count": 1,
             "absent_member_ids": ["M-001"], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
            {"from_on": "2026-10-17", "to_on": "2026-10-17", "valid_member_ids": ["M-001"], "valid_count": 1,
             "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
            {"from_on": "2026-10-18", "to_on": "2026-10-18", "valid_member_ids": [], "valid_count": 0,
             "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
        ])
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
                   "from_on": "2026-10-15", "to_on": "2026-10-18", "minimum_count": 1})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        partial = run([
            {"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
             "from_on": "2026-10-15", "to_on": "2026-10-18", "minimum_count": 1, "absences": [], "member_ids": []},
            {"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
             "from_on": "2026-10-15", "to_on": "2026-10-18", "minimum_count": 1,
             "absences": [{"member_id": "GHOST", "from_on": "2026-10-16", "to_on": "2026-10-16"}]},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def _group_coverage_ready(self):
        # 与 _absence_ready 相同的基础数据，另让小林 2026-10-15 完成入门二：
        # 小陈仅在安全组（A-001）有效，小林仅在协作组（A-002）有效。
        self._absence_ready()
        self.app.record_completion("A-002", "M-002", "2026-10-15")

    def test_group_coverage_example(self):
        self._group_coverage_ready()
        groups = [
            {"group_id": "G-SAFE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
            {"group_id": "G-COLLAB", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1},
        ]
        # 2026-10-15 小陈仅安全组有效、小林仅协作组有效，两组各需一人且无人
        # 请假时满足要求。
        result = self.app.group_coverage(groups, "2026-10-15", "2026-10-15", [])
        self.assertEqual(result, {
            "from_on": "2026-10-15",
            "to_on": "2026-10-15",
            "periods": [
                {"from_on": "2026-10-15", "to_on": "2026-10-15", "meets_requirements": True, "groups": [
                    {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                     "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
                    {"group_id": "G-COLLAB", "minimum_count": 1, "valid_member_ids": ["M-002"], "valid_count": 1,
                     "absent_member_ids": [], "available_member_ids": ["M-002"], "available_count": 1, "shortage_count": 0},
                ]},
            ],
        })
        # 小陈当天请假后安全组欠缺一人，整体不满足；协作组不受影响。
        absent = self.app.group_coverage(
            groups, "2026-10-15", "2026-10-15",
            [{"member_id": "M-001", "from_on": "2026-10-15", "to_on": "2026-10-15"}])
        period = absent["periods"][0]
        self.assertEqual(period["groups"][0], {
            "group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
            "absent_member_ids": ["M-001"], "available_member_ids": [], "available_count": 0, "shortage_count": 1})
        self.assertEqual(period["groups"][1]["shortage_count"], 0)
        self.assertFalse(period["meets_requirements"])

    def test_group_coverage_groups_judged_independently(self):
        self._group_coverage_ready()
        # 与 training_coverage 不同：不要求同一人的所有组同时有效。小陈仅安全
        # 组有效、小林仅协作组有效，两人分别进入各组合格名单；期望人数允许
        # 超过选中人数。
        groups = [
            {"group_id": "G-SAFE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
            {"group_id": "G-COLLAB", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 5},
        ]
        result = self.app.group_coverage(groups, "2026-10-15", "2026-10-17", [], ["M-001", "M-002"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "meets_requirements": False, "groups": [
                {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
                {"group_id": "G-COLLAB", "minimum_count": 5, "valid_member_ids": ["M-002"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-002"], "available_count": 1, "shortage_count": 4},
            ]},
        ])
        # 同一人可计入多个组：小陈也完成入门二后同时进入两组合格名单。
        self.app.enroll("A-002", "M-001")
        self.app.record_completion("A-002", "M-001", "2026-10-16")
        both = self.app.group_coverage(
            [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3, "minimum_count": 1},
             {"group_id": "G-EXTRA", "activity_ids": ["A-005"], "valid_days": 3, "minimum_count": 1}],
            "2026-10-16", "2026-10-16", [], ["M-001"])
        # 小陈在入门组合格；补训组未报名不合格。
        self.assertEqual(both["periods"][0]["groups"][0]["valid_member_ids"], ["M-001"])
        self.assertEqual(both["periods"][0]["groups"][1]["valid_member_ids"], [])
        multi = self.app.group_coverage(
            [{"group_id": "G-ONE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
             {"group_id": "G-TWO", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1}],
            "2026-10-16", "2026-10-17", [], ["M-001"])
        self.assertEqual(multi["periods"][0]["groups"][0]["valid_member_ids"], ["M-001"])
        self.assertEqual(multi["periods"][0]["groups"][1]["valid_member_ids"], ["M-001"])
        self.assertTrue(multi["periods"][0]["meets_requirements"])

    def test_group_coverage_periods_merge_split_and_absence_union(self):
        self._group_coverage_ready()
        groups = [
            {"group_id": "G-SAFE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
            {"group_id": "G-COLLAB", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1},
        ]
        # 重复、重叠、相邻的请假按日期并集处理；范围外的请假与未入选成员的
        # 请假不影响人数。小陈 16 至 17 日请假，小林 19 日请假（已不合格）。
        absences = [
            {"member_id": " M-001 ", "from_on": "2026-10-16", "to_on": "2026-10-17"},
            {"member_id": "M-001", "from_on": "2026-10-17", "to_on": "2026-10-17"},
            {"member_id": "M-002", "from_on": "2026-10-19", "to_on": "2026-10-19"},
            {"member_id": "M-001", "from_on": "2026-11-01", "to_on": "2026-11-02"},
            {"member_id": "M-003", "from_on": "2026-10-15", "to_on": "2026-10-17"},
        ]
        result = self.app.group_coverage(groups, "2026-10-15", "2026-10-19", absences, ["M-001", "M-002"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-15", "meets_requirements": True, "groups": [
                {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
                {"group_id": "G-COLLAB", "minimum_count": 1, "valid_member_ids": ["M-002"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-002"], "available_count": 1, "shortage_count": 0},
            ]},
            {"from_on": "2026-10-16", "to_on": "2026-10-17", "meets_requirements": False, "groups": [
                {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": ["M-001"], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
                {"group_id": "G-COLLAB", "minimum_count": 1, "valid_member_ids": ["M-002"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-002"], "available_count": 1, "shortage_count": 0},
            ]},
            # 18 日起两组均无人合格；小林 19 日的请假不进入名单（他已不合格），
            # 18、19 日各组名单均相同，合为一段。
            {"from_on": "2026-10-18", "to_on": "2026-10-19", "meets_requirements": False, "groups": [
                {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": [], "valid_count": 0,
                 "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
                {"group_id": "G-COLLAB", "minimum_count": 1, "valid_member_ids": [], "valid_count": 0,
                 "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
            ]},
        ])

    def test_group_coverage_personnel_swap_splits_record_change_does_not(self):
        self._group_coverage_ready()
        # 人数相同但人员更换仍分段：入门组 15 至 17 日小陈合格，18 至 19 日
        # 换成小林（小林 10-15 完成、有效三天至 17 日……改用 10-18 完成）。
        self.app.correct_completions([{"activity_id": "A-002", "member_id": "M-002", "completed_on": "2026-10-18"}])
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3, "minimum_count": 1}]
        result = self.app.group_coverage(groups, "2026-10-15", "2026-10-20", [], ["M-002", "M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "meets_requirements": True, "groups": [
                {"group_id": "G-ENTRY", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
            ]},
            {"from_on": "2026-10-18", "to_on": "2026-10-20", "meets_requirements": True, "groups": [
                {"group_id": "G-ENTRY", "minimum_count": 1, "valid_member_ids": ["M-002"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-002"], "available_count": 1, "shortage_count": 0},
            ]},
        ])
        # 仅完成依据变化不分段：小陈再登记入门二 10-18 的完成记录，合格人员
        # 不变，15 至 20 日合为一段。
        self.app.enroll("A-002", "M-001")
        self.app.record_completion("A-002", "M-001", "2026-10-18")
        merged = self.app.group_coverage(groups, "2026-10-15", "2026-10-20", [], ["M-001"])
        self.assertEqual(merged["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-20", "meets_requirements": True, "groups": [
                {"group_id": "G-ENTRY", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
            ]},
        ])

    def test_group_coverage_member_selection_and_empty(self):
        self._group_coverage_ready()
        groups = [
            {"group_id": "G-SAFE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
            {"group_id": "G-COLLAB", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 2},
        ]
        # 省略或 null 选择全员（按成员标识升序）；未合格成员的请假不出现在
        # 请假名单中。
        absences = [{"member_id": "M-003", "from_on": "2026-10-15", "to_on": "2026-10-17"}]
        expected = [
            {"from_on": "2026-10-15", "to_on": "2026-10-17", "meets_requirements": False, "groups": [
                {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
                {"group_id": "G-COLLAB", "minimum_count": 2, "valid_member_ids": ["M-002"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-002"], "available_count": 1, "shortage_count": 1},
            ]},
        ]
        self.assertEqual(self.app.group_coverage(groups, "2026-10-15", "2026-10-17", absences)["periods"], expected)
        self.assertEqual(self.app.group_coverage(groups, "2026-10-15", "2026-10-17", absences, None)["periods"], expected)
        # 空数组表示无人：仍返回一个覆盖全范围的区间，各组人数为零且欠缺数
        # 等于各自的要求人数；未入选成员的请假仍须校验但不影响结果。
        empty = self.app.group_coverage(groups, "2026-10-14", "2026-10-19", absences, [])
        self.assertEqual(empty, {
            "from_on": "2026-10-14",
            "to_on": "2026-10-19",
            "periods": [
                {"from_on": "2026-10-14", "to_on": "2026-10-19", "meets_requirements": False, "groups": [
                    {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": [], "valid_count": 0,
                     "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
                    {"group_id": "G-COLLAB", "minimum_count": 2, "valid_member_ids": [], "valid_count": 0,
                     "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 2},
                ]},
            ],
        })

    def test_group_coverage_validity_beyond_date_max(self):
        self._group_coverage_ready()
        # 有效期越过 9999-12-31：只按查询范围给出人数，不报溢出错误。
        groups = [{"group_id": "G-SAFE", "activity_ids": ["A-001"], "valid_days": 10 ** 12, "minimum_count": 1}]
        result = self.app.group_coverage(groups, "2026-10-14", "2026-10-20", [], ["M-001"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-14", "meets_requirements": False, "groups": [
                {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": [], "valid_count": 0,
                 "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
            ]},
            {"from_on": "2026-10-15", "to_on": "2026-10-20", "meets_requirements": True, "groups": [
                {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
            ]},
        ])

    def test_group_coverage_rejections(self):
        self._group_coverage_ready()
        valid_groups = [
            {"group_id": "G-SAFE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
            {"group_id": "G-COLLAB", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1},
        ]
        valid_absences = [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16"}]
        # 缺少 absences 或显式 null、非数组均被拒绝；空数组合法。
        with self.assertRaises(ValueError):
            self.app.group_coverage(valid_groups, "2026-10-14", "2026-10-19")
        for absences in [None, "x", 1, {}]:
            with self.assertRaises(ValueError, msg=absences):
                self.app.group_coverage(valid_groups, "2026-10-14", "2026-10-19", absences)
        # 组条目缺少 minimum_count、带多余字段或人数非法均被拒绝；true 不算 1。
        for groups in [[{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
                       [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1, "extra": 1}],
                       [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": True}],
                       [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 0}],
                       [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": -1}],
                       [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1.5}],
                       [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": "1"}],
                       [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": None}]]:
            with self.assertRaises(ValueError, msg=groups):
                self.app.group_coverage(groups, "2026-10-14", "2026-10-19", valid_absences)
        # 沿用 preview-absence-coverage 的既有校验：分组去重、活动跨组重复、
        # 未知活动、范围日期逆序、请假条目、成员选择。
        with self.assertRaises(ValueError):
            self.app.group_coverage([], "2026-10-14", "2026-10-19", valid_absences)
        with self.assertRaises(ValueError):
            self.app.group_coverage(
                [dict(valid_groups[0]), dict(valid_groups[0])], "2026-10-14", "2026-10-19", valid_absences)
        with self.assertRaises(ValueError):
            self.app.group_coverage(
                [{"group_id": "G-ONE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
                 {"group_id": "G-TWO", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1}],
                "2026-10-14", "2026-10-19", valid_absences)
        with self.assertRaises(ValueError):
            self.app.group_coverage(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3, "minimum_count": 1}],
                "2026-10-14", "2026-10-19", valid_absences)
        with self.assertRaises(ValueError):
            self.app.group_coverage(valid_groups, "2026-10-20", "2026-10-19", valid_absences)
        with self.assertRaises(ValueError):
            self.app.group_coverage(valid_groups, "2026-10-14", "2026-10-19",
                                    [{"member_id": "GHOST", "from_on": "2026-10-16", "to_on": "2026-10-16"}])
        with self.assertRaises(ValueError):
            self.app.group_coverage(valid_groups, "2026-10-14", "2026-10-19",
                                    [{"member_id": "M-001", "from_on": "2026-10-17", "to_on": "2026-10-16"}])
        with self.assertRaises(ValueError):
            self.app.group_coverage(valid_groups, "2026-10-14", "2026-10-19", valid_absences, ["M-001", " M-001 "])
        with self.assertRaises(ValueError):
            self.app.group_coverage(valid_groups, "2026-10-14", "2026-10-19", valid_absences, ["GHOST"])
        # 无人入选或请假在范围外时仍校验全部输入。
        with self.assertRaises(ValueError):
            self.app.group_coverage(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3, "minimum_count": 1}],
                "2026-10-14", "2026-10-19", valid_absences, [])
        with self.assertRaises(ValueError):
            self.app.group_coverage(valid_groups, "2026-10-14", "2026-10-19",
                                    [{"member_id": "GHOST", "from_on": "2026-11-01", "to_on": "2026-11-02"}], [])
        # 顶层人数参数不被接受。
        with self.assertRaises(TypeError):
            self.app.group_coverage(valid_groups, "2026-10-14", "2026-10-19", valid_absences, None, 1)

    def test_group_coverage_never_writes_and_legacy_data(self):
        self._group_coverage_ready()
        groups = [{"group_id": "G-SAFE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1}]
        absences = [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16"}]
        before = self.app.path.read_bytes()
        self.app.group_coverage(groups, "2026-10-14", "2026-10-19", absences)
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.group_coverage(groups, "2026-10-14", "2026-10-19",
                                    [{"member_id": "GHOST", "from_on": "2026-10-16", "to_on": "2026-10-16"}])
        self.assertEqual(before, self.app.path.read_bytes())
        # 旧数据缺少 completions：按无记录处理，查询不补写该字段。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.group_coverage(groups, "2026-10-14", "2026-10-19", absences)
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-19", "meets_requirements": False, "groups": [
                {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": [], "valid_count": 0,
                 "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
            ]},
        ])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        # 空目录上的失败查询不创建目录或文件。
        empty = self.root / "empty-group-coverage"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.group_coverage(groups, "2026-10-14", "2026-10-19", absences)
        self.assertFalse(empty.exists())

    def test_group_coverage_broken_history_and_os_error(self):
        self._group_coverage_ready()
        groups = [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1}]
        absences = [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16"}]
        broken = json.loads(self.app.path.read_text(encoding="utf-8"))
        broken["members"]["M-002"]["name"] = "   "
        self.app.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.group_coverage(groups, "2026-10-14", "2026-10-19", absences)
        self.app.path.unlink()
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.group_coverage(groups, "2026-10-14", "2026-10-19", absences)

    def test_cli_group_coverage_success_failure_and_partial_array(self):
        self._group_coverage_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "group-coverage", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": [{"group_id": " G-SAFE ", "activity_ids": [" A-001 "], "valid_days": 3, "minimum_count": 1},
                             {"group_id": "G-COLLAB", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1}],
                  "from_on": "2026-10-15", "to_on": "2026-10-15", "absences": []})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertNotIn("minimum_count", value)
        self.assertEqual(value["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-15", "meets_requirements": True, "groups": [
                {"group_id": "G-SAFE", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
                {"group_id": "G-COLLAB", "minimum_count": 1, "valid_member_ids": ["M-002"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-002"], "available_count": 1, "shortage_count": 0},
            ]},
        ])
        # 顶层人数参数不被接受。
        top_level = run({"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1}],
                         "from_on": "2026-10-15", "to_on": "2026-10-15", "minimum_count": 1, "absences": []})
        self.assertEqual(top_level.returncode, 2)
        self.assertIn("error", json.loads(top_level.stderr))
        self.assertEqual(top_level.stdout, "")
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}],
                   "from_on": "2026-10-15", "to_on": "2026-10-15", "absences": []})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        partial = run([
            {"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1}],
             "from_on": "2026-10-15", "to_on": "2026-10-15", "absences": [], "member_ids": []},
            {"groups": [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3, "minimum_count": 1}],
             "from_on": "2026-10-15", "to_on": "2026-10-15", "absences": []},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def _exclusive_group_coverage_ready(self):
        # 与 _group_coverage_ready 相同的基础数据，另让小陈 2026-10-15 完成
        # 入门二并撤销小林的完成记录：小陈当天在安全组与协作组均有效，
        # 小林两组均不合格。
        self._group_coverage_ready()
        self.app.enroll("A-002", "M-001")
        self.app.record_completion("A-002", "M-001", "2026-10-15")
        self.app.correct_completions([{"activity_id": "A-002", "member_id": "M-002", "completed_on": None}])

    def test_exclusive_group_coverage_shared_pool_example(self):
        self._exclusive_group_coverage_ready()
        groups = [
            {"group_id": "G-ONE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
            {"group_id": "G-TWO", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1},
        ]
        # 2026-10-15 仅小陈两组均有效且未请假：每组各自欠缺都为零，但同一人
        # 当天只能承担一个组岗位，整体填一岗、缺一岗且不满足。
        result = self.app.exclusive_group_coverage(groups, "2026-10-15", "2026-10-15", [], ["M-001"])
        self.assertEqual(result, {
            "from_on": "2026-10-15",
            "to_on": "2026-10-15",
            "periods": [
                {"from_on": "2026-10-15", "to_on": "2026-10-15",
                 "required_count": 2, "filled_count": 1, "shortage_count": 1, "meets_requirements": False, "groups": [
                    {"group_id": "G-ONE", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                     "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
                    {"group_id": "G-TWO", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                     "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
                ]},
            ],
        })
        # 增加仅第一组合格的小林后填满两岗：不能因先把小陈计入第一组而低估。
        self.app.enroll("A-001", "M-002")
        self.app.record_completion("A-001", "M-002", "2026-10-15")
        both = self.app.exclusive_group_coverage(groups, "2026-10-15", "2026-10-15", [], ["M-001", "M-002"])
        self.assertEqual(both["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-15",
             "required_count": 2, "filled_count": 2, "shortage_count": 0, "meets_requirements": True, "groups": [
                {"group_id": "G-ONE", "minimum_count": 1, "valid_member_ids": ["M-001", "M-002"], "valid_count": 2,
                 "absent_member_ids": [], "available_member_ids": ["M-001", "M-002"], "available_count": 2, "shortage_count": 0},
                {"group_id": "G-TWO", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
            ]},
        ])

    def test_exclusive_group_coverage_absence_splits_periods(self):
        self._exclusive_group_coverage_ready()
        self.app.enroll("A-001", "M-002")
        self.app.record_completion("A-001", "M-002", "2026-10-15")
        groups = [
            {"group_id": "G-ONE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
            {"group_id": "G-TWO", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1},
        ]
        # 小陈 16 日请假：15 日填满两岗，16 日协作组无人可出勤，整体只填一岗。
        result = self.app.exclusive_group_coverage(
            groups, "2026-10-15", "2026-10-16",
            [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16"}], ["M-001", "M-002"])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-15",
             "required_count": 2, "filled_count": 2, "shortage_count": 0, "meets_requirements": True, "groups": [
                {"group_id": "G-ONE", "minimum_count": 1, "valid_member_ids": ["M-001", "M-002"], "valid_count": 2,
                 "absent_member_ids": [], "available_member_ids": ["M-001", "M-002"], "available_count": 2, "shortage_count": 0},
                {"group_id": "G-TWO", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": [], "available_member_ids": ["M-001"], "available_count": 1, "shortage_count": 0},
            ]},
            {"from_on": "2026-10-16", "to_on": "2026-10-16",
             "required_count": 2, "filled_count": 1, "shortage_count": 1, "meets_requirements": False, "groups": [
                {"group_id": "G-ONE", "minimum_count": 1, "valid_member_ids": ["M-001", "M-002"], "valid_count": 2,
                 "absent_member_ids": ["M-001"], "available_member_ids": ["M-002"], "available_count": 1, "shortage_count": 0},
                {"group_id": "G-TWO", "minimum_count": 1, "valid_member_ids": ["M-001"], "valid_count": 1,
                 "absent_member_ids": ["M-001"], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
            ]},
        ])

    def test_exclusive_group_coverage_member_once_and_group_cap(self):
        self._exclusive_group_coverage_ready()
        # 小林两组都完成后，两人两组均合格：第一组需两人、第二组需一人，需求
        # 共三岗，但只有两人且每人每天最多承担一个岗位，填两岗缺一岗；各组
        # 原有欠缺仍按各自可出勤人数计算，都为零。
        self.app.enroll("A-001", "M-002")
        self.app.record_completion("A-001", "M-002", "2026-10-15")
        # 小林撤销完成记录后仍报名着入门二，直接重新登记完成即可。
        self.app.record_completion("A-002", "M-002", "2026-10-15")
        groups = [
            {"group_id": "G-ONE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 2},
            {"group_id": "G-TWO", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1},
        ]
        result = self.app.exclusive_group_coverage(groups, "2026-10-15", "2026-10-15", [], ["M-001", "M-002"])
        period = result["periods"][0]
        self.assertEqual([group["shortage_count"] for group in period["groups"]], [0, 0])
        self.assertEqual((period["required_count"], period["filled_count"], period["shortage_count"]), (3, 2, 1))
        self.assertFalse(period["meets_requirements"])
        # 每组最多计入本组需求人数：两组各需一人、两人均合格时填两岗而非四岗。
        capped = self.app.exclusive_group_coverage(
            [dict(groups[0], minimum_count=1), groups[1]], "2026-10-15", "2026-10-15", [], ["M-001", "M-002"])
        self.assertEqual((capped["periods"][0]["filled_count"], capped["periods"][0]["meets_requirements"]), (2, True))

    def test_exclusive_group_coverage_empty_selection(self):
        self._exclusive_group_coverage_ready()
        groups = [
            {"group_id": "G-ONE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
            {"group_id": "G-TWO", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 2},
        ]
        # 无人入选：仍校验全部输入和历史，返回覆盖全范围的一段，填岗数为零、
        # 整体欠缺等于需求总数。
        result = self.app.exclusive_group_coverage(groups, "2026-10-14", "2026-10-19", [], [])
        self.assertEqual(result["periods"], [
            {"from_on": "2026-10-14", "to_on": "2026-10-19",
             "required_count": 3, "filled_count": 0, "shortage_count": 3, "meets_requirements": False, "groups": [
                {"group_id": "G-ONE", "minimum_count": 1, "valid_member_ids": [], "valid_count": 0,
                 "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 1},
                {"group_id": "G-TWO", "minimum_count": 2, "valid_member_ids": [], "valid_count": 0,
                 "absent_member_ids": [], "available_member_ids": [], "available_count": 0, "shortage_count": 2},
            ]},
        ])
        with self.assertRaises(ValueError):
            self.app.exclusive_group_coverage(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3, "minimum_count": 1}],
                "2026-10-14", "2026-10-19", [], [])

    def test_exclusive_group_coverage_rejections(self):
        self._exclusive_group_coverage_ready()
        groups = [
            {"group_id": "G-ONE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
            {"group_id": "G-TWO", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1},
        ]
        # 校验与 group-coverage 完全一致：缺少 absences 或显式 null、逆序日期、
        # 未知或重复引用、条目字段缺失或多余均抛出 ValueError。
        with self.assertRaises(ValueError):
            self.app.exclusive_group_coverage(groups, "2026-10-15", "2026-10-15")
        with self.assertRaises(ValueError):
            self.app.exclusive_group_coverage(groups, "2026-10-15", "2026-10-15", None)
        with self.assertRaises(ValueError):
            self.app.exclusive_group_coverage(groups, "2026-10-16", "2026-10-15", [])
        with self.assertRaises(ValueError):
            self.app.exclusive_group_coverage(groups, "2026-10-15", "2026-10-15",
                                              [{"member_id": "GHOST", "from_on": "2026-10-15", "to_on": "2026-10-15"}])
        with self.assertRaises(ValueError):
            self.app.exclusive_group_coverage([dict(groups[0]), dict(groups[0])], "2026-10-15", "2026-10-15", [])
        with self.assertRaises(ValueError):
            self.app.exclusive_group_coverage(
                [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}], "2026-10-15", "2026-10-15", [])
        with self.assertRaises(ValueError):
            self.app.exclusive_group_coverage(groups, "2026-10-15", "2026-10-15", [], ["GHOST"])
        # 顶层人数参数不被接受。
        with self.assertRaises(TypeError):
            self.app.exclusive_group_coverage(groups, "2026-10-15", "2026-10-15", [], None, 1)

    def test_exclusive_group_coverage_never_writes(self):
        self._exclusive_group_coverage_ready()
        groups = [{"group_id": "G-ONE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1}]
        before = self.app.path.read_bytes()
        self.app.exclusive_group_coverage(groups, "2026-10-14", "2026-10-19", [])
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.exclusive_group_coverage(groups, "2026-10-14", "2026-10-19",
                                              [{"member_id": "GHOST", "from_on": "2026-10-16", "to_on": "2026-10-16"}])
        self.assertEqual(before, self.app.path.read_bytes())
        # 空目录上的失败查询不创建目录或文件。
        empty = self.root / "empty-exclusive-group-coverage"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.exclusive_group_coverage(groups, "2026-10-14", "2026-10-19",
                                           [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16"}])
        self.assertFalse(empty.exists())

    def test_cli_exclusive_group_coverage(self):
        self._exclusive_group_coverage_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "exclusive-group-coverage", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": [{"group_id": "G-ONE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
                             {"group_id": "G-TWO", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1}],
                  "from_on": "2026-10-15", "to_on": "2026-10-15", "absences": [], "member_ids": ["M-001"]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertNotIn("minimum_count", value)
        self.assertEqual(value["periods"][0]["required_count"], 2)
        self.assertEqual(value["periods"][0]["filled_count"], 1)
        self.assertEqual(value["periods"][0]["shortage_count"], 1)
        self.assertFalse(value["periods"][0]["meets_requirements"])
        # 顶层人数参数不被接受；失败仅标准错误含 error，标准输出为空。
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1}],
                   "from_on": "2026-10-15", "to_on": "2026-10-15", "minimum_count": 1, "absences": []})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def _start_options_ready(self):
        # 与 _absence_ready 相同的基础数据，另让小林 2026-10-15 完成入门二：
        # 有效五天时小陈、小林在 2026-10-15 至 19 日均合格。
        self._absence_ready()
        self.app.record_completion("A-002", "M-002", "2026-10-15")

    def test_absence_start_options_example(self):
        self._start_options_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 5}]
        # 小林 17 日请假、期望一人，小陈申请两天：起日只能是 15 或 18 日，
        # 请假者标识带首尾空白会先去除。
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1,
            [{"member_id": "M-002", "from_on": "2026-10-17", "to_on": "2026-10-17"}],
            " M-001 ", 2)
        self.assertEqual(result, {
            "from_on": "2026-10-15",
            "to_on": "2026-10-19",
            "minimum_count": 1,
            "member_id": "M-001",
            "days": 2,
            "start_windows": [
                {"from_on": "2026-10-15", "to_on": "2026-10-15"},
                {"from_on": "2026-10-18", "to_on": "2026-10-18"},
            ],
        })

    def test_absence_start_options_merging_and_no_deduction(self):
        self._absence_ready()
        # 小林 10-18 完成入门二：15 至 17 日小陈合格、18 至 20 日换成小林合格。
        self.app.record_completion("A-002", "M-002", "2026-10-18")
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 3}]
        # 小周从未合格，请假不扣人：每天可出勤人数都不变，连续可起日合并成
        # 一个最长区间，即使 18 日合格名单由小陈换成小林也不拆分。
        result = self.app.absence_start_options(groups, "2026-10-15", "2026-10-20", 1, [], "M-003", 2)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])

    def test_absence_start_options_already_absent_and_shortage(self):
        self._start_options_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 5}]
        # 本人当天已请假时不重复扣人：小陈 15 至 19 日均已请假，申请两天时
        # 每天仍只有小林一人在岗，全部起日可行。
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1,
            [{"member_id": "M-001", "from_on": "2026-10-15", "to_on": "2026-10-19"}],
            "M-001", 2)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-18"}])
        # 期望两人时小陈请假使每天只剩小林一人：无可起日。
        result = self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 2, [], "M-001", 1)
        self.assertEqual(result["start_windows"], [])

    def test_absence_start_options_days_overflow_and_date_max(self):
        self._start_options_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 5}]
        # days 超过查询天数时数组为空；等于查询天数时只有首日起得了。
        result = self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 6)
        self.assertEqual(result["start_windows"], [])
        result = self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 5)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-15"}])
        # 有效期长到溢出 date.max 时按整段合格处理；查询到 9999-12-31 也不溢出。
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 10**9}]
        result = self.app.absence_start_options(groups, "9999-12-25", "9999-12-31", 1, [], "M-001", 3)
        self.assertEqual(result["start_windows"], [{"from_on": "9999-12-25", "to_on": "9999-12-29"}])
        result = self.app.absence_start_options(groups, "9999-12-25", "9999-12-31", 1, [], "M-001", 10**9)
        self.assertEqual(result["start_windows"], [])
        # 请假一直排到范围最后一天：其前的起日仍然可行，末日之后不再计算。
        result = self.app.absence_start_options(
            groups, "9999-12-30", "9999-12-31", 1,
            [{"member_id": "M-002", "from_on": "9999-12-31", "to_on": "9999-12-31"}], "M-001", 1)
        self.assertEqual(result["start_windows"], [{"from_on": "9999-12-30", "to_on": "9999-12-30"}])

    def test_absence_start_options_rejections(self):
        self._start_options_ready()
        valid_groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 5}]
        valid_absences = [{"member_id": "M-002", "from_on": "2026-10-17", "to_on": "2026-10-17"}]
        # 缺少 member_id 或 days、标识空白或类型非法、days 非正整数或布尔均被拒绝。
        with self.assertRaises(ValueError):
            self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1, valid_absences)
        for member_id in [None, " ", 123, ["M-001"]]:
            with self.assertRaises(ValueError, msg=member_id):
                self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1, valid_absences, member_id, 2)
        for days in [None, 0, -1, True, "2", 2.0]:
            with self.assertRaises(ValueError, msg=days):
                self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1, valid_absences, "M-001", days)
        # 未知成员、未入选的成员（包括空选择）均被拒绝。
        with self.assertRaises(ValueError):
            self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1, valid_absences, "GHOST", 2)
        with self.assertRaises(ValueError):
            self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1, valid_absences, "M-001", 2, ["M-002"])
        with self.assertRaises(ValueError):
            self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1, valid_absences, "M-001", 2, [])
        # absences 省略或 null 拒绝，空数组合法；条目校验沿用拟请假预览。
        with self.assertRaises(ValueError):
            self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1, None, "M-001", 2)
        for absences in ["x", 1, {}]:
            with self.assertRaises(ValueError, msg=absences):
                self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1, absences, "M-001", 2)
        for absences in [[{}],
                         [{"member_id": "M-001", "from_on": "2026-10-16"}],
                         [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16", "extra": 1}],
                         [{"member_id": "GHOST", "from_on": "2026-10-16", "to_on": "2026-10-16"}],
                         [{"member_id": "M-001", "from_on": "2026-10-17", "to_on": "2026-10-16"}]]:
            with self.assertRaises(ValueError, msg=absences):
                self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1, absences, "M-001", 2)
        # 无可起日（days 超过查询天数）时仍校验全部输入：未知活动、未知成员。
        with self.assertRaises(ValueError):
            self.app.absence_start_options(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                "2026-10-15", "2026-10-19", 1, valid_absences, "M-001", 100)
        with self.assertRaises(ValueError):
            self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1,
                                           [{"member_id": "GHOST", "from_on": "2026-11-01", "to_on": "2026-11-02"}],
                                           "M-001", 100)
        # 沿用拟请假预览的既有校验：分组、逆序范围、期望人数、成员选择去重。
        with self.assertRaises(ValueError):
            self.app.absence_start_options([], "2026-10-15", "2026-10-19", 1, valid_absences, "M-001", 2)
        with self.assertRaises(ValueError):
            self.app.absence_start_options(valid_groups, "2026-10-20", "2026-10-19", 1, valid_absences, "M-001", 2)
        with self.assertRaises(ValueError):
            self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", True, valid_absences, "M-001", 2)
        with self.assertRaises(ValueError):
            self.app.absence_start_options(valid_groups, "2026-10-15", "2026-10-19", 1, valid_absences, "M-001", 2,
                                           ["M-001", " M-001 "])

    def test_absence_start_options_never_writes_and_legacy_data(self):
        self._start_options_ready()
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 5}]
        absences = [{"member_id": "M-002", "from_on": "2026-10-17", "to_on": "2026-10-17"}]
        before = self.app.path.read_bytes()
        self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, absences, "M-001", 2)
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, absences, "GHOST", 2)
        self.assertEqual(before, self.app.path.read_bytes())
        # 旧数据缺少 completions：按无记录处理，无人合格故无可起日，查询不补写该字段。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 2)
        self.assertEqual(result["start_windows"], [])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        # 空目录上的失败查询不创建目录或文件。
        empty = self.root / "empty-start-options"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, absences, "M-001", 2)
        self.assertFalse(empty.exists())

    def test_absence_start_options_broken_history_and_os_error(self):
        self._start_options_ready()
        groups = [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 3}]
        broken = json.loads(self.app.path.read_text(encoding="utf-8"))
        broken["members"]["M-002"]["name"] = "   "
        self.app.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 2)
        self.app.path.unlink()
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 2)

    def test_cli_absence_start_options_success_failure_and_partial_array(self):
        self._start_options_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "absence-start-options", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": [{"group_id": " G-ENTRY ", "activity_ids": [" A-001 ", "A-002"], "valid_days": 5}],
                  "from_on": "2026-10-15", "to_on": "2026-10-19", "minimum_count": 1,
                  "absences": [{"member_id": " M-002 ", "from_on": "2026-10-17", "to_on": "2026-10-17"}],
                  "member_id": " M-001 ", "days": 2})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), {
            "from_on": "2026-10-15",
            "to_on": "2026-10-19",
            "minimum_count": 1,
            "member_id": "M-001",
            "days": 2,
            "start_windows": [
                {"from_on": "2026-10-15", "to_on": "2026-10-15"},
                {"from_on": "2026-10-18", "to_on": "2026-10-18"},
            ],
        })
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 5}],
                   "from_on": "2026-10-15", "to_on": "2026-10-19", "minimum_count": 1,
                   "absences": [], "member_id": "GHOST", "days": 2})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        partial = run([
            {"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 5}],
             "from_on": "2026-10-15", "to_on": "2026-10-19", "minimum_count": 1,
             "absences": [], "member_id": "M-001", "days": 2},
            {"groups": [{"group_id": "G", "activity_ids": ["A-001"], "valid_days": 5}],
             "from_on": "2026-10-15", "to_on": "2026-10-19", "minimum_count": 1,
             "absences": [], "member_id": "M-001", "days": 0},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_absence_start_options_avoid_pending_training_example(self):
        self._start_options_ready()
        # 小陈已报名 17 日的待完成培训；两人在 15 至 19 日均合格、期望一人。
        self.app.create_activity("A-010", "十七号待完成", "2026-10-17", 2)
        self.app.enroll("A-010", "M-001")
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 5}]
        # 关闭或省略筛选时仍是连续的 15 至 18 日。
        result = self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 2)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-18"}])
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 2, avoid_pending_training=False)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-18"}])
        # 开启后覆盖 17 日的起日 16、17 被排除，只剩 15 日与 18 日两个单日区间。
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 2, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-15"},
            {"from_on": "2026-10-18", "to_on": "2026-10-18"},
        ])

    def _avoid_pending_ready(self):
        # 小陈、小林都凭组内 A-G 在 10 月持续合格，期望一人时人数规则永不阻挡
        # 小陈的候选，起日区间完全由待完成培训筛选塑造。
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-G", "通用培训", "2026-10-10", 4)
        self.app.enroll("A-G", "M-001")
        self.app.enroll("A-G", "M-002")
        self.app.record_completion("A-G", "M-001", "2026-10-10")
        self.app.record_completion("A-G", "M-002", "2026-10-10")
        # 17 日两场小陈都报名、均待完成：同日多场不重复排除。
        self.app.create_activity("A-P1", "十七日待办甲", "2026-10-17", 2)
        self.app.create_activity("A-P2", "十七日待办乙", "2026-10-17", 2)
        self.app.enroll("A-P1", "M-001")
        self.app.enroll("A-P2", "M-001")
        # 16 日只有小林报名：其他成员的待完成培训不阻挡小陈。
        self.app.create_activity("A-OTHER", "小林的待办", "2026-10-16", 2)
        self.app.enroll("A-OTHER", "M-002")
        # 20 日小陈有待完成培训，但落在查询范围外，不阻挡。
        self.app.create_activity("A-OUT", "范围外待办", "2026-10-20", 2)
        self.app.enroll("A-OUT", "M-001")
        # 18 日小陈报名的待完成培训，稍后登记一条晚于查询范围的完成记录。
        self.app.create_activity("A-FUT", "十八日待办", "2026-10-18", 2)
        self.app.enroll("A-FUT", "M-001")

    def test_absence_start_options_avoid_pending_training_rules(self):
        self._avoid_pending_ready()
        groups = [{"group_id": "G", "activity_ids": ["A-G"], "valid_days": 10**6}]
        # 关闭时人数规则每天满足：15 至 19 日全部可起。
        self.assertEqual(
            self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 1)["start_windows"],
            [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])
        # 开启后：17 日（两场，不重复排除）与 18 日阻挡；16 日他人培训、
        # 20 日本人范围外培训不阻挡。
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 1, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-16"},
            {"from_on": "2026-10-19", "to_on": "2026-10-19"},
        ])
        # 小陈 17 日已在 absences 中请假：待完成培训当天仍阻挡。
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1,
            [{"member_id": "M-001", "from_on": "2026-10-17", "to_on": "2026-10-17"}],
            "M-001", 1, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-16"},
            {"from_on": "2026-10-19", "to_on": "2026-10-19"},
        ])
        # 同样的已请假在关闭筛选时不阻挡（本人已请假不再扣人）。
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1,
            [{"member_id": "M-001", "from_on": "2026-10-17", "to_on": "2026-10-17"}],
            "M-001", 1)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])
        # 本人登记完成记录后不再待完成：两场都完成才解除 17 日的阻挡。
        self.app.record_completion("A-P1", "M-001", "2026-10-17")
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 1, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-16"},
            {"from_on": "2026-10-19", "to_on": "2026-10-19"},
        ])
        self.app.record_completion("A-P2", "M-001", "2026-10-17")
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 1, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-17"},
            {"from_on": "2026-10-19", "to_on": "2026-10-19"},
        ])
        # 完成日期晚于查询范围（18 日活动、2027 年才完成）同样解除阻挡。
        self.app.record_completion("A-FUT", "M-001", "2027-01-01")
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 1, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])

    def test_absence_start_options_avoid_pending_when_not_qualified(self):
        # 小陈只在 15、16 日合格；小林凭三场完成记录在 15 至 19 日都合格。
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-G1", "十五号场", "2026-10-15", 4)
        self.app.create_activity("A-G2", "十七号场", "2026-10-17", 4)
        self.app.create_activity("A-G3", "十九号场", "2026-10-19", 4)
        for activity_id in ("A-G1", "A-G2", "A-G3"):
            self.app.enroll(activity_id, "M-002")
        self.app.enroll("A-G1", "M-001")
        self.app.record_completion("A-G1", "M-001", "2026-10-15")
        self.app.record_completion("A-G1", "M-002", "2026-10-15")
        self.app.record_completion("A-G2", "M-002", "2026-10-17")
        self.app.record_completion("A-G3", "M-002", "2026-10-19")
        # 17 日小陈已不合格，却报名了当天的待完成培训。
        self.app.create_activity("A-NQ", "不合格日待办", "2026-10-17", 2)
        self.app.enroll("A-NQ", "M-001")
        groups = [{"group_id": "G", "activity_ids": ["A-G1", "A-G2", "A-G3"], "valid_days": 2}]
        # 关闭时：小陈 17 日不合格不扣人，小林一人满足期望，起日全可行。
        result = self.app.absence_start_options(groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 1)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])
        # 开启后：本人当天不合格时待完成培训仍阻挡。
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 1, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-16"},
            {"from_on": "2026-10-18", "to_on": "2026-10-19"},
        ])

    def test_absence_start_options_avoid_pending_training_rejections(self):
        self._start_options_ready()
        valid_groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 5}]
        # null、数字、字符串等非布尔值一律拒绝；布尔值与省略均合法。
        for value in [None, 0, 1, -1, 2.0, "true", "false", "", [], {}]:
            with self.assertRaises(ValueError, msg=value):
                self.app.absence_start_options(
                    valid_groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 2,
                    avoid_pending_training=value)
        # 开启筛选时仍校验全部引用与历史，即使没有可行起日。
        with self.assertRaises(ValueError):
            self.app.absence_start_options(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3}],
                "2026-10-15", "2026-10-19", 1, [], "M-001", 100, avoid_pending_training=True)
        with self.assertRaises(ValueError):
            self.app.absence_start_options(
                valid_groups, "2026-10-15", "2026-10-19", 1, [], "GHOST", 2, avoid_pending_training=True)
        # 非法筛选值优先于业务结果抛出，失败不返回部分结果。
        with self.assertRaises(ValueError):
            self.app.absence_start_options(
                valid_groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 2,
                avoid_pending_training="true")

    def test_absence_start_options_avoid_pending_training_never_writes_and_legacy(self):
        self._start_options_ready()
        self.app.create_activity("A-010", "十七号待完成", "2026-10-17", 2)
        self.app.enroll("A-010", "M-001")
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 5}]
        before = self.app.path.read_bytes()
        self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 2, avoid_pending_training=True)
        self.assertEqual(before, self.app.path.read_bytes())
        # 旧数据缺少 completions：全部报名按待完成处理；小陈在范围内的待办
        # （17 日）阻挡其起日，同时无人合格满足人数，结果为空且不补写字段。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.absence_start_options(
            groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 1, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        # 空目录上的失败查询不创建目录或文件。
        empty = self.root / "empty-avoid-pending"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.absence_start_options(
                groups, "2026-10-15", "2026-10-19", 1, [], "M-001", 2, avoid_pending_training=True)
        self.assertFalse(empty.exists())

    def test_absence_start_options_avoid_pending_training_date_boundaries(self):
        # 0001-01-01 与 9999-12-31 的待完成培训不导致溢出。
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-MIN", "元日待办", "0001-01-01", 2)
        self.app.create_activity("A-MAX", "末日待办", "9999-12-31", 2)
        self.app.create_activity("A-G1", "元日组合", "0001-01-01", 2)
        self.app.create_activity("A-G2", "末段组合", "9999-12-20", 2)
        self.app.enroll("A-MIN", "M-001")
        self.app.enroll("A-MAX", "M-001")
        for activity_id in ("A-G1", "A-G2"):
            self.app.enroll(activity_id, "M-001")
            self.app.enroll(activity_id, "M-002")
        self.app.record_completion("A-G1", "M-001", "0001-01-01")
        self.app.record_completion("A-G1", "M-002", "0001-01-01")
        self.app.record_completion("A-G2", "M-001", "9999-12-20")
        self.app.record_completion("A-G2", "M-002", "9999-12-20")
        gmin = [{"group_id": "G", "activity_ids": ["A-G1"], "valid_days": 10**9}]
        gmax = [{"group_id": "G", "activity_ids": ["A-G2"], "valid_days": 10**9}]
        # 元日当天待完成：两日请假只能从次日起。
        result = self.app.absence_start_options(
            gmin, "0001-01-01", "0001-01-03", 1, [], "M-001", 2, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [{"from_on": "0001-01-02", "to_on": "0001-01-02"}])
        # 末日当天待完成：29 日起的请假只覆盖 29、30 日；单日起日 29、30 可行。
        result = self.app.absence_start_options(
            gmax, "9999-12-29", "9999-12-31", 1, [], "M-001", 2, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [{"from_on": "9999-12-29", "to_on": "9999-12-29"}])
        result = self.app.absence_start_options(
            gmax, "9999-12-29", "9999-12-31", 1, [], "M-001", 1, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [{"from_on": "9999-12-29", "to_on": "9999-12-30"}])
        # 超长请假返回空数组且不溢出。
        result = self.app.absence_start_options(
            gmax, "9999-12-31", "9999-12-31", 1, [], "M-001", 10**9, avoid_pending_training=True)
        self.assertEqual(result["start_windows"], [])

    def test_cli_absence_start_options_avoid_pending_training(self):
        self._start_options_ready()
        self.app.create_activity("A-010", "十七号待完成", "2026-10-17", 2)
        self.app.enroll("A-010", "M-001")
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "absence-start-options", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        base = {"groups": [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"], "valid_days": 5}],
                "from_on": "2026-10-15", "to_on": "2026-10-19", "minimum_count": 1,
                "absences": [], "member_id": "M-001", "days": 2}
        ok = run({**base, "avoid_pending_training": True})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["start_windows"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-15"},
            {"from_on": "2026-10-18", "to_on": "2026-10-18"},
        ])
        omitted = run(base)
        self.assertEqual(omitted.returncode, 0, omitted.stderr)
        self.assertEqual(json.loads(omitted.stdout)["start_windows"],
                         [{"from_on": "2026-10-15", "to_on": "2026-10-18"}])
        bad = run({**base, "avoid_pending_training": "true"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        partial = run([{**base, "avoid_pending_training": True}, {**base, "avoid_pending_training": None}])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def _group_start_options_ready(self):
        # 小陈、小林凭 A-SAFE 在安全组、小陈和小周凭 A-COLLAB 在协作组于 10 月
        # 持续合格（有效期极长）；另让小陈报名一场范围内的待完成培训，验证培训
        # 报名本身不额外阻挡请假推荐。
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.add_member("M-004", "小吴")
        self.app.create_activity("A-SAFE", "安全培训", "2026-10-10", 3)
        self.app.create_activity("A-COLLAB", "协作培训", "2026-10-10", 3)
        self.app.enroll("A-SAFE", "M-001")
        self.app.enroll("A-SAFE", "M-002")
        self.app.enroll("A-COLLAB", "M-001")
        self.app.enroll("A-COLLAB", "M-003")
        self.app.record_completion("A-SAFE", "M-001", "2026-10-10")
        self.app.record_completion("A-SAFE", "M-002", "2026-10-10")
        self.app.record_completion("A-COLLAB", "M-001", "2026-10-10")
        self.app.record_completion("A-COLLAB", "M-003", "2026-10-10")
        self.app.create_activity("A-PEND", "期待完成培训", "2026-10-17", 2)
        self.app.enroll("A-PEND", "M-001")

    def _group_start_groups(self, safe_count=1, collab_count=1, valid_days=10**6):
        return [
            {"group_id": "G-SAFE", "activity_ids": ["A-SAFE"], "valid_days": valid_days, "minimum_count": safe_count},
            {"group_id": "G-COLLAB", "activity_ids": ["A-COLLAB"], "valid_days": valid_days, "minimum_count": collab_count},
        ]

    def test_group_absence_start_options_example(self):
        self._group_start_options_ready()
        # 题面示例：小陈和小林仅安全组合格、小周仅协作组合格，两组各需一人且
        # 无人请假；小陈请两天时安全组始终还有小林、协作组始终还有小周，报名
        # 的 17 日待完成培训不额外阻挡，起日窗口为 15 至 18 日。
        result = self.app.group_absence_start_options(
            self._group_start_groups(),
            "2026-10-15", "2026-10-19", [], " M-001 ", 2)
        self.assertEqual(result, {
            "from_on": "2026-10-15",
            "to_on": "2026-10-19",
            "member_id": "M-001",
            "days": 2,
            "start_windows": [{"from_on": "2026-10-15", "to_on": "2026-10-18"}],
        })

    def test_group_absence_start_options_groups_judged_independently(self):
        self._group_start_options_ready()
        # 若按 absence-start-options 的“所有组同时合格”口径，小陈是唯一两组都
        # 合格的人：他一请假合格即清零。分组独立核对时，安全组剩下小林、协作组
        # 剩下小周，两组各满足一人，全部起日可行；同一人（小陈）同时计入两组。
        result = self.app.group_absence_start_options(
            self._group_start_groups(), "2026-10-15", "2026-10-19", [], "M-001", 2)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-18"}])
        # 安全组要求两人时，小陈请假后安全组每天只剩小林一人：没有可行起日；
        # 协作组虽然始终满足，也不能挽救另一组的欠缺。
        result = self.app.group_absence_start_options(
            self._group_start_groups(safe_count=2), "2026-10-15", "2026-10-19", [], "M-001", 1)
        self.assertEqual(result["start_windows"], [])
        # 协作组同样允许要求超过人数：要求两人时小陈之外只有小周，小陈一旦在
        # 候选段内被扣人即欠缺；但申请人换成两组都不合格的小吴时他从不扣人，
        # 协作组每天仍有小陈和小周两人，全部起日可行。
        result = self.app.group_absence_start_options(
            self._group_start_groups(collab_count=2), "2026-10-15", "2026-10-19", [], "M-001", 1)
        self.assertEqual(result["start_windows"], [])
        result = self.app.group_absence_start_options(
            self._group_start_groups(collab_count=2), "2026-10-15", "2026-10-19", [], "M-004", 1)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])

    def test_group_absence_start_options_no_deduction_when_unqualified_or_absent(self):
        self._group_start_options_ready()
        # 申请人在两组都不合格时从不扣人：基线每天两组各有两人满足一人要求，
        # 小吴请两天的全部起日可行。
        result = self.app.group_absence_start_options(
            self._group_start_groups(), "2026-10-15", "2026-10-19", [], "M-004", 2)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-18"}])
        # 小陈全程已在 absences 中请假时不重复扣人：两组各余一人，全部起日可行。
        result = self.app.group_absence_start_options(
            self._group_start_groups(), "2026-10-15", "2026-10-19",
            [{"member_id": "M-001", "from_on": "2026-10-15", "to_on": "2026-10-19"}],
            "M-001", 2)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-18"}])
        # 重复、重叠、相邻的已有请假取并集：两组各需一人时，小陈 16、17 日已
        # 请假（两条记录重叠相邻），当天安全组仍有小林、协作组仍有小周，不重复
        # 扣人，故请两天的全部起日合成 15 至 18 日一个窗口，不因 16、17 日拆段。
        absences = [
            {"member_id": " M-001 ", "from_on": "2026-10-16", "to_on": "2026-10-17"},
            {"member_id": "M-001", "from_on": "2026-10-17", "to_on": "2026-10-17"},
        ]
        result = self.app.group_absence_start_options(
            self._group_start_groups(), "2026-10-15", "2026-10-19", absences, "M-001", 2)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-18"}])
        # 安全组要求两人时没有余裕：小陈已请假的日子安全组只剩小林一人，仍然
        # 欠缺（不重复扣人不等于凭空补人），任何起日都不可行。
        result = self.app.group_absence_start_options(
            self._group_start_groups(safe_count=2), "2026-10-15", "2026-10-19", absences, "M-001", 2)
        self.assertEqual(result["start_windows"], [])

    def test_group_absence_start_options_gaps_elsewhere_and_basis_change(self):
        self._group_coverage_ready()
        # 入门组 15 至 17 日小陈（A-001）与小林（A-002）分别合格、各凭一场，
        # 18 日起两组均无人合格；范围后段的缺口不影响完全落在 15 至 17 日的
        # 候选。小陈请一天时起日 15、16、17 可行，请两天时起日 15、16 可行。
        groups = [{"group_id": "G-ENTRY", "activity_ids": ["A-001", "A-002"],
                   "valid_days": 3, "minimum_count": 1}]
        result = self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-001", 1)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-17"}])
        result = self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-001", 2)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-16"}])
        # 两组独立、申请人小周在任何一组都不合格：他从不扣人，15 至 17 日两组
        # 各有一人满足，18 日起均欠缺；连续可行起日不因合格人员（小陈 vs 小林）
        # 或培训依据变化拆段，也不因后段缺口前移。
        independent = [
            {"group_id": "G-SAFE", "activity_ids": ["A-001"], "valid_days": 3, "minimum_count": 1},
            {"group_id": "G-COLLAB", "activity_ids": ["A-002"], "valid_days": 3, "minimum_count": 1},
        ]
        result = self.app.group_absence_start_options(independent, "2026-10-15", "2026-10-19", [], "M-003", 1)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-17"}])

    def test_group_absence_start_options_days_overflow_and_date_max(self):
        self._group_start_options_ready()
        groups = self._group_start_groups()
        # days 超过查询天数时数组为空；等于查询天数时只有首日起得了。
        result = self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-004", 6)
        self.assertEqual(result["start_windows"], [])
        result = self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-004", 5)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-15"}])
        # 有效期长到越过 date.max 时按整段合格处理；查询到 9999-12-31 也不溢出。
        far = self._group_start_groups(valid_days=10**9)
        result = self.app.group_absence_start_options(far, "9999-12-25", "9999-12-31", [], "M-004", 3)
        self.assertEqual(result["start_windows"], [{"from_on": "9999-12-25", "to_on": "9999-12-29"}])
        result = self.app.group_absence_start_options(far, "9999-12-31", "9999-12-31", [], "M-004", 1)
        self.assertEqual(result["start_windows"], [{"from_on": "9999-12-31", "to_on": "9999-12-31"}])
        result = self.app.group_absence_start_options(far, "9999-12-25", "9999-12-31", [], "M-004", 10**9)
        self.assertEqual(result["start_windows"], [])
        # 已有请假排到范围最后一天，且两组各要求两人：12-31 小陈已请假后两组
        # 都只剩一人而欠缺，未合格的申请人不补人也不扣人，故单日起日只有 30。
        far2 = self._group_start_groups(safe_count=2, collab_count=2, valid_days=10**9)
        result = self.app.group_absence_start_options(
            far2, "9999-12-30", "9999-12-31",
            [{"member_id": "M-001", "from_on": "9999-12-31", "to_on": "9999-12-31"}], "M-004", 1)
        self.assertEqual(result["start_windows"], [{"from_on": "9999-12-30", "to_on": "9999-12-30"}])

    def test_group_absence_start_options_member_selection(self):
        self._group_start_options_ready()
        groups = self._group_start_groups()
        # 省略或 null 选择全员；显式选择按输入顺序判定，申请人必须在名单内。
        ok = self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-001", 1)
        self.assertEqual(ok["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])
        ok = self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-001", 1, None)
        self.assertEqual(ok["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-003", 1, ["M-001", "M-002"])
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-001", 1, [])
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-001", 1, "GHOST")
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-001", 1,
                                                 ["M-001", " M-001 "])
        # 全员（显式给出）时小周 15 至 19 日请假：协作组只剩小陈一人，而小陈
        # 请一天会离开协作组，协作组欠缺，故没有可行起日；安全组不受影响（小林
        # 始终在岗），但任一组欠缺即整体排除该起日。
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19",
            [{"member_id": "M-003", "from_on": "2026-10-15", "to_on": "2026-10-19"}],
            "M-001", 1, ["M-001", "M-002", "M-003"])
        self.assertEqual(result["start_windows"], [])
        # 只选小陈、小林时小周不在内，他的请假条目仍合法但不影响人数：协作组
        # 本就只有小陈合格，他请一天使协作组欠缺，结果同样为空而非报错。
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19",
            [{"member_id": "M-003", "from_on": "2026-10-15", "to_on": "2026-10-19"}],
            "M-001", 1, ["M-001", "M-002"])
        self.assertEqual(result["start_windows"], [])
        # 换成两组都不合格的小吴申请：他不扣人，小周请假与未入选时的人数规则
        # 同上但与申请人无关；只选小陈、小林时两组各余一人满足要求，全部起日
        # 可行，且未入选成员小周的请假不影响任何组。
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19",
            [{"member_id": "M-003", "from_on": "2026-10-15", "to_on": "2026-10-19"}],
            "M-004", 1, ["M-001", "M-002", "M-004"])
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])

    def _exclusive_start_options_ready(self):
        # 小陈和小林凭 A-ONE、A-TWO 的双份完成记录在两个组均持续合格（有效期
        # 极长）；小周和小吴只报名第一组的 A-ONE、暂无完成记录，之后按需补登。
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.add_member("M-004", "小吴")
        self.app.create_activity("A-ONE", "第一组培训", "2026-10-10", 4)
        self.app.create_activity("A-TWO", "第二组培训", "2026-10-10", 2)
        for activity_id in ("A-ONE", "A-TWO"):
            for member_id in ("M-001", "M-002"):
                self.app.enroll(activity_id, member_id)
                self.app.record_completion(activity_id, member_id, "2026-10-10")
        self.app.enroll("A-ONE", "M-003")
        self.app.enroll("A-ONE", "M-004")

    def _exclusive_start_groups(self, one_count=1, two_count=1, valid_days=10**6):
        return [
            {"group_id": "G-ONE", "activity_ids": ["A-ONE"], "valid_days": valid_days, "minimum_count": one_count},
            {"group_id": "G-TWO", "activity_ids": ["A-TWO"], "valid_days": valid_days, "minimum_count": two_count},
        ]

    def test_group_absence_start_options_exclusive_example(self):
        self._exclusive_start_options_ready()
        groups = self._exclusive_start_groups()
        # 题面示例：小陈和小林在两组均有效且无人请假，两组各需一人。小陈请两
        # 天时默认口径下每组仍各余小林一人，窗口为 15 至 18 日；省略与显式
        # false 的结果与默认完全一致，结果字段不变。
        expected = {
            "from_on": "2026-10-15",
            "to_on": "2026-10-19",
            "member_id": "M-001",
            "days": 2,
            "start_windows": [{"from_on": "2026-10-15", "to_on": "2026-10-18"}],
        }
        self.assertEqual(self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", [], "M-001", 2), expected)
        self.assertEqual(self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", [], "M-001", 2, None, False), expected)
        # 开启独占核对后小陈整段离岗，每天只剩小林一人，无法同时填满两岗，
        # 窗口为空。
        exclusive = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", [], "M-001", 2, None, True)
        self.assertEqual(exclusive, {**expected, "start_windows": []})
        # 增加仅第一组有效的小周后：小周承担第一组、小林承担第二组，独占模式
        # 也返回 15 至 18 日的窗口，不因先把小林计入第一组而拒绝可行日期。
        self.app.record_completion("A-ONE", "M-003", "2026-10-10")
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", [], "M-001", 2, None, True)
        self.assertEqual(result, expected)

    def test_group_absence_start_options_exclusive_shared_pool(self):
        self._exclusive_start_options_ready()
        groups = self._exclusive_start_groups()
        # 小陈请一天时每天只剩小林一人：两组各自需求都是一人、合计需求两人，
        # 各组独立人数足够与总人数足够都不能代替同时满足，独占模式没有可行
        # 起日。申请人换成两组都不合格的小周（仅报名未完成）时他从不扣人，
        # 小陈、小林每天可分任两岗，全部起日可行。
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", [], "M-001", 1, None, True)
        self.assertEqual(result["start_windows"], [])
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", [], "M-003", 1, None, True)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])
        # 小周仅第一组有效后，本人已在 absences 中请假时不重复扣人：小陈全程
        # 已请假，他的候选请假不再使人员池缩小，每天小林、小周仍分任两岗，
        # 全部起日可行。
        self.app.record_completion("A-ONE", "M-003", "2026-10-10")
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19",
            [{"member_id": "M-001", "from_on": "2026-10-15", "to_on": "2026-10-19"}],
            "M-001", 1, None, True)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])
        # 不同日期可以采用不同分配：小林 17 日已请假，当天只剩小周（仅第一组
        # 有效），第二组无人能填；候选段不覆盖 17 日的起日不受影响。请一天时
        # 起日 15、16、18、19 可行，请两天时只有 15 与 18。
        absences = [{"member_id": "M-002", "from_on": "2026-10-17", "to_on": "2026-10-17"}]
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", absences, "M-001", 1, None, True)
        self.assertEqual(result["start_windows"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-16"},
            {"from_on": "2026-10-18", "to_on": "2026-10-19"},
        ])
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", absences, "M-001", 2, None, True)
        self.assertEqual(result["start_windows"], [
            {"from_on": "2026-10-15", "to_on": "2026-10-15"},
            {"from_on": "2026-10-18", "to_on": "2026-10-18"},
        ])

    def test_group_absence_start_options_exclusive_total_headcount_not_enough(self):
        self._exclusive_start_options_ready()
        groups = self._exclusive_start_groups()
        # 总人数足够也不能代替同时满足：小周和小吴都仅第一组有效，小林全程已
        # 请假、小陈申请离岗后，每天人员池恰有两人、等于需求总数，但第二组无
        # 人可承担，独占模式没有可行起日。
        self.app.record_completion("A-ONE", "M-003", "2026-10-10")
        self.app.record_completion("A-ONE", "M-004", "2026-10-10")
        absences = [{"member_id": "M-002", "from_on": "2026-10-15", "to_on": "2026-10-19"}]
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", absences, "M-001", 1, None, True)
        self.assertEqual(result["start_windows"], [])
        # 小林不请假时人员池为三人，第一组两人、第二组一人可同时填满，全部
        # 起日可行；独占可行时默认口径必然同样可行。
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", [], "M-001", 1, None, True)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", [], "M-001", 1)
        self.assertEqual(result["start_windows"], [{"from_on": "2026-10-15", "to_on": "2026-10-19"}])

    def test_group_absence_start_options_exclusive_never_writes(self):
        self._exclusive_start_options_ready()
        groups = self._exclusive_start_groups()
        before = self.app.path.read_bytes()
        self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", [], "M-001", 2, None, True)
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(
                groups, "2026-10-15", "2026-10-19", [], "GHOST", 2, None, True)
        self.assertEqual(before, self.app.path.read_bytes())
        # 缺少 completions 的旧数据按无记录处理：无人合格，独占与默认口径
        # 都没有可行起日，也不补写字段。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.group_absence_start_options(
            groups, "2026-10-15", "2026-10-19", [], "M-001", 1, None, True)
        self.assertEqual(result["start_windows"], [])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))

    def test_group_absence_start_options_rejections(self):
        self._group_start_options_ready()
        valid_groups = self._group_start_groups()
        valid_absences = [{"member_id": "M-002", "from_on": "2026-10-17", "to_on": "2026-10-17"}]
        # 缺少 member_id 或 days、标识空白或类型非法、days 非正整数或布尔均被拒绝。
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19", valid_absences)
        for member_id in [None, " ", 123, ["M-001"]]:
            with self.assertRaises(ValueError, msg=member_id):
                self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                     valid_absences, member_id, 2)
        for days in [None, 0, -1, True, "2", 2.0]:
            with self.assertRaises(ValueError, msg=days):
                self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                     valid_absences, "M-001", days)
        # 未知成员、未入选成员（含空选择）均被拒绝。
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                 valid_absences, "GHOST", 2)
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                 valid_absences, "M-001", 2, ["M-002"])
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                 valid_absences, "M-001", 2, [])
        # absences 省略或 null 拒绝，空数组合法；条目校验沿用 group-coverage。
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                 None, "M-001", 2)
        for absences in ["x", 1, {}]:
            with self.assertRaises(ValueError, msg=absences):
                self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                     absences, "M-001", 2)
        for absences in [[{}],
                         [{"member_id": "M-001", "from_on": "2026-10-16"}],
                         [{"member_id": "M-001", "from_on": "2026-10-16", "to_on": "2026-10-16", "extra": 1}],
                         [{"member_id": "GHOST", "from_on": "2026-10-16", "to_on": "2026-10-16"}],
                         [{"member_id": "M-001", "from_on": "2026-10-17", "to_on": "2026-10-16"}]]:
            with self.assertRaises(ValueError, msg=absences):
                self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                     absences, "M-001", 2)
        # 组条目缺少 minimum_count、带多余字段或人数非法均被拒绝；true 不算 1。
        for groups in [[{"group_id": "G", "activity_ids": ["A-SAFE"], "valid_days": 3}],
                       [{"group_id": "G", "activity_ids": ["A-SAFE"], "valid_days": 3,
                         "minimum_count": 1, "extra": 1}],
                       [{"group_id": "G", "activity_ids": ["A-SAFE"], "valid_days": 3, "minimum_count": True}],
                       [{"group_id": "G", "activity_ids": ["A-SAFE"], "valid_days": 3, "minimum_count": 0}],
                       [{"group_id": "G", "activity_ids": ["A-SAFE"], "valid_days": 3, "minimum_count": 1.5}],
                       [{"group_id": "G", "activity_ids": ["A-SAFE"], "valid_days": 3, "minimum_count": "1"}]]:
            with self.assertRaises(ValueError, msg=groups):
                self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19",
                                                     valid_absences, "M-001", 2)
        # 沿用 group-coverage 的既有校验：空分组、重复组、活动跨组重复、未知
        # 活动、范围逆序。
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options([], "2026-10-15", "2026-10-19",
                                                 valid_absences, "M-001", 2)
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(
                [dict(valid_groups[0]), dict(valid_groups[0])], "2026-10-15", "2026-10-19",
                valid_absences, "M-001", 2)
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(
                [{"group_id": "G-ONE", "activity_ids": ["A-SAFE"], "valid_days": 3, "minimum_count": 1},
                 {"group_id": "G-TWO", "activity_ids": ["A-SAFE"], "valid_days": 3, "minimum_count": 1}],
                "2026-10-15", "2026-10-19", valid_absences, "M-001", 2)
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3, "minimum_count": 1}],
                "2026-10-15", "2026-10-19", valid_absences, "M-001", 2)
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(valid_groups, "2026-10-20", "2026-10-19",
                                                 valid_absences, "M-001", 2)
        # 即使 days 超过查询天数没有可行起日，仍校验全部输入和历史。
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(
                [{"group_id": "G", "activity_ids": ["GHOST"], "valid_days": 3, "minimum_count": 1}],
                "2026-10-15", "2026-10-19", valid_absences, "M-001", 100)
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(
                valid_groups, "2026-10-15", "2026-10-19",
                [{"member_id": "GHOST", "from_on": "2026-11-01", "to_on": "2026-11-02"}],
                "M-001", 100)
        # 不接受顶层人数参数，也没有待完成培训筛选参数。
        with self.assertRaises(TypeError):
            self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                 valid_absences, "M-001", 2, None, False, 1)
        with self.assertRaises(TypeError):
            self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                 valid_absences, "M-001", 2,
                                                 avoid_pending_training=True)
        # exclusive 非布尔值（含 null、数字和字符串）统一拒绝。
        for exclusive in [None, 0, 1, 1.0, "true", ""]:
            with self.assertRaises(ValueError, msg=exclusive):
                self.app.group_absence_start_options(valid_groups, "2026-10-15", "2026-10-19",
                                                     valid_absences, "M-001", 2, None, exclusive)

    def test_group_absence_start_options_never_writes_and_legacy_data(self):
        self._group_start_options_ready()
        groups = self._group_start_groups()
        absences = [{"member_id": "M-002", "from_on": "2026-10-17", "to_on": "2026-10-17"}]
        before = self.app.path.read_bytes()
        self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", absences, "M-001", 2)
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19",
                                                 absences, "GHOST", 2)
        self.assertEqual(before, self.app.path.read_bytes())
        # 旧数据缺少 completions：按无记录处理，每组申请人请假后都无人满足，
        # 结果为空且查询不补写该字段。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("completions", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-001", 2)
        self.assertEqual(result["start_windows"], [])
        self.assertNotIn("completions", json.loads(self.app.path.read_text(encoding="utf-8")))
        # 空目录上的失败查询不创建目录或文件。
        empty = self.root / "empty-group-start-options"
        fresh = TeamPlanner(empty)
        with self.assertRaises(ValueError):
            fresh.group_absence_start_options(groups, "2026-10-15", "2026-10-19",
                                              absences, "M-001", 2)
        self.assertFalse(empty.exists())

    def test_group_absence_start_options_broken_history_and_os_error(self):
        self._group_start_options_ready()
        groups = self._group_start_groups()
        broken = json.loads(self.app.path.read_text(encoding="utf-8"))
        broken["members"]["M-002"]["name"] = "   "
        self.app.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-001", 2)
        self.app.path.unlink()
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.group_absence_start_options(groups, "2026-10-15", "2026-10-19", [], "M-001", 2)

    def test_cli_group_absence_start_options_success_failure_and_partial_array(self):
        self._group_start_options_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run(
                [sys.executable, "-m", "team_planner", "--root", str(self.root),
                 "group-absence-start-options", name], text=True, capture_output=True)
        before = self.app.path.read_bytes()
        ok = run({"groups": [
                     {"group_id": " G-SAFE ", "activity_ids": [" A-SAFE "], "valid_days": 10**6, "minimum_count": 1},
                     {"group_id": "G-COLLAB", "activity_ids": ["A-COLLAB"], "valid_days": 10**6, "minimum_count": 1}],
                 "from_on": "2026-10-15", "to_on": "2026-10-19", "absences": [],
                 "member_id": " M-001 ", "days": 2})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), {
            "from_on": "2026-10-15",
            "to_on": "2026-10-19",
            "member_id": "M-001",
            "days": 2,
            "start_windows": [{"from_on": "2026-10-15", "to_on": "2026-10-18"}],
        })
        # 顶层人数参数不被接受。
        top_level = run({"groups": [{"group_id": "G", "activity_ids": ["A-SAFE"],
                                     "valid_days": 3, "minimum_count": 1}],
                         "from_on": "2026-10-15", "to_on": "2026-10-19", "absences": [],
                         "minimum_count": 1, "member_id": "M-001", "days": 2})
        self.assertEqual(top_level.returncode, 2)
        self.assertIn("error", json.loads(top_level.stderr))
        self.assertEqual(top_level.stdout, "")
        bad = run({"groups": [{"group_id": "G", "activity_ids": ["A-SAFE"], "valid_days": 3}],
                   "from_on": "2026-10-15", "to_on": "2026-10-19", "absences": [],
                   "member_id": "M-001", "days": 2})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(bad.stdout, "")
        partial = run([
            {"groups": [{"group_id": "G", "activity_ids": ["A-SAFE"], "valid_days": 3, "minimum_count": 1}],
             "from_on": "2026-10-15", "to_on": "2026-10-19", "absences": [],
             "member_id": "M-001", "days": 2, "member_ids": []},
            {"groups": [{"group_id": "G", "activity_ids": ["A-SAFE"], "valid_days": 3, "minimum_count": 1}],
             "from_on": "2026-10-15", "to_on": "2026-10-19", "absences": [],
             "member_id": "M-001", "days": 0},
        ])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_cli_group_absence_start_options_exclusive(self):
        self._exclusive_start_options_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root),
                                   "group-absence-start-options", name], text=True, capture_output=True)
        base = {"groups": self._exclusive_start_groups(),
                "from_on": "2026-10-15", "to_on": "2026-10-19", "absences": [],
                "member_id": "M-001", "days": 2}
        before = self.app.path.read_bytes()
        # 省略与显式 false 的结果一致：窗口为 15 至 18 日。
        for payload in (base, {**base, "exclusive": False}):
            ok = run(payload)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(json.loads(ok.stdout), {
                "from_on": "2026-10-15",
                "to_on": "2026-10-19",
                "member_id": "M-001",
                "days": 2,
                "start_windows": [{"from_on": "2026-10-15", "to_on": "2026-10-18"}],
            })
        # 开启独占核对后每天只剩小林一人，无法同时填满两岗，窗口为空。
        exclusive = run({**base, "exclusive": True})
        self.assertEqual(exclusive.returncode, 0, exclusive.stderr)
        self.assertEqual(json.loads(exclusive.stdout)["start_windows"], [])
        # 非布尔值（含 null、数字和字符串）失败：标准输出为空、标准错误含
        # error 的 JSON 并返回 2；顶层数组逐项执行，出错即停。
        for value in ("true", 1, None):
            bad = run({**base, "exclusive": value})
            self.assertEqual(bad.returncode, 2)
            self.assertIn("error", json.loads(bad.stderr))
            self.assertEqual(bad.stdout, "")
        partial = run([{**base, "exclusive": True}, {**base, "exclusive": None}])
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
