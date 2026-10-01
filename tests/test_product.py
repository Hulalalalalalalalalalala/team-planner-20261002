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

    def test_reschedule_conflict_then_success(self):
        self.app.add_member("M-001", "小陈")
        self.app.create_activity("A-001", "一", "2026-10-15", 2)
        self.app.create_activity("A-002", "二", "2026-10-16", 2)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-001")
        before = self.app.path.read_bytes()
        # 小陈 already has another activity on 2026-10-16, so moving there fails.
        with self.assertRaises(ValueError):
            self.app.reschedule_activity("A-001", "2026-10-16")
        self.assertEqual(before, self.app.path.read_bytes())
        moved = self.app.reschedule_activity("A-001", "2026-10-17")
        self.assertEqual(moved, {"activity_id": "A-001", "title": "一", "on": "2026-10-17", "capacity": 2, "participants": ["M-001"]})
        # The new date persists across reopen and shows in roster and sorted list.
        self.assertEqual(TeamPlanner(self.root).roster("A-001")["on"], "2026-10-17")
        self.assertEqual([a["activity_id"] for a in TeamPlanner(self.root).activities()], ["A-002", "A-001"])
        # Completion recorded after the move uses the new activity date.
        self.app.record_completion("A-001", "M-001", "2026-10-17")
        self.assertEqual(self.app.completions("M-001")[0]["completed_on"], "2026-10-17")

    def test_reschedule_same_date_is_noop(self):
        self._ready()
        self.app.record_completion("A-001", "M-001", "2026-10-16")
        before = self.app.path.read_bytes()
        # Same date succeeds without rewriting, even with completions.
        self.assertEqual(self.app.reschedule_activity("A-001", "2026-10-15")["on"], "2026-10-15")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_reschedule_rejections_leave_state_unchanged(self):
        self._ready()
        self.app.record_completion("A-002", "M-001", "2026-10-16")
        before = self.app.path.read_bytes()
        # Any completion on the activity blocks a real date change.
        with self.assertRaises(ValueError):
            self.app.reschedule_activity("A-002", "2026-10-20")
        for kwargs in [
            {"activity_id": "GHOST", "on": "2026-10-20"},
            {"activity_id": "  ", "on": "2026-10-20"},
            {"activity_id": "A-001", "on": "2026-02-30"},
            {"activity_id": "A-001", "on": " 2026-10-20"},
            {"activity_id": "A-001", "on": "2026-1-5"},
            {"activity_id": "A-001", "on": 10},
        ]:
            with self.assertRaises(ValueError):
                self.app.reschedule_activity(**kwargs)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_reschedule_without_shared_members(self):
        self._ready()
        # An empty activity can move onto a day that already has activities.
        self.app.create_activity("A-003", "三", "2026-10-11", 3)
        self.assertEqual(self.app.reschedule_activity("A-003", "2026-10-15")["on"], "2026-10-15")
        # Same-day activities without common members do not conflict.
        self.app.create_activity("A-004", "四", "2026-10-12", 3)
        self.app.enroll("A-004", "M-002")
        self.assertEqual(self.app.reschedule_activity("A-004", "2026-10-15")["on"], "2026-10-15")

    def test_cli_reschedule(self):
        self._ready()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
            json.dump([{"activity_id": "A-001", "on": "2026-10-18"}, {"activity_id": "GHOST", "on": "2026-10-20"}], stream)
            name = stream.name
        result = subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "reschedule", name], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        # The earlier item in the array kept its successful reschedule.
        self.assertEqual(TeamPlanner(self.root).roster("A-001")["on"], "2026-10-18")
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
            json.dump({"activity_id": "A-002", "on": "2026-10-20"}, stream)
            name = stream.name
        ok = subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), "reschedule", name], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["on"], "2026-10-20")

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

if __name__ == "__main__":
    unittest.main()
