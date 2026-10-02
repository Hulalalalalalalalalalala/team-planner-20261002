import csv
from datetime import date
import io
import json
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

    def cancel_enrollments(self, member_id, activity_ids):
        member_id = text(member_id, "member_id")
        if not isinstance(activity_ids, list) or not activity_ids:
            raise ValueError("activity_ids must be a nonempty array")
        ids = []
        seen = set()
        for activity_id in activity_ids:
            activity_id = text(activity_id, "activity_id")
            if activity_id in seen:
                raise ValueError("activity_ids must not contain duplicates")
            seen.add(activity_id)
            ids.append(activity_id)
        data = self._read()
        activities = data.get("activities", {})
        if member_id not in data.get("members", {}):
            raise ValueError("unknown member")
        selected = []
        # Validate every selected activity first: existence, an enrollment of
        # this member, and no own completion record. Other members' records do
        # not block the cancellation; the whole call takes effect only when all
        # conditions hold.
        for activity_id in ids:
            activity = activities.get(activity_id)
            if activity is None:
                raise ValueError("unknown activity")
            if member_id not in activity.get("participants", []):
                raise ValueError("member is not enrolled in the activity")
            if member_id in data.get("completions", {}).get(activity_id, {}):
                raise ValueError("member has a completion record for the activity")
            selected.append(activity)
        # Removing the member keeps everyone else's relative order; titles,
        # dates and capacities are untouched. Completion records stay as they
        # are and the freed seats are usable immediately.
        for activity in selected:
            activity["participants"].remove(member_id)
        self._write(data)
        return selected

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

    def reschedule_activities(self, changes):
        if not isinstance(changes, list) or not changes:
            raise ValueError("changes must be a nonempty array")
        entries = []
        seen = set()
        for item in changes:
            if not isinstance(item, dict) or set(item) != {"activity_id", "on"}:
                raise ValueError("each change must contain only activity_id and on")
            activity_id = text(item["activity_id"], "activity_id")
            on = day(item["on"], "on")
            if activity_id in seen:
                raise ValueError("changes must not contain duplicate activity ids")
            seen.add(activity_id)
            entries.append((activity_id, on))
        data = self._read()
        activities = data.get("activities", {})
        selected = []
        for activity_id, on in entries:
            activity = activities.get(activity_id)
            if activity is None:
                raise ValueError("unknown activity")
            selected.append(activity)
        # Only activities whose date actually moves are constrained; entries
        # keeping their date may carry completion records or old conflicts.
        moving = [(activity, on) for activity, (_, on) in zip(selected, entries) if on != activity["on"]]
        # Nothing to change: return the activities as they are and leave the
        # file untouched, even when completion records or conflicts exist.
        if not moving:
            return selected
        completions = data.get("completions", {})
        final_on = {activity_id: on for activity_id, on in entries}
        # Conflicts are judged against every activity's date AFTER the whole
        # group moves, so feasible swaps are not rejected by an intermediate
        # state. Validate everything before touching any date.
        for activity, on in moving:
            activity_id = activity["activity_id"]
            if completions.get(activity_id):
                raise ValueError("activity already has completion records")
            participants = set(activity.get("participants", []))
            for other_id, other in activities.items():
                if other_id == activity_id:
                    continue
                other_on = final_on.get(other_id, other.get("on"))
                if other_on != on:
                    continue
                # Completion keeps the enrollment, so participants lists already
                # include members who finished the training; an activity in
                # which only other members are enrolled is no conflict.
                if participants & set(other.get("participants", [])):
                    raise ValueError("participant is enrolled in another activity on that date")
        for activity, on in moving:
            activity["on"] = on
        self._write(data)
        return selected

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

    def record_completions(self, records):
        if not isinstance(records, list) or not records:
            raise ValueError("records must be a nonempty array")
        entries = []
        seen = set()
        # Normalize and check the whole input before reading history: bad
        # types, shapes or dates must never create a missing directory or file.
        for item in records:
            if not isinstance(item, dict) or set(item) != {"activity_id", "member_id", "completed_on"}:
                raise ValueError("each record must contain only activity_id, member_id and completed_on")
            activity_id = text(item["activity_id"], "activity_id")
            member_id = text(item["member_id"], "member_id")
            completed_on = day(item["completed_on"], "completed_on")
            # The same activity/member pair is rejected even with an identical
            # date: duplicates are never merged into one record.
            if (activity_id, member_id) in seen:
                raise ValueError("records must not contain duplicate activity and member pairs")
            seen.add((activity_id, member_id))
            entries.append((activity_id, member_id, completed_on))
        data = self._read()
        activities = data.get("activities", {})
        members = data.get("members", {})
        completions = data.get("completions", {})
        selected = []
        # Validate every record first: the group takes effect only when each
        # activity and member exists, the member is enrolled, no own completion
        # exists for that activity, and the date is on or after the activity.
        # Other members' completion records never block the registration.
        for activity_id, member_id, completed_on in entries:
            activity = activities.get(activity_id)
            if activity is None:
                raise ValueError("unknown activity")
            if member_id not in members:
                raise ValueError("unknown member")
            if member_id not in activity.get("participants", []):
                raise ValueError("member is not enrolled in the activity")
            if member_id in completions.get(activity_id, {}):
                raise ValueError("completion already recorded")
            if date.fromisoformat(completed_on) < date.fromisoformat(activity["on"]):
                raise ValueError("completed_on must be on or after the activity date")
            selected.append((activity, member_id, completed_on))
        # Recording completions neither cancels enrollments nor frees capacity;
        # participant order, profiles, activity fields and existing records are
        # left untouched. Records may span activities and members.
        done = data.setdefault("completions", {})
        results = []
        for activity, member_id, completed_on in selected:
            done.setdefault(activity["activity_id"], {})[member_id] = completed_on
            results.append({"activity_id": activity["activity_id"], "member_id": member_id, "completed_on": completed_on, "title": activity["title"], "on": activity["on"]})
        self._write(data)
        return results

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
        if from_on is not None:
            from_on = day(from_on, "from_on")
        if to_on is not None:
            to_on = day(to_on, "to_on")
        if from_on is not None and to_on is not None and from_on > to_on:
            raise ValueError("from_on must be on or before to_on")
        if status not in ("all", "pending", "completed"):
            raise ValueError("status must be one of all, pending, completed")
        data = self._read()
        if member_id not in data.get("members", {}):
            raise ValueError("unknown member")
        activities = data.get("activities", {})
        completions = data.get("completions", {})
        # Conflicts come from the member's full current schedule, ignoring the
        # date range and status filters, so a completed or filtered-out
        # enrollment still shows up as a same-day conflict.
        enrolled_on = {}
        for activity_id, activity in activities.items():
            if member_id in activity.get("participants", []):
                enrolled_on.setdefault(activity.get("on"), set()).add(activity_id)
        records = []
        for activity_id, activity in activities.items():
            if member_id not in activity.get("participants", []):
                continue
            on = activity["on"]
            if from_on is not None and on < from_on:
                continue
            if to_on is not None and on > to_on:
                continue
            completed_on = completions.get(activity_id, {}).get(member_id)
            current = "completed" if completed_on is not None else "pending"
            if status != "all" and current != status:
                continue
            records.append({
                "activity_id": activity_id,
                "title": activity["title"],
                "on": on,
                "status": current,
                "completed_on": completed_on,
                "conflict_activity_ids": sorted(aid for aid in enrolled_on.get(on, ()) if aid != activity_id),
            })
        return sorted(records, key=lambda r: (r["on"], r["activity_id"]))

    def export_schedule(self, from_on=None, to_on=None, status="all"):
        if from_on is not None:
            from_on = day(from_on, "from_on")
        if to_on is not None:
            to_on = day(to_on, "to_on")
        if from_on is not None and to_on is not None and from_on > to_on:
            raise ValueError("from_on must be on or before to_on")
        if status not in ("all", "pending", "completed"):
            raise ValueError("status must be one of all, pending, completed")
        data = self._read()
        members = data.get("members", {})
        activities = data.get("activities", {})
        completions = data.get("completions", {})
        # Conflicts come from each member's full current enrollment, ignoring
        # the date range and status filters, exactly like member_schedule:
        # a completed or filtered-out enrollment still counts, other members'
        # enrollments never do.
        enrolled_on = {}
        for activity_id, activity in activities.items():
            for member_id in activity.get("participants", []):
                enrolled_on.setdefault(member_id, {}).setdefault(activity.get("on"), set()).add(activity_id)
        rows = []
        for activity_id, activity in activities.items():
            on = activity["on"]
            if from_on is not None and on < from_on:
                continue
            if to_on is not None and on > to_on:
                continue
            done = completions.get(activity_id, {})
            for member_id in activity.get("participants", []):
                completed_on = done.get(member_id)
                current = "completed" if completed_on is not None else "pending"
                if status != "all" and current != status:
                    continue
                conflicts = sorted(aid for aid in enrolled_on[member_id].get(on, ()) if aid != activity_id)
                rows.append([
                    member_id,
                    members[member_id]["name"],
                    activity_id,
                    activity["title"],
                    on,
                    current,
                    completed_on if completed_on is not None else "",
                    json.dumps(conflicts, ensure_ascii=False, separators=(",", ":")),
                ])
        rows.sort(key=lambda row: (row[4], row[2], row[0]))
        buffer = io.StringIO()
        # QUOTE_MINIMAL quotes exactly the cells containing a comma, a double
        # quote or a newline; inner quotes are doubled. CRLF line endings, no
        # BOM, so Chinese text and embedded newlines survive untouched.
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(["member_id", "name", "activity_id", "title", "on", "status", "completed_on", "conflict_activity_ids"])
        writer.writerows(rows)
        return {"csv": buffer.getvalue(), "row_count": len(rows)}
