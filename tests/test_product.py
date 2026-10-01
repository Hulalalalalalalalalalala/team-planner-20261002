import json
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

    def _schedule_ready(self):
        # 小陈 is enrolled in A-001 and A-002 both on the 15th and finished
        # only A-001; 小林 is enrolled in A-001 but must not create a conflict
        # for 小陈. A-003 on the 20th is 小陈's alone.
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小周")
        self.app.create_activity("A-001", "一", "2026-10-15", 5)
        self.app.create_activity("A-002", "二", "2026-10-15", 5)
        self.app.create_activity("A-003", "三", "2026-10-20", 5)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-001")
        self.app.enroll("A-003", "M-001")
        self.app.enroll("A-001", "M-002")
        self.app.record_completion("A-001", "M-001", "2026-10-15")

    def test_schedule_rows_sorted_with_fields(self):
        self._schedule_ready()
        rows = self.app.member_schedule("  M-001 ")
        self.assertEqual([(r["on"], r["activity_id"]) for r in rows], [("2026-10-15", "A-001"), ("2026-10-15", "A-002"), ("2026-10-20", "A-003")])
        self.assertEqual(rows[0], {"activity_id": "A-001", "title": "一", "on": "2026-10-15", "status": "completed", "completed_on": "2026-10-15", "conflict_activity_ids": ["A-002"]})
        self.assertEqual(rows[1]["status"], "pending")
        self.assertIsNone(rows[1]["completed_on"])
        self.assertEqual(rows[1]["conflict_activity_ids"], ["A-001"])
        self.assertEqual(rows[2]["conflict_activity_ids"], [])
        # A member without enrollments gets an empty list; other members'
        # same-day enrollments never appear.
        self.assertEqual(self.app.member_schedule("M-003"), [])
        self.assertEqual(self.app.member_schedule("M-002")[0]["conflict_activity_ids"], [])

    def test_schedule_filters_keep_hidden_conflicts(self):
        self._schedule_ready()
        # pending hides completed A-001, but A-002 still lists it as a conflict.
        pending = self.app.member_schedule("M-001", status="pending")
        self.assertEqual([r["activity_id"] for r in pending], ["A-002", "A-003"])
        self.assertEqual(pending[0]["conflict_activity_ids"], ["A-001"])
        completed = self.app.member_schedule("M-001", status="completed")
        self.assertEqual([r["activity_id"] for r in completed], ["A-001"])
        self.assertEqual(completed[0]["conflict_activity_ids"], ["A-002"])
        # Inclusive bounds on both ends.
        window = self.app.member_schedule("M-001", from_on="2026-10-15", to_on="2026-10-15")
        self.assertEqual([r["activity_id"] for r in window], ["A-001", "A-002"])
        self.assertEqual([r["activity_id"] for r in self.app.member_schedule("M-001", from_on="2026-10-16")], ["A-003"])
        self.assertEqual(self.app.member_schedule("M-001", to_on="2026-10-01"), [])
        self.assertEqual([r["activity_id"] for r in self.app.member_schedule("M-001", None, None, "all")], ["A-001", "A-002", "A-003"])

    def test_schedule_reflects_transfer_reschedule_and_merge(self):
        self._schedule_ready()
        self.app.create_activity("A-004", "四", "2026-10-21", 5)
        self.app.transfer_enrollment("A-003", "A-004", "M-001")
        self.assertEqual({r["activity_id"] for r in self.app.member_schedule("M-001")}, {"A-001", "A-002", "A-004"})
        # A-003 is now empty for 小陈, so moving A-004 to the 20th is allowed;
        # the query reflects the new date immediately.
        self.app.reschedule_activity("A-004", "2026-10-20")
        rows = TeamPlanner(self.root).member_schedule("M-001")
        row = next(r for r in rows if r["activity_id"] == "A-004")
        self.assertEqual(row["on"], "2026-10-20")
        self.assertEqual(row["conflict_activity_ids"], [])
        self.app.merge_member("M-002", "M-001")
        with self.assertRaises(ValueError):
            self.app.member_schedule("M-002")
        # The merged duplicate seat in A-001 collapses to one entry.
        self.assertEqual([r["activity_id"] for r in self.app.member_schedule("M-001")].count("A-001"), 1)

    def test_schedule_rejections_and_read_only(self):
        self._schedule_ready()
        before = self.app.path.read_bytes()
        for kwargs in [
            {"member_id": 9},
            {"member_id": "   "},
            {"member_id": None},
            {"member_id": "GHOST"},
            {"member_id": "M-001", "from_on": "2026-02-30"},
            {"member_id": "M-001", "to_on": "2026/10/15"},
            {"member_id": "M-001", "from_on": " 2026-10-15"},
            {"member_id": "M-001", "from_on": 10},
            {"member_id": "M-001", "from_on": "2026-10-20", "to_on": "2026-10-01"},
            {"member_id": "M-001", "status": "done"},
            {"member_id": "M-001", "status": None},
        ]:
            with self.assertRaises(ValueError):
                self.app.member_schedule(**kwargs)
        # Neither successful nor failed queries modify the data file.
        self.assertEqual(before, self.app.path.read_bytes())
        self.app.member_schedule("M-001")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_schedule_legacy_file_and_empty_dir_stay_untouched(self):
        legacy = self.root / "legacy"
        other = TeamPlanner(legacy)
        other.add_member("M-009", "老")
        other.create_activity("L-1", "旧", "2026-10-15", 3)
        other.enroll("L-1", "M-009")
        raw = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("completions", raw)
        row = TeamPlanner(legacy).member_schedule("M-009")[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["completed_on"])
        self.assertNotIn("completions", json.loads((legacy / "data.json").read_text(encoding="utf-8")))
        fresh = TeamPlanner(self.root / "empty")
        with self.assertRaises(ValueError):
            fresh.member_schedule("GHOST")
        self.assertFalse((self.root / "empty").exists())

    def test_cli_schedule(self):
        self._schedule_ready()
        def run(payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "schedule", name], text=True, capture_output=True)
        ok = run({"member_id": " M-001 ", "status": "pending"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        rows = json.loads(ok.stdout)
        self.assertEqual([r["activity_id"] for r in rows], ["A-002", "A-003"])
        self.assertEqual(rows[0]["conflict_activity_ids"], ["A-001"])
        batch = run([{"member_id": "M-001", "status": "completed"}, {"member_id": "M-003"}])
        self.assertEqual(batch.returncode, 0, batch.stderr)
        self.assertEqual([[r["activity_id"] for r in part] for part in json.loads(batch.stdout)], [["A-001"], []])
        failure = run({"member_id": "GHOST"})
        self.assertEqual(failure.returncode, 2)
        self.assertIn("error", json.loads(failure.stderr))
        self.assertEqual(failure.stdout, "")
        partial = run([{"member_id": "M-001"}, {"member_id": "GHOST"}])
        self.assertEqual(partial.returncode, 2)
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(partial.stdout, "")

if __name__ == "__main__":
    unittest.main()
