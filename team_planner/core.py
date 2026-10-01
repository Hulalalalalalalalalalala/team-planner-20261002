from datetime import date
from .storage import JsonStore, text, positive, day

class TeamPlanner(JsonStore):
    def add_member(self, member_id, name):
        member_id, name = text(member_id, "member_id"), text(name, "name")
        data = self._read()
        members = data.setdefault("members", {})
        if member_id in members:
            raise ValueError("member already exists")
        members[member_id] = {"member_id": member_id, "name": name}
        self._write(data)
        return members[member_id]

    def create_activity(self, activity_id, title, on, capacity):
        activity_id, title = text(activity_id, "activity_id"), text(title, "title")
        on, capacity = date.fromisoformat(on).isoformat(), positive(capacity, "capacity")
        data = self._read()
        activities = data.setdefault("activities", {})
        if activity_id in activities:
            raise ValueError("activity already exists")
        activity = {"activity_id": activity_id, "title": title, "on": on, "capacity": capacity, "participants": []}
        activities[activity_id] = activity
        self._write(data)
        return activity

    def enroll(self, activity_id, member_id):
        data = self._read()
        activity = data.get("activities", {}).get(activity_id)
        if activity is None or member_id not in data.get("members", {}):
            raise ValueError("unknown activity or member")
        if member_id in activity["participants"]:
            raise ValueError("member already enrolled")
        if len(activity["participants"]) >= activity["capacity"]:
            raise ValueError("activity is full")
        activity["participants"].append(member_id)
        self._write(data)
        return activity

    def reschedule_activity(self, activity_id, on):
        activity_id = text(activity_id, "activity_id")
        on = day(on, "on")
        data = self._read()
        activity = data.get("activities", {}).get(activity_id)
        if activity is None:
            raise ValueError("unknown activity")
        # Same date is always a no-op, even with completion records or a
        # same-day conflict; nothing is rewritten.
        if on == activity["on"]:
            return activity
        if data.get("completions", {}).get(activity_id):
            raise ValueError("activity already has completion records")
        participants = set(activity["participants"])
        for other_id, other in data.get("activities", {}).items():
            if other_id == activity_id or other.get("on") != on:
                continue
            # Completion keeps the enrollment, so participants lists already
            # include members who finished the training.
            if participants & set(other.get("participants", [])):
                raise ValueError("participant is enrolled in another activity on that date")
        activity["on"] = on
        self._write(data)
        return activity

    def roster(self, activity_id):
        data = self._read()
        activity = data.get("activities", {}).get(activity_id)
        if activity is None:
            raise ValueError("unknown activity")
        return {"activity_id": activity_id, "title": activity["title"], "on": activity["on"], "capacity": activity["capacity"], "members": [data["members"][member] for member in activity["participants"]]}

    def record_completion(self, activity_id, member_id, completed_on):
        activity_id = text(activity_id, "activity_id")
        member_id = text(member_id, "member_id")
        completed_on = day(completed_on, "completed_on")
        data = self._read()
        activity = data.get("activities", {}).get(activity_id)
        if activity is None:
            raise ValueError("unknown activity")
        if member_id not in data.get("members", {}):
            raise ValueError("unknown member")
        if member_id not in activity["participants"]:
            raise ValueError("member is not enrolled in the activity")
        done = data.setdefault("completions", {})
        if member_id in done.get(activity_id, {}):
            raise ValueError("completion already recorded")
        if date.fromisoformat(completed_on) < date.fromisoformat(activity["on"]):
            raise ValueError("completed_on must be on or after the activity date")
        # Recording completion neither cancels enrollment nor frees capacity;
        # the participant order in the activity is left untouched.
        done.setdefault(activity_id, {})[member_id] = completed_on
        self._write(data)
        return {"activity_id": activity_id, "member_id": member_id, "completed_on": completed_on, "title": activity["title"], "on": activity["on"]}

    def completions(self, member_id):
        member_id = text(member_id, "member_id")
        data = self._read()
        if member_id not in data.get("members", {}):
            raise ValueError("unknown member")
        records = []
        for activity_id, members in data.get("completions", {}).items():
            if member_id not in members:
                continue
            activity = data.get("activities", {}).get(activity_id)
            if activity is None:
                continue
            records.append({"activity_id": activity_id, "member_id": member_id, "completed_on": members[member_id], "title": activity["title"], "on": activity["on"]})
        return sorted(records, key=lambda r: (r["completed_on"], r["on"], r["activity_id"]))

    def merge_member(self, source_member_id, target_member_id):
        source_member_id = text(source_member_id, "source_member_id")
        target_member_id = text(target_member_id, "target_member_id")
        if source_member_id == target_member_id:
            raise ValueError("source_member_id and target_member_id must differ")
        data = self._read()
        members = data.get("members", {})
        if source_member_id not in members or target_member_id not in members:
            raise ValueError("unknown member")
        # Reject the whole merge before mutating anything if any activity has
        # conflicting completion dates; no date is picked and no record is
        # overwritten. Missing completions (legacy files) means no records.
        for records in data.get("completions", {}).values():
            if source_member_id in records and target_member_id in records:
                if records[source_member_id] != records[target_member_id]:
                    raise ValueError("completion records conflict")
        # Merge enrollments: source alone is replaced in place; when both are
        # enrolled, one target entry stays at the earlier position.
        for activity in data.get("activities", {}).values():
            participants = activity.get("participants", [])
            if source_member_id not in participants:
                continue
            if target_member_id in participants:
                keep = min(participants.index(source_member_id), participants.index(target_member_id))
                participants[:] = [p for p in participants if p != source_member_id and p != target_member_id]
                participants.insert(keep, target_member_id)
            else:
                participants[:] = [target_member_id if p == source_member_id else p for p in participants]
        # Attribute all completion records to the retained member; equal dates
        # collapse into the target's single existing record.
        for records in data.get("completions", {}).values():
            if source_member_id in records:
                if target_member_id not in records:
                    records[target_member_id] = records[source_member_id]
                del records[source_member_id]
        retained = members[target_member_id]
        del members[source_member_id]
        self._write(data)
        return retained

    def activities(self):
        return sorted(self._read().get("activities", {}).values(), key=lambda a: (a["on"], a["activity_id"]))
