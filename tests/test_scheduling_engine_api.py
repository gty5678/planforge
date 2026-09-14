import unittest

from construction_manager.domain import ScheduleModel
from construction_manager.scheduler import calculate_schedule_details
from construction_manager.scheduling import ScheduleOptions, SchedulingEngine


class SchedulingEngineApiTests(unittest.TestCase):
    def test_engine_result_preserves_legacy_schedule_contract(self) -> None:
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "constraint_start": None,
            }
        ]
        legacy_dates, legacy_reasons = calculate_schedule_details(
            tasks, [], "2026-09-07"
        )

        result = SchedulingEngine.calculate(
            ScheduleModel(tasks, [], "2026-09-07"), ScheduleOptions()
        )

        self.assertEqual(result.dates, legacy_dates)
        self.assertEqual(result.reasons, legacy_reasons)
        self.assertEqual(result.crew_assignments, {})


if __name__ == "__main__":
    unittest.main()
