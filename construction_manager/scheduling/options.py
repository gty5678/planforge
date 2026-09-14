from __future__ import annotations

from dataclasses import dataclass

from ..domain import OptimizationGoal, ResourceLevelingMode


@dataclass(frozen=True, slots=True)
class ScheduleOptions:
    """User-selectable scheduling policy, separate from project data."""

    optimization_goal: OptimizationGoal | str = OptimizationGoal.STABLE
    status_date: str | None = None
    crew_max_daily_hours: float | None = None
    crew_max_consecutive_days: int | None = None
    resource_leveling_mode: ResourceLevelingMode | str = ResourceLevelingMode.DELAY

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "optimization_goal", OptimizationGoal(self.optimization_goal)
        )
        object.__setattr__(
            self,
            "resource_leveling_mode",
            ResourceLevelingMode(self.resource_leveling_mode),
        )
