from pathlib import Path
import json
import os
import tempfile
from datetime import date

class JsonStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "data.json"

    def _read(self):
        if not self.path.exists():
            return {}
        raw = self.path.read_bytes()
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("data.json must be valid UTF-8") from error
        try:
            value = json.loads(decoded)
        except json.JSONDecodeError as error:
            raise ValueError("data.json must contain valid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("stored document must be an object")
        validate_document(value)
        return value

    def _write(self, value):
        self.root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".data-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be a nonempty string")
    return value.strip()

def positive(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(label + " must be a positive integer")
    return value

def day(value, label):
    # Only a real YYYY-MM-DD calendar date is accepted: no other types,
    # no surrounding whitespace, no relaxed parsing.
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise ValueError(label + " must be a YYYY-MM-DD date string")
    try:
        parsed = date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except (ValueError, OverflowError):
        raise ValueError(label + " must be a real YYYY-MM-DD date")
    if parsed.isoformat() != value:
        raise ValueError(label + " must be a real YYYY-MM-DD date")
    return parsed.isoformat()

def _collection(value, label):
    # A present collection must be a JSON object; null is not an empty object.
    if not isinstance(value, dict):
        raise ValueError(label + " must be an object")
    return value

def _key(key, label):
    # JSON object keys are always strings; reject empty ones and any with
    # surrounding whitespace (internal whitespace is allowed).
    if not key or key != key.strip():
        raise ValueError(label + " keys must be nonempty strings without surrounding whitespace")

def validate_document(data):
    # Validate the whole historical document, including records a query would
    # filter out, so one bad entry rejects every read of the file.
    members = {} if "members" not in data else _collection(data["members"], "members")
    activities = {} if "activities" not in data else _collection(data["activities"], "activities")
    completions = {} if "completions" not in data else _collection(data["completions"], "completions")

    for key, member in members.items():
        _key(key, "members")
        if not isinstance(member, dict):
            raise ValueError("member entry must be an object")
        if member.get("member_id") != key:
            raise ValueError("member_id must match its object key")
        text(member.get("name"), "name")

    activity_dates = {}
    for key, activity in activities.items():
        _key(key, "activities")
        if not isinstance(activity, dict):
            raise ValueError("activity entry must be an object")
        if activity.get("activity_id") != key:
            raise ValueError("activity_id must match its object key")
        text(activity.get("title"), "title")
        on = day(activity.get("on"), "on")
        activity_dates[key] = on
        capacity = positive(activity.get("capacity"), "capacity")
        participants = activity.get("participants")
        if not isinstance(participants, list):
            raise ValueError("participants must be an array")
        seen = set()
        for member_id in participants:
            if not isinstance(member_id, str) or not member_id or member_id != member_id.strip():
                raise ValueError("participant identifiers must be nonempty strings without surrounding whitespace")
            if member_id not in members:
                raise ValueError("participant references an unknown member")
            if member_id in seen:
                raise ValueError("participants must not contain duplicate members")
            seen.add(member_id)
        if len(participants) > capacity:
            raise ValueError("participants exceed the activity capacity")

    for activity_id, done in completions.items():
        _key(activity_id, "completions")
        if activity_id not in activities:
            raise ValueError("completion references an unknown activity")
        if not isinstance(done, dict):
            raise ValueError("completion records must be an object")
        enrolled = activities[activity_id]["participants"]
        on = activity_dates[activity_id]
        for member_id, completed_on in done.items():
            _key(member_id, "completion")
            if member_id not in enrolled:
                raise ValueError("completion member must be enrolled in the activity")
            if day(completed_on, "completed_on") < on:
                raise ValueError("completed_on must be on or after the activity date")
