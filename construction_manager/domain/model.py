from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any, TypeVar

from ..work_calendar import WorkCalendar


class ActivityType(StrEnum):
    CONSTRUCTION = "work"
    LOGISTICS = "logistics"
    WAITING = "wait"
    INSPECTION = "inspection"
    MILESTONE = "milestone"


class CalendarType(StrEnum):
    WORKING = "working"
    ELAPSED = "elapsed"


class EventStatus(StrEnum):
    DERIVED = "derived"
    PENDING = "pending"
    PLANNED = "planned"
    OCCURRED = "occurred"


class RelationType(StrEnum):
    FS = "FS"
    SS = "SS"
    FF = "FF"
    SF = "SF"


class LagCalendar(StrEnum):
    CALENDAR_DAY = "calendar_day"
    WORKDAY = "workday"


class ResourceType(StrEnum):
    RESOURCE = "resource"
    CREW = "crew"
    WORKFACE = "workface"
    ACCESS = "access"
    EQUIPMENT = "equipment"
    INSPECTOR = "inspector"


class OptimizationGoal(StrEnum):
    EARLIEST = "earliest"
    STABLE = "stable"
    CONTINUITY = "continuity"


class ResourceLevelingMode(StrEnum):
    DELAY = "delay"
    ALLOW_OVERLOAD = "allow_overload"


EnumT = TypeVar("EnumT", bound=StrEnum)


def _enum(enum_type: type[EnumT], value: object, field_name: str) -> EnumT:
    try:
        return enum_type(str(value))
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(
            f"Invalid {field_name} {value!r}; expected one of: {allowed}"
        ) from error


class _LegacyMapping(Mapping[str, Any]):
    """Temporary read-only adapter for code not migrated from row dictionaries yet."""

    def _legacy_values(self) -> dict[str, Any]:
        raise NotImplementedError

    def __getitem__(self, key: str) -> Any:
        return self._legacy_values()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._legacy_values())

    def __len__(self) -> int:
        return len(self._legacy_values())


@dataclass(frozen=True, slots=True)
class Resource(_LegacyMapping):
    id: int
    name: str = ""
    type: ResourceType = ResourceType.RESOURCE
    capacity: float = 1.0
    demand: float = 1.0
    enabled: bool = True
    skills: tuple[str, ...] = ()
    availability_exceptions: Mapping[str, bool] = field(default_factory=dict)
    activation_event_id: int | None = None
    deactivation_event_id: int | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", _enum(ResourceType, self.type, "resource_type"))
        if self.capacity < 0 or self.demand < 0:
            raise ValueError("Resource capacity and demand cannot be negative")

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any] | Resource) -> Resource:
        if isinstance(row, cls):
            return row
        known = {
            "id", "name", "resource_type", "capacity", "demand_amount", "enabled",
            "skills", "availability_exceptions", "activation_event_id",
            "deactivation_event_id",
        }
        return cls(
            id=int(row["id"]),
            name=str(row.get("name", "")),
            type=_enum(ResourceType, row.get("resource_type", "resource"), "resource_type"),
            capacity=float(row.get("capacity", 1)),
            demand=float(row.get("demand_amount", 1)),
            enabled=bool(row.get("enabled", 1)),
            skills=tuple(str(skill) for skill in (row.get("skills") or ())),
            availability_exceptions=dict(row.get("availability_exceptions") or {}),
            activation_event_id=(int(row["activation_event_id"]) if row.get("activation_event_id") is not None else None),
            deactivation_event_id=(int(row["deactivation_event_id"]) if row.get("deactivation_event_id") is not None else None),
            attributes={key: value for key, value in row.items() if key not in known},
        )

    def _legacy_values(self) -> dict[str, Any]:
        return {
            **self.attributes, "id": self.id, "name": self.name,
            "resource_type": self.type, "capacity": self.capacity,
            "demand_amount": self.demand, "enabled": self.enabled,
            "skills": self.skills, "availability_exceptions": self.availability_exceptions,
            "activation_event_id": self.activation_event_id,
            "deactivation_event_id": self.deactivation_event_id,
        }


@dataclass(frozen=True, slots=True)
class Activity(_LegacyMapping):
    id: int
    name: str = ""
    duration: timedelta = timedelta(hours=8)
    type: ActivityType = ActivityType.CONSTRUCTION
    calendar_type: CalendarType = CalendarType.WORKING
    trade: str = "杂工"
    parent_id: int | None = None
    resources: tuple[Resource, ...] = ()
    event_status: EventStatus = EventStatus.DERIVED
    event_time: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", _enum(ActivityType, self.type, "task_type"))
        object.__setattr__(
            self,
            "calendar_type",
            _enum(CalendarType, self.calendar_type, "calendar_type"),
        )
        object.__setattr__(
            self,
            "event_status",
            _enum(EventStatus, self.event_status, "event_status"),
        )
        if not isinstance(self.duration, timedelta):
            raise TypeError("Activity duration must be a timedelta")
        if self.duration < timedelta(0) or (
            self.duration == timedelta(0) and self.type is not ActivityType.MILESTONE
        ):
            raise ValueError("Activity duration must be positive (or zero for a milestone)")
        if self.type is ActivityType.MILESTONE and self.duration != timedelta(0):
            raise ValueError("Milestone duration must be zero")

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any] | Activity) -> Activity:
        if isinstance(row, cls):
            return row
        raw_minutes = row.get("duration_minutes")
        if raw_minutes is None:
            raw_minutes = round(float(row.get("duration", 0)) * 60 * WorkCalendar.HOURS_PER_DAY)
        minutes = int(raw_minutes)
        activity_type = _enum(ActivityType, row.get("task_type", "work"), "task_type")
        if minutes < 0 or (minutes == 0 and activity_type is not ActivityType.MILESTONE):
            raise ValueError("Activity duration must be positive (or zero for a milestone)")
        if activity_type is ActivityType.MILESTONE and minutes != 0:
            raise ValueError("Milestone duration must be zero")
        known = {
            "id", "name", "duration", "duration_days", "duration_minutes", "task_type",
            "calendar_type", "trade", "parent_id", "resources", "event_status", "event_time",
        }
        return cls(
            id=int(row["id"]), name=str(row.get("name", "")),
            duration=timedelta(minutes=minutes), type=activity_type,
            calendar_type=_enum(CalendarType, row.get("calendar_type", "working"), "calendar_type"),
            trade=str(row.get("trade", "杂工")),
            parent_id=int(row["parent_id"]) if row.get("parent_id") is not None else None,
            resources=tuple(Resource.from_mapping(item) for item in (row.get("resources") or ())),
            event_status=_enum(EventStatus, row.get("event_status", "derived"), "event_status"),
            event_time=str(row["event_time"]) if row.get("event_time") else None,
            attributes={key: value for key, value in row.items() if key not in known},
        )

    @property
    def duration_minutes(self) -> int:
        return int(self.duration.total_seconds() // 60)

    @property
    def is_milestone(self) -> bool:
        return self.type is ActivityType.MILESTONE

    @property
    def uses_elapsed_time(self) -> bool:
        return self.calendar_type is CalendarType.ELAPSED

    def _legacy_values(self) -> dict[str, Any]:
        days = self.duration_minutes / (60 * WorkCalendar.HOURS_PER_DAY)
        return {
            **self.attributes, "id": self.id, "name": self.name,
            "duration": days, "duration_days": days,
            "duration_minutes": self.duration_minutes, "task_type": self.type,
            "calendar_type": self.calendar_type, "trade": self.trade,
            "parent_id": self.parent_id, "resources": self.resources,
            "event_status": self.event_status, "event_time": self.event_time,
        }


@dataclass(frozen=True, slots=True)
class Dependency(_LegacyMapping):
    predecessor: int
    successor: int
    relation: RelationType = RelationType.FS
    lag: timedelta = timedelta(0)
    lag_calendar: LagCalendar = LagCalendar.CALENDAR_DAY
    attributes: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "relation", _enum(RelationType, self.relation, "relation_type")
        )
        object.__setattr__(
            self,
            "lag_calendar",
            _enum(LagCalendar, self.lag_calendar, "lag_unit"),
        )
        if not isinstance(self.lag, timedelta):
            raise TypeError("Dependency lag must be a timedelta")

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any] | Dependency) -> Dependency:
        if isinstance(row, cls):
            return row
        known = {"predecessor_id", "successor_id", "relation_type", "lag_days", "lag_unit"}
        return cls(
            predecessor=int(row["predecessor_id"]), successor=int(row["successor_id"]),
            relation=_enum(RelationType, row.get("relation_type", "FS"), "relation_type"),
            lag=timedelta(days=float(row.get("lag_days", 0))),
            lag_calendar=_enum(LagCalendar, row.get("lag_unit", "calendar_day"), "lag_unit"),
            attributes={key: value for key, value in row.items() if key not in known},
        )

    def _legacy_values(self) -> dict[str, Any]:
        return {
            **self.attributes, "predecessor_id": self.predecessor,
            "successor_id": self.successor, "relation_type": self.relation,
            "lag_days": self.lag.total_seconds() / 86400,
            "lag_unit": self.lag_calendar,
        }


@dataclass(frozen=True, slots=True)
class ScheduleModel:
    """Validated project data required to calculate a schedule."""

    tasks: Sequence[Activity | Mapping[str, Any]]
    dependencies: Sequence[Dependency | Mapping[str, Any]]
    project_start: str
    calendar: WorkCalendar | None = None
    available_resources: Sequence[Resource | Mapping[str, Any]] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tasks", tuple(Activity.from_mapping(item) for item in self.tasks))
        object.__setattr__(self, "dependencies", tuple(Dependency.from_mapping(item) for item in self.dependencies))
        if self.available_resources is not None:
            object.__setattr__(self, "available_resources", tuple(Resource.from_mapping(item) for item in self.available_resources))
