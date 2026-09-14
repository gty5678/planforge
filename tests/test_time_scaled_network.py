from __future__ import annotations

import unittest

from construction_manager.network_data import (
    assign_time_lanes,
    collapsed_network_data,
    leaf_scheduled_tasks,
)


class TimeScaledNetworkDataTests(unittest.TestCase):
    def test_only_scheduled_leaf_activities_are_drawn(self) -> None:
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "name": "主体工程",
                "duration": 2,
                "calculated_start": "2026-09-07T08:00:00",
                "calculated_finish": "2026-09-08T17:00:00",
            },
            {
                "id": 2,
                "parent_id": 1,
                "name": "钢筋",
                "duration": 1,
                "calculated_start": "2026-09-07T08:00:00",
                "calculated_finish": "2026-09-07T17:00:00",
            },
            {
                "id": 3,
                "parent_id": 1,
                "name": "混凝土",
                "duration": 1,
                "calculated_start": "2026-09-08T08:00:00",
                "calculated_finish": "2026-09-08T17:00:00",
            },
            {
                "id": 4,
                "parent_id": None,
                "name": "待排任务",
                "duration": 1,
                "calculated_start": None,
                "calculated_finish": None,
            },
        ]

        result = leaf_scheduled_tasks(tasks)

        self.assertEqual([task["id"] for task in result], [2, 3])

    def test_parallel_activities_use_separate_lanes_and_later_work_reuses_one(self) -> None:
        tasks = [
            {
                "id": 1,
                "calculated_start": "2026-09-07T08:00:00",
                "calculated_finish": "2026-09-08T17:00:00",
            },
            {
                "id": 2,
                "calculated_start": "2026-09-07T13:00:00",
                "calculated_finish": "2026-09-09T17:00:00",
            },
            {
                "id": 3,
                "calculated_start": "2026-09-11T08:00:00",
                "calculated_finish": "2026-09-11T17:00:00",
            },
        ]

        lanes = assign_time_lanes(tasks)

        self.assertNotEqual(lanes[1], lanes[2])
        self.assertEqual(lanes[1], lanes[3])

    def test_segment_children_collapse_to_summary_but_plain_leaf_stays_visible(self) -> None:
        tasks = [
            self._task(100, "楼层计划", None, "2026-09-07", "2026-09-12"),
            self._task(10, "砌筑", 100, "2026-09-07", "2026-09-10"),
            self._task(11, "砌筑（1段）", 10, "2026-09-07", "2026-09-08"),
            self._task(12, "砌筑（2段）", 10, "2026-09-09", "2026-09-10"),
            self._task(20, "验收", 100, "2026-09-11", "2026-09-12"),
        ]

        display_tasks, _, source_to_display = collapsed_network_data(tasks, [])

        self.assertEqual({task["id"] for task in display_tasks}, {10, 20})
        self.assertEqual(source_to_display[11], 10)
        self.assertEqual(source_to_display[12], 10)
        self.assertEqual(source_to_display[20], 20)

    def test_segment_relations_are_collapsed_deduplicated_and_never_self_linked(self) -> None:
        tasks = [
            self._task(10, "砌筑", None, "2026-09-07", "2026-09-10"),
            self._task(11, "砌筑（1段）", 10, "2026-09-07", "2026-09-08"),
            self._task(12, "砌筑（2段）", 10, "2026-09-09", "2026-09-10"),
            self._task(20, "抹灰", None, "2026-09-11", "2026-09-14"),
            self._task(21, "抹灰（1段）", 20, "2026-09-11", "2026-09-12"),
            self._task(22, "抹灰（2段）", 20, "2026-09-13", "2026-09-14"),
        ]
        dependencies = [
            self._relation(11, 21),
            self._relation(12, 22),
            self._relation(11, 12),
        ]

        _, display_dependencies, _ = collapsed_network_data(tasks, dependencies)

        self.assertEqual(len(display_dependencies), 1)
        self.assertEqual(
            (
                display_dependencies[0]["predecessor_id"],
                display_dependencies[0]["successor_id"],
                display_dependencies[0]["relation_type"],
            ),
            (10, 20, "FS"),
        )
        self.assertEqual(display_dependencies[0]["collapsed_count"], 2)

    @staticmethod
    def _task(
        task_id: int,
        name: str,
        parent_id: int | None,
        start: str,
        finish: str,
    ) -> dict:
        return {
            "id": task_id,
            "name": name,
            "parent_id": parent_id,
            "duration": 1,
            "calculated_start": f"{start}T08:00:00",
            "calculated_finish": f"{finish}T17:00:00",
        }

    @staticmethod
    def _relation(predecessor_id: int, successor_id: int) -> dict:
        return {
            "predecessor_id": predecessor_id,
            "successor_id": successor_id,
            "relation_type": "FS",
            "lag_days": 0,
            "lag_unit": "calendar_day",
        }


if __name__ == "__main__":
    unittest.main()
