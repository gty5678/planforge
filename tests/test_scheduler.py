import tempfile
import unittest
import sqlite3
from datetime import date, datetime, time
from pathlib import Path

from construction_manager.database import Database
from construction_manager.scheduler import (
    ScheduleError,
    calculate_schedule,
    calculate_schedule_details,
    crew_actual_work_intervals,
    critical_path,
    schedule_sort_key,
)
from construction_manager.work_calendar import WorkCalendar


class SchedulerTests(unittest.TestCase):
    def test_critical_path_follows_only_driving_relations(self) -> None:
        tasks = [
            {"id": 10, "parent_id": None, "duration": 1, "constraint_start": None},
            {"id": 1, "parent_id": 10, "duration": 3, "constraint_start": None},
            {"id": 2, "parent_id": 10, "duration": 2, "constraint_start": None},
            {"id": 3, "parent_id": None, "duration": 1, "constraint_start": None},
        ]
        dependencies = [
            {"predecessor_id": 1, "successor_id": 2, "relation_type": "FS", "lag_days": 0}
        ]
        schedule = calculate_schedule(tasks, dependencies, "2026-09-07")
        calculated_tasks = [
            task
            | {
                "calculated_start": schedule[task["id"]][0],
                "calculated_finish": schedule[task["id"]][1],
            }
            for task in tasks
        ]
        task_ids, relations = critical_path(calculated_tasks, dependencies)
        self.assertEqual(task_ids, {1, 2, 10})
        self.assertEqual(relations, {(1, 2, "FS")})

    def test_critical_path_includes_driving_exclusive_resource_chain(self) -> None:
        workface = {
            "id": 20,
            "name": "一层A区",
            "resource_type": "workface",
            "capacity": 1,
            "enabled": 1,
        }
        tasks = [
            {"id": 10, "parent_id": None, "duration": 2},
            {
                "id": 1,
                "parent_id": 10,
                "duration": 1,
                "resources": [workface],
                "calculated_start": "2026-09-07T08:00",
                "calculated_finish": "2026-09-07T17:00",
            },
            {
                "id": 2,
                "parent_id": 10,
                "duration": 1,
                "resources": [workface],
                "calculated_start": "2026-09-08T08:00",
                "calculated_finish": "2026-09-08T17:00",
            },
            {
                "id": 3,
                "parent_id": None,
                "duration": 1,
                "resources": [],
                "calculated_start": "2026-09-07T08:00",
                "calculated_finish": "2026-09-07T17:00",
            },
        ]
        driving_relations: set[tuple[int, int, str]] = set()
        task_ids, relations = critical_path(
            tasks, [], driving_relations_out=driving_relations
        )
        self.assertEqual(task_ids, {1, 2, 10})
        self.assertEqual(relations, set())
        self.assertEqual(driving_relations, {(1, 2, "resource")})

    def test_critical_path_excludes_resource_predecessor_with_real_float(self) -> None:
        workface = {
            "id": 20,
            "name": "一层A区",
            "resource_type": "workface",
            "capacity": 1,
            "enabled": 1,
        }
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "resources": [workface],
                "calculated_start": "2026-09-07T08:00",
                "calculated_finish": "2026-09-07T17:00",
            },
            {
                "id": 2,
                "parent_id": None,
                "duration": 1,
                "resources": [workface],
                "calculated_start": "2026-09-09T08:00",
                "calculated_finish": "2026-09-09T17:00",
            },
        ]
        driving_relations: set[tuple[int, int, str]] = set()
        task_ids, _ = critical_path(
            tasks, [], driving_relations_out=driving_relations
        )
        self.assertEqual(task_ids, {2})
        self.assertEqual(driving_relations, set())

    def test_critical_path_reports_resource_activation_as_driving_relation(self) -> None:
        activation = {
            "id": 1,
            "name": "作业面移交",
            "parent_id": None,
            "duration": 0,
            "task_type": "milestone",
            "calculated_start": "2026-09-07T08:00",
            "calculated_finish": "2026-09-07T08:00",
        }
        workface = {
            "id": 20,
            "name": "一层A区",
            "resource_type": "workface",
            "capacity": 1,
            "enabled": 1,
            "activation_event_id": 1,
        }
        task = {
            "id": 2,
            "name": "墙体砌筑",
            "parent_id": None,
            "duration": 1,
            "resources": [workface],
            "calculated_start": "2026-09-07T08:00",
            "calculated_finish": "2026-09-07T17:00",
        }
        driving_relations: set[tuple[int, int, str]] = set()

        task_ids, relations = critical_path(
            [activation, task],
            [],
            driving_relations_out=driving_relations,
        )

        self.assertEqual(task_ids, {1, 2})
        self.assertEqual(relations, set())
        self.assertEqual(driving_relations, {(1, 2, "availability")})

    def test_constraint_can_break_critical_predecessor_chain(self) -> None:
        tasks = [
            {"id": 1, "parent_id": None, "duration": 1, "constraint_start": None},
            {"id": 2, "parent_id": None, "duration": 1, "constraint_start": "2026-09-21"},
        ]
        dependencies = [
            {"predecessor_id": 1, "successor_id": 2, "relation_type": "FS", "lag_days": 0}
        ]
        schedule = calculate_schedule(tasks, dependencies, "2026-09-07")
        calculated_tasks = [
            task
            | {
                "calculated_start": schedule[task["id"]][0],
                "calculated_finish": schedule[task["id"]][1],
            }
            for task in tasks
        ]
        task_ids, relations = critical_path(calculated_tasks, dependencies)
        self.assertEqual(task_ids, {2})
        self.assertEqual(relations, set())

    def setUp(self) -> None:
        self.tasks = [
            {"id": 1, "parent_id": None, "duration": 3, "constraint_start": None},
            {"id": 2, "parent_id": None, "duration": 2, "constraint_start": None},
            {"id": 3, "parent_id": None, "duration": 4, "constraint_start": None},
            {"id": 4, "parent_id": None, "duration": 1, "constraint_start": None},
            {"id": 5, "parent_id": None, "duration": 2, "constraint_start": None},
        ]
        self.dependencies = [
            {"predecessor_id": 1, "successor_id": 2, "relation_type": "FS"},
            {"predecessor_id": 1, "successor_id": 3, "relation_type": "SS"},
            {"predecessor_id": 3, "successor_id": 4, "relation_type": "FF"},
            {"predecessor_id": 4, "successor_id": 5, "relation_type": "SF"},
        ]

    def test_four_relationship_types(self) -> None:
        result = calculate_schedule(self.tasks, self.dependencies, "2026-01-01")
        self.assertEqual(result[1], ("2026-01-01T08:00", "2026-01-05T17:00"))
        self.assertEqual(result[2], ("2026-01-06T08:00", "2026-01-07T17:00"))
        self.assertEqual(result[3], ("2026-01-01T08:00", "2026-01-06T17:00"))
        self.assertEqual(result[4], ("2026-01-06T08:00", "2026-01-06T17:00"))
        self.assertEqual(result[5], ("2026-01-02T08:00", "2026-01-05T17:00"))

    def test_cycle_is_rejected(self) -> None:
        cyclic = self.dependencies + [
            {"predecessor_id": 2, "successor_id": 1, "relation_type": "FS"}
        ]
        with self.assertRaises(ScheduleError):
            calculate_schedule(self.tasks, cyclic, "2026-01-01")

    def test_summary_dates_cover_children(self) -> None:
        tasks = [
            {"id": 10, "parent_id": None, "duration": 1, "constraint_start": None},
            {"id": 11, "parent_id": 10, "duration": 2, "constraint_start": None},
            {"id": 12, "parent_id": 10, "duration": 3, "constraint_start": "2026-01-05"},
        ]
        result = calculate_schedule(tasks, [], "2026-01-01")
        self.assertEqual(result[10], ("2026-01-01T08:00", "2026-01-07T17:00"))

    def test_relationship_lag_counts_calendar_days(self) -> None:
        tasks = [
            {"id": 1, "parent_id": None, "duration": 1, "constraint_start": None},
            {"id": 2, "parent_id": None, "duration": 1, "constraint_start": None},
        ]
        dependencies = [
            {
                "predecessor_id": 1,
                "successor_id": 2,
                "relation_type": "FS",
                "lag_days": 2,
            }
        ]
        result = calculate_schedule(tasks, dependencies, "2026-09-04")
        self.assertEqual(result[1], ("2026-09-04T08:00", "2026-09-04T17:00"))
        self.assertEqual(result[2], ("2026-09-07T08:00", "2026-09-07T17:00"))

    def test_flow_lag_counts_calendar_days_including_weekends(self) -> None:
        tasks = [
            {"id": 1, "parent_id": None, "duration": 1, "constraint_start": None},
            {"id": 2, "parent_id": None, "duration": 1, "constraint_start": None},
        ]
        dependencies = [
            {
                "predecessor_id": 1,
                "successor_id": 2,
                "relation_type": "FS",
                "lag_days": 1,
                "lag_unit": "calendar_day",
            }
        ]
        result = calculate_schedule(tasks, dependencies, "2026-09-04")
        self.assertEqual(result[1], ("2026-09-04T08:00", "2026-09-04T17:00"))
        self.assertEqual(result[2], ("2026-09-07T08:00", "2026-09-07T17:00"))

    def test_lag_respects_calendar_exceptions(self) -> None:
        tasks = [
            {"id": 1, "parent_id": None, "duration": 1, "constraint_start": None},
            {"id": 2, "parent_id": None, "duration": 1, "constraint_start": None},
        ]
        dependencies = [
            {
                "predecessor_id": 1,
                "successor_id": 2,
                "relation_type": "FS",
                "lag_days": 2,
            }
        ]
        calendar = WorkCalendar({"2026-09-07": False})
        result = calculate_schedule(tasks, dependencies, "2026-09-04", calendar)
        self.assertEqual(result[2], ("2026-09-08T08:00", "2026-09-08T17:00"))

    def test_fractional_tasks_continue_within_one_day(self) -> None:
        tasks = [
            {"id": 1, "parent_id": None, "duration": 0.5, "constraint_start": None},
            {"id": 2, "parent_id": None, "duration": 0.25, "constraint_start": None},
        ]
        dependencies = [{"predecessor_id": 1, "successor_id": 2, "relation_type": "FS"}]
        result = calculate_schedule(tasks, dependencies, "2026-09-07")
        self.assertEqual(result[1], ("2026-09-07T08:00", "2026-09-07T12:00"))
        self.assertEqual(result[2], ("2026-09-07T13:00", "2026-09-07T15:00"))

    def test_shared_resource_serializes_independent_activities(self) -> None:
        crew = {
            "id": 10,
            "name": "泥瓦一班",
            "capacity": 1,
            "enabled": 1,
            "demand_amount": 1,
        }
        tasks = [
            {"id": 1, "parent_id": None, "duration": 1, "resources": [crew]},
            {"id": 2, "parent_id": None, "duration": 1, "resources": [crew]},
        ]
        result, reasons = calculate_schedule_details(tasks, [], "2026-09-07")
        self.assertEqual(result[1], ("2026-09-07T08:00", "2026-09-07T17:00"))
        self.assertEqual(result[2], ("2026-09-08T08:00", "2026-09-08T17:00"))
        self.assertIn("泥瓦一班", reasons[2])

    def test_recalculation_preserves_previous_exclusive_resource_order(self) -> None:
        crew = {
            "id": 10,
            "name": "水电一班",
            "resource_type": "crew",
            "capacity": 1,
            "enabled": 1,
            "skills": ["水电工"],
        }
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 2,
                "crew_required": True,
                "trade": "水电工",
                "resources": [],
                "calculated_start": "2026-09-08T08:00",
                "calculated_finish": "2026-09-08T17:00",
                "calculated_crew_id": 10,
            },
            {
                "id": 2,
                "parent_id": None,
                "duration": 1,
                "crew_required": True,
                "trade": "水电工",
                "resources": [],
                "calculated_start": "2026-09-07T08:00",
                "calculated_finish": "2026-09-07T17:00",
                "calculated_crew_id": 10,
            },
        ]
        assignments = {}
        result, reasons = calculate_schedule_details(
            tasks,
            [],
            "2026-09-07",
            available_resources=[crew],
            crew_assignments_out=assignments,
        )
        self.assertEqual(result[2], ("2026-09-07T08:00", "2026-09-07T17:00"))
        self.assertEqual(result[1], ("2026-09-08T08:00", "2026-09-09T17:00"))
        self.assertEqual(assignments, {1: 10, 2: 10})
        self.assertIn("沿用上次资源排程顺序", reasons[1])

    def test_stable_resource_order_never_overrides_process_dependency(self) -> None:
        resource = {
            "id": 10,
            "name": "共享作业面",
            "resource_type": "workface",
            "capacity": 1,
            "enabled": 1,
        }
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "resources": [resource],
                "calculated_start": "2026-09-08T08:00",
                "calculated_finish": "2026-09-08T17:00",
            },
            {
                "id": 2,
                "parent_id": None,
                "duration": 1,
                "resources": [resource],
                "calculated_start": "2026-09-07T08:00",
                "calculated_finish": "2026-09-07T17:00",
            },
        ]
        dependencies = [
            {"predecessor_id": 1, "successor_id": 2, "relation_type": "FS"}
        ]
        result = calculate_schedule(tasks, dependencies, "2026-09-07")
        self.assertEqual(result[1][0], "2026-09-07T08:00")
        self.assertEqual(result[2][0], "2026-09-08T08:00")

    def test_earliest_goal_can_rebuild_previous_resource_order(self) -> None:
        crew = {
            "id": 10,
            "name": "水电一班",
            "resource_type": "crew",
            "capacity": 1,
            "enabled": 1,
            "skills": ["水电工"],
        }
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "crew_required": True,
                "trade": "水电工",
                "resources": [],
                "calculated_start": "2026-09-08T08:00",
                "calculated_finish": "2026-09-08T17:00",
                "calculated_crew_id": 10,
            },
            {
                "id": 2,
                "parent_id": None,
                "duration": 1,
                "crew_required": True,
                "trade": "水电工",
                "resources": [],
                "calculated_start": "2026-09-07T08:00",
                "calculated_finish": "2026-09-07T17:00",
                "calculated_crew_id": 10,
            },
        ]
        result, _ = calculate_schedule_details(
            tasks,
            [],
            "2026-09-07",
            available_resources=[crew],
            optimization_goal="earliest",
        )
        self.assertEqual(result[1][0], "2026-09-07T08:00")
        self.assertEqual(result[2][0], "2026-09-08T08:00")

    def test_continuity_goal_can_wait_for_predecessor_crew(self) -> None:
        crews = [
            {
                "id": 10,
                "name": "综合一班",
                "resource_type": "crew",
                "capacity": 1,
                "enabled": 1,
                "skills": ["泥瓦工", "专项工"],
            },
            {
                "id": 11,
                "name": "泥瓦二班",
                "resource_type": "crew",
                "capacity": 1,
                "enabled": 1,
                "skills": ["泥瓦工"],
            },
        ]
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "crew_required": True,
                "trade": "泥瓦工",
                "resources": [],
                "priority": 100,
            },
            {
                "id": 2,
                "parent_id": None,
                "duration": 2,
                "crew_required": True,
                "trade": "专项工",
                "resources": [],
                "priority": 90,
            },
            {
                "id": 3,
                "parent_id": None,
                "duration": 1,
                "crew_required": True,
                "trade": "泥瓦工",
                "resources": [],
            },
        ]
        dependencies = [
            {"predecessor_id": 1, "successor_id": 3, "relation_type": "FS"}
        ]
        assignments = {}
        result, _ = calculate_schedule_details(
            tasks,
            dependencies,
            "2026-09-07",
            available_resources=crews,
            crew_assignments_out=assignments,
            optimization_goal="continuity",
        )
        self.assertEqual(assignments[1], 10)
        self.assertEqual(assignments[3], 10)
        self.assertEqual(result[3][0], "2026-09-10T08:00")

    def test_continuity_goal_finishes_one_task_group_before_switching(self) -> None:
        crew = {
            "id": 10,
            "name": "安装一班",
            "resource_type": "crew",
            "capacity": 1,
            "enabled": 1,
            "skills": ["安装工"],
        }
        tasks = [
            {"id": 100, "parent_id": None, "duration": 2, "sort_order": 1},
            {"id": 200, "parent_id": None, "duration": 2, "sort_order": 2},
            {
                "id": 1, "parent_id": 100, "duration": 1, "sort_order": 10,
                "crew_required": True, "trade": "安装工", "resources": [],
            },
            {
                "id": 3, "parent_id": 200, "duration": 1, "sort_order": 20,
                "crew_required": True, "trade": "安装工", "resources": [],
            },
            {
                "id": 2, "parent_id": 100, "duration": 1, "sort_order": 30,
                "crew_required": True, "trade": "安装工", "resources": [],
            },
            {
                "id": 4, "parent_id": 200, "duration": 1, "sort_order": 40,
                "crew_required": True, "trade": "安装工", "resources": [],
            },
        ]
        result, _ = calculate_schedule_details(
            tasks, [], "2026-09-07", available_resources=[crew],
            optimization_goal="continuity",
        )
        self.assertEqual(result[1][0], "2026-09-07T08:00")
        self.assertEqual(result[2][0], "2026-09-08T08:00")
        self.assertEqual(result[3][0], "2026-09-09T08:00")
        self.assertEqual(result[4][0], "2026-09-10T08:00")

    def test_continuity_goal_switches_group_when_project_finish_would_slip(self) -> None:
        crew = {
            "id": 10,
            "name": "安装一班",
            "resource_type": "crew",
            "capacity": 1,
            "enabled": 1,
            "skills": ["安装工"],
        }
        tasks = [
            {"id": 100, "parent_id": None, "duration": 2, "sort_order": 1},
            {"id": 200, "parent_id": None, "duration": 1, "sort_order": 2},
            {
                "id": 1, "parent_id": 100, "duration": 1, "sort_order": 10,
                "crew_required": True, "trade": "安装工", "resources": [],
            },
            {
                "id": 3, "parent_id": 200, "duration": 1, "sort_order": 20,
                "crew_required": True, "trade": "安装工", "resources": [],
            },
            {
                "id": 2, "parent_id": 100, "duration": 1, "sort_order": 30,
                "crew_required": True, "trade": "安装工", "resources": [],
            },
            {
                "id": 4, "parent_id": None, "duration": 5, "sort_order": 40,
                "crew_required": False, "resources": [],
            },
        ]
        dependencies = [
            {"predecessor_id": 3, "successor_id": 4, "relation_type": "FS"}
        ]
        result, _ = calculate_schedule_details(
            tasks, dependencies, "2026-09-07", available_resources=[crew],
            optimization_goal="continuity",
        )
        self.assertEqual(result[3][0], "2026-09-08T08:00")
        self.assertEqual(result[2][0], "2026-09-09T08:00")

    def test_crew_daily_hour_limit_extends_task(self) -> None:
        crew = {
            "id": 10,
            "name": "水电一班",
            "resource_type": "crew",
            "capacity": 1,
            "enabled": 1,
            "skills": ["水电工"],
        }
        task = {
            "id": 1,
            "parent_id": None,
            "duration": 1,
            "crew_required": True,
            "trade": "水电工",
            "resources": [],
        }
        result, _ = calculate_schedule_details(
            [task],
            [],
            "2026-09-07",
            available_resources=[crew],
            crew_max_daily_hours=4,
        )
        self.assertEqual(result[1], ("2026-09-07T08:00", "2026-09-08T12:00"))

    def test_crew_consecutive_day_limit_inserts_rest_day(self) -> None:
        crew = {
            "id": 10,
            "name": "水电一班",
            "resource_type": "crew",
            "capacity": 1,
            "enabled": 1,
            "skills": ["水电工"],
        }
        task = {
            "id": 1,
            "parent_id": None,
            "duration": 4,
            "crew_required": True,
            "trade": "水电工",
            "resources": [],
        }
        result, reasons = calculate_schedule_details(
            [task],
            [],
            "2026-09-07",
            calendar=WorkCalendar(weekend_working=True),
            available_resources=[crew],
            crew_max_consecutive_days=2,
        )
        self.assertEqual(result[1][1], "2026-09-11T17:00")
        self.assertIn("连续工作上限", reasons[1])

    def test_resource_overload_mode_keeps_parallel_tasks_and_warns(self) -> None:
        shared = {
            "id": 10,
            "name": "共享作业面",
            "resource_type": "workface",
            "capacity": 1,
            "enabled": 1,
        }
        tasks = [
            {"id": task_id, "parent_id": None, "duration": 1, "resources": [shared]}
            for task_id in (1, 2)
        ]
        result, reasons = calculate_schedule_details(
            tasks, [], "2026-09-07", resource_leveling_mode="allow_overload"
        )
        self.assertEqual(result[1], result[2])
        self.assertIn("允许资源超负荷：共享作业面", reasons[2])

    def test_status_date_freezes_prior_work_and_floors_remaining_work(self) -> None:
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "resources": [],
                "calculated_start": "2026-09-07T08:00",
                "calculated_finish": "2026-09-07T17:00",
            },
            {"id": 2, "parent_id": None, "duration": 1, "resources": []},
        ]
        result, reasons = calculate_schedule_details(
            tasks, [], "2026-09-01", status_date="2026-09-08"
        )
        self.assertEqual(result[1], ("2026-09-07T08:00", "2026-09-07T17:00"))
        self.assertEqual(result[2][0], "2026-09-08T08:00")
        self.assertEqual(reasons[1], "排程基准日前任务保持原时间")

    def test_crew_required_task_waits_when_no_matching_crew_exists(self) -> None:
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "crew_required": True,
                "resources": [],
            },
            {
                "id": 2,
                "parent_id": None,
                "duration": 1,
                "crew_required": False,
                "resources": [],
            },
        ]
        dependencies = [
            {"predecessor_id": 1, "successor_id": 2, "relation_type": "FS"}
        ]
        result, reasons = calculate_schedule_details(
            tasks, dependencies, "2026-09-07"
        )
        self.assertEqual(result, {})
        self.assertEqual(reasons[1], "没有具备“杂工”能力的可用班组，暂不排程")
        self.assertEqual(reasons[2], "前置任务尚未具备排程条件")

    def test_pending_event_blocks_successor(self) -> None:
        tasks = [
            {
                "id": 1,
                "name": "图纸批准",
                "parent_id": None,
                "duration": 0,
                "task_type": "milestone",
                "event_status": "pending",
            },
            {"id": 2, "name": "现场放样", "parent_id": None, "duration": 1},
        ]
        dependencies = [
            {
                "predecessor_id": 1,
                "successor_id": 2,
                "relation_type": "FS",
                "constraint_category": "drawing",
            }
        ]
        result, reasons = calculate_schedule_details(tasks, dependencies, "2026-09-07")
        self.assertEqual(result, {})
        self.assertEqual(reasons[1], "外部事件尚未发生")
        self.assertIn("图纸批准", reasons[2])

    def test_planned_event_gates_working_task(self) -> None:
        tasks = [
            {
                "id": 1,
                "name": "材料预计到场",
                "parent_id": None,
                "duration": 0,
                "task_type": "milestone",
                "event_status": "planned",
                "event_time": "2026-09-12T10:30",
            },
            {"id": 2, "name": "材料搬运", "parent_id": None, "duration": 1},
        ]
        dependencies = [
            {
                "predecessor_id": 1,
                "successor_id": 2,
                "relation_type": "FS",
                "constraint_category": "material",
            }
        ]
        result, reasons = calculate_schedule_details(tasks, dependencies, "2026-09-07")
        self.assertEqual(result[1], ("2026-09-12T10:30", "2026-09-12T10:30"))
        self.assertEqual(result[2], ("2026-09-14T08:00", "2026-09-14T17:00"))
        self.assertEqual(reasons[2], "由材料约束控制")

    def test_scheduler_automatically_assigns_matching_crew(self) -> None:
        crew = {
            "id": 10,
            "name": "泥瓦一班",
            "resource_type": "crew",
            "capacity": 1,
            "enabled": 1,
            "skills": ["泥瓦工"],
        }
        task = {
            "id": 1,
            "parent_id": None,
            "duration": 1,
            "crew_required": True,
            "trade": "泥瓦工",
            "resources": [],
        }
        assignments = {}
        result, reasons = calculate_schedule_details(
            [task],
            [],
            "2026-09-07",
            available_resources=[crew],
            crew_assignments_out=assignments,
        )
        self.assertEqual(result[1], ("2026-09-07T08:00", "2026-09-07T17:00"))
        self.assertEqual(assignments, {1: 10})
        self.assertEqual(reasons[1], "系统分配班组：泥瓦一班")

    def test_crew_calendar_day_off_delays_assigned_task(self) -> None:
        crew = {
            "id": 10,
            "name": "泥瓦一班",
            "resource_type": "crew",
            "capacity": 1,
            "enabled": 1,
            "skills": ["泥瓦工"],
            "availability_exceptions": {"2026-09-07": False},
        }
        task = {
            "id": 1,
            "parent_id": None,
            "duration": 1,
            "crew_required": True,
            "trade": "泥瓦工",
            "resources": [],
        }
        assignments = {}
        result, reasons = calculate_schedule_details(
            [task],
            [],
            "2026-09-07",
            available_resources=[crew],
            crew_assignments_out=assignments,
        )
        self.assertEqual(result[1], ("2026-09-08T08:00", "2026-09-08T17:00"))
        self.assertEqual(assignments, {1: 10})
        self.assertIn("受资源日历影响：泥瓦一班", reasons[1])

    def test_actual_crew_work_intervals_follow_crew_calendar(self) -> None:
        crew = {
            "id": 10,
            "name": "泥瓦一班",
            "resource_type": "crew",
            "capacity": 1,
            "enabled": 1,
            "availability_exceptions": {"2026-09-08": False},
        }
        task = {
            "id": 1,
            "name": "砌墙",
            "parent_id": None,
            "duration": 2,
            "task_type": "work",
            "calendar_type": "working",
            "resources": [],
            "assigned_crew": crew,
            "calculated_start": "2026-09-07T08:00",
            "calculated_finish": "2026-09-09T17:00",
        }
        rows = crew_actual_work_intervals([task], crew, WorkCalendar())
        self.assertEqual(
            [(row["start"].isoformat(), row["finish"].isoformat()) for row in rows],
            [
                ("2026-09-07T08:00:00", "2026-09-07T12:00:00"),
                ("2026-09-07T13:00:00", "2026-09-07T17:00:00"),
                ("2026-09-09T08:00:00", "2026-09-09T12:00:00"),
                ("2026-09-09T13:00:00", "2026-09-09T17:00:00"),
            ],
        )
        self.assertEqual(sum(row["hours"] for row in rows), 16)

    def test_path_segment_calendar_closure_delays_logistics_task(self) -> None:
        path = {
            "id": 20,
            "name": "东侧走廊",
            "resource_type": "access",
            "capacity": 1,
            "enabled": 1,
            "demand_amount": 1,
            "availability_exceptions": {"2026-09-07": False},
        }
        task = {
            "id": 1,
            "parent_id": None,
            "duration": 1,
            "duration_minutes": 480,
            "task_type": "logistics",
            "calendar_type": "working",
            "crew_required": False,
            "resources": [path],
        }
        result, reasons = calculate_schedule_details([task], [], "2026-09-07")
        self.assertEqual(result[1], ("2026-09-08T08:00", "2026-09-08T17:00"))
        self.assertIn("受资源日历影响：东侧走廊", reasons[1])

    def test_dynamic_workface_waits_for_activation_event(self) -> None:
        activation = {
            "id": 1,
            "name": "临时通道开放",
            "parent_id": None,
            "duration": 0,
            "task_type": "milestone",
            "event_status": "planned",
            "event_time": "2026-09-08T13:00",
        }
        path_workface = {
            "id": 20,
            "name": "新增作业面A",
            "resource_type": "access",
            "path_segment_id": 7,
            "is_workface": 1,
            "capacity": 1,
            "enabled": 1,
            "activation_event_id": 1,
        }
        work = {
            "id": 2,
            "name": "新增区域施工",
            "parent_id": None,
            "duration": 1,
            "task_type": "work",
            "calendar_type": "working",
            "resources": [path_workface],
            "spatial_occupancy_mode": "normal",
        }
        result, _ = calculate_schedule_details(
            [activation, work], [], "2026-09-07"
        )
        self.assertEqual(result[2][0], "2026-09-08T13:00")

        calculated = [
            task
            | {
                "calculated_start": result[task["id"]][0],
                "calculated_finish": result[task["id"]][1],
            }
            for task in (activation, work)
        ]
        critical_ids, _ = critical_path(calculated, [])
        self.assertEqual(critical_ids, {1, 2})

    def test_pending_workface_activation_blocks_task(self) -> None:
        activation = {
            "id": 1,
            "name": "移交新作业面",
            "parent_id": None,
            "duration": 0,
            "task_type": "milestone",
            "event_status": "pending",
        }
        work = {
            "id": 2,
            "name": "新作业面施工",
            "parent_id": None,
            "duration": 1,
            "resources": [
                {
                    "id": 20,
                    "name": "新作业面",
                    "resource_type": "access",
                    "path_segment_id": 7,
                    "capacity": 1,
                    "enabled": 1,
                    "activation_event_id": 1,
                }
            ],
        }
        result, reasons = calculate_schedule_details(
            [activation, work], [], "2026-09-07"
        )
        self.assertNotIn(2, result)
        self.assertIn("移交新作业面", reasons[2])

    def test_disabled_path_blocks_assigned_task(self) -> None:
        task = {
            "id": 1,
            "name": "通道内施工",
            "parent_id": None,
            "duration": 1,
            "resources": [
                {
                    "id": 20,
                    "name": "封闭通道",
                    "resource_type": "access",
                    "path_segment_id": 7,
                    "capacity": 1,
                    "enabled": 0,
                }
            ],
        }
        result, reasons = calculate_schedule_details([task], [], "2026-09-07")
        self.assertNotIn(1, result)
        self.assertIn("封闭通道", reasons[1])

    def test_construction_can_share_or_close_a_traffic_path(self) -> None:
        path = {
            "id": 20,
            "name": "施工兼通行面",
            "resource_type": "access",
            "path_segment_id": 7,
            "capacity": 1,
            "enabled": 1,
            "demand_amount": 1,
        }
        logistics = {
            "id": 2,
            "parent_id": None,
            "duration": 1,
            "task_type": "logistics",
            "resources": [path],
        }
        construction = {
            "id": 1,
            "parent_id": None,
            "duration": 1,
            "task_type": "work",
            "resources": [path],
            "spatial_occupancy_mode": "normal",
        }
        shared, _ = calculate_schedule_details(
            [construction, logistics], [], "2026-09-07"
        )
        self.assertEqual(shared[1], shared[2])

        construction["spatial_occupancy_mode"] = "close"
        closed, _ = calculate_schedule_details(
            [construction, logistics], [], "2026-09-07"
        )
        self.assertEqual(closed[2][0], "2026-09-08T08:00")

    def test_scheduler_prefers_crew_that_can_finish_before_day_off(self) -> None:
        crews = [
            {
                "id": 10,
                "name": "休假班组",
                "resource_type": "crew",
                "capacity": 1,
                "enabled": 1,
                "skills": ["泥瓦工"],
                "availability_exceptions": {"2026-09-08": False},
            },
            {
                "id": 11,
                "name": "正常班组",
                "resource_type": "crew",
                "capacity": 1,
                "enabled": 1,
                "skills": ["泥瓦工"],
                "availability_exceptions": {},
            },
        ]
        task = {
            "id": 1,
            "parent_id": None,
            "duration": 2,
            "crew_required": True,
            "trade": "泥瓦工",
            "resources": [],
        }
        assignments = {}
        result, _ = calculate_schedule_details(
            [task],
            [],
            "2026-09-07",
            available_resources=crews,
            crew_assignments_out=assignments,
        )
        self.assertEqual(assignments, {1: 11})
        self.assertEqual(result[1][1], "2026-09-08T17:00")

    def test_scheduler_spreads_parallel_tasks_across_matching_crews(self) -> None:
        crews = [
            {
                "id": crew_id,
                "name": name,
                "resource_type": "crew",
                "capacity": 1,
                "enabled": 1,
                "skills": ["泥瓦工"],
            }
            for crew_id, name in ((10, "泥瓦一班"), (11, "泥瓦二班"))
        ]
        tasks = [
            {
                "id": task_id,
                "parent_id": None,
                "duration": 1,
                "crew_required": True,
                "trade": "泥瓦工",
                "resources": [],
            }
            for task_id in (1, 2, 3)
        ]
        assignments = {}
        result, _ = calculate_schedule_details(
            tasks,
            [],
            "2026-09-07",
            available_resources=crews,
            crew_assignments_out=assignments,
        )
        self.assertEqual(result[1][0], result[2][0])
        self.assertEqual({assignments[1], assignments[2]}, {10, 11})
        self.assertEqual(result[3][0], "2026-09-08T08:00")

    def test_scheduler_keeps_crew_continuous_on_same_workface(self) -> None:
        crews = [
            {
                "id": crew_id,
                "name": name,
                "resource_type": "crew",
                "capacity": 1,
                "enabled": 1,
                "skills": ["泥瓦工"],
            }
            for crew_id, name in ((10, "泥瓦一班"), (11, "泥瓦二班"))
        ]
        workface = {
            "id": 20,
            "name": "3层A区",
            "resource_type": "workface",
            "capacity": 1,
            "enabled": 1,
        }
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "crew_required": True,
                "trade": "泥瓦工",
                "resources": [],
            },
            {
                "id": 2,
                "parent_id": None,
                "duration": 1,
                "crew_required": True,
                "trade": "泥瓦工",
                "resources": [workface],
            },
            {
                "id": 3,
                "parent_id": None,
                "duration": 1,
                "crew_required": True,
                "trade": "泥瓦工",
                "resources": [workface],
            },
        ]
        assignments = {}
        calculate_schedule_details(
            tasks,
            [],
            "2026-09-07",
            available_resources=crews + [workface],
            crew_assignments_out=assignments,
        )
        self.assertEqual(assignments[1], 10)
        self.assertEqual(assignments[2], 11)
        self.assertEqual(assignments[3], 11)

    def test_different_resources_can_run_in_parallel(self) -> None:
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "resources": [{"id": 10, "name": "一班", "capacity": 1, "enabled": 1}],
            },
            {
                "id": 2,
                "parent_id": None,
                "duration": 1,
                "resources": [{"id": 11, "name": "二班", "capacity": 1, "enabled": 1}],
            },
        ]
        result = calculate_schedule(tasks, [], "2026-09-07")
        self.assertEqual(result[1], result[2])

    def test_resource_capacity_allows_two_parallel_activities(self) -> None:
        shared = {"id": 10, "name": "共享作业面", "capacity": 2, "enabled": 1}
        tasks = [
            {"id": task_id, "parent_id": None, "duration": 1, "resources": [shared]}
            for task_id in (1, 2, 3)
        ]
        result = calculate_schedule(tasks, [], "2026-09-07")
        self.assertEqual(result[1], result[2])
        self.assertEqual(result[3][0], "2026-09-08T08:00")

    def test_elapsed_wait_can_start_after_working_finish(self) -> None:
        tasks = [
            {"id": 1, "parent_id": None, "duration": 1, "task_type": "work"},
            {
                "id": 2,
                "parent_id": None,
                "duration_minutes": 720,
                "task_type": "wait",
                "calendar_type": "elapsed",
            },
            {
                "id": 3,
                "parent_id": None,
                "duration_minutes": 0,
                "task_type": "milestone",
            },
        ]
        dependencies = [
            {"predecessor_id": 1, "successor_id": 2, "relation_type": "FS"},
            {"predecessor_id": 2, "successor_id": 3, "relation_type": "FS"},
        ]
        result = calculate_schedule(tasks, dependencies, "2026-09-07")
        self.assertEqual(result[2], ("2026-09-07T17:00", "2026-09-08T05:00"))
        self.assertEqual(result[3], ("2026-09-08T08:00", "2026-09-08T08:00"))

    def test_noisy_elapsed_task_is_forced_off_rest_days(self) -> None:
        noisy_task = {
            "id": 1,
            "parent_id": None,
            "duration_minutes": 960,
            "task_type": "inspection",
            "calendar_type": "elapsed",
            "has_noise": True,
        }
        quiet_task = noisy_task | {"id": 2, "has_noise": False}
        result = calculate_schedule([noisy_task, quiet_task], [], "2026-09-04")
        self.assertEqual(result[1], ("2026-09-04T08:00", "2026-09-07T17:00"))
        self.assertEqual(result[2], ("2026-09-04T08:00", "2026-09-05T00:00"))

    def test_project_can_ignore_noise_calendar_restriction(self) -> None:
        noisy_task = {
            "id": 1,
            "parent_id": None,
            "duration_minutes": 960,
            "task_type": "inspection",
            "calendar_type": "elapsed",
            "has_noise": True,
        }
        calendar = WorkCalendar(ignore_noise_restrictions=True)
        result = calculate_schedule([noisy_task], [], "2026-09-04", calendar)
        self.assertEqual(result[1], ("2026-09-04T08:00", "2026-09-05T00:00"))

    def test_explicit_constraint_can_start_before_project_fallback(self) -> None:
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "constraint_start": "2026-08-13",
            }
        ]
        result = calculate_schedule(tasks, [], "2026-08-27")
        self.assertEqual(result[1], ("2026-08-13T08:00", "2026-08-13T17:00"))

    def test_successor_follows_early_constrained_chain_before_project_fallback(self) -> None:
        tasks = [
            {
                "id": 1,
                "parent_id": None,
                "duration": 1,
                "constraint_start": "2026-08-13",
            },
            {"id": 2, "parent_id": None, "duration": 1, "constraint_start": None},
        ]
        dependencies = [
            {"predecessor_id": 1, "successor_id": 2, "relation_type": "FS"}
        ]
        result = calculate_schedule(tasks, dependencies, "2026-08-27")
        self.assertEqual(result[1], ("2026-08-13T08:00", "2026-08-13T17:00"))
        self.assertEqual(result[2], ("2026-08-14T08:00", "2026-08-14T17:00"))

    def test_tasks_sort_by_calculated_start_with_undated_last(self) -> None:
        tasks = [
            {"id": 1, "calculated_start": "2026-09-10T08:00", "sort_order": 10},
            {"id": 2, "calculated_start": None, "sort_order": 20},
            {"id": 3, "calculated_start": "2026-09-07T13:00", "sort_order": 30},
            {"id": 4, "calculated_start": "2026-09-07T08:00", "sort_order": 40},
        ]
        self.assertEqual(
            [task["id"] for task in sorted(tasks, key=schedule_sort_key)],
            [4, 3, 1, 2],
        )


class DatabaseTests(unittest.TestCase):
    def test_event_instance_status_and_time_are_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            event_id = database.add_task(
                "图纸批准",
                0,
                None,
                None,
                "",
                task_type="milestone",
                event_status="occurred",
                event_time="2026-09-08T14:30",
            )
            event = database.task(event_id)
            self.assertEqual(event["event_status"], "occurred")
            self.assertEqual(event["event_time"], "2026-09-08T14:30")

    def test_dependency_keeps_business_category_and_lag_calendar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            event_id = database.add_task(
                "验收通过", 0, None, None, "", task_type="milestone"
            )
            task_id = database.add_task("封板", 1, None, None, "")
            relation_id = database.add_dependency(
                event_id,
                task_id,
                "FS",
                2,
                constraint_category="inspection",
                lag_unit="workday",
            )
            relation = database.dependency(relation_id)
            self.assertEqual(relation["constraint_category"], "inspection")
            self.assertEqual(relation["lag_unit"], "workday")

            overview = database.constraint_overview()
            self.assertTrue(
                any(
                    row["kind"] == "temporal"
                    and row["category"] == "inspection"
                    for row in overview
                )
            )
            self.assertTrue(
                any(row["kind"] == "state" and row["subject"] == "验收通过" for row in overview)
            )

    def test_resources_are_attached_to_tasks_and_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            workface = database.add_resource("3层A区", "workface")
            equipment = database.add_resource("施工升降机", "equipment")
            task_id = database.add_task(
                "3层A区砌墙",
                1,
                None,
                None,
                "",
                trade="泥瓦工",
                resource_ids=[equipment, workface],
            )
            self.assertEqual(
                {resource["id"] for resource in database.task(task_id)["resources"]},
                {equipment, workface},
            )
            database.delete_resource(equipment)
            self.assertEqual(
                [resource["id"] for resource in database.task(task_id)["resources"]],
                [workface],
            )

    def test_spatial_objects_and_operational_resources_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            workface = database.add_resource("3层A区", "workface")
            access = database.add_resource("东侧通道", "access")
            crew = database.add_resource("泥瓦一班", "crew", skills=["泥瓦工"])
            equipment = database.add_resource("施工电梯", "equipment")
            inspector = database.add_resource("质量员", "inspector")

            self.assertEqual(
                {item["id"] for item in database.spatial_objects()},
                {workface, access},
            )
            self.assertEqual(
                {item["id"] for item in database.operational_resources()},
                {crew, equipment, inspector},
            )

    def test_crew_can_have_multiple_trade_skills(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            crew = database.add_resource(
                "综合安装班", "crew", skills=["水电工", "安装工"]
            )
            resource = next(item for item in database.resources() if item["id"] == crew)
            self.assertEqual(set(resource["skills"]), {"水电工", "安装工"})

    def test_crew_calendar_exceptions_are_saved_and_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            crew = database.add_resource("综合安装班", "crew", skills=["安装工"])
            database.save_crew_calendar_exception(
                crew, "2026-09-09", False, "班组请假"
            )
            rows = database.crew_calendar_exceptions(crew)
            self.assertEqual(
                [(row["exception_date"], row["is_working"], row["name"]) for row in rows],
                [("2026-09-09", 0, "班组请假")],
            )
            resource = next(item for item in database.resources() if item["id"] == crew)
            self.assertEqual(
                resource["availability_exceptions"], {"2026-09-09": False}
            )
            database.delete_resource(crew)
            self.assertEqual(database.crew_calendar_exceptions(crew), [])

    def test_path_network_route_generates_segmented_logistics_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            database.add_resource("运输班", "crew", skills=["杂工"])
            loading = database.add_path_node("卸货区", "loading")
            elevator = database.add_path_node("货梯口", "elevator")
            room = database.add_path_node("房间A", "workface")
            first = database.add_path_segment(
                "卸货区至货梯", loading, elevator, travel_minutes=15
            )
            second = database.add_path_segment(
                "货梯至房间A", elevator, room, travel_minutes=10
            )
            route = database.add_route_template("卸货区至房间A", "杂工")
            database.add_route_template_step(route, first, demand_amount=1)
            database.add_route_template_step(route, second, demand_amount=1)

            summary, children = database.create_route_instance(route, "瓷砖运输")

            self.assertEqual(len(children), 2)
            self.assertEqual([item["parent_id"] for item in database.direct_children(summary)], [summary, summary])
            relations = database.dependencies()
            self.assertEqual(
                (relations[0]["predecessor_id"], relations[0]["successor_id"]),
                tuple(children),
            )
            self.assertEqual(relations[0]["constraint_category"], "logistics")
            tasks = {item["id"]: item for item in database.tasks()}
            self.assertEqual(
                [resource["resource_type"] for resource in tasks[children[0]]["resources"]],
                ["access"],
            )
            result = calculate_schedule(
                database.tasks(),
                relations,
                "2026-09-07",
                available_resources=database.resources(),
            )
            self.assertEqual(result[children[0]], ("2026-09-07T08:00", "2026-09-07T08:15"))
            self.assertEqual(result[children[1]], ("2026-09-07T08:15", "2026-09-07T08:25"))

    def test_path_segment_calendar_is_attached_to_access_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            left = database.add_path_node("A")
            right = database.add_path_node("B")
            segment = database.add_path_segment("A-B通道", left, right)
            database.save_path_segment_calendar_exception(
                segment, "2026-09-10", False, "封路"
            )
            resource = next(
                item for item in database.resources() if item.get("path_segment_id") == segment
            )
            self.assertEqual(
                resource["availability_exceptions"], {"2026-09-10": False}
            )
            self.assertEqual(
                database.path_segment_calendar_exceptions(segment)[0]["name"], "封路"
            )
            task_id = database.add_task(
                "通道检查",
                1,
                None,
                None,
                "",
                task_type="inspection",
                resource_ids=[resource["id"]],
            )
            result = calculate_schedule(
                database.tasks(), [], "2026-09-10", available_resources=database.resources()
            )
            self.assertEqual(result[task_id][0], "2026-09-11T08:00")

    def test_path_can_be_workface_with_dynamic_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            opened = database.add_task(
                "便道开放", 0, None, None, "", task_type="milestone"
            )
            closed = database.add_task(
                "便道拆除", 0, None, None, "", task_type="milestone"
            )
            left = database.add_path_node("入口")
            right = database.add_path_node("动态作业区")
            segment = database.add_path_segment(
                "临时便道兼作业面",
                left,
                right,
                is_workface=True,
                available_from="2026-09-08",
                available_until="2026-09-30",
                activation_event_id=opened,
                deactivation_event_id=closed,
            )
            resource = next(
                item for item in database.resources()
                if item.get("path_segment_id") == segment
            )
            self.assertEqual(resource["is_workface"], 1)
            self.assertEqual(resource["activation_event_id"], opened)
            self.assertEqual(resource["deactivation_event_id"], closed)

            task_id = database.add_task(
                "便道区域施工",
                1,
                None,
                None,
                "",
                resource_ids=[resource["id"]],
                spatial_occupancy_mode="close",
                traffic_reduction=0.5,
            )
            task = database.task(task_id)
            self.assertEqual(task["spatial_occupancy_mode"], "close")
            self.assertEqual(task["traffic_reduction"], 0.5)

    def test_unavailable_route_uses_configured_alternative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            database.update_settings("测试计划", "2026-09-07")
            a = database.add_path_node("A")
            b = database.add_path_node("B")
            primary_segment = database.add_path_segment(
                "主通道", a, b, available_from="2026-09-10"
            )
            alternative_segment = database.add_path_segment("备用通道", a, b)
            alternative = database.add_route_template("备用路线")
            database.add_route_template_step(alternative, alternative_segment)
            primary = database.add_route_template(
                "首选路线", alternative_route_id=alternative
            )
            database.add_route_template_step(primary, primary_segment)

            _, children = database.create_route_instance(primary, "材料运输")
            task = database.task(children[0])
            self.assertEqual(task["route_template_id"], alternative)
            self.assertEqual(task["resources"][0]["path_segment_id"], alternative_segment)

    def test_task_rejects_manually_assigned_crew_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            crew = database.add_resource("泥瓦一班", "crew", skills=["泥瓦工"])
            with self.assertRaisesRegex(ValueError, "自动计算"):
                database.add_task(
                    "电缆敷设", 1, None, None, "", trade="水电工", resource_ids=[crew]
                )

    def test_unclassified_crew_is_not_an_automatic_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            database.add_resource("待分类班组", "crew")
            task_id = database.add_task("临时任务", 1, None, None, "", trade="杂工")
            result, reasons = calculate_schedule_details(
                database.tasks(),
                [],
                "2026-09-07",
                available_resources=database.resources(),
            )
            self.assertNotIn(task_id, result)
            self.assertIn("没有具备", reasons[task_id])

    def test_calculated_crew_assignment_is_saved_as_schedule_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            crew = database.add_resource("安装一班", "crew", skills=["安装工"])
            task_id = database.add_task(
                "设备安装", 1, None, None, "", trade="安装工"
            )
            assignments = {}
            result, reasons = calculate_schedule_details(
                database.tasks(),
                [],
                "2026-09-07",
                available_resources=database.resources(),
                crew_assignments_out=assignments,
            )
            database.save_calculated_dates(result, reasons, assignments)
            self.assertEqual(database.task(task_id)["assigned_crew"]["id"], crew)

    def test_schedule_runs_preserve_results_and_can_be_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            task_id = database.add_task("主体施工", 1, None, None, "")
            first_id = database.save_calculated_dates(
                {task_id: ("2026-09-07T08:00", "2026-09-07T17:00")},
                {task_id: "原计划"},
                name="方案 A",
                status_date="2026-09-06",
                optimization_goal="stable",
            )
            second_id = database.save_calculated_dates(
                {task_id: ("2026-09-10T08:00", "2026-09-10T17:00")},
                {task_id: "当前预测"},
                name="方案 B",
                status_date="2026-09-09",
                optimization_goal="fastest",
            )

            self.assertEqual(database.current_schedule_run_id(), second_id)
            self.assertEqual(database.task(task_id)["calculated_start"], "2026-09-10T08:00")
            self.assertEqual(
                database.task(task_id, first_id)["calculated_start"],
                "2026-09-07T08:00",
            )
            database.set_current_schedule_run(first_id)
            self.assertEqual(database.task(task_id)["schedule_reason"], "原计划")
            runs = {run["id"]: run for run in database.schedule_runs()}
            self.assertEqual(runs[first_id]["name"], "方案 A")
            self.assertEqual(runs[second_id]["result_count"], 1)

    def test_legacy_task_schedule_columns_are_migrated_then_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old_schedule.db"
            database = Database(path)
            task_id = database.add_task("迁移任务", 1, None, None, "")
            with database.session() as connection:
                connection.execute("ALTER TABLE tasks ADD COLUMN calculated_start TEXT")
                connection.execute("ALTER TABLE tasks ADD COLUMN calculated_finish TEXT")
                connection.execute("ALTER TABLE tasks ADD COLUMN calculated_crew_id INTEGER")
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN schedule_reason TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    """UPDATE tasks SET calculated_start = '2026-09-07T08:00',
                           calculated_finish = '2026-09-07T17:00',
                           schedule_reason = '旧版结果'
                       WHERE id = ?""",
                    (task_id,),
                )

            migrated = Database(path)
            with migrated.session() as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(tasks)")
                }
            self.assertTrue(
                {
                    "calculated_start",
                    "calculated_finish",
                    "calculated_crew_id",
                    "schedule_reason",
                }.isdisjoint(columns)
            )
            self.assertEqual(migrated.schedule_runs()[0]["name"], "迁移前当前计划")
            self.assertEqual(migrated.task(task_id)["schedule_reason"], "旧版结果")

    def test_crud_and_dependency_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            first = database.add_task("基础施工", 3, None, None, "")
            second = database.add_task("主体施工", 5, None, None, "")
            database.add_dependency(first, second, "FS")
            self.assertEqual(len(database.dependencies()), 1)
            database.delete_task(first)
            self.assertEqual(database.dependencies(), [])

    def test_old_dependency_table_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old_schedule.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """CREATE TABLE dependencies (
                       id INTEGER PRIMARY KEY,
                       predecessor_id INTEGER NOT NULL,
                       successor_id INTEGER NOT NULL,
                       relation_type TEXT NOT NULL
                   )"""
            )
            connection.commit()
            connection.close()
            database = Database(path)
            with database.session() as migrated:
                columns = {
                    row[1] for row in migrated.execute("PRAGMA table_info(dependencies)")
                }
            self.assertIn("lag_days", columns)
            self.assertIn("lag_unit", columns)

    def test_existing_workday_lags_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old_schedule.db"
            database = Database(path)
            first = database.add_task("养护前", 1, None, None, "")
            second = database.add_task("养护后", 1, None, None, "")
            relation_id = database.add_dependency(first, second, "FS", 7)
            with database.session() as connection:
                connection.execute(
                    "UPDATE dependencies SET lag_unit = 'workday' WHERE id = ?",
                    (relation_id,),
                )

            migrated = Database(path)
            self.assertEqual(
                migrated.dependency(relation_id)["lag_unit"], "workday"
            )

    def test_old_settings_table_gets_weekend_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old_schedule.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """CREATE TABLE schedule_settings (
                       id INTEGER PRIMARY KEY,
                       plan_name TEXT NOT NULL,
                       project_start TEXT NOT NULL
                   )"""
            )
            connection.execute(
                "INSERT INTO schedule_settings VALUES (1, '旧计划', '2026-09-01')"
            )
            connection.commit()
            connection.close()
            database = Database(path)
            self.assertEqual(database.settings()["weekend_working"], 0)
            self.assertEqual(database.settings()["ignore_noise_restrictions"], 0)
            self.assertEqual(database.settings()["optimization_goal"], "stable")
            self.assertEqual(database.settings()["auto_recalculate"], 1)
            database.update_settings(
                "旧计划",
                "2026-09-01",
                True,
                True,
                work_sessions="06:00-14:00,14:00-22:00",
                required_finish="2027-01-31",
                status_date="2026-09-07",
                optimization_goal="continuity",
                overtime_lunch=True,
                overtime_night=True,
                overtime_holiday=True,
                crew_max_daily_hours=10,
                crew_max_consecutive_days=5,
                resource_leveling_mode="allow_overload",
                auto_recalculate=False,
            )
            settings = database.settings()
            self.assertEqual(settings["weekend_working"], 1)
            self.assertEqual(settings["ignore_noise_restrictions"], 1)
            self.assertEqual(settings["work_sessions"], "06:00-14:00,14:00-22:00")
            self.assertEqual(settings["required_finish"], "2027-01-31")
            self.assertEqual(settings["status_date"], "2026-09-07")
            self.assertEqual(settings["optimization_goal"], "continuity")
            self.assertEqual(settings["crew_max_daily_hours"], 10)
            self.assertEqual(settings["crew_max_consecutive_days"], 5)
            self.assertEqual(settings["resource_leveling_mode"], "allow_overload")
            self.assertEqual(settings["auto_recalculate"], 0)

    def test_dependency_can_be_edited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            first = database.add_task("任务一", 2, None, None, "")
            second = database.add_task("任务二", 3, None, None, "")
            third = database.add_task("任务三", 4, None, None, "")
            relation_id = database.add_dependency(first, second, "FS", 1)
            database.update_dependency(relation_id, first, third, "SS", 5)
            relation = database.dependency(relation_id)
            self.assertEqual(relation["successor_id"], third)
            self.assertEqual(relation["relation_type"], "SS")
            self.assertEqual(relation["lag_days"], 5)

    def test_task_can_be_dragged_between_hierarchy_levels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            parent = database.add_task("楼层", 1, None, None, "")
            first = database.add_task("任务一", 1, None, None, "")
            second = database.add_task("任务二", 1, None, None, "")

            database.move_task(second, parent)
            database.move_task(first, parent, second)
            self.assertEqual(
                [task["id"] for task in database.direct_children(parent)],
                [first, second],
            )
            with self.assertRaises(ValueError):
                database.move_task(parent, first)
            database.move_task(first, None)
            self.assertIsNone(database.task(first)["parent_id"])

    def test_task_with_dependencies_cannot_become_drop_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            target = database.add_task("已有关系任务", 1, None, None, "")
            successor = database.add_task("后续", 1, None, None, "")
            moving = database.add_task("拖动任务", 1, None, None, "")
            database.add_dependency(target, successor, "FS")
            with self.assertRaises(ValueError):
                database.move_task(moving, target)

    def test_task_segments_are_not_forced_into_an_internal_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            summary = database.add_task("砌墙", 10, None, None, "")
            segment_ids, durations = database.split_task(summary, 5)
            self.assertEqual(durations, [2, 2, 2, 2, 2])
            self.assertEqual(len(segment_ids), 5)
            self.assertEqual(database.dependencies(), [])

    def test_noise_flag_is_saved_and_inherited_by_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            summary = database.add_task(
                "有噪音拆除", 2, None, None, "", has_noise=True
            )
            segment_ids, _ = database.split_task(summary, 2)
            self.assertTrue(database.task(summary)["has_noise"])
            self.assertTrue(all(database.task(task_id)["has_noise"] for task_id in segment_ids))

    def test_migration_removes_only_legacy_internal_segment_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.db"
            database = Database(path)
            first_summary = database.add_task("砌墙", 2, None, None, "")
            second_summary = database.add_task("抹灰", 2, None, None, "")
            first_segments, _ = database.split_task(first_summary, 2)
            second_segments, _ = database.split_task(second_summary, 2)
            internal_id = database.add_dependency(first_segments[0], first_segments[1], "FS")
            cross_id = database.add_dependency(first_segments[0], second_segments[0], "FS")

            migrated = Database(path)
            remaining_ids = {row["id"] for row in migrated.dependencies()}
            self.assertNotIn(internal_id, remaining_ids)
            self.assertIn(cross_id, remaining_ids)

    def test_trade_is_saved_and_inherited_by_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            summary = database.add_task("卫生间反梁", 4, None, None, "", "泥瓦工")
            segment_ids, _ = database.split_task(summary, 4)
            self.assertEqual(database.task(summary)["trade"], "泥瓦工")
            self.assertEqual(
                [database.task(task_id)["trade"] for task_id in segment_ids],
                ["泥瓦工"] * 4,
            )
            first = database.task(segment_ids[0])
            database.update_task(
                segment_ids[0], first["name"], first["duration"], summary, None, "", "水电工"
            )
            self.assertEqual(database.task(segment_ids[0])["trade"], "水电工")
            database.update_task(
                summary, "卫生间反梁", 4, None, None, "", "安装工", True
            )
            self.assertEqual(
                [database.task(task_id)["trade"] for task_id in segment_ids],
                ["安装工"] * 4,
            )

    def test_task_can_be_split_below_one_day_per_segment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            crew = database.add_resource("综合一班", "crew", skills=["杂工"])
            summary = database.add_task("16套室内施工", 10, None, None, "")
            segment_ids, durations = database.split_task(summary, 16)
            self.assertEqual(len(segment_ids), 16)
            self.assertTrue(all(duration == 0.625 for duration in durations))
            result = calculate_schedule(
                database.tasks(),
                database.dependencies(),
                "2026-09-07",
                available_resources=database.resources(),
            )
            self.assertEqual(result[segment_ids[0]], ("2026-09-07T08:00", "2026-09-07T14:00"))

    def test_split_task_migrates_existing_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            before = database.add_task("前置", 1, None, None, "")
            target = database.add_task("待划分工序", 4, None, None, "")
            after = database.add_task("后续", 1, None, None, "")
            incoming_id = database.add_dependency(before, target, "SS", 2)
            outgoing_id = database.add_dependency(target, after, "FF", 3)

            segment_ids, _ = database.split_task(target, 4)

            incoming = database.dependency(incoming_id)
            outgoing = database.dependency(outgoing_id)
            self.assertEqual(incoming["predecessor_id"], before)
            self.assertEqual(incoming["successor_id"], segment_ids[0])
            self.assertEqual((incoming["relation_type"], incoming["lag_days"]), ("SS", 2))
            self.assertEqual(outgoing["predecessor_id"], segment_ids[-1])
            self.assertEqual(outgoing["successor_id"], after)
            self.assertEqual((outgoing["relation_type"], outgoing["lag_days"]), ("FF", 3))
            self.assertEqual(len(database.dependencies()), 2)
            calculate_schedule(database.tasks(), database.dependencies(), "2026-09-07")

    def test_editing_summary_redistributes_duration_to_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            crew = database.add_resource("综合一班", "crew", skills=["杂工"])
            summary = database.add_task("卫生间反梁", 4, None, None, "")
            segment_ids, _ = database.split_task(summary, 4)

            database.update_task(summary, "卫生间反梁", 2, None, "2026-09-08", "")

            segments = database.direct_children(summary)
            self.assertEqual([segment["duration"] for segment in segments], [0.5] * 4)
            self.assertEqual(
                [segment["constraint_start"] for segment in segments],
                ["2026-09-08"] * 4,
            )
            self.assertEqual(database.task(summary)["duration"], 2)
            result = calculate_schedule(
                database.tasks(),
                database.dependencies(),
                "2026-09-07",
                available_resources=database.resources(),
            )
            self.assertEqual(result[segment_ids[0]], ("2026-09-08T08:00", "2026-09-08T12:00"))

    def test_summary_start_constraint_updates_all_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            summary = database.add_task("3层施工", 4, None, None, "")
            child = database.add_task("A区", 2, summary, None, "")
            grandchild = database.add_task("A区砌墙", 1, child, None, "")
            sibling = database.add_task("B区砌墙", 1, summary, None, "")

            database.update_task(summary, "3层施工", 4, None, "2026-09-15", "")
            self.assertEqual(database.task(child)["constraint_start"], "2026-09-15")
            self.assertEqual(database.task(grandchild)["constraint_start"], "2026-09-15")
            self.assertEqual(database.task(sibling)["constraint_start"], "2026-09-15")

            new_child = database.add_task("C区砌墙", 1, summary, None, "")
            self.assertEqual(database.task(new_child)["constraint_start"], "2026-09-15")

            moved_child = database.add_task("D区砌墙", 1, None, None, "")
            database.move_task(moved_child, summary)
            self.assertEqual(database.task(moved_child)["constraint_start"], "2026-09-15")

            database.update_task(summary, "3层施工", 4, None, None, "")
            self.assertIsNone(database.task(grandchild)["constraint_start"])
            self.assertIsNone(database.task(new_child)["constraint_start"])
            self.assertIsNone(database.task(moved_child)["constraint_start"])

    def test_editing_one_segment_updates_displayed_summary_total(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            summary = database.add_task("卫生间反梁", 2, None, None, "")
            segment_ids, _ = database.split_task(summary, 4)
            first = database.task(segment_ids[0])
            database.update_task(segment_ids[0], first["name"], 0.25, summary, None, "")
            self.assertEqual(database.task(summary)["duration"], 1.75)
            database.update_task(summary, "反梁（改名）", 1.75, None, None, "")
            self.assertEqual(
                [segment["duration"] for segment in database.direct_children(summary)],
                [0.25, 0.5, 0.5, 0.5],
            )

    def test_flow_relations_allow_real_resource_or_constraint_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            database.update_settings("测试计划", "2026-09-07")
            masonry_crew = database.add_resource("砌墙班", "crew", skills=["杂工"])
            follow_up_crew = database.add_resource("后续班", "crew", skills=["杂工"])
            masonry = database.add_task("砌墙", 10, None, None, "")
            follow_up = database.add_task("后续工序", 5, None, None, "")
            database.split_task(masonry, 5)
            database.split_task(follow_up, 5)
            relation_ids, extra_buffer = database.create_flow_relations(
                masonry, follow_up, 7, WorkCalendar()
            )
            self.assertEqual(len(relation_ids), 5)
            self.assertEqual(extra_buffer, 0)
            flow_relations = [
                database.dependency(relation_id) for relation_id in relation_ids
            ]
            self.assertTrue(
                all(relation["lag_unit"] == "calendar_day" for relation in flow_relations)
            )

            tasks = database.tasks()
            relations = database.dependencies()
            result = calculate_schedule(
                tasks,
                relations,
                "2026-09-07",
                available_resources=database.resources(),
            )
            database.save_calculated_dates(result)
            successor_segments = database.direct_children(follow_up)
            calendar = WorkCalendar()
            gaps = []
            for previous, current in zip(successor_segments, successor_segments[1:]):
                expected = calendar.next_work_time(datetime.fromisoformat(previous["calculated_finish"]))
                gaps.append(datetime.fromisoformat(current["calculated_start"]) > expected)
            self.assertTrue(any(gaps))

    def test_entire_flow_relation_group_can_be_found_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            first = database.add_task("砌墙", 4, None, None, "")
            second = database.add_task("抹灰", 4, None, None, "")
            database.split_task(first, 4)
            database.split_task(second, 4)
            relation_ids, _ = database.create_flow_relations(first, second, 0)
            group_ids = database.flow_dependency_ids(relation_ids[1])
            self.assertEqual(group_ids, relation_ids)
            database.delete_dependencies(group_ids)
            remaining_ids = {relation["id"] for relation in database.dependencies()}
            self.assertTrue(remaining_ids.isdisjoint(relation_ids))
            self.assertEqual(len(remaining_ids), 0)


    def test_process_library_generates_instance_relation_in_same_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            preparation = database.add_process_template("基层处理", "泥瓦工")
            leveling = database.add_process_template("自流平", "泥瓦工")
            rule_id = database.add_process_template_dependency(
                preparation, leveling, "FS", 1, "calendar_day", "process"
            )
            scope = database.add_task("一层A区", 1, None, None, "")
            first = database.add_task(
                "基层处理实例", 1, scope, None, "", process_template_id=preparation
            )
            second = database.add_task(
                "自流平实例", 1, scope, None, "", process_template_id=leveling
            )

            self.assertEqual(database.sync_process_template_dependencies(), 1)
            relation = database.dependencies()[0]
            self.assertEqual((relation["predecessor_id"], relation["successor_id"]), (first, second))
            self.assertEqual(relation["source_type"], "process_template")
            self.assertEqual(relation["template_relation_id"], rule_id)
            self.assertEqual(relation["lag_days"], 1)

    def test_process_library_matches_corresponding_segments_without_internal_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            masonry = database.add_process_template("砌筑", "泥瓦工")
            plaster = database.add_process_template("抹灰", "抹灰工")
            database.add_process_template_dependency(masonry, plaster)
            masonry_task = database.add_task(
                "砌筑", 4, None, None, "", process_template_id=masonry
            )
            plaster_task = database.add_task(
                "抹灰", 4, None, None, "", process_template_id=plaster
            )
            masonry_segments, _ = database.split_task(masonry_task, 2)
            plaster_segments, _ = database.split_task(plaster_task, 2)

            database.sync_process_template_dependencies()
            pairs = {
                (row["predecessor_id"], row["successor_id"])
                for row in database.dependencies()
            }
            self.assertEqual(pairs, set(zip(masonry_segments, plaster_segments)))
            self.assertFalse(
                any(left in masonry_segments and right in masonry_segments for left, right in pairs)
            )

    def test_process_sync_preserves_manual_relation_and_manual_wins_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            first_template = database.add_process_template("工艺A")
            second_template = database.add_process_template("工艺B")
            database.add_process_template_dependency(first_template, second_template)
            first = database.add_task(
                "A实例", 1, None, None, "", process_template_id=first_template
            )
            second = database.add_task(
                "B实例", 1, None, None, "", process_template_id=second_template
            )
            manual_id = database.add_dependency(first, second, "SS")

            self.assertEqual(database.sync_process_template_dependencies(), 0)
            relations = database.dependencies()
            self.assertEqual(len(relations), 1)
            self.assertEqual(relations[0]["id"], manual_id)
            self.assertEqual(relations[0]["source_type"], "manual")

    def test_process_library_rejects_template_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            first = database.add_process_template("工艺A")
            second = database.add_process_template("工艺B")
            third = database.add_process_template("工艺C")
            database.add_process_template_dependency(first, second)
            database.add_process_template_dependency(second, third)
            with self.assertRaisesRegex(ValueError, "循环"):
                database.add_process_template_dependency(third, first)

    def test_deleting_process_template_unassigns_tasks_and_removes_generated_relations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            first_template = database.add_process_template("工艺A")
            second_template = database.add_process_template("工艺B")
            database.add_process_template_dependency(first_template, second_template)
            first = database.add_task(
                "A实例", 1, None, None, "", process_template_id=first_template
            )
            database.add_task(
                "B实例", 1, None, None, "", process_template_id=second_template
            )
            database.sync_process_template_dependencies()

            database.delete_process_template(first_template)
            self.assertIsNone(database.task(first)["process_template_id"])
            self.assertEqual(database.dependencies(), [])
            self.assertEqual(database.process_template_dependencies(), [])

    def test_work_template_creates_complete_task_and_event_instance_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            template_id = database.add_process_template("自流平作业", "泥瓦工")
            drawing = database.add_work_template_step(
                template_id, "施工图批准", "milestone", "prerequisite",
                duration_minutes=0, event_status="pending"
            )
            transport = database.add_work_template_step(
                template_id, "材料运至作业面", "logistics", "prerequisite",
                duration_minutes=60,
            )
            construction = database.add_work_template_step(
                template_id, "自流平施工", "work", "execution", "泥瓦工",
                duration_minutes=480,
            )
            curing = database.add_work_template_step(
                template_id, "养护", "wait", "postcondition", duration_minutes=1440,
                calendar_type="elapsed",
            )
            accepted = database.add_work_template_step(
                template_id, "验收通过", "milestone", "output", duration_minutes=0
            )
            relation_ids = [
                database.add_work_template_step_dependency(template_id, left, right)
                for left, right in zip(
                    (drawing, transport, construction, curing),
                    (transport, construction, curing, accepted),
                )
            ]

            root_id = database.create_work_template_instance(
                template_id, "3层A区自流平", constraint_start="2026-09-07"
            )
            children = database.direct_children(root_id)
            self.assertEqual(
                [child["name"] for child in children],
                ["施工图批准", "材料运至作业面", "自流平施工", "养护", "验收通过"],
            )
            self.assertTrue(all(child["template_instance_id"] == root_id for child in children))
            self.assertTrue(all(child["constraint_start"] == "2026-09-07" for child in children))
            self.assertEqual(children[0]["duration_minutes"], 0)
            self.assertEqual(children[0]["event_status"], "pending")
            self.assertEqual(children[3]["calendar_type"], "elapsed")
            relations = database.dependencies()
            self.assertEqual(len(relations), 4)
            self.assertTrue(all(row["source_type"] == "work_template" for row in relations))
            self.assertEqual(
                {row["work_template_step_relation_id"] for row in relations}, set(relation_ids)
            )

    def test_work_template_step_rule_change_rebuilds_existing_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            template_id = database.add_process_template("验收作业")
            inspect = database.add_work_template_step(
                template_id, "检查", "inspection", duration_minutes=60
            )
            pass_event = database.add_work_template_step(
                template_id, "验收通过", "milestone", "output", duration_minutes=0
            )
            relation_id = database.add_work_template_step_dependency(
                template_id, inspect, pass_event, constraint_category="inspection"
            )
            database.create_work_template_instance(template_id, "一层验收")
            self.assertEqual(len(database.dependencies()), 1)

            database.delete_work_template_step_dependency(relation_id)
            database.sync_work_template_dependencies()
            self.assertEqual(database.dependencies(), [])

    def test_custom_trade_color_and_name_are_project_editable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            database.add_trade("防水工", "#123ABC")
            crew_id = database.add_resource(
                "防水班组", "crew", skills=["防水工"]
            )
            task_id = database.add_task(
                "卫生间防水", 1, None, None, "", trade="防水工"
            )

            self.assertEqual(database.trade_color_map()["防水工"], "#123ABC")
            self.assertIn("防水工", database.task(task_id)["trade"])
            self.assertIn("防水工", database.resources()[0]["skills"])

            database.update_trade("防水工", "防水专业", "#ABC123")
            self.assertEqual(database.task(task_id)["trade"], "防水专业")
            crew = next(row for row in database.resources() if row["id"] == crew_id)
            self.assertEqual(crew["skills"], ["防水专业"])
            self.assertEqual(database.trade_color_map()["防水专业"], "#ABC123")

    def test_used_trade_cannot_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "schedule.db")
            database.add_trade("焊工", "#445566")
            database.add_task("钢件焊接", 1, None, None, "", trade="焊工")
            with self.assertRaisesRegex(ValueError, "仍被"):
                database.delete_trade("焊工")


class WorkCalendarTests(unittest.TestCase):
    def test_custom_daily_sessions_change_productive_time(self) -> None:
        calendar = WorkCalendar(
            sessions=WorkCalendar.parse_sessions("06:00-10:00,14:00-18:00")
        )
        start, finish = calendar.finish_from_start_time(datetime(2026, 9, 7, 5), 1)
        self.assertEqual(start, datetime(2026, 9, 7, 6))
        self.assertEqual(finish, datetime(2026, 9, 7, 18))

    def test_overtime_settings_extend_sessions_and_enable_holiday(self) -> None:
        calendar = WorkCalendar.from_settings(
            {
                "work_sessions": "08:00-12:00,13:00-17:00",
                "overtime_lunch": True,
                "overtime_night": True,
                "overtime_holiday": True,
            },
            {"2026-10-01": False},
        )
        self.assertTrue(calendar.is_working_day(date(2026, 10, 1)))
        self.assertEqual(
            calendar.SESSIONS,
            ((time(8), time(17)), (time(17), time(22))),
        )

    def test_weekends_are_skipped(self) -> None:
        calendar = WorkCalendar()
        start, finish = calendar.finish_from_start(date(2026, 9, 4), 2)
        self.assertEqual(start.isoformat(), "2026-09-04")
        self.assertEqual(finish.isoformat(), "2026-09-07")

    def test_weekends_can_be_working_days(self) -> None:
        calendar = WorkCalendar(weekend_working=True)
        start, finish = calendar.finish_from_start(date(2026, 9, 4), 2)
        self.assertEqual(start.isoformat(), "2026-09-04")
        self.assertEqual(finish.isoformat(), "2026-09-05")

    def test_calendar_exception_overrides_working_weekend(self) -> None:
        calendar = WorkCalendar({"2026-09-05": False}, weekend_working=True)
        start, finish = calendar.finish_from_start(date(2026, 9, 4), 2)
        self.assertEqual(start.isoformat(), "2026-09-04")
        self.assertEqual(finish.isoformat(), "2026-09-06")

    def test_holiday_and_makeup_workday_exceptions(self) -> None:
        calendar = WorkCalendar({"2026-09-07": False, "2026-09-05": True})
        self.assertFalse(calendar.is_working_day(date(2026, 9, 7)))
        self.assertTrue(calendar.is_working_day(date(2026, 9, 5)))

    def test_working_days_between_uses_real_calendar(self) -> None:
        calendar = WorkCalendar()
        self.assertEqual(
            calendar.working_days_between(date(2026, 9, 7), date(2026, 9, 18)), 10
        )

    def test_natural_days_between_includes_weekend_and_both_end_dates(self) -> None:
        calendar = WorkCalendar()
        self.assertEqual(
            calendar.natural_days_between(date(2026, 9, 4), date(2026, 9, 7)), 4
        )
        self.assertEqual(
            calendar.natural_days_between(date(2026, 9, 7), date(2026, 9, 7)), 1
        )

    def test_work_hours_between_uses_calculated_start_and_finish(self) -> None:
        calendar = WorkCalendar()
        self.assertEqual(
            calendar.work_hours_between(
                datetime(2026, 9, 4, 13, 0),
                datetime(2026, 9, 7, 12, 0),
            ),
            8,
        )

    def test_work_hours_between_respects_working_weekends(self) -> None:
        calendar = WorkCalendar(weekend_working=True)
        self.assertEqual(
            calendar.work_hours_between(
                datetime(2026, 9, 4, 8, 0),
                datetime(2026, 9, 6, 17, 0),
            ),
            24,
        )

    def test_work_intervals_exclude_lunch_nights_and_weekends(self) -> None:
        calendar = WorkCalendar()
        intervals = calendar.work_intervals_between(
            datetime(2026, 9, 4, 8), datetime(2026, 9, 7, 17)
        )
        self.assertEqual(
            intervals,
            [
                (datetime(2026, 9, 4, 8), datetime(2026, 9, 4, 12)),
                (datetime(2026, 9, 4, 13), datetime(2026, 9, 4, 17)),
                (datetime(2026, 9, 7, 8), datetime(2026, 9, 7, 12)),
                (datetime(2026, 9, 7, 13), datetime(2026, 9, 7, 17)),
            ],
        )


if __name__ == "__main__":
    unittest.main()
