from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ScheduleResult:
    """Calculated dates plus the decisions needed by persistence and UI."""

    dates: dict[int, tuple[str, str]] = field(default_factory=dict)
    reasons: dict[int, str] = field(default_factory=dict)
    crew_assignments: dict[int, int] = field(default_factory=dict)
