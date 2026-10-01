from datetime import date
from .storage import JsonStore, text, positive, day

STATUSES = ("all", "pending", "completed")

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

    def transfer_enrollment(self, source_activity_id, target_activity_id, member_id):
        source_activity_id = text(source_activity_id, "source_activity_id")
        target_activity_id = text(target_activity_id, "target_activity_id")
        member_id = text(member_id, "member_id")
        data = self._read()
        activities = data.get("activities", {})
        source = activities.get(source_activity_id)
        target = activities.get(target_activity_id)
        if source is None or target is None or member_id not in data.get("members", {}):
            raise ValueError("unknown activity or member")
        if source_activity_id == target_activity_id:
            raise ValueError("source and target activities must differ")
        if member_id not in source["participants"]:
            raise ValueError("member is not enrolled in the source activity")
        if member_id in target["participants"]:
            raise ValueError("member is already enrolled in the target activity")
        if len(target["participants"]) >= target["capacity"]:
            raise ValueError("target activity is full")
        # A finished training can neither be moved nor have its completion
        # record left behind; other members' records do not block the transfer.
        if member_id in data.get("completions", {}).get(source_activity_id, {}):
            raise ValueError("member has a completion record for the source activity")
        # The member must not keep a seat in a third activity on the target
        # date. Completion does not cancel enrollment, so participants lists
        # already cover finished trainings; source and target are excluded.
        for other_id, other in activities.items():
            if other_id in (source_activity_id, target_activity_id):
                continue
            if other.get("on") == target["on"] and member_id in other.get("participants", []):
                raise ValueError("member is enrolled in another activity on the target date")
        source["participants"].remove(member_id)
        target["participants"].append(member_id)
        self._write(data)
        return target

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
        source = text(source_member_id, "source_member_id")
        target = text(target_member_id, "target_member_id")
        if source == target:
            raise ValueError("source and target must be different members")
        data = self._read()
        members = data.get("members", {})
        if source not in members or target not in members:
            raise ValueError("unknown member")
        completions = data.get("completions", {})
        # Validate every activity first: conflicting completion dates reject
        # the whole merge before any participant list is touched.
        for done in completions.values():
            if source in done and target in done and done[source] != done[target]:
                raise ValueError("completion dates differ for the two members")
        for activity in data.get("activities", {}).values():
            participants = activity.setdefault("participants", [])
            if source not in participants:
                continue
            source_index = participants.index(source)
            if target in participants:
                target_index = participants.index(target)
                # One retained seat at the earlier of the two positions;
                # everyone else keeps their relative order.
                keep_index, drop_index = (source_index, target_index) if source_index < target_index else (target_index, source_index)
                participants[keep_index] = target
                del participants[drop_index]
            else:
                participants[source_index] = target
        for activity_id, done in completions.items():
            if source not in done:
                continue
            merged = {}
            for member_id, completed_on in done.items():
                if member_id == source:
                    if target not in done:
                        merged[target] = completed_on
                else:
                    merged[member_id] = completed_on
            completions[activity_id] = merged
        del members[source]
        self._write(data)
        return members[target]

    def activities(self):
        return sorted(self._read().get("activities", {}).values(), key=lambda a: (a["on"], a["activity_id"]))

    def member_schedule(self, member_id, from_on=None, to_on=None, status="all"):
        member_id = text(member_id, "member_id")
        start = day(from_on, "from_on") if from_on is not None else None
        end = day(to_on, "to_on") if to_on is not None else None
        if start is not None and end is not None and start > end:
            raise ValueError("from_on must be on or before to_on")
        if status not in STATUSES:
            raise ValueError("status must be one of all, pending, completed")
        data = self._read()
        if member_id not in data.get("members", {}):
            raise ValueError("unknown member")
        activities = data.get("activities", {})
        completions = data.get("completions", {})
        # All activities the member is enrolled in, keyed by date, are known
        # first: same-day conflicts count even when the other enrollment is
        # completed or hidden by the date/status filters.
        same_day = {}
        for other_id, other in activities.items():
            if member_id in other.get("participants", []):
                same_day.setdefault(other.get("on"), set()).add(other_id)
        records = []
        for activity_id, activity in activities.items():
            if member_id not in activity.get("participants", []):
                continue
            on = activity["on"]
            if start is not None and on < start:
                continue
            if end is not None and on > end:
                continue
            completed_on = completions.get(activity_id, {}).get(member_id)
            row_status = "completed" if completed_on is not None else "pending"
            if status != "all" and row_status != status:
                continue
            records.append({
                "activity_id": activity_id,
                "title": activity["title"],
                "on": on,
                "status": row_status,
                "completed_on": completed_on,
                "conflict_activity_ids": sorted(aid for aid in same_day.get(on, ()) if aid != activity_id),
            })
        return sorted(records, key=lambda r: (r["on"], r["activity_id"]))
