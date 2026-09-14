"""Backward-compatible scheduling facade.

New application code should use :class:`SchedulingEngine`.  The function
exports remain here so existing integrations can migrate without a flag day.
"""

from .scheduling.engine import (
    CONSTRAINT_CATEGORY_LABELS,
    LAG_UNIT_LABELS,
    RELATION_LABELS,
    ScheduleError,
    SchedulingEngine,
    calculate_schedule,
    calculate_schedule_details,
    lagged_event,
    schedule_sort_key,
)
from .scheduling.critical_path import critical_path
from .scheduling.crew_assignment import crew_actual_work_intervals

__all__ = [
    "CONSTRAINT_CATEGORY_LABELS",
    "LAG_UNIT_LABELS",
    "RELATION_LABELS",
    "ScheduleError",
    "SchedulingEngine",
    "calculate_schedule",
    "calculate_schedule_details",
    "critical_path",
    "crew_actual_work_intervals",
    "lagged_event",
    "schedule_sort_key",
]
