from __future__ import annotations

from datetime import datetime, timedelta

from .scheduler import schedule_sort_key


def leaf_scheduled_tasks(tasks: list[dict]) -> list[dict]:
    """Return scheduled leaf activities suitable for a precedence network."""
    parent_ids = {
        int(task["parent_id"])
        for task in tasks
        if task.get("parent_id") is not None
    }
    result = [
        task
        for task in tasks
        if int(task["id"]) not in parent_ids
        and task.get("calculated_start")
        and task.get("calculated_finish")
    ]
    result.sort(key=schedule_sort_key)
    return result


def collapsed_network_data(
    tasks: list[dict], dependencies: list[dict]
) -> tuple[list[dict], list[dict], dict[int, int]]:
    """Collapse segment groups while retaining every unsplit leaf activity.

    A segment group follows the application's existing flow-summary rule: it has
    at least two direct children and all of those children are leaves. Higher
    grouping levels are omitted, so mixed hierarchies remain understandable.
    """
    task_by_id = {int(task["id"]): task for task in tasks}
    children: dict[int, list[int]] = {}
    for task in tasks:
        parent_id = task.get("parent_id")
        if parent_id is not None:
            children.setdefault(int(parent_id), []).append(int(task["id"]))

    segment_summary_ids = {
        parent_id
        for parent_id, child_ids in children.items()
        if len(child_ids) >= 2
        and all(child_id not in children for child_id in child_ids)
    }
    source_to_display = {task_id: task_id for task_id in task_by_id}
    for summary_id in segment_summary_ids:
        for child_id in children[summary_id]:
            source_to_display[child_id] = summary_id

    display_ids = set(segment_summary_ids)
    display_ids.update(
        task_id
        for task_id, task in task_by_id.items()
        if task_id not in children
        and (
            task.get("parent_id") is None
            or int(task["parent_id"]) not in segment_summary_ids
        )
    )
    display_tasks = [
        task_by_id[task_id]
        for task_id in display_ids
        if task_by_id[task_id].get("calculated_start")
        and task_by_id[task_id].get("calculated_finish")
    ]
    display_tasks.sort(key=schedule_sort_key)
    scheduled_ids = {int(task["id"]) for task in display_tasks}

    collapsed_by_key: dict[tuple[int, int, str], dict] = {}
    for relation in dependencies:
        predecessor_id = source_to_display.get(
            int(relation["predecessor_id"]), int(relation["predecessor_id"])
        )
        successor_id = source_to_display.get(
            int(relation["successor_id"]), int(relation["successor_id"])
        )
        if (
            predecessor_id == successor_id
            or predecessor_id not in scheduled_ids
            or successor_id not in scheduled_ids
        ):
            continue
        relation_type = str(relation.get("relation_type", "FS"))
        key = (predecessor_id, successor_id, relation_type)
        if key in collapsed_by_key:
            collapsed_by_key[key]["collapsed_count"] += 1
            continue
        item = dict(relation)
        item["predecessor_id"] = predecessor_id
        item["successor_id"] = successor_id
        item["relation_type"] = relation_type
        item["collapsed_count"] = 1
        collapsed_by_key[key] = item

    return display_tasks, list(collapsed_by_key.values()), source_to_display


def assign_time_lanes(
    tasks: list[dict], *, clearance_days: float = 1.25
) -> dict[int, int]:
    """Assign non-overlapping display lanes without changing activity times."""
    lane_ends: list[datetime] = []
    lane_by_id: dict[int, int] = {}
    for task in tasks:
        start = datetime.fromisoformat(task["calculated_start"])
        finish = datetime.fromisoformat(task["calculated_finish"])
        visual_end = max(start, finish) + timedelta(days=clearance_days)
        lane = next(
            (
                index
                for index, occupied_until in enumerate(lane_ends)
                if start >= occupied_until
            ),
            len(lane_ends),
        )
        if lane == len(lane_ends):
            lane_ends.append(visual_end)
        else:
            lane_ends[lane] = visual_end
        lane_by_id[int(task["id"])] = lane
    return lane_by_id
