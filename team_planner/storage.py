from pathlib import Path
import json
import os
import tempfile
from datetime import date

COLLECTIONS = ("members", "activities", "completions")

class JsonStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "data.json"

    def _read(self):
        # A missing file is an empty history. Any other filesystem problem
        # (permissions, data.json being a directory, ...) stays an OSError.
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("data.json must be a valid UTF-8 file")
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError("data.json must be valid JSON")
        if not isinstance(value, dict):
            raise ValueError("stored document must be an object")
        # The whole history is validated on every read, so a query can never
        # hide an unrelated broken record behind its filters.
        validate_document(value)
        return value

    def _write(self, value):
        # Validate before touching the filesystem: invalid data never creates
        # a missing directory or file and never replaces an existing file.
        validate_document(value)
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

def identifier(value, label):
    # Stored keys and participant ids are matched exactly, never trimmed:
    # surrounding whitespace or an empty string are invalid on disk.
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(label + " must be a nonempty string without surrounding whitespace")
    return value

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

def validate_document(data):
    # Business consistency check for a whole historical document. Missing
    # collections are empty; present ones must be JSON objects (null is not an
    # empty object). Extra fields on the document, members and activities are
    # allowed and left untouched by the caller.
    if not isinstance(data, dict):
        raise ValueError("stored document must be an object")
    for name in COLLECTIONS:
        if name in data and not isinstance(data[name], dict):
            raise ValueError(name + " must be an object")
    members = data.get("members", {})
    activities = data.get("activities", {})
    completions = data.get("completions", {})

    for key, member in members.items():
        identifier(key, "member key")
        if not isinstance(member, dict):
            raise ValueError("member entry must be an object")
        if not isinstance(member.get("member_id"), str) or member["member_id"] != key:
            raise ValueError("member_id must match the member object key")
        if not isinstance(member.get("name"), str) or not member["name"].strip():
            raise ValueError("member name must be a nonblank string")

    activity_dates = {}
    for key, activity in activities.items():
        identifier(key, "activity key")
        if not isinstance(activity, dict):
            raise ValueError("activity entry must be an object")
        if not isinstance(activity.get("activity_id"), str) or activity["activity_id"] != key:
            raise ValueError("activity_id must match the activity object key")
        if not isinstance(activity.get("title"), str) or not activity["title"].strip():
            raise ValueError("activity title must be a nonblank string")
        activity_dates[key] = day(activity.get("on"), "on")
        positive(activity.get("capacity"), "capacity")
        participants = activity.get("participants")
        if not isinstance(participants, list):
            raise ValueError("participants must be an array")
        seen = set()
        for member_id in participants:
            identifier(member_id, "participant id")
            if member_id in seen:
                raise ValueError("participants must not contain duplicate member ids")
            if member_id not in members:
                raise ValueError("participant references an unknown member")
            seen.add(member_id)
        if len(participants) > activity["capacity"]:
            raise ValueError("participant count must not exceed capacity")

    for activity_id, done in completions.items():
        identifier(activity_id, "completion activity key")
        if not isinstance(done, dict):
            raise ValueError("completion records must be an object")
        if activity_id not in activities:
            raise ValueError("completion references an unknown activity")
        enrolled = set(activities[activity_id]["participants"])
        for member_id, completed_on in done.items():
            identifier(member_id, "completion member id")
            if member_id not in enrolled:
                raise ValueError("completion references a member not enrolled in the activity")
            completed_on = day(completed_on, "completed_on")
            if date.fromisoformat(completed_on) < date.fromisoformat(activity_dates[activity_id]):
                raise ValueError("completed_on must be on or after the activity date")
