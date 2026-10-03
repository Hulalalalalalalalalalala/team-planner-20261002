import csv
from datetime import date, timedelta
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

    def enroll_batch(self, records):
        # The whole group takes effect at once: every entry is normalized and
        # checked before any participant list is touched.
        if not isinstance(records, list) or not records:
            raise ValueError("records must be a nonempty array")
        entries = []
        seen = set()
        for item in records:
            if not isinstance(item, dict) or set(item) != {"activity_id", "member_id"}:
                raise ValueError("each record must contain only activity_id and member_id")
            activity_id = text(item["activity_id"], "activity_id")
            member_id = text(item["member_id"], "member_id")
            # A repeated activity/member pair rejects the whole group; one
            # member may still join several activities and one activity may
            # still receive several members.
            pair = (activity_id, member_id)
            if pair in seen:
                raise ValueError("records must not contain duplicate activity and member pairs")
            seen.add(pair)
            entries.append((activity_id, member_id))
        data = self._read()
        activities = data.get("activities", {})
        members = data.get("members", {})
        for activity_id, member_id in entries:
            activity = activities.get(activity_id)
            if activity is None or member_id not in members:
                raise ValueError("unknown activity or member")
            if member_id in activity["participants"]:
                raise ValueError("member already enrolled")
        # Capacity is judged on the final rosters: the original list plus
        # every new enrollment of this group. Completed enrollments keep
        # their seats, so the participants lists already cover them.
        delta = {}
        for activity_id, _ in entries:
            delta[activity_id] = delta.get(activity_id, 0) + 1
        for activity_id, change in delta.items():
            activity = activities[activity_id]
            if len(activity["participants"]) + change > activity["capacity"]:
                raise ValueError("activity is full")
        # Same-day conflicts are judged on the final enrollments too: a new
        # enrollment must not collide with the member's existing enrollments
        # on that date (completed ones still count) nor with another entry of
        # this group. Other members' enrollments and historical conflicts on
        # dates the member is not newly joining never block the group.
        joining = {}
        for activity_id, member_id in entries:
            joining.setdefault(member_id, {}).setdefault(activities[activity_id]["on"], set()).add(activity_id)
        for activity_id, member_id in entries:
            on = activities[activity_id]["on"]
            if len(joining[member_id][on]) > 1:
                raise ValueError("member is enrolled in another activity on that date")
            for other_id, other in activities.items():
                if other_id != activity_id and other.get("on") == on and member_id in other.get("participants", []):
                    raise ValueError("member is enrolled in another activity on that date")
        # New members are appended to each roster in input order; everyone
        # already enrolled keeps their relative position.
        for activity_id, member_id in entries:
            activities[activity_id]["participants"].append(member_id)
        self._write(data)
        # One full activity object per entry, in input order; an activity
        # named by several entries reflects the final roster after the group.
        return [activities[activity_id] for activity_id, _ in entries]

    def preview_enrollments(self, records):
        # Read-only preview of enroll_batch: the same normalization and
        # validation, but nothing is ever written back and no missing
        # directory or file is created.
        if not isinstance(records, list) or not records:
            raise ValueError("records must be a nonempty array")
        entries = []
        seen = set()
        for item in records:
            if not isinstance(item, dict) or set(item) != {"activity_id", "member_id"}:
                raise ValueError("each record must contain only activity_id and member_id")
            activity_id = text(item["activity_id"], "activity_id")
            member_id = text(item["member_id"], "member_id")
            # A repeated activity/member pair rejects the preview, exactly as
            # it rejects the mutating batch.
            pair = (activity_id, member_id)
            if pair in seen:
                raise ValueError("records must not contain duplicate activity and member pairs")
            seen.add(pair)
            entries.append((activity_id, member_id))
        data = self._read()
        activities = data.get("activities", {})
        members = data.get("members", {})
        # Structural errors reject the preview outright, exactly as in the
        # mutating batch: unknown activity or member, or a member already
        # enrolled in the named activity.
        for activity_id, member_id in entries:
            activity = activities.get(activity_id)
            if activity is None or member_id not in members:
                raise ValueError("unknown activity or member")
            if member_id in activity["participants"]:
                raise ValueError("member already enrolled")
        # Remaining seats are judged on the final rosters: capacity minus the
        # original list minus every new enrollment of this group. Entries for
        # the same activity share one count, an overbooked activity keeps its
        # negative count, and completed enrollments still occupy their seats.
        delta = {}
        for activity_id, _ in entries:
            delta[activity_id] = delta.get(activity_id, 0) + 1
        remaining = {
            activity_id: activities[activity_id]["capacity"] - (len(activities[activity_id].get("participants", [])) + change)
            for activity_id, change in delta.items()
        }
        # Conflicts are judged on the final enrollments too: the member's
        # current enrollments on the target date plus the other entries of
        # this group on that date, excluding the entry's own target.
        # Completed enrollments still count; other members' enrollments and
        # other dates never do.
        joining = {}
        for activity_id, member_id in entries:
            joining.setdefault(member_id, {}).setdefault(activities[activity_id]["on"], set()).add(activity_id)
        preview = []
        for activity_id, member_id in entries:
            on = activities[activity_id]["on"]
            conflicts = set(joining[member_id][on])
            for other_id, other in activities.items():
                if other.get("on") == on and member_id in other.get("participants", []):
                    conflicts.add(other_id)
            conflicts.discard(activity_id)
            preview.append({
                "activity_id": activity_id,
                "member_id": member_id,
                "remaining_seats": remaining[activity_id],
                "conflict_activity_ids": sorted(conflicts),
            })
        # Both kinds of obstacles are reported together; only a group where
        # every entry leaves a nonnegative seat count and no same-day
        # conflict can be enrolled.
        can_enroll = all(item["remaining_seats"] >= 0 and not item["conflict_activity_ids"] for item in preview)
        return {"can_enroll": can_enroll, "records": preview}

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

    def transfer_enrollments(self, changes):
        # The whole group takes effect at once: every change is normalized and
        # checked before any participant list is touched.
        if not isinstance(changes, list) or not changes:
            raise ValueError("changes must be a nonempty array")
        entries = []
        seen = set()
        for item in changes:
            if not isinstance(item, dict) or set(item) != {"source_activity_id", "target_activity_id", "member_id"}:
                raise ValueError("each change must contain only source_activity_id, target_activity_id and member_id")
            source_activity_id = text(item["source_activity_id"], "source_activity_id")
            target_activity_id = text(item["target_activity_id"], "target_activity_id")
            member_id = text(item["member_id"], "member_id")
            # One member may move only once per group, so no entry can undo or
            # chain another entry's move.
            if member_id in seen:
                raise ValueError("changes must not contain duplicate member ids")
            seen.add(member_id)
            entries.append((source_activity_id, target_activity_id, member_id))
        data = self._read()
        activities = data.get("activities", {})
        members = data.get("members", {})
        completions = data.get("completions", {})
        for source_activity_id, target_activity_id, member_id in entries:
            source = activities.get(source_activity_id)
            target = activities.get(target_activity_id)
            if source is None or target is None or member_id not in members:
                raise ValueError("unknown activity or member")
            if source_activity_id == target_activity_id:
                raise ValueError("source and target activities must differ")
            if member_id not in source["participants"]:
                raise ValueError("member is not enrolled in the source activity")
            if member_id in target["participants"]:
                raise ValueError("member is already enrolled in the target activity")
            # Only the member's own completion record for the source activity
            # blocks the move; other members' records never do, and no record
            # is ever deleted or migrated.
            if member_id in completions.get(source_activity_id, {}):
                raise ValueError("member has a completion record for the source activity")
        # Capacity is judged on the final rosters after the whole group moves,
        # so a swap or rotation between full activities is not rejected by an
        # intermediate state.
        delta = {}
        for source_activity_id, target_activity_id, _ in entries:
            delta[source_activity_id] = delta.get(source_activity_id, 0) - 1
            delta[target_activity_id] = delta.get(target_activity_id, 0) + 1
        for activity_id, change in delta.items():
            activity = activities[activity_id]
            if len(activity["participants"]) + change > activity["capacity"]:
                raise ValueError("target activity is full")
        # Same-day conflicts are judged on the final enrollments too: each
        # member appears once, so they only leave their source and join their
        # target; a seat in a third activity on the target date rejects the
        # whole group. Completed enrollments still count; unrelated historical
        # conflicts do not.
        for source_activity_id, target_activity_id, member_id in entries:
            target_on = activities[target_activity_id]["on"]
            for other_id, other in activities.items():
                if other_id in (source_activity_id, target_activity_id):
                    continue
                if other.get("on") == target_on and member_id in other.get("participants", []):
                    raise ValueError("member is enrolled in another activity on the target date")
        # Members who stay keep their relative order; moved members are
        # appended to their target in input order.
        for source_activity_id, target_activity_id, member_id in entries:
            activities[source_activity_id]["participants"].remove(member_id)
            activities[target_activity_id]["participants"].append(member_id)
        self._write(data)
        # One full target object per entry, in input order; a target named by
        # several entries reflects the final roster after the whole group.
        return [activities[target_activity_id] for _, target_activity_id, _ in entries]

    def preview_transfer_enrollments(self, changes):
        # Read-only preview of transfer_enrollments: the same normalization and
        # validation, but nothing is ever written back and no missing directory
        # or file is created.
        if not isinstance(changes, list) or not changes:
            raise ValueError("changes must be a nonempty array")
        entries = []
        seen = set()
        for item in changes:
            if not isinstance(item, dict) or set(item) != {"source_activity_id", "target_activity_id", "member_id"}:
                raise ValueError("each change must contain only source_activity_id, target_activity_id and member_id")
            source_activity_id = text(item["source_activity_id"], "source_activity_id")
            target_activity_id = text(item["target_activity_id"], "target_activity_id")
            member_id = text(item["member_id"], "member_id")
            # One member may move only once per group, so no entry can undo or
            # chain another entry's move.
            if member_id in seen:
                raise ValueError("changes must not contain duplicate member ids")
            seen.add(member_id)
            entries.append((source_activity_id, target_activity_id, member_id))
        data = self._read()
        activities = data.get("activities", {})
        members = data.get("members", {})
        completions = data.get("completions", {})
        # Structural errors reject the preview outright, exactly as in the
        # mutating batch: unknown activity or member, identical activities, a
        # missing source enrollment or an existing target enrollment.
        for source_activity_id, target_activity_id, member_id in entries:
            source = activities.get(source_activity_id)
            target = activities.get(target_activity_id)
            if source is None or target is None or member_id not in members:
                raise ValueError("unknown activity or member")
            if source_activity_id == target_activity_id:
                raise ValueError("source and target activities must differ")
            if member_id not in source["participants"]:
                raise ValueError("member is not enrolled in the source activity")
            if member_id in target["participants"]:
                raise ValueError("member is already enrolled in the target activity")
        # Remaining seats are judged on the final rosters after the whole group
        # moves, so a swap or rotation between full activities leaves every
        # named target with a nonnegative count. Entries carrying a completion
        # record still count toward the group headcount.
        delta = {}
        for source_activity_id, target_activity_id, _ in entries:
            delta[source_activity_id] = delta.get(source_activity_id, 0) - 1
            delta[target_activity_id] = delta.get(target_activity_id, 0) + 1
        remaining = {
            activity_id: activities[activity_id]["capacity"] - (len(activities[activity_id].get("participants", [])) + change)
            for activity_id, change in delta.items()
        }
        preview = []
        for source_activity_id, target_activity_id, member_id in entries:
            # Only the member's own completion record for the source activity is
            # reported; other members' records never are. A missing completions
            # collection means no records at all.
            has_completion = member_id in completions.get(source_activity_id, {})
            # Conflicts use the final enrollments too: the member only leaves
            # the source and joins the target, both of which are excluded, so a
            # seat kept in a third activity on the target date is an obstacle.
            # Completed enrollments still count; other members' enrollments and
            # unrelated historical conflicts never do.
            target_on = activities[target_activity_id]["on"]
            excluded = {source_activity_id, target_activity_id}
            conflict_activity_ids = sorted(
                other_id
                for other_id, other in activities.items()
                if other_id not in excluded
                and other.get("on") == target_on
                and member_id in other.get("participants", [])
            )
            preview.append({
                "source_activity_id": source_activity_id,
                "target_activity_id": target_activity_id,
                "member_id": member_id,
                "has_completion": has_completion,
                "remaining_seats": remaining[target_activity_id],
                "conflict_activity_ids": conflict_activity_ids,
            })
        # All three kinds of obstacles are reported together; only a group where
        # every entry is free of a completion record, free of same-day conflicts
        # and leaves a nonnegative seat count can be transferred.
        can_transfer = all(
            not item["has_completion"] and not item["conflict_activity_ids"] and item["remaining_seats"] >= 0
            for item in preview
        )
        return {"can_transfer": can_transfer, "changes": preview}

    def split_activity(self, source_activity_id, activity_id, title, on, capacity, member_ids):
        # The new session and the enrollment move take effect together: every
        # value is normalized and checked before the source roster is touched
        # or the new activity is created.
        source_activity_id = text(source_activity_id, "source_activity_id")
        activity_id, title = text(activity_id, "activity_id"), text(title, "title")
        on, capacity = day(on, "on"), positive(capacity, "capacity")
        if not isinstance(member_ids, list) or not member_ids:
            raise ValueError("member_ids must be a nonempty array")
        selected = []
        seen = set()
        for member_id in member_ids:
            member_id = text(member_id, "member_id")
            if member_id in seen:
                raise ValueError("member_ids must not contain duplicates")
            seen.add(member_id)
            selected.append(member_id)
        # The new session must seat everyone moved into it.
        if capacity < len(selected):
            raise ValueError("capacity must be at least the number of moved members")
        data = self._read()
        activities = data.get("activities", {})
        members = data.get("members", {})
        source = activities.get(source_activity_id)
        if source is None:
            raise ValueError("unknown activity")
        if activity_id in activities:
            raise ValueError("activity already exists")
        completions = data.get("completions", {})
        for member_id in selected:
            if member_id not in members:
                raise ValueError("unknown member")
            if member_id not in source["participants"]:
                raise ValueError("member is not enrolled in the source activity")
            # Only the member's own completion record for the source activity
            # blocks the move; other members' records never do, and no record
            # is ever deleted or migrated.
            if member_id in completions.get(source_activity_id, {}):
                raise ValueError("member has a completion record for the source activity")
        # A moved member must not keep a seat in a third activity on the new
        # date. Completion does not cancel enrollment, so participants lists
        # already cover finished trainings; the source is excluded and other
        # members' enrollments or other dates never block the split.
        for member_id in selected:
            for other_id, other in activities.items():
                if other_id == source_activity_id:
                    continue
                if other.get("on") == on and member_id in other.get("participants", []):
                    raise ValueError("member is enrolled in another activity on that date")
        # Members who stay keep their relative order; the new roster follows
        # the input order exactly. The source keeps all its other fields and
        # stays in the file even when its roster becomes empty.
        moving = set(selected)
        source["participants"] = [member_id for member_id in source["participants"] if member_id not in moving]
        activity = {"activity_id": activity_id, "title": title, "on": on, "capacity": capacity, "participants": list(selected)}
        activities[activity_id] = activity
        self._write(data)
        return {"source_activity": source, "new_activity": activity}

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

    def preview_reschedule_activities(self, changes):
        # Read-only preview of reschedule_activities: the same normalization
        # and validation, but nothing is ever written back and no missing
        # directory or file is created.
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
        completions = data.get("completions", {})
        selected = []
        for activity_id, on in entries:
            activity = activities.get(activity_id)
            if activity is None:
                raise ValueError("unknown activity")
            selected.append(activity)
        # Dates are compared after the whole group is applied: selected
        # activities use their requested date, everything else keeps its own,
        # so a feasible swap shows no conflict.
        final_on = {activity_id: on for activity_id, on in entries}
        preview = []
        for activity, (activity_id, on) in zip(selected, entries):
            previous_on = activity["on"]
            completion_member_ids = []
            conflicts = []
            # Only a real date change is checked; an unchanged date reports no
            # obstacles even when completion records or old conflicts exist.
            if on != previous_on:
                completion_member_ids = sorted(completions.get(activity_id, {}))
                participants = set(activity.get("participants", []))
                blocked = {}
                for other_id, other in activities.items():
                    if other_id == activity_id:
                        continue
                    other_on = final_on.get(other_id, other.get("on"))
                    if other_on != on:
                        continue
                    # Completed enrollments keep their seats, so participants
                    # lists already cover finished trainings; other members'
                    # enrollments never count as a conflict.
                    for member_id in participants & set(other.get("participants", [])):
                        blocked.setdefault(member_id, []).append(other_id)
                conflicts = [{"member_id": member_id, "activity_ids": sorted(ids)} for member_id, ids in sorted(blocked.items())]
            preview.append({
                "activity_id": activity_id,
                "previous_on": previous_on,
                "on": on,
                "completion_member_ids": completion_member_ids,
                "conflicts": conflicts,
            })
        # Both kinds of obstacles are reported together; only a fully clear
        # group can be rescheduled.
        can_reschedule = all(not item["completion_member_ids"] and not item["conflicts"] for item in preview)
        return {"can_reschedule": can_reschedule, "activities": preview}

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
        # The whole group takes effect at once: every entry is normalized and
        # checked before any record is written.
        if not isinstance(records, list) or not records:
            raise ValueError("records must be a nonempty array")
        entries = []
        seen = set()
        for item in records:
            if not isinstance(item, dict) or set(item) != {"activity_id", "member_id", "completed_on"}:
                raise ValueError("each record must contain only activity_id, member_id and completed_on")
            activity_id = text(item["activity_id"], "activity_id")
            member_id = text(item["member_id"], "member_id")
            completed_on = day(item["completed_on"], "completed_on")
            # A repeated activity/member pair rejects the whole group even when
            # both entries carry the same completion date; records are never
            # merged. Cross-activity and cross-member entries are allowed.
            pair = (activity_id, member_id)
            if pair in seen:
                raise ValueError("records must not contain duplicate activity and member pairs")
            seen.add(pair)
            entries.append((activity_id, member_id, completed_on))
        data = self._read()
        activities = data.get("activities", {})
        members = data.get("members", {})
        done = data.setdefault("completions", {})
        # Validate every entry first: the activity and member must exist, the
        # member must be enrolled, the completion date must not precede the
        # activity date, and neither the stored history nor an earlier entry in
        # this group may already hold the member's own record. Other members'
        # records never block the entry.
        for activity_id, member_id, completed_on in entries:
            activity = activities.get(activity_id)
            if activity is None:
                raise ValueError("unknown activity")
            if member_id not in members:
                raise ValueError("unknown member")
            if member_id not in activity.get("participants", []):
                raise ValueError("member is not enrolled in the activity")
            if member_id in done.get(activity_id, {}):
                raise ValueError("completion already recorded")
            if date.fromisoformat(completed_on) < date.fromisoformat(activity["on"]):
                raise ValueError("completed_on must be on or after the activity date")
        # Only the selected records are added, in input order; enrollments keep
        # occupying capacity and participant order, profiles and activity fields
        # (including extra fields) are left untouched.
        result = []
        for activity_id, member_id, completed_on in entries:
            done.setdefault(activity_id, {})[member_id] = completed_on
            activity = activities[activity_id]
            result.append({"activity_id": activity_id, "member_id": member_id, "completed_on": completed_on, "title": activity["title"], "on": activity["on"]})
        self._write(data)
        return result

    def correct_completions(self, records):
        # The whole group takes effect at once: every entry is normalized and
        # checked before any stored record is changed.
        if not isinstance(records, list) or not records:
            raise ValueError("records must be a nonempty array")
        entries = []
        seen = set()
        for item in records:
            if not isinstance(item, dict) or set(item) != {"activity_id", "member_id", "completed_on"}:
                raise ValueError("each record must contain only activity_id, member_id and completed_on")
            activity_id = text(item["activity_id"], "activity_id")
            member_id = text(item["member_id"], "member_id")
            # null revokes the record; any other value must be a strict, real
            # YYYY-MM-DD date exactly like a fresh completion entry.
            completed_on = None if item["completed_on"] is None else day(item["completed_on"], "completed_on")
            # A repeated activity/member pair rejects the whole group even when
            # both entries set the same date; same-date entries are not merged.
            pair = (activity_id, member_id)
            if pair in seen:
                raise ValueError("records must not contain duplicate activity and member pairs")
            seen.add(pair)
            entries.append((activity_id, member_id, completed_on))
        data = self._read()
        activities = data.get("activities", {})
        members = data.get("members", {})
        done = data.get("completions", {})
        # Validate every entry first: the activity and member must exist, the
        # member must stay enrolled, and the member's own record must already
        # exist so it can be corrected. A new date must not precede the
        # activity date; revocation (null) carries no date check.
        previous = []
        for activity_id, member_id, completed_on in entries:
            activity = activities.get(activity_id)
            if activity is None:
                raise ValueError("unknown activity")
            if member_id not in members:
                raise ValueError("unknown member")
            if member_id not in activity.get("participants", []):
                raise ValueError("member is not enrolled in the activity")
            record = done.get(activity_id, {}).get(member_id)
            if record is None:
                raise ValueError("no completion record to correct")
            previous.append(record)
            if completed_on is not None and date.fromisoformat(completed_on) < date.fromisoformat(activity["on"]):
                raise ValueError("completed_on must be on or after the activity date")
        # Only the selected records change: a new date replaces the old one and
        # null deletes just that member's record. Enrollments keep occupying
        # capacity and participant order; profiles, activity fields (including
        # extra fields) and other members' records are left untouched.
        changed = False
        result = []
        for (activity_id, member_id, completed_on), old in zip(entries, previous):
            activity = activities[activity_id]
            records_for_activity = done[activity_id]
            if completed_on is None:
                del records_for_activity[member_id]
            else:
                records_for_activity[member_id] = completed_on
            if completed_on != old:
                changed = True
            result.append({"activity_id": activity_id, "member_id": member_id, "title": activity["title"], "on": activity["on"], "previous_completed_on": old, "completed_on": completed_on})
        # A group where every date already equals the stored one succeeds but
        # leaves the file untouched, like a same-date reschedule.
        if changed:
            self._write(data)
        return result

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

    def merge_activities(self, target_activity_id, source_activity_ids):
        # The whole merge takes effect at once: every identifier is normalized
        # and every rule is checked before any roster or completion record is
        # touched.
        target_activity_id = text(target_activity_id, "target_activity_id")
        if not isinstance(source_activity_ids, list) or not source_activity_ids:
            raise ValueError("source_activity_ids must be a nonempty array")
        source_ids = []
        seen = set()
        for activity_id in source_activity_ids:
            activity_id = text(activity_id, "source_activity_id")
            if activity_id in seen:
                raise ValueError("source_activity_ids must not contain duplicates")
            seen.add(activity_id)
            source_ids.append(activity_id)
        if target_activity_id in seen:
            raise ValueError("target_activity_id must not appear in source_activity_ids")
        data = self._read()
        activities = data.get("activities", {})
        target = activities.get(target_activity_id)
        if target is None or any(activities.get(activity_id) is None for activity_id in source_ids):
            raise ValueError("unknown activity")
        target_on = target["on"]
        # Titles may differ; every source must run on the target's date.
        for activity_id in source_ids:
            if activities[activity_id]["on"] != target_on:
                raise ValueError("every source activity must be on the target activity date")
        # The target roster keeps its original order; members not already in it
        # are appended by source input order and each source's own roster
        # order, one seat per member even when the same member sits in several
        # sources. Empty source rosters add nobody and are still merged away.
        merged_participants = list(target.get("participants", []))
        enrolled = set(merged_participants)
        transferring = []
        for activity_id in source_ids:
            for member_id in activities[activity_id].get("participants", []):
                if member_id in enrolled:
                    continue
                enrolled.add(member_id)
                merged_participants.append(member_id)
                transferring.append(member_id)
        if len(merged_participants) > target["capacity"]:
            raise ValueError("target activity is full")
        # Only members newly joining the target are checked for same-day
        # conflicts, and only after every source is gone: the sources are
        # excluded together with the target, so a seat kept in a third
        # activity on the same date rejects the merge. Completed enrollments
        # still count; the target's original members keep their historical
        # conflicts, and other members or other dates never block the merge.
        excluded = {target_activity_id, *source_ids}
        for member_id in transferring:
            for other_id, other in activities.items():
                if other_id in excluded:
                    continue
                if other.get("on") == target_on and member_id in other.get("participants", []):
                    raise ValueError("member is enrolled in another activity on that date")
        # Completion records flow into the target. One existing record keeps
        # its date; records in several places merge only when the dates agree
        # - differing dates reject the whole merge without picking or
        # rewriting a date. Members without a record stay pending.
        completions = data.get("completions", {})
        merged_done = dict(completions.get(target_activity_id, {}))
        for activity_id in source_ids:
            for member_id, completed_on in completions.get(activity_id, {}).items():
                if member_id in merged_done and merged_done[member_id] != completed_on:
                    raise ValueError("completion dates differ for the merged activities")
                merged_done.setdefault(member_id, completed_on)
        # All checks passed: rebuild the target roster (keeping its id, title,
        # date, capacity and extra fields) and delete the sources together
        # with the completion entries keyed to them.
        target["participants"] = merged_participants
        for activity_id in source_ids:
            del activities[activity_id]
        if target_activity_id in completions or merged_done:
            completions[target_activity_id] = merged_done
        for activity_id in source_ids:
            completions.pop(activity_id, None)
        self._write(data)
        return target

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

    def enrollment_options(self, member_id, from_on=None, to_on=None, available_only=False):
        member_id = text(member_id, "member_id")
        if from_on is not None:
            from_on = day(from_on, "from_on")
        if to_on is not None:
            to_on = day(to_on, "to_on")
        if from_on is not None and to_on is not None and from_on > to_on:
            raise ValueError("from_on must be on or before to_on")
        if type(available_only) is not bool:
            raise ValueError("available_only must be a boolean")
        data = self._read()
        if member_id not in data.get("members", {}):
            raise ValueError("unknown member")
        activities = data.get("activities", {})
        # The member's current enrollments per date. Completed trainings keep
        # their seats, so the participants lists already cover them: they are
        # never candidates and they supply the same-day conflicts. Other
        # members' enrollments never enter either structure.
        enrolled_on = {}
        enrolled = set()
        for activity_id, activity in activities.items():
            if member_id in activity.get("participants", []):
                enrolled.add(activity_id)
                enrolled_on.setdefault(activity.get("on"), set()).add(activity_id)
        options = []
        for activity_id, activity in activities.items():
            if activity_id in enrolled:
                continue
            on = activity["on"]
            if from_on is not None and on < from_on:
                continue
            if to_on is not None and on > to_on:
                continue
            # Completed enrollments occupy seats too, so remaining seats use
            # the whole current participant count.
            remaining_seats = activity["capacity"] - len(activity.get("participants", []))
            # A candidate is never among the member's own enrollments, so every
            # same-day enrollment recorded above is an "other" activity.
            conflicts = sorted(enrolled_on.get(on, ()))
            available = remaining_seats > 0 and not conflicts
            # available_only only filters the output: a full and conflicting
            # candidate still carries both facts when all options are returned.
            if available_only and not available:
                continue
            options.append({
                "activity_id": activity_id,
                "title": activity["title"],
                "on": on,
                "capacity": activity["capacity"],
                "remaining_seats": remaining_seats,
                "available": available,
                "conflict_activity_ids": conflicts,
            })
        return sorted(options, key=lambda r: (r["on"], r["activity_id"]))

    def training_progress(self, activity_ids, as_of, member_ids=None):
        # Read-only multi-member reconciliation: one cutoff date applies only
        # to completion dates; current rosters and profiles are used as they
        # are, never reconstructed historically.
        if not isinstance(activity_ids, list) or not activity_ids:
            raise ValueError("activity_ids must be a nonempty array")
        selected_ids = []
        seen_activities = set()
        for activity_id in activity_ids:
            activity_id = text(activity_id, "activity_id")
            if activity_id in seen_activities:
                raise ValueError("activity_ids must not contain duplicates")
            seen_activities.add(activity_id)
            selected_ids.append(activity_id)
        as_of = day(as_of, "as_of")
        # member_ids omitted or null means every member; an empty array means
        # no members at all. Given ids are returned in input order.
        if member_ids is None:
            selected_member_ids = None
        elif not isinstance(member_ids, list):
            raise ValueError("member_ids must be an array or null")
        else:
            selected_member_ids = []
            seen_members = set()
            for member_id in member_ids:
                member_id = text(member_id, "member_id")
                if member_id in seen_members:
                    raise ValueError("member_ids must not contain duplicates")
                seen_members.add(member_id)
                selected_member_ids.append(member_id)
        data = self._read()
        members = data.get("members", {})
        activities = data.get("activities", {})
        completions = data.get("completions", {})
        selected_activities = []
        for activity_id in selected_ids:
            activity = activities.get(activity_id)
            if activity is None:
                raise ValueError("unknown activity")
            selected_activities.append(activity)
        if selected_member_ids is None:
            # Everyone, by ascending identifier.
            ordered_member_ids = sorted(members)
        else:
            for member_id in selected_member_ids:
                if member_id not in members:
                    raise ValueError("unknown member")
            ordered_member_ids = selected_member_ids
        cutoff = date.fromisoformat(as_of)
        rows = []
        for member_id in ordered_member_ids:
            trainings = []
            completed_count = 0
            for activity_id, activity in zip(selected_ids, selected_activities):
                # A stored completion only exists for enrolled members; the
                # enrollment check still decides the status on its own so a
                # missing record is the same as no record.
                completed_on = completions.get(activity_id, {}).get(member_id)
                if member_id not in activity.get("participants", []):
                    status = "not_enrolled"
                    completed_on = None
                elif completed_on is not None and date.fromisoformat(completed_on) <= cutoff:
                    # The cutoff only counts the record; the original date is
                    # reported unchanged. A later record stays visible below.
                    status = "completed"
                    completed_count += 1
                else:
                    # Enrolled with no record, or with a record dated after the
                    # cutoff: pending and still missing.
                    status = "pending"
                trainings.append({
                    "activity_id": activity_id,
                    "title": activity["title"],
                    "on": activity["on"],
                    "status": status,
                    "completed_on": completed_on,
                })
            rows.append({
                "member_id": member_id,
                "name": members[member_id]["name"],
                "completed_count": completed_count,
                # Not enrolled counts as missing too; future activities stay
                # among the required items.
                "remaining_count": len(selected_activities) - completed_count,
                "trainings": trainings,
            })
        return {"as_of": as_of, "required_count": len(selected_activities), "members": rows}

    def training_requirements(self, groups, as_of, member_ids=None):
        # Read-only reconciliation against ephemeral training groups: the
        # groups exist only for this call, are never stored and are never
        # inferred from activity titles. One cutoff date applies only to
        # completion dates; current rosters and profiles are used as they are,
        # never reconstructed historically.
        if not isinstance(groups, list) or not groups:
            raise ValueError("groups must be a nonempty array")
        selected_groups = []
        seen_groups = set()
        seen_activities = set()
        for item in groups:
            if not isinstance(item, dict) or set(item) != {"group_id", "activity_ids"}:
                raise ValueError("each group must contain only group_id and activity_ids")
            group_id = text(item["group_id"], "group_id")
            if group_id in seen_groups:
                raise ValueError("group_ids must not contain duplicates")
            seen_groups.add(group_id)
            activity_ids = item["activity_ids"]
            if not isinstance(activity_ids, list) or not activity_ids:
                raise ValueError("activity_ids must be a nonempty array")
            group_activities = []
            for activity_id in activity_ids:
                activity_id = text(activity_id, "activity_id")
                # An activity satisfies one requirement within exactly one
                # group: it may not repeat inside a group or cross groups.
                if activity_id in seen_activities:
                    raise ValueError("activity_ids must not repeat within or across groups")
                seen_activities.add(activity_id)
                group_activities.append(activity_id)
            selected_groups.append((group_id, group_activities))
        as_of = day(as_of, "as_of")
        # member_ids follows training_progress exactly: omitted or null means
        # every member, an empty array means no members at all.
        if member_ids is None:
            selected_member_ids = None
        elif not isinstance(member_ids, list):
            raise ValueError("member_ids must be an array or null")
        else:
            selected_member_ids = []
            seen_members = set()
            for member_id in member_ids:
                member_id = text(member_id, "member_id")
                if member_id in seen_members:
                    raise ValueError("member_ids must not contain duplicates")
                seen_members.add(member_id)
                selected_member_ids.append(member_id)
        data = self._read()
        members = data.get("members", {})
        activities = data.get("activities", {})
        completions = data.get("completions", {})
        for _, activity_ids in selected_groups:
            for activity_id in activity_ids:
                if activities.get(activity_id) is None:
                    raise ValueError("unknown activity")
        if selected_member_ids is None:
            # Everyone, by ascending identifier.
            ordered_member_ids = sorted(members)
        else:
            for member_id in selected_member_ids:
                if member_id not in members:
                    raise ValueError("unknown member")
            ordered_member_ids = selected_member_ids
        cutoff = date.fromisoformat(as_of)
        rows = []
        for member_id in ordered_member_ids:
            group_rows = []
            completed_count = 0
            for group_id, activity_ids in selected_groups:
                # A stored completion only exists for enrolled members; the
                # enrollment check still decides the status on its own so a
                # missing record is the same as no record.
                candidates = []
                enrolled_any = False
                for activity_id in activity_ids:
                    activity = activities[activity_id]
                    if member_id in activity.get("participants", []):
                        enrolled_any = True
                    completed_on = completions.get(activity_id, {}).get(member_id)
                    if (
                        member_id in activity.get("participants", [])
                        and completed_on is not None
                        and date.fromisoformat(completed_on) <= cutoff
                    ):
                        candidates.append((completed_on, activity["on"], activity_id))
                if candidates:
                    # Finishing any session of the group satisfies it once; the
                    # first qualifying record wins by completion date, activity
                    # date and activity id, keeping its original completion
                    # date. A record dated after the cutoff never qualifies.
                    completed_on, _, activity_id = min(candidates)
                    group_rows.append({
                        "group_id": group_id,
                        "status": "completed",
                        "activity_id": activity_id,
                        "completed_on": completed_on,
                    })
                    completed_count += 1
                elif enrolled_any:
                    # Enrolled in at least one session without a qualifying
                    # record: still pending, no session or date is reported.
                    group_rows.append({"group_id": group_id, "status": "pending", "activity_id": None, "completed_on": None})
                else:
                    group_rows.append({"group_id": group_id, "status": "not_enrolled", "activity_id": None, "completed_on": None})
            rows.append({
                "member_id": member_id,
                "name": members[member_id]["name"],
                "completed_count": completed_count,
                # Not enrolled counts as missing too; future activities stay
                # among the required groups.
                "remaining_count": len(selected_groups) - completed_count,
                "groups": group_rows,
            })
        return {"as_of": as_of, "required_count": len(selected_groups), "members": rows}

    def training_validity(self, groups, as_of, member_ids=None):
        # Read-only retraining-validity reconciliation against the same
        # ephemeral training groups as training_requirements: the groups exist
        # only for this call, are never stored and are never inferred from
        # activity titles. One cutoff date applies to completion dates; the
        # validity window is counted in calendar days from the selected
        # completion date, so neither historical rosters nor the current date
        # are ever consulted.
        if not isinstance(groups, list) or not groups:
            raise ValueError("groups must be a nonempty array")
        selected_groups = []
        seen_groups = set()
        seen_activities = set()
        for item in groups:
            if not isinstance(item, dict) or set(item) != {"group_id", "activity_ids", "valid_days"}:
                raise ValueError("each group must contain only group_id, activity_ids and valid_days")
            group_id = text(item["group_id"], "group_id")
            if group_id in seen_groups:
                raise ValueError("group_ids must not contain duplicates")
            seen_groups.add(group_id)
            valid_days = positive(item["valid_days"], "valid_days")
            activity_ids = item["activity_ids"]
            if not isinstance(activity_ids, list) or not activity_ids:
                raise ValueError("activity_ids must be a nonempty array")
            group_activities = []
            for activity_id in activity_ids:
                activity_id = text(activity_id, "activity_id")
                # As in training_requirements, an activity belongs to exactly
                # one group and may not repeat inside it.
                if activity_id in seen_activities:
                    raise ValueError("activity_ids must not repeat within or across groups")
                seen_activities.add(activity_id)
                group_activities.append(activity_id)
            selected_groups.append((group_id, group_activities, valid_days))
        as_of = day(as_of, "as_of")
        # member_ids follows training_requirements exactly: omitted or null
        # means every member, an empty array means no members at all.
        if member_ids is None:
            selected_member_ids = None
        elif not isinstance(member_ids, list):
            raise ValueError("member_ids must be an array or null")
        else:
            selected_member_ids = []
            seen_members = set()
            for member_id in member_ids:
                member_id = text(member_id, "member_id")
                if member_id in seen_members:
                    raise ValueError("member_ids must not contain duplicates")
                seen_members.add(member_id)
                selected_member_ids.append(member_id)
        data = self._read()
        members = data.get("members", {})
        activities = data.get("activities", {})
        completions = data.get("completions", {})
        for _, activity_ids, _ in selected_groups:
            for activity_id in activity_ids:
                if activities.get(activity_id) is None:
                    raise ValueError("unknown activity")
        if selected_member_ids is None:
            # Everyone, by ascending identifier.
            ordered_member_ids = sorted(members)
        else:
            for member_id in selected_member_ids:
                if member_id not in members:
                    raise ValueError("unknown member")
            ordered_member_ids = selected_member_ids
        cutoff = date.fromisoformat(as_of)
        rows = []
        for member_id in ordered_member_ids:
            group_rows = []
            valid_count = 0
            for group_id, activity_ids, valid_days in selected_groups:
                # A stored completion only exists for enrolled members; the
                # enrollment check still decides the status on its own so a
                # missing record is the same as no record.
                candidates = []
                enrolled_any = False
                for activity_id in activity_ids:
                    activity = activities[activity_id]
                    if member_id in activity.get("participants", []):
                        enrolled_any = True
                    completed_on = completions.get(activity_id, {}).get(member_id)
                    if (
                        member_id in activity.get("participants", [])
                        and completed_on is not None
                        and date.fromisoformat(completed_on) <= cutoff
                    ):
                        candidates.append((completed_on, activity["on"], activity_id))
                if candidates:
                    # Only records completed by the cutoff are eligible: a
                    # future record is never picked and never hides an older
                    # expired one. The LATEST completion date wins; an equal
                    # completion date breaks by activity date and activity id,
                    # both ascending, keeping the original completion date.
                    latest_completed = max(candidate[0] for candidate in candidates)
                    completed_on, _, activity_id = min(
                        candidate for candidate in candidates if candidate[0] == latest_completed
                    )
                    age_days = (cutoff - date.fromisoformat(completed_on)).days
                    # Less than valid_days calendar days after completion is
                    # still valid; the validity end day itself is expired.
                    if age_days < valid_days:
                        status = "valid"
                        valid_count += 1
                    else:
                        status = "expired"
                    group_rows.append({
                        "group_id": group_id,
                        "status": status,
                        "activity_id": activity_id,
                        "completed_on": completed_on,
                    })
                elif enrolled_any:
                    # Enrolled in at least one session without an eligible
                    # record: pending, no session or date is reported.
                    group_rows.append({"group_id": group_id, "status": "pending", "activity_id": None, "completed_on": None})
                else:
                    group_rows.append({"group_id": group_id, "status": "not_enrolled", "activity_id": None, "completed_on": None})
            rows.append({
                "member_id": member_id,
                "name": members[member_id]["name"],
                "valid_count": valid_count,
                # Expired, pending and not enrolled all count as missing;
                # future activities stay among the required groups.
                "remaining_count": len(selected_groups) - valid_count,
                "groups": group_rows,
            })
        return {"as_of": as_of, "required_count": len(selected_groups), "members": rows}

    def training_alerts(self, groups, as_of, through_on, member_ids=None):
        # Read-only alert view over the same ephemeral training groups as
        # training_validity: the record picked per member and group follows the
        # exact same rules, so future records never participate or mask older
        # ones. Both window dates are explicit; the current date is never
        # consulted, and an expiry past 9999-12-31 is simply outside the
        # window rather than an overflow error.
        if not isinstance(groups, list) or not groups:
            raise ValueError("groups must be a nonempty array")
        selected_groups = []
        seen_groups = set()
        seen_activities = set()
        for item in groups:
            if not isinstance(item, dict) or set(item) != {"group_id", "activity_ids", "valid_days"}:
                raise ValueError("each group must contain only group_id, activity_ids and valid_days")
            group_id = text(item["group_id"], "group_id")
            if group_id in seen_groups:
                raise ValueError("group_ids must not contain duplicates")
            seen_groups.add(group_id)
            valid_days = positive(item["valid_days"], "valid_days")
            activity_ids = item["activity_ids"]
            if not isinstance(activity_ids, list) or not activity_ids:
                raise ValueError("activity_ids must be a nonempty array")
            group_activities = []
            for activity_id in activity_ids:
                activity_id = text(activity_id, "activity_id")
                # As in training_validity, an activity belongs to exactly one
                # group and may not repeat inside it.
                if activity_id in seen_activities:
                    raise ValueError("activity_ids must not repeat within or across groups")
                seen_activities.add(activity_id)
                group_activities.append(activity_id)
            selected_groups.append((group_id, group_activities, valid_days))
        as_of = day(as_of, "as_of")
        through_on = day(through_on, "through_on")
        if through_on < as_of:
            raise ValueError("through_on must be on or after as_of")
        # member_ids follows training_validity exactly: omitted or null means
        # every member, an empty array means no members at all.
        if member_ids is None:
            selected_member_ids = None
        elif not isinstance(member_ids, list):
            raise ValueError("member_ids must be an array or null")
        else:
            selected_member_ids = []
            seen_members = set()
            for member_id in member_ids:
                member_id = text(member_id, "member_id")
                if member_id in seen_members:
                    raise ValueError("member_ids must not contain duplicates")
                seen_members.add(member_id)
                selected_member_ids.append(member_id)
        data = self._read()
        members = data.get("members", {})
        activities = data.get("activities", {})
        completions = data.get("completions", {})
        for _, activity_ids, _ in selected_groups:
            for activity_id in activity_ids:
                if activities.get(activity_id) is None:
                    raise ValueError("unknown activity")
        if selected_member_ids is None:
            # Everyone, by ascending identifier.
            ordered_member_ids = sorted(members)
        else:
            for member_id in selected_member_ids:
                if member_id not in members:
                    raise ValueError("unknown member")
            ordered_member_ids = selected_member_ids
        cutoff = date.fromisoformat(as_of)
        window_end = date.fromisoformat(through_on)
        alerts = []
        for member_id in ordered_member_ids:
            for group_id, activity_ids, valid_days in selected_groups:
                # The qualifying record, enrollment fallback and same-day tie
                # break are exactly training_validity's selection.
                candidates = []
                enrolled_any = False
                for activity_id in activity_ids:
                    activity = activities[activity_id]
                    if member_id in activity.get("participants", []):
                        enrolled_any = True
                    completed_on = completions.get(activity_id, {}).get(member_id)
                    if (
                        member_id in activity.get("participants", [])
                        and completed_on is not None
                        and date.fromisoformat(completed_on) <= cutoff
                    ):
                        candidates.append((completed_on, activity["on"], activity_id))
                if candidates:
                    latest_completed = max(candidate[0] for candidate in candidates)
                    completed_on, _, activity_id = min(
                        candidate for candidate in candidates if candidate[0] == latest_completed
                    )
                    # The expiry is the completion day plus valid_days calendar
                    # days. A result past date.max is beyond every possible
                    # window end, so it is treated as out of window instead of
                    # raising; timedelta itself also rejects huge day counts.
                    try:
                        expires = date.fromisoformat(completed_on) + timedelta(days=valid_days)
                    except OverflowError:
                        continue
                    days_remaining = (expires - cutoff).days
                    if expires <= cutoff:
                        status = "expired"
                    elif expires <= window_end:
                        status = "expiring"
                    else:
                        # Still valid through the whole window: no alert.
                        continue
                    alerts.append({
                        "member_id": member_id,
                        "name": members[member_id]["name"],
                        "group_id": group_id,
                        "status": status,
                        "activity_id": activity_id,
                        "completed_on": completed_on,
                        "expires_on": expires.isoformat(),
                        "days_remaining": days_remaining,
                    })
                elif enrolled_any:
                    # Enrolled in at least one session without an eligible
                    # record: pending; not enrolling any session is a separate
                    # alert, and both are kept.
                    alerts.append({
                        "member_id": member_id,
                        "name": members[member_id]["name"],
                        "group_id": group_id,
                        "status": "pending",
                        "activity_id": None,
                        "completed_on": None,
                        "expires_on": None,
                        "days_remaining": None,
                    })
                else:
                    alerts.append({
                        "member_id": member_id,
                        "name": members[member_id]["name"],
                        "group_id": group_id,
                        "status": "not_enrolled",
                        "activity_id": None,
                        "completed_on": None,
                        "expires_on": None,
                        "days_remaining": None,
                    })
        return {"as_of": as_of, "through_on": through_on, "alerts": alerts}

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
