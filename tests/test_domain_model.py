from datetime import timedelta
import unittest

from construction_manager.domain import (
    Activity,
    ActivityType,
    Dependency,
    EventStatus,
    LagCalendar,
    RelationType,
    ResourceType,
    ScheduleModel,
)
from construction_manager.scheduler import ScheduleError, calculate_schedule_details


class DomainModelTests(unittest.TestCase):
    def test_schedule_model_parses_legacy_rows_into_typed_values(self) -> None:
        model = ScheduleModel(
            tasks=[{
                "id": 7,
                "name": "运输",
                "duration_minutes": 90,
                "task_type": "logistics",
                "calendar_type": "elapsed",
                "resources": [{"id": 3, "resource_type": "access"}],
            }],
            dependencies=[],
            project_start="2026-09-07",
        )

        activity = model.tasks[0]
        self.assertIsInstance(activity, Activity)
        self.assertIs(activity.type, ActivityType.LOGISTICS)
        self.assertEqual(activity.duration, timedelta(minutes=90))
        self.assertIs(activity.resources[0].type, ResourceType.ACCESS)

    def test_dependency_has_typed_relation_lag_and_calendar(self) -> None:
        dependency = Dependency.from_mapping({
            "predecessor_id": 1,
            "successor_id": 2,
            "relation_type": "SS",
            "lag_days": 1.5,
            "lag_unit": "workday",
        })

        self.assertIs(dependency.relation, RelationType.SS)
        self.assertEqual(dependency.lag, timedelta(days=1.5))
        self.assertIs(dependency.lag_calendar, LagCalendar.WORKDAY)

    def test_typo_in_activity_type_fails_at_scheduler_boundary(self) -> None:
        with self.assertRaisesRegex(ScheduleError, "task_type"):
            calculate_schedule_details(
                [{"id": 1, "duration_minutes": 60, "task_type": "logistic"}],
                [],
                "2026-09-07",
            )

    def test_milestone_invariant_is_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "Milestone duration"):
            Activity(
                id=1,
                duration=timedelta(minutes=1),
                type=ActivityType.MILESTONE,
                event_status=EventStatus.PENDING,
            )


if __name__ == "__main__":
    unittest.main()
