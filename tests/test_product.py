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

    def test_record_completion_and_reopen(self):
        self.app.add_member("M-001", "小陈")
        self.app.create_activity("A-001", "新成员产品介绍", "2026-10-15", 2)
        self.app.enroll("A-001", "M-001")
        with self.assertRaises(ValueError):
            self.app.record_completion("A-001", "M-001", "2026-10-14")
        record = self.app.record_completion("A-001", "M-001", "2026-10-16")
        self.assertEqual(record, {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16", "title": "新成员产品介绍", "on": "2026-10-15"})
        reopened = TeamPlanner(self.root)
        self.assertEqual(reopened.completions("M-001"), [record])
        # 登记完成不取消报名、不改变名单
        self.assertEqual(reopened.roster("A-001")["members"], [{"member_id": "M-001", "name": "小陈"}])
        before = reopened.path.read_bytes()
        with self.assertRaises(ValueError):
            reopened.record_completion("A-001", "M-001", "2026-10-17")
        self.assertEqual(before, reopened.path.read_bytes())

    def test_completion_rejections_leave_state_unchanged(self):
        self.app.add_member("M-001", "小陈")
        self.app.add_member("M-002", "小林")
        self.app.create_activity("A-001", "Training", "2026-10-15", 2)
        self.app.enroll("A-001", "M-001")
        before = self.app.path.read_bytes()
        bad = [
            ("A-001", "M-001", "2026-13-01"),    # 无效日期
            ("A-001", "M-001", "2026-02-30"),    # 不存在的日期
            ("A-001", "M-001", " 2026-10-16"),   # 额外空白
            ("A-001", "M-001", "2026-10-16 "),   # 额外空白
            ("A-001", "M-001", "2026-1-6"),      # 非 YYYY-MM-DD
            ("A-001", "M-001", 20261016),        # 非字符串
            ("A-001", "M-001", None),            # 非字符串
            ("A-404", "M-001", "2026-10-16"),    # 活动不存在
            ("A-001", "M-404", "2026-10-16"),    # 成员不存在
            ("A-001", "M-002", "2026-10-16"),    # 未报名
            ("A-001", "M-001", "2026-10-14"),    # 早于活动日期
            (" ", "M-001", "2026-10-16"),        # 空白标识
            ("A-001", "", "2026-10-16"),         # 空白标识
        ]
        for activity_id, member_id, completed_on in bad:
            with self.assertRaises(ValueError, msg=(activity_id, member_id, completed_on)):
                self.app.record_completion(activity_id, member_id, completed_on)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_completion_rejection_creates_no_file(self):
        with self.assertRaises(ValueError):
            self.app.record_completion("A-001", "M-001", "2026-10-16")
        self.assertFalse(self.app.path.exists())

    def test_completions_query_and_sorting(self):
        with self.assertRaises(ValueError):
            self.app.completions("M-404")
        self.app.add_member("M-001", "小陈")
        self.assertEqual(self.app.completions("M-001"), [])
        self.app.create_activity("A-001", "One", "2026-10-15", 2)
        self.app.create_activity("A-002", "Two", "2026-10-16", 2)
        self.app.enroll("A-001", "M-001")
        self.app.enroll("A-002", "M-001")
        second = self.app.record_completion("A-002", "M-001", "2026-10-20")
        first = self.app.record_completion("A-001", "M-001", "2026-10-15")
        self.assertEqual(self.app.completions("M-001"), [first, second])
        with self.assertRaises(ValueError):
            self.app.completions("  ")

    def test_cli_complete_flow(self):
        def run(action, payload):
            input_file = self.root / "input.json"
            input_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "team_planner", "--root", str(self.root), action, str(input_file)], text=True, capture_output=True)
        self.assertEqual(run("member", [{"member_id": "M-001", "name": "小陈"}, {"member_id": "M-002", "name": "小林"}]).returncode, 0)
        self.assertEqual(run("activity", {"activity_id": "A-001", "title": "新成员产品介绍", "on": "2026-10-15", "capacity": 2}).returncode, 0)
        self.assertEqual(run("enroll", {"activity_id": "A-001", "member_id": "M-001"}).returncode, 0)
        early = run("complete", {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-14"})
        self.assertEqual(early.returncode, 2)
        self.assertIn("error", json.loads(early.stderr))
        ok = run("complete", {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        record = json.loads(ok.stdout)
        self.assertEqual(record, {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16", "title": "新成员产品介绍", "on": "2026-10-15"})
        again = run("complete", {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-16"})
        self.assertEqual(again.returncode, 2)
        not_enrolled = run("complete", {"activity_id": "A-001", "member_id": "M-002", "completed_on": "2026-10-16"})
        self.assertEqual(not_enrolled.returncode, 2)
        query = run("completions", {"member_id": "M-001"})
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertEqual(json.loads(query.stdout), [record])
        empty = run("completions", {"member_id": "M-002"})
        self.assertEqual(json.loads(empty.stdout), [])

if __name__ == "__main__":
    unittest.main()
