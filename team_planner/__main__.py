import argparse
import json
from pathlib import Path
import sys
import tempfile
from . import TeamPlanner

ACTIONS = {'member': 'add_member', 'activity': 'create_activity', 'enroll': 'enroll', 'transfer': 'transfer_enrollment', 'reschedule': 'reschedule_activity', 'roster': 'roster', 'list': 'activities', 'complete': 'record_completion', 'completions': 'completions', 'merge': 'merge_member', 'schedule': 'member_schedule'}

def samples(name):
    return json.loads((Path(__file__).resolve().parent.parent / "examples" / name).read_text(encoding="utf-8"))

def demo(app):
    for member in samples("members.json"):
        app.add_member(**member)
    app.create_activity(**samples("activity.json"))
    app.enroll(**samples("enrollment.json"))
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
