from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import date, datetime, time, timedelta

from ..domain import (
    Activity,
    ActivityType,
    Dependency,
    EventStatus,
    LagCalendar,
    OptimizationGoal,
    Resource,
    ResourceLevelingMode,
    ResourceType,
    ScheduleModel,
)
from ..work_calendar import WorkCalendar
from .options import ScheduleOptions
from .result import ScheduleResult

RELATION_LABELS = {"FS": "完成 → 开始（FS）", "SS": "开始 → 开始（SS）", "FF": "完成 → 完成（FF）", "SF": "开始 → 完成（SF）"}
CONSTRAINT_CATEGORY_LABELS = {
    "process": "工艺",
    "material": "材料",
    "drawing": "图纸",
    "inspection": "验收",
    "handover": "移交",
    "legal": "手续/法规",
    "logistics": "物流",
}
LAG_UNIT_LABELS = {"calendar_day": "自然日", "workday": "工作日"}


class ScheduleError(ValueError):
    pass


class SchedulingEngine:
    """Stable application entry point for schedule calculation."""

    @staticmethod
    def calculate(
        model: ScheduleModel, options: ScheduleOptions | None = None
    ) -> ScheduleResult:
        options = options or ScheduleOptions()
        crew_assignments: dict[int, int] = {}
        dates, reasons = calculate_schedule_details(
            list(model.tasks),
            list(model.dependencies),
            model.project_start,
            model.calendar,
            available_resources=(
                list(model.available_resources)
                if model.available_resources is not None
                else None
            ),
            crew_assignments_out=crew_assignments,
            optimization_goal=options.optimization_goal,
            status_date=options.status_date,
            crew_max_daily_hours=options.crew_max_daily_hours,
            crew_max_consecutive_days=options.crew_max_consecutive_days,
            resource_leveling_mode=options.resource_leveling_mode,
        )
        return ScheduleResult(dates, reasons, crew_assignments)


def _raw_lagged_event(
    calendar: WorkCalendar, value: datetime, relation: Dependency
) -> datetime:
    """Apply a relationship lag without normalising the result to working time."""
    lag_days = relation.lag.total_seconds() / 86400
    if relation.lag_calendar is LagCalendar.WORKDAY:
        return calendar.add_work_hours(value, lag_days * calendar.HOURS_PER_DAY)
    return value + timedelta(days=lag_days)


def lagged_event(
    calendar: WorkCalendar, value: datetime, relation: Dependency | dict
) -> datetime:
    """Apply relationship waiting and return the next productive work time."""
    return calendar.next_work_time(
        _raw_lagged_event(calendar, value, Dependency.from_mapping(relation))
    )


def schedule_sort_key(task: dict) -> tuple:
    """Sort dated tasks first by calculated start, keeping a stable tie-breaker."""
    raw_start = task.get("calculated_start")
    if raw_start:
        try:
            start = datetime.fromisoformat(str(raw_start))
        except ValueError:
            start = datetime.max
        missing = 0
    else:
        start = datetime.max
        missing = 1
    return (
        missing,
        start,
        int(task.get("sort_order", 0)),
        int(task.get("id", 0)),
    )


def _duration_minutes(task: Activity) -> int:
    return task.duration_minutes


def _uses_work_calendar(task: Activity, calendar: WorkCalendar | None = None) -> bool:
    """Noisy work may never use elapsed time across calendar rest days."""
    ignore_noise = bool(
        calendar is not None and getattr(calendar, "ignore_noise_restrictions", False)
    )
    return not task.uses_elapsed_time or (
        bool(task.get("has_noise", 0)) and not ignore_noise
    )


def _finish_for_task(calendar: WorkCalendar, start: datetime, task: Activity) -> tuple[datetime, datetime]:
    minutes = _duration_minutes(task)
    if not _uses_work_calendar(task, calendar):
        return start, start + timedelta(minutes=minutes)
    if minutes == 0:
        actual_start = calendar.next_work_time(start)
        return actual_start, actual_start
    return calendar.finish_from_start_time(start, minutes / 60 / calendar.HOURS_PER_DAY)


def _start_for_finish(calendar: WorkCalendar, finish: datetime, task: Activity) -> datetime:
    minutes = _duration_minutes(task)
    if not _uses_work_calendar(task, calendar):
        return finish - timedelta(minutes=minutes)
    return calendar.start_for_finish_time(
        finish, minutes / 60 / calendar.HOURS_PER_DAY
    )


def _task_resources(task: Activity) -> list[dict]:
    resources = task.resources
    selected: list[dict] = []
    for raw_resource in resources:
        if not raw_resource.get("enabled", 1):
            continue
        resource = dict(raw_resource)
        if (
            raw_resource.type is ResourceType.ACCESS
            and task.type is not ActivityType.LOGISTICS
        ):
            mode = str(task.get("spatial_occupancy_mode", "reduce"))
            if mode == "normal":
                resource["demand_amount"] = 0.0
            elif mode == "close":
                resource["demand_amount"] = float(resource.get("capacity", 1))
            else:
                resource["demand_amount"] = max(
                    0.0, float(task.get("traffic_reduction", 1))
                )
        selected.append(resource)
    return selected


def _calendar_with_resource_availability(
    calendar: WorkCalendar, resources: list[dict]
) -> tuple[WorkCalendar, set[str]]:
    """Intersect the project calendar with crew and path availability calendars."""
    calendar_resources = [
        resource for resource in resources if resource.get("availability_exceptions")
    ]
    if not calendar_resources:
        return calendar, set()
    exceptions = {
        day.isoformat(): is_working for day, is_working in calendar.exceptions.items()
    }
    restricted_names: set[str] = set()
    for resource in calendar_resources:
        for raw_day, is_available in resource.get("availability_exceptions", {}).items():
            day = date.fromisoformat(str(raw_day))
            prior_working = exceptions.get(day.isoformat(), calendar.is_working_day(day))
            effective_working = bool(prior_working) and bool(is_available)
            exceptions[day.isoformat()] = effective_working
            if prior_working and not effective_working:
                restricted_names.add(str(resource.get("name", "资源")))
    return (
        WorkCalendar(
            exceptions,
            weekend_working=calendar.weekend_working,
            ignore_noise_restrictions=getattr(
                calendar, "ignore_noise_restrictions", False
            ),
            sessions=calendar.SESSIONS,
        ),
        restricted_names,
    )


def _capacity_blockers(
    allocations: list[tuple[datetime, datetime, float, int]],
    start: datetime,
    finish: datetime,
    demand: float,
    capacity: float,
) -> list[tuple[datetime, datetime, float, int]]:
    """Return allocations active in the first interval that exceeds capacity."""
    overlaps = [
        allocation
        for allocation in allocations
        if allocation[0] < finish and start < allocation[1]
    ]
    points = sorted(
        {start, finish}
        | {max(start, allocation[0]) for allocation in overlaps}
        | {min(finish, allocation[1]) for allocation in overlaps}
    )
    for left, right in zip(points, points[1:]):
        if right <= left:
            continue
        active = [
            allocation
            for allocation in overlaps
            if allocation[0] < right and left < allocation[1]
        ]
        if demand + sum(item[2] for item in active) > capacity + 1e-9:
            return active
    return []


def _earliest_resource_slot(
    calendar: WorkCalendar,
    task: dict,
    logical_start: datetime,
    resources: list[dict],
    allocations: dict[int, list[tuple[datetime, datetime, float, int]]],
    *,
    allow_overload: bool = False,
    crew_max_daily_hours: float | None = None,
    crew_max_consecutive_days: int | None = None,
) -> tuple[datetime, datetime, set[str], set[str], set[str]]:
    activity_calendar, restricted_calendar_names = _calendar_with_resource_availability(
        calendar, resources
    )
    crew = next(
        (resource for resource in resources if resource.get("resource_type") == "crew"),
        None,
    )
    if crew is not None and crew_max_daily_hours is not None:
        activity_calendar = activity_calendar.limited_daily_hours(
            min(float(crew_max_daily_hours), activity_calendar.HOURS_PER_DAY)
        )
    start = logical_start
    delayed_resources: set[str] = set()
    overloaded_resources: set[str] = set()
    delayed_crew_calendars: set[str] = set()
    if _uses_work_calendar(task, activity_calendar):
        if (
            restricted_calendar_names
            and activity_calendar.next_work_time(start) != calendar.next_work_time(start)
        ):
            delayed_crew_calendars.update(restricted_calendar_names)
        start = activity_calendar.next_work_time(start)
    while True:
        actual_start, finish = _finish_for_task(activity_calendar, start, task)
        if (
            crew is not None
            and crew_max_consecutive_days
            and _uses_work_calendar(task, activity_calendar)
        ):
            crew_id = int(crew["id"])
            existing_dates = {
                interval_start.date()
                for allocation in allocations[crew_id]
                for interval_start, _ in activity_calendar.work_intervals_between(
                    allocation[0], allocation[1]
                )
            }
            candidate_dates = {
                interval_start.date()
                for interval_start, _ in activity_calendar.work_intervals_between(
                    actual_start, finish
                )
            }
            violation: date | None = None
            streak = 0
            previous_day: date | None = None
            for work_day in sorted(existing_dates | candidate_dates):
                streak = (
                    streak + 1
                    if previous_day is not None
                    and work_day == previous_day + timedelta(days=1)
                    else 1
                )
                if streak > int(crew_max_consecutive_days) and work_day in candidate_dates:
                    violation = work_day
                    break
                previous_day = work_day
            if violation is not None:
                exceptions = {
                    day.isoformat(): working
                    for day, working in activity_calendar.exceptions.items()
                }
                exceptions[violation.isoformat()] = False
                activity_calendar = WorkCalendar(
                    exceptions,
                    weekend_working=activity_calendar.weekend_working,
                    ignore_noise_restrictions=activity_calendar.ignore_noise_restrictions,
                    sessions=activity_calendar.SESSIONS,
                )
                delayed_crew_calendars.add(
                    f"{crew.get('name', '班组')}连续工作上限"
                )
                continue
        if restricted_calendar_names:
            base_start, base_finish = _finish_for_task(calendar, start, task)
            if actual_start != base_start or finish != base_finish:
                delayed_crew_calendars.update(restricted_calendar_names)
        blocking_finishes: list[datetime] = []
        for resource in resources:
            resource_id = int(resource["id"])
            demand = float(resource.get("demand_amount", 1))
            capacity = float(resource.get("capacity", 1))
            blockers = _capacity_blockers(
                allocations[resource_id], actual_start, finish, demand, capacity
            )
            if blockers:
                resource_name = str(resource.get("name", f"资源{resource_id}"))
                if allow_overload:
                    overloaded_resources.add(resource_name)
                else:
                    delayed_resources.add(resource_name)
                    blocking_finishes.extend(item[1] for item in blockers)
        if not blocking_finishes:
            return (
                actual_start,
                finish,
                delayed_resources,
                delayed_crew_calendars,
                overloaded_resources,
            )
        start = min(value for value in blocking_finishes if value > actual_start)
        if _uses_work_calendar(task, activity_calendar):
            start = activity_calendar.next_work_time(start)


def _parsed_schedule_span(task: dict) -> tuple[datetime, datetime] | None:
    """Return the previously saved span when both endpoints are valid."""
    raw_start = task.get("calculated_start")
    raw_finish = task.get("calculated_finish")
    if not raw_start or not raw_finish:
        return None
    try:
        return datetime.fromisoformat(str(raw_start)), datetime.fromisoformat(
            str(raw_finish)
        )
    except ValueError:
        return None


def _has_successor_path(
    outgoing: dict[int, list[int]], start_id: int, target_id: int
) -> bool:
    """Return whether target_id is reachable from start_id."""
    pending = [start_id]
    visited: set[int] = set()
    while pending:
        task_id = pending.pop()
        if task_id == target_id:
            return True
        if task_id in visited:
            continue
        visited.add(task_id)
        pending.extend(outgoing.get(task_id, ()))
    return False


def crew_actual_work_intervals(
    tasks: list[Activity | dict],
    crew: Resource | dict,
    calendar: WorkCalendar,
    max_consecutive_days: int = 0,
) -> list[dict]:
    """Return the productive intervals actually assigned to one crew by the schedule."""
    tasks = [Activity.from_mapping(task) for task in tasks]
    crew = Resource.from_mapping(crew)
    crew_id = int(crew["id"])
    rows: list[dict] = []
    for task in tasks:
        assigned_crew = task.get("assigned_crew")
        if not assigned_crew or int(assigned_crew["id"]) != crew_id:
            continue
        span = _parsed_schedule_span(task)
        if span is None or span[1] <= span[0]:
            continue
        resources = {
            int(resource["id"]): resource for resource in _task_resources(task)
        }
        resources[crew_id] = crew
        activity_calendar, _ = _calendar_with_resource_availability(
            calendar, list(resources.values())
        )
        start, finish = span
        if _uses_work_calendar(task, activity_calendar):
            intervals = activity_calendar.work_intervals_between(start, finish)
        else:
            intervals = []
            cursor = start
            while cursor < finish:
                next_midnight = datetime.combine(cursor.date() + timedelta(days=1), time())
                interval_finish = min(finish, next_midnight)
                intervals.append((cursor, interval_finish))
                cursor = interval_finish
        location = "、".join(
            str(resource.get("name", ""))
            for resource in resources.values()
            if resource.get("resource_type") in {"workface", "access"}
            and resource.get("name")
        )
        for interval_start, interval_finish in intervals:
            rows.append(
                {
                    "task_id": int(task["id"]),
                    "task_name": str(task.get("name", task["id"])),
                    "task_type": str(task.get("task_type", "work")),
                    "location": location,
                    "start": interval_start,
                    "finish": interval_finish,
                    "hours": (interval_finish - interval_start).total_seconds() / 3600,
                }
            )
    if max_consecutive_days > 0:
        blocked_dates: set[date] = set()
        streak = 0
        previous_worked_date: date | None = None
        for work_date in sorted({row["start"].date() for row in rows}):
            next_streak = (
                streak + 1
                if previous_worked_date is not None
                and work_date == previous_worked_date + timedelta(days=1)
                else 1
            )
            if next_streak > int(max_consecutive_days):
                blocked_dates.add(work_date)
                streak = 0
                continue
            streak = next_streak
            previous_worked_date = work_date
        rows = [row for row in rows if row["start"].date() not in blocked_dates]
    rows.sort(key=lambda row: (row["start"], row["finish"], row["task_id"]))
    return rows


def calculate_schedule_details(
    tasks: list[Activity | dict],
    dependencies: list[Dependency | dict],
    project_start: str,
    calendar=None,
    available_resources: list[dict] | None = None,
    crew_assignments_out: dict[int, int] | None = None,
    optimization_goal: str = "stable",
    status_date: str | None = None,
    crew_max_daily_hours: float | None = None,
    crew_max_consecutive_days: int | None = None,
    resource_leveling_mode: str = "delay",
    _compare_alternatives: bool = True,
    _group_continuity: bool = True,
    _group_barrier_rounds: int = 3,
) -> tuple[dict[int, tuple[str, str]], dict[int, str]]:
    """Generate an earliest feasible schedule with renewable-resource capacity checks."""
    if not tasks:
        return {}, {}
    try:
        tasks = [Activity.from_mapping(task) for task in tasks]
        dependencies = [Dependency.from_mapping(item) for item in dependencies]
        if available_resources is not None:
            available_resources = [
                Resource.from_mapping(item) for item in available_resources
            ]
    except (KeyError, TypeError, ValueError) as error:
        raise ScheduleError(f"排程输入模型无效：{error}") from error
    try:
        optimization_goal = OptimizationGoal(optimization_goal)
    except ValueError as error:
        raise ScheduleError("未知的排程优化目标。") from error
    try:
        resource_leveling_mode = ResourceLevelingMode(resource_leveling_mode)
    except ValueError as error:
        raise ScheduleError("未知的资源平衡方式。") from error
    task_by_id = {task.id: task for task in tasks}
    children = defaultdict(list)
    for task in tasks:
        children[task.parent_id].append(task.id)
    leaf_ids = {task_id for task_id in task_by_id if not children.get(task_id)}

    resource_pool = list(available_resources or [])
    if available_resources is None:
        seen_resource_ids: set[int] = set()
        for task in tasks:
            for resource in _task_resources(task):
                resource_id = int(resource["id"])
                if resource_id not in seen_resource_ids:
                    seen_resource_ids.add(resource_id)
                    resource_pool.append(resource)
    resource_by_id = {int(resource["id"]): resource for resource in resource_pool}
    preserve_resource_order = (
        optimization_goal is OptimizationGoal.STABLE
        and resource_leveling_mode is ResourceLevelingMode.DELAY
    )

    incoming, outgoing = defaultdict(list), defaultdict(list)
    indegree = {task_id: 0 for task_id in leaf_ids}
    relation_pairs: set[tuple[int, int]] = set()
    for relation in dependencies:
        pred, succ = relation.predecessor, relation.successor
        if pred not in leaf_ids or succ not in leaf_ids:
            raise ScheduleError("汇总任务不能参与任务关系，请只连接最末级任务。")
        incoming[succ].append(relation)
        outgoing[pred].append(succ)
        indegree[succ] += 1
        relation_pairs.add((pred, succ))

    availability_incoming: dict[int, list[int]] = defaultdict(list)
    availability_pairs: set[tuple[int, int]] = set()
    for task_id in leaf_ids:
        for resource in _task_resources(task_by_id[task_id]):
            activation_id = resource.get("activation_event_id")
            if activation_id is None:
                continue
            activation_id = int(activation_id)
            pair = (activation_id, task_id)
            if (
                activation_id not in leaf_ids
                or activation_id == task_id
                or pair in relation_pairs
                or pair in availability_pairs
                or _has_successor_path(outgoing, task_id, activation_id)
            ):
                continue
            availability_pairs.add(pair)
            availability_incoming[task_id].append(activation_id)
            outgoing[activation_id].append(task_id)
            indegree[task_id] += 1

    # Preserve the last feasible order on exclusive resources. These are transient
    # calculation constraints, not user-created process dependencies.
    stable_incoming: dict[int, list[int]] = defaultdict(list)
    tasks_by_resource: dict[int, list[tuple[datetime, datetime, int]]] = defaultdict(list)
    prior_crew_by_task: dict[int, int] = {}
    for task_id in leaf_ids:
        task = task_by_id[task_id]
        prior_span = _parsed_schedule_span(task)
        if prior_span is None:
            continue
        task_resource_ids: set[int] = set()
        if preserve_resource_order:
            task_resource_ids = {
                int(resource["id"])
                for resource in _task_resources(task)
                if float(resource.get("capacity", 1)) <= 1
                and float(resource.get("demand_amount", 1)) > 0
            }
        if task.get("crew_required") and task.get("calculated_crew_id") is not None:
            crew_id = int(task["calculated_crew_id"])
            crew = resource_by_id.get(crew_id)
            required_trade = str(task.get("trade", "杂工"))
            if (
                crew is not None
                and crew.get("resource_type") == "crew"
                and crew.get("enabled", 1)
                and required_trade in (crew.get("skills") or [])
            ):
                prior_crew_by_task[task_id] = crew_id
                if (
                    preserve_resource_order
                    and float(crew.get("capacity", 1)) <= 1
                ):
                    task_resource_ids.add(crew_id)
        for resource_id in task_resource_ids:
            tasks_by_resource[resource_id].append((*prior_span, task_id))

    stable_pairs: set[tuple[int, int]] = set()
    for resource_tasks in tasks_by_resource.values():
        resource_tasks.sort(key=lambda item: (item[0], item[1], item[2]))
        for previous, current in zip(resource_tasks, resource_tasks[1:]):
            pred, succ = previous[2], current[2]
            pair = (pred, succ)
            if pred == succ or pair in relation_pairs or pair in stable_pairs:
                continue
            if _has_successor_path(outgoing, succ, pred):
                continue
            stable_pairs.add(pair)
            stable_incoming[succ].append(pred)
            outgoing[pred].append(succ)
            indegree[succ] += 1
    def priority_key(task_id: int) -> tuple:
        task = task_by_id[task_id]
        return (
            -int(task.get("priority", 50)),
            int(task.get("sort_order", 0)),
            task_id,
        )

    queue = sorted(
        (task_id for task_id, degree in indegree.items() if degree == 0),
        key=priority_key,
    )
    order = []
    active_group_id: int | None = None

    def queue_key(task_id: int) -> tuple:
        task = task_by_id[task_id]
        group_id = task.get("parent_id") if task.get("crew_required") else None
        continues_active_group = (
            optimization_goal == "continuity"
            and _group_continuity
            and active_group_id is not None
            and group_id is not None
            and int(group_id) == active_group_id
        )
        return (0 if continues_active_group else 1, *priority_key(task_id))

    while queue:
        queue.sort(key=queue_key)
        task_id = queue.pop(0)
        order.append(task_id)
        selected_task = task_by_id[task_id]
        selected_group_id = (
            selected_task.get("parent_id")
            if selected_task.get("crew_required")
            else None
        )
        if (
            optimization_goal == "continuity"
            and _group_continuity
            and selected_group_id is not None
        ):
            active_group_id = int(selected_group_id)
        for successor in outgoing[task_id]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    if len(order) != len(leaf_ids):
        raise ScheduleError("任务关系中存在循环，无法计算日期。")

    calendar = calendar or WorkCalendar()
    baseline = calendar.next_work_time(
        datetime.combine(date.fromisoformat(project_start), calendar.SESSIONS[0][0])
    )
    status_floor = (
        calendar.next_work_time(
            datetime.combine(date.fromisoformat(status_date), calendar.SESSIONS[0][0])
        )
        if status_date
        else None
    )
    if status_floor is not None:
        baseline = max(baseline, status_floor)
    calculated: dict[int, tuple[datetime, datetime]] = {}
    reasons: dict[int, str] = {}
    allocations: dict[int, list[tuple[datetime, datetime, float, int]]] = defaultdict(list)
    calculated_crew_assignments: dict[int, int] = {}
    crew_history: dict[
        int, list[tuple[datetime, datetime, set[int], int | None]]
    ] = defaultdict(list)
    frozen_ids: set[int] = set()
    if status_floor is not None:
        for task_id in leaf_ids:
            task = task_by_id[task_id]
            prior_span = _parsed_schedule_span(task)
            if (
                prior_span is None
                or prior_span[0] >= status_floor
                or task.is_milestone
            ):
                continue
            frozen_ids.add(task_id)
            calculated[task_id] = prior_span
            reasons[task_id] = "排程基准日前任务保持原时间"
            fixed_resources = [
                resource
                for resource in _task_resources(task)
                if resource.get("resource_type") != "crew"
            ]
            crew_id = prior_crew_by_task.get(task_id)
            assigned_crew = resource_by_id.get(crew_id) if crew_id is not None else None
            scheduled_resources = fixed_resources + (
                [assigned_crew] if assigned_crew is not None else []
            )
            for resource in scheduled_resources:
                allocations[int(resource["id"])].append(
                    (
                        prior_span[0],
                        prior_span[1],
                        float(resource.get("demand_amount", 1)),
                        task_id,
                    )
                )
            if assigned_crew is not None:
                calculated_crew_assignments[task_id] = int(assigned_crew["id"])
                workface_ids = {
                    int(resource["id"])
                    for resource in fixed_resources
                    if resource.get("resource_type") == "workface"
                }
                crew_history[int(assigned_crew["id"])].append(
                    (prior_span[0], prior_span[1], workface_ids, task.get("parent_id"))
                )
    for task_id in order:
        task = task_by_id[task_id]
        if task_id in frozen_ids:
            continue
        if task.is_milestone:
            event_status = task.event_status
            if event_status is EventStatus.PENDING:
                reasons[task_id] = "外部事件尚未发生"
                continue
            if event_status in {EventStatus.PLANNED, EventStatus.OCCURRED}:
                raw_event_time = task.event_time
                if not raw_event_time:
                    reasons[task_id] = "事件尚未填写发生时间"
                    continue
                try:
                    event_time = datetime.fromisoformat(str(raw_event_time))
                except ValueError as error:
                    raise ScheduleError(f"事件“{task.get('name', task_id)}”的时间格式无效。") from error
                calculated[task_id] = event_time, event_time
                reasons[task_id] = (
                    "按实际发生时间记录"
                    if event_status is EventStatus.OCCURRED
                    else "按预计发生时间安排"
                )
                continue
        disabled_spaces = [
            str(resource.get("name", "未命名空间"))
            for resource in (task.get("resources") or [])
            if resource.get("resource_type") == "access"
            and resource.get("path_segment_id") is not None
            and not resource.get("enabled", 1)
        ]
        if disabled_spaces:
            reasons[task_id] = f"所需作业面或路径已停用：{'、'.join(disabled_spaces)}"
            continue
        unavailable_predecessors = [
            int(relation["predecessor_id"])
            for relation in incoming[task_id]
            if int(relation["predecessor_id"]) not in calculated
        ]
        unavailable_predecessors.extend(
            predecessor_id
            for predecessor_id in stable_incoming[task_id]
            if predecessor_id not in calculated
        )
        unavailable_predecessors.extend(
            predecessor_id
            for predecessor_id in availability_incoming[task_id]
            if predecessor_id not in calculated
        )
        if unavailable_predecessors:
            blocked_names = "、".join(
                str(task_by_id[pred_id].get("name", pred_id))
                for pred_id in unavailable_predecessors
            )
            has_pending_event = any(
                task_by_id[pred_id].is_milestone
                for pred_id in unavailable_predecessors
            )
            reasons[task_id] = (
                f"等待前置事件：{blocked_names}"
                if has_pending_event
                else "前置任务尚未具备排程条件"
            )
            continue
        duration_minutes = _duration_minutes(task)
        if duration_minutes < 0 or (
            duration_minutes == 0 and not task.is_milestone
        ):
            raise ScheduleError("任务工期必须大于 0。")
        start_constraints: list[datetime] = []
        if status_floor is not None:
            start_constraints.append(status_floor)
        if task.get("constraint_start"):
            constraint = datetime.combine(
                date.fromisoformat(task["constraint_start"]), calendar.SESSIONS[0][0]
            )
            start_constraints.append(calendar.next_work_time(constraint))
        elif not incoming[task_id] and not stable_incoming[task_id]:
            # The project date is only the fallback for independent tasks.
            # An explicitly constrained chain may legitimately start earlier.
            start_constraints.append(baseline)
        for relation in incoming[task_id]:
            pred_start, pred_finish = calculated[int(relation["predecessor_id"])]
            code = relation.relation
            elapsed = not _uses_work_calendar(task, calendar)
            if code == "FS":
                raw = _raw_lagged_event(calendar, pred_finish, relation)
                constraint = raw if elapsed else calendar.next_work_time(raw)
            elif code == "SS":
                raw = _raw_lagged_event(calendar, pred_start, relation)
                constraint = raw if elapsed else calendar.next_work_time(raw)
            elif code == "FF":
                raw = _raw_lagged_event(calendar, pred_finish, relation)
                constraint = _start_for_finish(calendar, raw, task)
            else:
                raw = _raw_lagged_event(calendar, pred_start, relation)
                constraint = _start_for_finish(calendar, raw, task)
            start_constraints.append(constraint)
        for predecessor_id in stable_incoming[task_id]:
            predecessor_finish = calculated[predecessor_id][1]
            constraint = (
                predecessor_finish
                if not _uses_work_calendar(task, calendar)
                else calendar.next_work_time(predecessor_finish)
            )
            start_constraints.append(constraint)
        for predecessor_id in availability_incoming[task_id]:
            start_constraints.append(
                calendar.next_work_time(calculated[predecessor_id][1])
            )
        for resource in _task_resources(task):
            if resource.get("available_from"):
                start_constraints.append(
                    calendar.next_work_time(
                        datetime.combine(
                            date.fromisoformat(str(resource["available_from"])),
                            calendar.SESSIONS[0][0],
                        )
                    )
                )
        logical_start = max(start_constraints) if start_constraints else baseline
        fixed_resources = [
            resource
            for resource in _task_resources(task)
            if resource.get("resource_type") != "crew"
        ]
        workface_ids = {
            int(resource["id"])
            for resource in fixed_resources
            if resource.get("resource_type") == "workface"
        }
        predecessor_crews = {
            calculated_crew_assignments[int(relation["predecessor_id"])]
            for relation in incoming[task_id]
            if int(relation["predecessor_id"]) in calculated_crew_assignments
            and task_by_id[int(relation["predecessor_id"])].get("trade")
            == task.get("trade")
        }
        candidate_crews: list[dict | None] = [None]
        if task.get("crew_required"):
            required_trade = str(task.get("trade", "杂工"))
            prior_crew_id = (
                prior_crew_by_task.get(task_id)
                if optimization_goal == "stable"
                else None
            )
            if prior_crew_id is not None:
                candidate_crews = [resource_by_id[prior_crew_id]]
            else:
                candidate_crews = [
                    resource
                    for resource in resource_pool
                    if resource.get("resource_type") == "crew"
                    and resource.get("enabled", 1)
                    and required_trade in (resource.get("skills") or [])
                ]
            if not candidate_crews:
                reasons[task_id] = f"没有具备“{required_trade}”能力的可用班组，暂不排程"
                continue

        choices = []
        for crew in candidate_crews:
            scheduled_resources = fixed_resources + ([crew] if crew is not None else [])
            start, finish, delayed, calendar_delayed, overloaded = _earliest_resource_slot(
                calendar,
                task,
                logical_start,
                scheduled_resources,
                allocations,
                allow_overload=resource_leveling_mode == "allow_overload",
                crew_max_daily_hours=crew_max_daily_hours,
                crew_max_consecutive_days=crew_max_consecutive_days,
            )
            latest_finishes: list[datetime] = []
            for resource in scheduled_resources:
                if resource.get("available_until"):
                    latest_finishes.append(
                        datetime.combine(
                            date.fromisoformat(str(resource["available_until"])),
                            time.max,
                        )
                    )
                deactivation_id = resource.get("deactivation_event_id")
                if deactivation_id is not None:
                    deactivation_id = int(deactivation_id)
                    raw_event_time = task_by_id.get(deactivation_id, {}).get("event_time")
                    if raw_event_time:
                        latest_finishes.append(
                            datetime.fromisoformat(str(raw_event_time))
                        )
                    elif deactivation_id in calculated:
                        latest_finishes.append(calculated[deactivation_id][0])
            if latest_finishes and finish > min(latest_finishes):
                continue
            crew_id = int(crew["id"]) if crew else -1
            continuity_rank = 0
            idle_seconds = 0.0
            if crew is not None:
                prior_assignments = [
                    entry for entry in crew_history[crew_id] if entry[1] <= start
                ]
                last_assignment = (
                    max(prior_assignments, key=lambda entry: entry[1])
                    if prior_assignments
                    else None
                )
                if crew_id in predecessor_crews:
                    continuity_rank = 0
                elif last_assignment and workface_ids & last_assignment[2]:
                    continuity_rank = 1
                elif (
                    last_assignment
                    and task.get("parent_id") is not None
                    and task.get("parent_id") == last_assignment[3]
                ):
                    continuity_rank = 2
                else:
                    continuity_rank = 3
                idle_seconds = (
                    (start - last_assignment[1]).total_seconds()
                    if last_assignment
                    else float("inf")
                )
            choices.append(
                (
                    start,
                    continuity_rank,
                    idle_seconds,
                    finish,
                    crew_id,
                    crew,
                    delayed,
                    calendar_delayed,
                    overloaded,
                    scheduled_resources,
                )
            )
        if not choices:
            reasons[task_id] = "所需作业面或路径已停用，任务无法排入其有效期"
            continue
        (
            start,
            _,
            _,
            finish,
            _,
            assigned_crew,
            delayed_resources,
            delayed_crew_calendars,
            overloaded_resources,
            scheduled_resources,
        ) = min(
            choices,
            key=(
                (lambda choice: (choice[1], choice[0], choice[3], choice[2], choice[4]))
                if optimization_goal == "continuity"
                else (
                    lambda choice: (
                        choice[0],
                        choice[3],
                        choice[1],
                        choice[2],
                        choice[4],
                    )
                )
            ),
        )

        calculated[task_id] = start, finish
        for resource in scheduled_resources:
            allocations[int(resource["id"])].append(
                (start, finish, float(resource.get("demand_amount", 1)), task_id)
            )
        if assigned_crew is not None:
            assigned_crew_id = int(assigned_crew["id"])
            calculated_crew_assignments[task_id] = assigned_crew_id
            crew_history[assigned_crew_id].append(
                (start, finish, workface_ids, task.get("parent_id"))
            )
        crew_reason = (
            f"系统分配班组：{assigned_crew['name']}" if assigned_crew is not None else ""
        )
        calendar_reason = (
            f"受资源日历影响：{'、'.join(sorted(delayed_crew_calendars))}"
            if delayed_crew_calendars
            else ""
        )
        stable_reason = (
            "沿用上次资源排程顺序" if stable_incoming[task_id] else ""
        )
        overload_reason = (
            f"允许资源超负荷：{'、'.join(sorted(overloaded_resources))}"
            if overloaded_resources
            else ""
        )
        if delayed_resources:
            resources_text = "、".join(sorted(delayed_resources))
            delay_reason = f"等待资源可用：{resources_text}"
            reasons[task_id] = "；".join(
                filter(
                    None,
                    (
                        crew_reason,
                        calendar_reason,
                        stable_reason,
                        overload_reason,
                        delay_reason,
                    ),
                )
            )
        elif crew_reason or calendar_reason or stable_reason or overload_reason:
            reasons[task_id] = "；".join(
                filter(
                    None,
                    (crew_reason, calendar_reason, stable_reason, overload_reason),
                )
            )
        elif incoming[task_id]:
            categories = {
                CONSTRAINT_CATEGORY_LABELS.get(
                    str(relation.get("constraint_category", "process")), "其他"
                )
                for relation in incoming[task_id]
            }
            reasons[task_id] = f"由{'、'.join(sorted(categories))}约束控制"
        elif task.get("constraint_start"):
            reasons[task_id] = "由最早开始日期控制"
        else:
            reasons[task_id] = "按计划起始日期安排"

    visiting = set()
    def summary_dates(task_id: int) -> tuple[datetime, datetime] | None:
        if task_id in calculated:
            return calculated[task_id]
        if task_id in leaf_ids:
            return None
        if task_id in visiting:
            raise ScheduleError("任务层级中存在循环。")
        visiting.add(task_id)
        spans = [summary_dates(child_id) for child_id in children[task_id]]
        visiting.remove(task_id)
        if not spans or any(span is None for span in spans):
            reasons[task_id] = "下级任务尚未全部具备排程条件"
            return None
        valid_spans = [span for span in spans if span is not None]
        calculated[task_id] = (
            min(span[0] for span in valid_spans),
            max(span[1] for span in valid_spans),
        )
        return calculated[task_id]
    for task_id in task_by_id:
        summary_dates(task_id)
    values = {
        task_id: (start.isoformat(timespec="minutes"), finish.isoformat(timespec="minutes"))
        for task_id, (start, finish) in calculated.items()
    }
    if optimization_goal == "earliest" and _compare_alternatives and values:
        candidates = [(values, reasons, calculated_crew_assignments)]
        for alternative_goal in ("continuity", "stable"):
            alternative_assignments: dict[int, int] = {}
            alternative_values, alternative_reasons = calculate_schedule_details(
                tasks,
                dependencies,
                project_start,
                calendar,
                available_resources,
                alternative_assignments,
                optimization_goal=alternative_goal,
                status_date=status_date,
                crew_max_daily_hours=crew_max_daily_hours,
                crew_max_consecutive_days=crew_max_consecutive_days,
                resource_leveling_mode=resource_leveling_mode,
                _compare_alternatives=False,
            )
            if alternative_values:
                candidates.append(
                    (alternative_values, alternative_reasons, alternative_assignments)
                )
        values, reasons, calculated_crew_assignments = min(
            candidates,
            key=lambda candidate: max(finish for _, finish in candidate[0].values()),
        )
    elif (
        optimization_goal == "continuity"
        and _compare_alternatives
        and _group_continuity
        and _group_barrier_rounds > 0
        and values
    ):
        # Group continuity is preferred, but it may not worsen the project finish.
        # The ungrouped alternative is the escape path when waiting for a whole
        # task family would hold back the controlling chain.
        alternative_assignments: dict[int, int] = {}
        alternative_values, alternative_reasons = calculate_schedule_details(
            tasks,
            dependencies,
            project_start,
            calendar,
            available_resources,
            alternative_assignments,
            optimization_goal="continuity",
            status_date=status_date,
            crew_max_daily_hours=crew_max_daily_hours,
            crew_max_consecutive_days=crew_max_consecutive_days,
            resource_leveling_mode=resource_leveling_mode,
            _compare_alternatives=False,
            _group_continuity=False,
        )
        if alternative_values and max(
            finish for _, finish in alternative_values.values()
        ) < max(finish for _, finish in values.values()):
            values = alternative_values
            reasons = alternative_reasons
            calculated_crew_assignments = alternative_assignments

        def group_switch_count(
            candidate_values: dict[int, tuple[str, str]],
            candidate_assignments: dict[int, int],
        ) -> tuple[int, Counter]:
            timelines: dict[int, list[tuple[str, int]]] = defaultdict(list)
            for candidate_task_id, crew_id in candidate_assignments.items():
                task = task_by_id[candidate_task_id]
                parent_id = task.get("parent_id")
                if parent_id is None or candidate_task_id not in candidate_values:
                    continue
                timelines[int(crew_id)].append(
                    (candidate_values[candidate_task_id][0], int(parent_id))
                )
            transition_counts: Counter = Counter()
            total = 0
            for timeline in timelines.values():
                timeline.sort()
                for (_, previous_group), (_, current_group) in zip(
                    timeline, timeline[1:]
                ):
                    if previous_group == current_group:
                        continue
                    total += 1
                    transition_counts[frozenset((previous_group, current_group))] += 1
            return total, transition_counts

        # Repeated segment-to-segment relations describe a pipeline between two
        # task families. Try a small number of the noisiest family transitions as
        # temporary all-before-all barriers. Keep one only when it does not make
        # the project finish later; this supplies group continuity without turning
        # every pipeline in the project into a rigid serial chain.
        relation_group_counts: Counter = Counter()
        for relation in dependencies:
            predecessor = task_by_id.get(int(relation["predecessor_id"]))
            successor = task_by_id.get(int(relation["successor_id"]))
            if predecessor is None or successor is None:
                continue
            predecessor_group = predecessor.get("parent_id")
            successor_group = successor.get("parent_id")
            if (
                predecessor_group is None
                or successor_group is None
                or predecessor_group == successor_group
                or predecessor.get("trade") != successor.get("trade")
            ):
                continue
            group_pair = (int(predecessor_group), int(successor_group))
            relation_group_counts[group_pair] += 1

        best_switches, transitions = group_switch_count(
            values, calculated_crew_assignments
        )
        scored_group_edges = sorted(
            (
                (
                    transitions.get(frozenset((predecessor_group, successor_group)), 0),
                    relation_count,
                    predecessor_group,
                    successor_group,
                )
                for (predecessor_group, successor_group), relation_count
                in relation_group_counts.items()
                if relation_count >= 2
                and transitions.get(
                    frozenset((predecessor_group, successor_group)), 0
                )
                > 0
            ),
            reverse=True,
        )[:8]
        dependency_pairs = {
            (int(relation["predecessor_id"]), int(relation["successor_id"]))
            for relation in dependencies
        }
        base_outgoing: dict[int, list[int]] = defaultdict(list)
        for predecessor_id, successor_id in dependency_pairs:
            base_outgoing[predecessor_id].append(successor_id)
        selected_group_edge: tuple[int, int] | None = None
        selected_group_barriers: list[dict] = []
        best_finish = max(finish for _, finish in values.values())
        for _, _, predecessor_group, successor_group in scored_group_edges:
            predecessor_tasks = [
                task_id for task_id in children[predecessor_group] if task_id in leaf_ids
            ]
            successor_tasks = [
                task_id for task_id in children[successor_group] if task_id in leaf_ids
            ]
            if not predecessor_tasks or not successor_tasks:
                continue
            if any(
                _has_successor_path(base_outgoing, successor_id, predecessor_id)
                for predecessor_id in predecessor_tasks
                for successor_id in successor_tasks
            ):
                continue
            group_barriers = [
                {
                    "predecessor_id": predecessor_id,
                    "successor_id": successor_id,
                    "relation_type": "FS",
                    "lag_days": 0,
                    "lag_unit": "calendar_day",
                    "constraint_category": "process",
                }
                for predecessor_id in predecessor_tasks
                for successor_id in successor_tasks
                if (predecessor_id, successor_id) not in dependency_pairs
            ]
            if not group_barriers:
                continue
            barrier_assignments: dict[int, int] = {}
            barrier_values, barrier_reasons = calculate_schedule_details(
                tasks,
                list(dependencies) + group_barriers,
                project_start,
                calendar,
                available_resources,
                barrier_assignments,
                optimization_goal="continuity",
                status_date=status_date,
                crew_max_daily_hours=crew_max_daily_hours,
                crew_max_consecutive_days=crew_max_consecutive_days,
                resource_leveling_mode=resource_leveling_mode,
                _compare_alternatives=False,
                _group_continuity=False,
            )
            if not barrier_values:
                continue
            barrier_finish = max(
                finish for _, finish in barrier_values.values()
            )
            barrier_switches, _ = group_switch_count(
                barrier_values, barrier_assignments
            )
            if (barrier_finish, barrier_switches) < (best_finish, best_switches):
                values = barrier_values
                reasons = barrier_reasons
                calculated_crew_assignments = barrier_assignments
                best_finish = barrier_finish
                best_switches = barrier_switches
                selected_group_edge = (predecessor_group, successor_group)
                selected_group_barriers = group_barriers
        if selected_group_edge is not None:
            if _group_barrier_rounds > 1:
                refined_assignments: dict[int, int] = {}
                refined_values, refined_reasons = calculate_schedule_details(
                    tasks,
                    list(dependencies) + selected_group_barriers,
                    project_start,
                    calendar,
                    available_resources,
                    refined_assignments,
                    optimization_goal="continuity",
                    status_date=status_date,
                    crew_max_daily_hours=crew_max_daily_hours,
                    crew_max_consecutive_days=crew_max_consecutive_days,
                    resource_leveling_mode=resource_leveling_mode,
                    _compare_alternatives=True,
                    _group_continuity=True,
                    _group_barrier_rounds=_group_barrier_rounds - 1,
                )
                refined_switches, _ = group_switch_count(
                    refined_values, refined_assignments
                )
                refined_finish = max(
                    finish for _, finish in refined_values.values()
                )
                if (refined_finish, refined_switches) <= (
                    best_finish,
                    best_switches,
                ):
                    values = refined_values
                    reasons = refined_reasons
                    calculated_crew_assignments = refined_assignments
            for group_id in selected_group_edge:
                for task_id in children[group_id]:
                    if task_id not in leaf_ids:
                        continue
                    reasons[task_id] = "；".join(
                        filter(
                            None,
                            (
                                reasons.get(task_id, ""),
                                "按同一任务组连续施工优化",
                            ),
                        )
                    )
    if crew_assignments_out is not None:
        crew_assignments_out.clear()
        crew_assignments_out.update(calculated_crew_assignments)
    return values, reasons


def calculate_schedule(
    tasks,
    dependencies,
    project_start: str,
    calendar=None,
    available_resources: list[dict] | None = None,
) -> dict[int, tuple[str, str]]:
    """Compatibility wrapper returning only calculated dates."""
    values, _ = calculate_schedule_details(
        tasks, dependencies, project_start, calendar, available_resources
    )
    return values


def critical_path(
    tasks: list[Activity | dict],
    dependencies: list[Dependency | dict],
    calendar: WorkCalendar | None = None,
    crew_max_daily_hours: float | None = None,
    driving_relations_out: set[tuple[int, int, str]] | None = None,
) -> tuple[set[int], set[tuple[int, int, str]]]:
    """Return tasks and process links controlling project finish.

    ``driving_relations_out`` optionally receives realised non-process links for
    visualisation. They remain schedule output and are never persisted as process
    dependencies.
    """
    if driving_relations_out is not None:
        driving_relations_out.clear()
    try:
        tasks = [Activity.from_mapping(task) for task in tasks]
        dependencies = [Dependency.from_mapping(item) for item in dependencies]
    except (KeyError, TypeError, ValueError) as error:
        raise ScheduleError(f"关键路径输入模型无效：{error}") from error
    calendar = calendar or WorkCalendar()
    task_by_id = {task.id: task for task in tasks}
    children: dict[int, list[int]] = defaultdict(list)
    for task in tasks:
        if task.get("parent_id") is not None:
            children[int(task["parent_id"])].append(task.id)
    leaf_ids = {task_id for task_id in task_by_id if not children.get(task_id)}
    dated_leaf_ids = {
        task_id
        for task_id in leaf_ids
        if task_by_id[task_id].get("calculated_start")
        and task_by_id[task_id].get("calculated_finish")
    }
    if not dated_leaf_ids:
        return set(), set()

    incoming: dict[int, list[dict]] = defaultdict(list)
    for relation in dependencies:
        successor_id = relation.successor
        predecessor_id = relation.predecessor
        if predecessor_id in leaf_ids and successor_id in leaf_ids:
            incoming[successor_id].append(relation)

    # Reconstruct the realised order on exclusive crews, workfaces, equipment and
    # path segments. Resource order is a schedule result, so it is not stored as a
    # process dependency, but it can still be the reason a task controls completion.
    task_resources: dict[int, list[dict]] = {}
    resource_tasks: dict[int, list[int]] = defaultdict(list)
    for task_id in dated_leaf_ids:
        resources = {
            int(resource["id"]): resource
            for resource in _task_resources(task_by_id[task_id])
        }
        assigned_crew = task_by_id[task_id].get("assigned_crew")
        if assigned_crew and assigned_crew.get("enabled", 1):
            resources[int(assigned_crew["id"])] = dict(assigned_crew)
        task_resources[task_id] = list(resources.values())
        for resource_id, resource in resources.items():
            if (
                float(resource.get("capacity", 1)) <= 1
                and float(resource.get("demand_amount", 1)) > 0
            ):
                resource_tasks[resource_id].append(task_id)

    resource_incoming: dict[int, list[int]] = defaultdict(list)
    resource_pairs: set[tuple[int, int]] = set()
    for resource_task_ids in resource_tasks.values():
        resource_task_ids.sort(
            key=lambda task_id: (
                datetime.fromisoformat(task_by_id[task_id]["calculated_start"]),
                datetime.fromisoformat(task_by_id[task_id]["calculated_finish"]),
                task_id,
            )
        )
        for predecessor_id, successor_id in zip(
            resource_task_ids, resource_task_ids[1:]
        ):
            pair = (predecessor_id, successor_id)
            if pair in resource_pairs:
                continue
            predecessor_finish = datetime.fromisoformat(
                task_by_id[predecessor_id]["calculated_finish"]
            )
            successor = task_by_id[successor_id]
            successor_start = datetime.fromisoformat(successor["calculated_start"])
            activity_calendar, _ = _calendar_with_resource_availability(
                calendar, task_resources[successor_id]
            )
            if (
                successor.get("assigned_crew")
                and crew_max_daily_hours is not None
            ):
                activity_calendar = activity_calendar.limited_daily_hours(
                    min(float(crew_max_daily_hours), activity_calendar.HOURS_PER_DAY)
                )
            required_start = predecessor_finish
            if _uses_work_calendar(successor, activity_calendar):
                required_start = activity_calendar.next_work_time(required_start)
            if abs((required_start - successor_start).total_seconds()) <= 60:
                resource_pairs.add(pair)
                resource_incoming[successor_id].append(predecessor_id)

    availability_incoming: dict[int, list[int]] = defaultdict(list)
    for successor_id, resources in task_resources.items():
        successor = task_by_id[successor_id]
        successor_start = datetime.fromisoformat(successor["calculated_start"])
        activity_calendar, _ = _calendar_with_resource_availability(
            calendar, resources
        )
        for resource in resources:
            activation_id = resource.get("activation_event_id")
            if activation_id is None:
                continue
            activation_id = int(activation_id)
            if activation_id not in dated_leaf_ids or activation_id == successor_id:
                continue
            activation_finish = datetime.fromisoformat(
                task_by_id[activation_id]["calculated_finish"]
            )
            required_start = activation_finish
            if _uses_work_calendar(successor, activity_calendar):
                required_start = activity_calendar.next_work_time(required_start)
            if abs((required_start - successor_start).total_seconds()) <= 60:
                availability_incoming[successor_id].append(activation_id)

    project_finish = max(
        datetime.fromisoformat(task_by_id[task_id]["calculated_finish"])
        for task_id in dated_leaf_ids
    )
    tolerance_seconds = 60
    critical_leaf_ids = {
        task_id
        for task_id in dated_leaf_ids
        if abs(
            (
                datetime.fromisoformat(task_by_id[task_id]["calculated_finish"])
                - project_finish
            ).total_seconds()
        )
        <= tolerance_seconds
    }
    critical_relations: set[tuple[int, int, str]] = set()
    pending = deque(critical_leaf_ids)
    while pending:
        successor_id = pending.popleft()
        successor = task_by_id[successor_id]
        successor_start = datetime.fromisoformat(successor["calculated_start"])
        for relation in incoming.get(successor_id, []):
            predecessor_id = int(relation["predecessor_id"])
            predecessor = task_by_id[predecessor_id]
            predecessor_start = datetime.fromisoformat(predecessor["calculated_start"])
            predecessor_finish = datetime.fromisoformat(predecessor["calculated_finish"])
            relation_type = relation.relation.value
            elapsed = not _uses_work_calendar(successor, calendar)
            if relation_type == "FS":
                raw = _raw_lagged_event(calendar, predecessor_finish, relation)
                required_start = raw if elapsed else calendar.next_work_time(raw)
            elif relation_type == "SS":
                raw = _raw_lagged_event(calendar, predecessor_start, relation)
                required_start = raw if elapsed else calendar.next_work_time(raw)
            elif relation_type == "FF":
                raw = _raw_lagged_event(calendar, predecessor_finish, relation)
                required_start = _start_for_finish(calendar, raw, successor)
            else:
                raw = _raw_lagged_event(calendar, predecessor_start, relation)
                required_start = _start_for_finish(calendar, raw, successor)
            if abs((required_start - successor_start).total_seconds()) > tolerance_seconds:
                continue
            critical_relations.add((predecessor_id, successor_id, relation_type))
            if predecessor_id not in critical_leaf_ids:
                critical_leaf_ids.add(predecessor_id)
                pending.append(predecessor_id)
        for predecessor_id in resource_incoming.get(successor_id, []):
            if driving_relations_out is not None:
                driving_relations_out.add(
                    (predecessor_id, successor_id, "resource")
                )
            if predecessor_id not in critical_leaf_ids:
                critical_leaf_ids.add(predecessor_id)
                pending.append(predecessor_id)
        for predecessor_id in availability_incoming.get(successor_id, []):
            if driving_relations_out is not None:
                driving_relations_out.add(
                    (predecessor_id, successor_id, "availability")
                )
            if predecessor_id not in critical_leaf_ids:
                critical_leaf_ids.add(predecessor_id)
                pending.append(predecessor_id)

    critical_task_ids = set(critical_leaf_ids)
    for task_id in tuple(critical_leaf_ids):
        parent_id = task_by_id[task_id].get("parent_id")
        while parent_id is not None:
            parent_id = int(parent_id)
            critical_task_ids.add(parent_id)
            parent_id = task_by_id[parent_id].get("parent_id")
    return critical_task_ids, critical_relations
