import argparse
import json
from pathlib import Path
import sys
import tempfile
from . import TeamPlanner

ACTIONS = {'member': 'add_member', 'activity': 'create_activity', 'enroll': 'enroll', 'enroll-batch': 'enroll_batch', 'cancel': 'cancel_enrollments', 'transfer': 'transfer_enrollment', 'transfer-batch': 'transfer_enrollments', 'preview-transfer': 'preview_transfer_enrollments', 'split-activity': 'split_activity', 'reschedule': 'reschedule_activity', 'reschedule-batch': 'reschedule_activities', 'preview-reschedule': 'preview_reschedule_activities', 'roster': 'roster', 'list': 'activities', 'complete': 'record_completion', 'complete-batch': 'record_completions', 'correct-completions': 'correct_completions', 'completions': 'completions', 'merge': 'merge_member', 'schedule': 'member_schedule', 'enrollment-options': 'enrollment_options', 'training-progress': 'training_progress', 'export-schedule': 'export_schedule'}

def samples(name):
    return json.loads((Path(__file__).resolve().parent.parent / "examples" / name).read_text(encoding="utf-8"))

def demo(app):
    for member in samples("members.json"):
        app.add_member(**member)
    app.create_activity(**samples("activity.json"))
    app.enroll(**samples("enrollment.json"))
    # 2026-10-15 的虚构安排：A-002 与 A-001 同日，由小陈和小周报满；
    # 小陈在 A-003 已登记完成，报名仍然保留，当天原本互为冲突。
    app.create_activity("A-002", "安全规范", "2026-10-15", 2)
    app.create_activity("A-003", "团队协作", "2026-10-15", 2)
    app.enroll("A-002", "M-001")
    app.enroll("A-002", "M-003")
    app.enroll("A-003", "M-001")
    app.record_completion("A-003", "M-001", "2026-10-15")
    # 一次取消两场，标识带首尾空白也会先去除；满员的 A-002 释放席位，
    # 返回顺序与输入一致，且各活动只移除小陈本人。
    cancelled = app.cancel_enrollments("M-001", [" A-002 ", "A-001"])
    if [a["activity_id"] for a in cancelled] != ["A-002", "A-001"]:
        raise ValueError("demo: cancelled activities must follow the input order")
    if cancelled[0]["participants"] != ["M-003"] or cancelled[1]["participants"] != []:
        raise ValueError("demo: only the cancelling member is removed")
    # 取消后小陈只剩已完成的 A-003，同日冲突随之消失。
    schedule = app.member_schedule("M-001")
    if [r["activity_id"] for r in schedule] != ["A-003"] or schedule[0]["conflict_activity_ids"] != []:
        raise ValueError("demo: same-day conflicts must disappear after cancellation")
    # 释放的席位立即可用：小林报进原本满员的 A-002，小陈重新报回 A-001。
    app.enroll("A-002", "M-002")
    app.enroll("A-001", "M-001")
    # 小陈在 A-003 已有本人完成记录：整次取消被拒绝，两边名单均不变，
    # 完成记录继续保留。
    try:
        app.cancel_enrollments("M-001", ["A-001", "A-003"])
    except ValueError:
        pass
    else:
        raise ValueError("demo: cancelling a completed activity must fail")
    if [m["member_id"] for m in app.roster("A-001")["members"]] != ["M-001"]:
        raise ValueError("demo: rosters must stay unchanged after a rejected cancellation")
    if [m["member_id"] for m in app.roster("A-003")["members"]] != ["M-001"]:
        raise ValueError("demo: the completed activity roster must stay unchanged")
    if [r["activity_id"] for r in app.completions("M-001")] != ["A-003"]:
        raise ValueError("demo: completion records must be kept")
    # 整组完成登记：2026-10-15 当天两场虚构培训（小陈的 A-001、小周的 A-002）
    # 一次性提交，允许跨活动、跨成员，标识带首尾空白会先去除；返回顺序与输入
    # 一致，字段与单条 complete 相同，两场都应登记成功。
    recorded = app.record_completions([
        {"activity_id": " A-001 ", "member_id": "M-001", "completed_on": "2026-10-15"},
        {"activity_id": "A-002", "member_id": " M-003 ", "completed_on": "2026-10-15"},
    ])
    if [r["activity_id"] for r in recorded] != ["A-001", "A-002"]:
        raise ValueError("demo: batch records must follow the input order")
    if recorded[0] != {"activity_id": "A-001", "member_id": "M-001", "completed_on": "2026-10-15", "title": "新成员产品介绍", "on": "2026-10-15"}:
        raise ValueError("demo: batch record fields must match the single complete result")
    # 末条失败（小陈在 A-003 已有完成记录）时整组拒绝：首条小林在 A-002 的
    # 新记录也不得写入，数据文件字节不变。
    before = app.path.read_bytes()
    try:
        app.record_completions([
            {"activity_id": "A-002", "member_id": "M-002", "completed_on": "2026-10-15"},
            {"activity_id": "A-003", "member_id": "M-001", "completed_on": "2026-10-15"},
        ])
    except ValueError:
        pass
    else:
        raise ValueError("demo: a batch with a failing last record must be rejected")
    if before != app.path.read_bytes():
        raise ValueError("demo: a rejected batch must leave data.json bytes unchanged")
    if app.completions("M-002") != []:
        raise ValueError("demo: no record from the rejected batch may survive")
    # 完成登记不取消报名：两场新完成的培训仍然占位、名单顺序不变。
    if [m["member_id"] for m in app.roster("A-001")["members"]] != ["M-001"]:
        raise ValueError("demo: completion keeps the enrollment")
    return app.roster("A-001")

def main(argv=None):
    parser = argparse.ArgumentParser(description="团队培训安排")
    parser.add_argument("--root", required=True, help="local data directory")
    parser.add_argument("action", choices=[*ACTIONS, "demo"])
    parser.add_argument("input", nargs="?", help="UTF-8 JSON object, or array of objects, containing API arguments")
    args = parser.parse_args(argv)
    try:
        if args.action == "demo":
            # Sample operations run in a fresh child directory and never overwrite user's data.
            Path(args.root).mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="sample-", dir=args.root) as location:
                value = demo(TeamPlanner(location))
        else:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8")) if args.input else {}
            app = TeamPlanner(args.root)
            method = getattr(app, ACTIONS[args.action])
            if isinstance(payload, list):
                value = []
                for row in payload:
                    if not isinstance(row, dict):
                        raise ValueError("each input must be an object")
                    value.append(method(**row))
            elif isinstance(payload, dict):
                value = method(**payload)
            else:
                raise ValueError("input must be an object or array")
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
