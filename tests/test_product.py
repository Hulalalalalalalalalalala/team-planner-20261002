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

    def test_transfer_enrollment_frees_source_seat(self):
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小王")
        self.app.create_activity("A-001", "新成员产品介绍", "2026-10-15", 2)
        self.app.create_activity("A-002", "进阶培训", "2026-10-16", 2)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-001", "M-002")
        self.app.enroll("A-002", "M-003")
        moved = self.app.transfer_enrollment("A-001", "A-002", "M-001")
        self.assertEqual(moved, {"activity_id": "A-002", "title": "进阶培训", "on": "2026-10-16", "capacity": 2, "participants": ["M-003", "M-001"]})
        reopened = TeamPlanner(self.root)
        self.assertEqual(reopened.roster("A-001")["members"], [{"member_id": "M-002", "name": "小林"}])
        self.assertEqual([m["member_id"] for m in reopened.roster("A-002")["members"]], ["M-003", "M-001"])
        # The freed seat on the source activity can be enrolled again.
        reopened.enroll("A-001", "M-003")
        self.assertEqual(reopened.roster("A-001")["members"], [{"member_id": "M-002", "name": "小林"}, {"member_id": "M-003", "name": "小王"}])
        # Titles, dates, capacities and the member profile are unchanged.
        self.assertEqual([(a["activity_id"], a["title"], a["on"], a["capacity"]) for a in reopened.activities()], [("A-001", "新成员产品介绍", "2026-10-15", 2), ("A-002", "进阶培训", "2026-10-16", 2)])
        self.assertEqual(reopened._read()["members"]["M-001"], {"member_id": "M-001", "name": "小陈"})

    def test_transfer_rejections_leave_state_unchanged(self):
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.add_member("M-003", "小王")
        self.app.create_activity("A-001", "新成员产品介绍", "2026-10-15", 3)
        self.app.create_activity("A-002", "进阶培训", "2026-10-16", 1)
        self.app.create_activity("A-003", "同日另一活动", "2026-10-16", 3)
        self.app.create_activity("A-004", "另一目标", "2026-10-17", 2)
        for member in ["M-001", "M-002", "M-003"]:
            self.app.enroll("A-001", member)
        before = self.app.path.read_bytes()
        for kwargs in [
            {"source_activity_id": "  ", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": 9},
            {"source_activity_id": "GHOST", "target_activity_id": "A-002", "member_id": "M-001"},
            {"source_activity_id": "A-001", "target_activity_id": "GHOST", "member_id": "M-001"},
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "GHOST"},
            {"source_activity_id": "A-001", "target_activity_id": "A-001", "member_id": "M-001"},
            {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-001"},
        ]:
            with self.assertRaises(ValueError):
                self.app.transfer_enrollment(**kwargs)
        self.assertEqual(before, self.app.path.read_bytes())
        # Already enrolled in the target activity rejects the transfer.
        self.app.enroll("A-003", "M-001")
        with self.assertRaises(ValueError):
            self.app.transfer_enrollment("A-001", "A-003", "M-001")
        # Enrollment in a third activity on the target date is a conflict,
        # and it still counts after that training is completed.
        with self.assertRaises(ValueError):
            self.app.transfer_enrollment("A-001", "A-002", "M-001")
        self.app.record_completion("A-003", "M-001", "2026-10-16")
        with self.assertRaises(ValueError):
            self.app.transfer_enrollment("A-001", "A-002", "M-001")
        # A full target activity rejects the transfer.
        self.app.enroll("A-002", "M-002")
        with self.assertRaises(ValueError):
            self.app.transfer_enrollment("A-001", "A-002", "M-003")
        # A completion record on the source activity blocks the transfer,
        # and the record itself is never removed or moved.
        self.app.record_completion("A-001", "M-001", "2026-10-15")
        with self.assertRaises(ValueError):
            self.app.transfer_enrollment("A-001", "A-004", "M-001")
        self.assertEqual(self.app.completions("M-001"), [
            {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-15", "title": "新成员产品介绍", "on": "2026-10-15"},
            {"activity_id": "A-003", "member_id": "M-001", "completed_on": "2026-10-16", "title": "同日另一活动", "on": "2026-10-16"},
        ])
        # Another member's completion on the source does not block transfer.
        moved = self.app.transfer_enrollment("A-001", "A-004", "M-003")
        self.assertEqual(moved["participants"], ["M-003"])
        self.assertEqual(self.app.roster("A-001")["members"], [{"member_id": "M-001", "name": "小陈"}, {"member_id": "M-002", "name": "小林"}])
        self.assertEqual(self.app.completions("M-003"), [])
        self.assertEqual(len(self.app.completions("M-001")), 2)

    def test_transfer_failure_does_not_create_file(self):
        fresh = TeamPlanner(self.root / "empty")
        with self.assertRaises(ValueError):
            fresh.transfer_enrollment("A", "B", "M")
        self.assertFalse((self.root / "empty" / "data.json").exists())

    def test_cli_transfer(self):
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-001", "新成员产品介绍", "2026-10-15", 1)
        self.app.create_activity("A-002", "进阶培训", "2026-10-16", 2)
        self.app.enroll("A-001", "M-001")
        def run(action, payload):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                json.dump(payload, stream)
                name = stream.name
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), action, name], text=True, capture_output=True)
        ok = run("transfer", {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-001"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), {"activity_id": "A-002", "capacity": 2, "on": "2026-10-16", "participants": ["M-001"], "title": "进阶培训"})
        # Array input processes items in order; a later failure keeps earlier successes.
        batch = run("transfer", [
            {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-001"},
            {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "GHOST"},
        ])
        self.assertEqual(batch.returncode, 2)
        self.assertIn("error", json.loads(batch.stderr))
        self.assertEqual(self.app.roster("A-001")["members"], [{"member_id": "M-001", "name": "小陈"}])
        failed = run("transfer", {"source_activity_id": "A-001", "target_activity_id": "A-002", "member_id": "M-002"})
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))

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

if __name__ == "__main__":
    unittest.main()
