import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from team_planner import TeamPlanner

# A valid historical document for 2026-10-15: extra fields at every level,
# same-day multiple enrollments, a completed enrollment still occupying its
# seat, and an empty completion object that must be kept.
LEGACY = {
    "note": "imported from the old planner",
    "members": {
        "M-001": {"member_id": "M-001", "name": "小陈", "team": "平台"},
        "M-002": {"member_id": "M-002", "name": "小林"},
        "M-003": {"member_id": "M-003", "name": "小周"},
    },
    "activities": {
        "A-001": {"activity_id": "A-001", "title": "新成员产品介绍", "on": "2026-10-15", "capacity": 2, "participants": ["M-001", "M-002"], "location": "一号会议室"},
        "A-002": {"activity_id": "A-002", "title": "安全规范", "on": "2026-10-15", "capacity": 2, "participants": ["M-001", "M-003"]},
        "A-003": {"activity_id": "A-003", "title": "团队协作", "on": "2026-10-15", "capacity": 2, "participants": ["M-001"]},
    },
    "completions": {
        "A-001": {},
        "A-003": {"M-001": "2026-10-15"},
    },
}

class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = TeamPlanner(self.root)

    def write(self, value):
        self.app.path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            self.app.path.write_bytes(value)
        else:
            self.app.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return self.app.path.read_bytes()

    def test_valid_legacy_document_queries_normally(self):
        self.write(LEGACY)
        # Same-day multiple enrollments stay accepted; completion keeps the seat.
        self.assertEqual([a["activity_id"] for a in self.app.activities()], ["A-001", "A-002", "A-003"])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-002")["members"]], ["M-001", "M-003"])
        self.assertEqual([m["member_id"] for m in self.app.roster("A-003")["members"]], ["M-001"])
        self.assertEqual([r["activity_id"] for r in self.app.completions("M-001")], ["A-003"])
        self.assertEqual(self.app.completions("M-002"), [])
        rows = self.app.member_schedule("M-001")
        self.assertEqual([(r["activity_id"], r["status"]) for r in rows],
                         [("A-001", "pending"), ("A-002", "pending"), ("A-003", "completed")])
        self.assertEqual(rows[0]["conflict_activity_ids"], ["A-002", "A-003"])

    def test_missing_collections_are_empty_sets(self):
        self.write({})
        self.assertEqual(self.app.activities(), [])
        self.write({"members": {}, "activities": {}})
        # No completions field means no completion records, and an unknown
        # member is still rejected by the normal business rules.
        with self.assertRaises(ValueError):
            self.app.completions("M-001")
        # null is not an empty object, even for a missing-data equivalent.
        self.write({"members": {}, "activities": {}, "completions": None})
        with self.assertRaises(ValueError):
            self.app.activities()

    def test_extra_fields_survive_a_successful_modification(self):
        self.write(LEGACY)
        self.app.enroll("A-003", "M-002")
        raw = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["note"], "imported from the old planner")
        self.assertEqual(raw["members"]["M-001"]["team"], "平台")
        self.assertEqual(raw["activities"]["A-001"]["location"], "一号会议室")
        self.assertEqual(raw["activities"]["A-003"]["participants"], ["M-001", "M-002"])
        # The empty completion object is preserved rather than rewritten away.
        self.assertEqual(raw["completions"]["A-001"], {})

    def test_invalid_documents_are_rejected_everywhere_with_bytes_unchanged(self):
        corrupt_documents = [
            ["top level"],
            None,
            "just text",
            42,
            {"members": None},
            {"members": []},
            {"activities": None},
            {"completions": None},
            {"completions": []},
            {"members": {"M-001": None}},
            {"members": {"M-001": []}},
            {"members": {"M-001": "小陈"}},
            {"members": {"M-001": {"member_id": "M-002", "name": "小陈"}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "   "}}},
            {"members": {"M-001": {"member_id": "M-001"}}},
            {"members": {"M-001": {"member_id": 9, "name": "小陈"}}},
            {"members": {" M-001": {"member_id": " M-001", "name": "小陈"}}},
            {"members": {"": {"member_id": "", "name": "小陈"}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": None}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": []}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-002", "title": "培训", "on": "2026-10-15", "capacity": 1, "participants": []}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "  ", "on": "2026-10-15", "capacity": 1, "participants": []}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-02-30", "capacity": 1, "participants": []}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026/10/15", "capacity": 1, "participants": []}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": " 2026-10-15", "capacity": 1, "participants": []}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": 20261015, "capacity": 1, "participants": []}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": True, "participants": []}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 0, "participants": []}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": "2", "participants": []}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 1}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 1, "participants": None}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {" A-001": {"activity_id": " A-001", "title": "培训", "on": "2026-10-15", "capacity": 1, "participants": []}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 1, "participants": ["GHOST"]}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-001", " M-001 "]}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-001", "M-001"]}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-001", 3]}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}, "M-002": {"member_id": "M-002", "name": "小林"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 1, "participants": ["M-001", "M-002"]}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-001"]}},
             "completions": {"GHOST": {}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-001"]}},
             "completions": {" A-001": {"M-001": "2026-10-15"}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-001"]}},
             "completions": {"A-001": []}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": []}},
             "completions": {"A-001": {"M-001": "2026-10-15"}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-001"]}},
             "completions": {"A-001": {"M-001": "2026-10-14"}}},
            {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
             "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-001"]}},
             "completions": {"A-001": {"M-001": "2026/10/15"}}},
        ]
        queries = [
            lambda: self.app.activities(),
            lambda: self.app.roster("A-001"),
            lambda: self.app.member_schedule("M-001"),
            lambda: self.app.completions("M-001"),
            lambda: self.app.add_member("M-999", "新人"),
            lambda: self.app.enroll("A-001", "M-001"),
        ]
        for index, document in enumerate(corrupt_documents):
            before = self.write(document)
            for query in queries:
                with self.assertRaises(ValueError, msg=(index, query.__doc__)):
                    query()
            self.assertEqual(self.app.path.read_bytes(), before, index)

    def test_batch_completion_also_rejects_broken_history(self):
        # The batch entry runs the same whole-history validation: a valid group
        # against a broken document fails with ValueError and leaves the bytes.
        good_records = [{"activity_id": "A-001", "member_id": "M-002", "completed_on": "2026-10-15"}]
        broken = dict(LEGACY)
        broken["members"] = dict(LEGACY["members"])
        broken["members"]["M-002"] = {"member_id": "M-002", "name": "   "}
        before = self.write(broken)
        with self.assertRaises(ValueError):
            self.app.record_completions(good_records)
        self.assertEqual(self.app.path.read_bytes(), before)
        # A broken group (duplicate pair) fails argument validation before the
        # filesystem is even touched, so the valid history stays byte-identical.
        self.write(LEGACY)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.record_completions([
                {"activity_id": "A-002", "member_id": "M-001", "completed_on": "2026-10-15"},
                {"activity_id": " A-002 ", "member_id": " M-001 ", "completed_on": "2026-10-15"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_correct_completions_also_rejects_broken_history(self):
        # Correcting runs the same whole-history validation: a valid group
        # against a broken document fails with ValueError and leaves the bytes.
        good_records = [{"activity_id": "A-003", "member_id": "M-001", "completed_on": "2026-10-16"}]
        broken = dict(LEGACY)
        broken["members"] = dict(LEGACY["members"])
        broken["members"]["M-002"] = {"member_id": "M-002", "name": "   "}
        before = self.write(broken)
        with self.assertRaises(ValueError):
            self.app.correct_completions(good_records)
        self.assertEqual(self.app.path.read_bytes(), before)
        # A broken group (duplicate pair) fails argument validation before the
        # filesystem is even touched, so the valid history stays byte-identical.
        self.write(LEGACY)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.correct_completions([
                {"activity_id": "A-003", "member_id": "M-001", "completed_on": "2026-10-16"},
                {"activity_id": " A-003 ", "member_id": " M-001 ", "completed_on": None},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_correct_completions_missing_completions_field_has_nothing(self):
        # An old file without a completions field means zero records, so every
        # correction is rejected and the file is not created or rewritten.
        document = {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
                    "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-001"]}}}
        before = self.write(document)
        for completed_on in ["2026-10-16", None]:
            with self.assertRaises(ValueError):
                self.app.correct_completions([{"activity_id": "A-001", "member_id": "M-001", "completed_on": completed_on}])
            self.assertEqual(self.app.path.read_bytes(), before)

    def test_correct_completions_os_error_stays_an_os_error(self):
        # data.json itself being a directory stays an operating-system error.
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.correct_completions([{"activity_id": "A-001", "member_id": "M-001", "completed_on": None}])

    def test_preview_reschedule_also_rejects_broken_history(self):
        # The preview runs the same whole-history validation: a valid group
        # against a broken document fails with ValueError and leaves the bytes.
        good_changes = [{"activity_id": "A-002", "on": "2026-10-16"}]
        broken = dict(LEGACY)
        broken["members"] = dict(LEGACY["members"])
        broken["members"]["M-002"] = {"member_id": "M-002", "name": "   "}
        before = self.write(broken)
        with self.assertRaises(ValueError):
            self.app.preview_reschedule_activities(good_changes)
        self.assertEqual(self.app.path.read_bytes(), before)
        # A broken group (duplicate id after normalization) fails argument
        # validation before the filesystem is even touched.
        self.write(LEGACY)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.preview_reschedule_activities([
                {"activity_id": "A-002", "on": "2026-10-16"},
                {"activity_id": " A-002 ", "on": "2026-10-17"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_preview_reschedule_os_error_stays_an_os_error(self):
        # data.json itself being a directory stays an operating-system error.
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.preview_reschedule_activities([{"activity_id": "A-001", "on": "2026-10-16"}])

    def test_preview_transfer_also_rejects_broken_history(self):
        # The preview runs the same whole-history validation: a valid group
        # against a broken document fails with ValueError and leaves the bytes.
        good_changes = [{"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-003"}]
        broken = dict(LEGACY)
        broken["members"] = dict(LEGACY["members"])
        broken["members"]["M-002"] = {"member_id": "M-002", "name": "   "}
        before = self.write(broken)
        with self.assertRaises(ValueError):
            self.app.preview_transfer_enrollments(good_changes)
        self.assertEqual(self.app.path.read_bytes(), before)
        # A broken group (duplicate member after normalization) fails argument
        # validation before the filesystem is even touched.
        self.write(LEGACY)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.preview_transfer_enrollments([
                {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-003"},
                {"source_activity_id": "A-002", "target_activity_id": "A-003", "member_id": " M-003 "},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_preview_transfer_os_error_stays_an_os_error(self):
        # data.json itself being a directory stays an operating-system error.
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.preview_transfer_enrollments([
                {"source_activity_id": "A-002", "target_activity_id": "A-001", "member_id": "M-003"},
            ])

    def test_preview_enroll_also_rejects_broken_history(self):
        # The preview runs the same whole-history validation: a valid group
        # against a broken document fails with ValueError and leaves the bytes.
        good_records = [{"activity_id": "A-002", "member_id": "M-002"}]
        broken = dict(LEGACY)
        broken["members"] = dict(LEGACY["members"])
        broken["members"]["M-002"] = {"member_id": "M-002", "name": "   "}
        before = self.write(broken)
        with self.assertRaises(ValueError):
            self.app.preview_enrollments(good_records)
        self.assertEqual(self.app.path.read_bytes(), before)
        # A broken group (duplicate pair after normalization) fails argument
        # validation before the filesystem is even touched.
        self.write(LEGACY)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.preview_enrollments([
                {"activity_id": " A-002 ", "member_id": "M-002"},
                {"activity_id": "A-002", "member_id": " M-002 "},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_preview_enroll_os_error_stays_an_os_error(self):
        # data.json itself being a directory stays an operating-system error.
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.preview_enrollments([{"activity_id": "A-001", "member_id": "M-003"}])

    def test_split_activity_also_rejects_broken_history(self):
        # The split runs the same whole-history validation: a valid request
        # against a broken document fails with ValueError and leaves the bytes.
        good = {"source_activity_id": "A-001", "activity_id": "A-101", "title": "进阶", "on": "2026-10-15", "capacity": 1, "member_ids": ["M-002"]}
        broken = dict(LEGACY)
        broken["members"] = dict(LEGACY["members"])
        broken["members"]["M-002"] = {"member_id": "M-002", "name": "   "}
        before = self.write(broken)
        with self.assertRaises(ValueError):
            self.app.split_activity(**good)
        self.assertEqual(self.app.path.read_bytes(), before)
        # A broken request (duplicate member after normalization) fails
        # argument validation before the filesystem is even touched.
        self.write(LEGACY)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.split_activity("A-001", "A-101", "进阶", "2026-10-15", 2, ["M-002", " M-002 "])
        self.assertEqual(self.app.path.read_bytes(), before)
        # The valid legacy document splits normally: M-001's own completion
        # record for A-003 does not block moving M-002 out of A-001.
        result = self.app.split_activity(**good)
        self.assertEqual(result["new_activity"]["participants"], ["M-002"])
        self.assertEqual(result["source_activity"]["participants"], ["M-001"])

    def test_split_activity_os_error_stays_an_os_error(self):
        # data.json itself being a directory stays an operating-system error.
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.split_activity("A-001", "A-101", "进阶", "2026-10-15", 1, ["M-002"])

    def test_merge_activities_also_rejects_broken_history(self):
        # The merge runs the same whole-history validation: a feasible request
        # against a broken document fails with ValueError and leaves the bytes.
        # A-003 (capacity 2, only M-001) absorbs A-001's M-002 without overflow;
        # the document below is broken via M-002's blank name instead.
        good = {"target_activity_id": "A-003", "source_activity_ids": ["A-001"]}
        broken = dict(LEGACY)
        broken["members"] = dict(LEGACY["members"])
        broken["members"]["M-002"] = {"member_id": "M-002", "name": "   "}
        before = self.write(broken)
        with self.assertRaises(ValueError):
            self.app.merge_activities(**good)
        self.assertEqual(self.app.path.read_bytes(), before)
        # A broken request (duplicate source after normalization) fails
        # argument validation before the filesystem is even touched.
        self.write(LEGACY)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.merge_activities("A-003", ["A-001", " A-001 "])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_merge_activities_os_error_stays_an_os_error(self):
        # data.json itself being a directory stays an operating-system error.
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.merge_activities("A-001", ["A-002"])

    def test_unrelated_broken_record_is_not_hidden_by_filters(self):
        # Querying M-001's own data must still fail because of M-002's broken
        # profile and A-002's broken completion record.
        document = {
            "members": {
                "M-001": {"member_id": "M-001", "name": "小陈"},
                "M-002": {"member_id": "M-002", "name": "   "},
            },
            "activities": {
                "A-001": {"activity_id": "A-001", "title": "一", "on": "2026-10-15", "capacity": 2, "participants": ["M-001"]},
                "A-002": {"activity_id": "A-002", "title": "二", "on": "2026-10-15", "capacity": 2, "participants": ["M-002"]},
            },
            "completions": {"A-002": {"M-002": "2026-10-14"}},
        }
        self.write(document)
        with self.assertRaises(ValueError):
            self.app.member_schedule("M-001")
        with self.assertRaises(ValueError):
            self.app.completions("M-001")
        with self.assertRaises(ValueError):
            self.app.roster("A-001")

    def test_unknown_participant_and_early_completion_specifically_rejected(self):
        document = {
            "members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
            "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-001"]}},
        }
        document["activities"]["A-001"]["participants"].append("M-GHOST")
        before = self.write(document)
        with self.assertRaises(ValueError):
            self.app.roster("A-001")
        self.assertEqual(self.app.path.read_bytes(), before)
        document["activities"]["A-001"]["participants"].pop()
        document["completions"] = {"A-001": {"M-001": "2026-10-14"}}
        before = self.write(document)
        with self.assertRaises(ValueError):
            self.app.completions("M-001")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_malformed_json_and_bad_utf8(self):
        before = self.write(b'{"members": ')
        with self.assertRaises(ValueError):
            self.app.activities()
        self.assertEqual(self.app.path.read_bytes(), before)
        before = self.write(b'\xff\xfe not json')
        with self.assertRaises(ValueError):
            self.app.activities()
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_missing_file_and_directory_are_not_created_by_queries(self):
        empty = self.root / "empty"
        fresh = TeamPlanner(empty)
        self.assertEqual(fresh.activities(), [])
        self.assertFalse(empty.exists())
        with self.assertRaises(ValueError):
            fresh.member_schedule("M-001")
        self.assertFalse(empty.exists())

    def test_os_error_reading_stays_an_os_error(self):
        # data.json itself being a directory is an operating-system error, not
        # a validation failure.
        self.app.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.app.activities()

    def test_cli_rejects_invalid_history_with_error_json_only_on_stderr(self):
        def run(action="list", payload=None):
            args = [sys.executable, "-m", "team_planner", "--root", str(self.root), action]
            if payload is not None:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as stream:
                    json.dump(payload, stream)
                    args.append(stream.name)
            return subprocess.run(args, text=True, capture_output=True)

        self.write(LEGACY)
        ok = run("schedule", {"member_id": "M-001"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(len(json.loads(ok.stdout)), 3)

        bad = {"members": {"M-001": {"member_id": "M-001", "name": "小陈"}},
               "activities": {"A-001": {"activity_id": "A-001", "title": "培训", "on": "2026-10-15", "capacity": 2, "participants": ["M-GHOST"]}}}
        self.write(bad)
        failed = run()
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))

        # Early completion date is rejected through the command as well.
        bad["activities"]["A-001"]["participants"] = ["M-001"]
        bad["completions"] = {"A-001": {"M-001": "2026-10-14"}}
        self.write(bad)
        failed = run("completions", {"member_id": "M-001"})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))

if __name__ == "__main__":
    unittest.main()
