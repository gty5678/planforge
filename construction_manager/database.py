from __future__ import annotations

import sqlite3
import math
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from .trades import TRADES, TRADE_COLORS, normalized_trade


class Database:
    """SQLite persistence for one local construction schedule."""

    def __init__(self, path: str | Path | None = None) -> None:
        root = Path(__file__).resolve().parent.parent
        self.path = Path(path) if path else root / "data" / "schedule.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS schedule_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            plan_name TEXT NOT NULL DEFAULT '工程进度计划',
            project_start TEXT NOT NULL,
            weekend_working INTEGER NOT NULL DEFAULT 0 CHECK (weekend_working IN (0, 1)),
            ignore_noise_restrictions INTEGER NOT NULL DEFAULT 0
                CHECK (ignore_noise_restrictions IN (0, 1)),
            work_sessions TEXT NOT NULL DEFAULT '08:00-12:00,13:00-17:00',
            required_finish TEXT,
            status_date TEXT,
            optimization_goal TEXT NOT NULL DEFAULT 'stable',
            overtime_lunch INTEGER NOT NULL DEFAULT 0,
            overtime_night INTEGER NOT NULL DEFAULT 0,
            overtime_holiday INTEGER NOT NULL DEFAULT 0,
            crew_max_daily_hours REAL NOT NULL DEFAULT 8,
            crew_max_consecutive_days INTEGER NOT NULL DEFAULT 0,
            resource_leveling_mode TEXT NOT NULL DEFAULT 'delay',
            auto_recalculate INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS trade_definitions (
            name TEXT PRIMARY KEY,
            color TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS process_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            default_trade TEXT NOT NULL DEFAULT '杂工',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS process_template_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            predecessor_template_id INTEGER NOT NULL,
            successor_template_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL CHECK (relation_type IN ('FS', 'SS', 'FF', 'SF')),
            lag_days INTEGER NOT NULL DEFAULT 0 CHECK (lag_days >= 0),
            lag_unit TEXT NOT NULL DEFAULT 'calendar_day' CHECK (lag_unit IN ('workday', 'calendar_day')),
            constraint_category TEXT NOT NULL DEFAULT 'process',
            FOREIGN KEY (predecessor_template_id) REFERENCES process_templates(id) ON DELETE CASCADE,
            FOREIGN KEY (successor_template_id) REFERENCES process_templates(id) ON DELETE CASCADE,
            CHECK (predecessor_template_id <> successor_template_id),
            UNIQUE (predecessor_template_id, successor_template_id)
        );

        CREATE TABLE IF NOT EXISTS work_template_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            task_type TEXT NOT NULL DEFAULT 'work',
            role TEXT NOT NULL DEFAULT 'execution',
            default_trade TEXT NOT NULL DEFAULT '杂工',
            duration_minutes INTEGER NOT NULL DEFAULT 480 CHECK (duration_minutes >= 0),
            calendar_type TEXT NOT NULL DEFAULT 'working',
            has_noise INTEGER NOT NULL DEFAULT 0 CHECK (has_noise IN (0, 1)),
            event_status TEXT NOT NULL DEFAULT 'derived',
            sort_order INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (template_id) REFERENCES process_templates(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS work_template_step_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            predecessor_step_id INTEGER NOT NULL,
            successor_step_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL CHECK (relation_type IN ('FS', 'SS', 'FF', 'SF')),
            lag_days INTEGER NOT NULL DEFAULT 0 CHECK (lag_days >= 0),
            lag_unit TEXT NOT NULL DEFAULT 'calendar_day' CHECK (lag_unit IN ('workday', 'calendar_day')),
            constraint_category TEXT NOT NULL DEFAULT 'process',
            FOREIGN KEY (template_id) REFERENCES process_templates(id) ON DELETE CASCADE,
            FOREIGN KEY (predecessor_step_id) REFERENCES work_template_steps(id) ON DELETE CASCADE,
            FOREIGN KEY (successor_step_id) REFERENCES work_template_steps(id) ON DELETE CASCADE,
            CHECK (predecessor_step_id <> successor_step_id),
            UNIQUE (predecessor_step_id, successor_step_id)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER,
            name TEXT NOT NULL,
            trade TEXT NOT NULL DEFAULT '杂工',
            duration INTEGER NOT NULL DEFAULT 1 CHECK (duration >= 1),
            duration_days REAL,
            duration_minutes INTEGER,
            task_type TEXT NOT NULL DEFAULT 'work',
            event_status TEXT NOT NULL DEFAULT 'derived',
            event_time TEXT,
            has_noise INTEGER NOT NULL DEFAULT 0 CHECK (has_noise IN (0, 1)),
            calendar_type TEXT NOT NULL DEFAULT 'working',
            priority INTEGER NOT NULL DEFAULT 50,
            constraint_start TEXT,
            calculated_start TEXT,
            calculated_finish TEXT,
            calculated_crew_id INTEGER,
            schedule_reason TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            process_template_id INTEGER,
            work_template_step_id INTEGER,
            template_instance_id INTEGER,
            route_template_id INTEGER,
            spatial_occupancy_mode TEXT NOT NULL DEFAULT 'reduce',
            traffic_reduction REAL NOT NULL DEFAULT 1,
            FOREIGN KEY (parent_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (process_template_id) REFERENCES process_templates(id) ON DELETE SET NULL,
            FOREIGN KEY (work_template_step_id) REFERENCES work_template_steps(id) ON DELETE SET NULL,
            FOREIGN KEY (template_instance_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            predecessor_id INTEGER NOT NULL,
            successor_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL CHECK (relation_type IN ('FS', 'SS', 'FF', 'SF')),
            lag_days INTEGER NOT NULL DEFAULT 0 CHECK (lag_days >= 0),
            lag_unit TEXT NOT NULL DEFAULT 'calendar_day' CHECK (lag_unit IN ('workday', 'calendar_day')),
            constraint_category TEXT NOT NULL DEFAULT 'process',
            source_type TEXT NOT NULL DEFAULT 'manual',
            template_relation_id INTEGER,
            work_template_step_relation_id INTEGER,
            FOREIGN KEY (predecessor_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (successor_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (template_relation_id) REFERENCES process_template_dependencies(id) ON DELETE CASCADE,
            FOREIGN KEY (work_template_step_relation_id) REFERENCES work_template_step_dependencies(id) ON DELETE CASCADE,
            CHECK (predecessor_id <> successor_id),
            UNIQUE (predecessor_id, successor_id)
        );

        CREATE TABLE IF NOT EXISTS calendar_exceptions (
            exception_date TEXT PRIMARY KEY,
            is_working INTEGER NOT NULL CHECK (is_working IN (0, 1)),
            name TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            resource_type TEXT NOT NULL CHECK (
                resource_type IN ('crew', 'workface', 'equipment', 'access', 'inspector')
            ),
            capacity REAL NOT NULL DEFAULT 1 CHECK (capacity > 0),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, resource_type)
        );

        CREATE TABLE IF NOT EXISTS task_resource_demands (
            task_id INTEGER NOT NULL,
            resource_id INTEGER NOT NULL,
            demand_amount REAL NOT NULL DEFAULT 1 CHECK (demand_amount > 0),
            PRIMARY KEY (task_id, resource_id),
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS crew_skills (
            resource_id INTEGER NOT NULL,
            trade TEXT NOT NULL,
            PRIMARY KEY (resource_id, trade),
            FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS crew_calendar_exceptions (
            resource_id INTEGER NOT NULL,
            exception_date TEXT NOT NULL,
            is_working INTEGER NOT NULL CHECK (is_working IN (0, 1)),
            name TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (resource_id, exception_date),
            FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS path_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            node_type TEXT NOT NULL DEFAULT 'junction',
            notes TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS path_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            from_node_id INTEGER NOT NULL,
            to_node_id INTEGER NOT NULL,
            direction TEXT NOT NULL DEFAULT 'two_way'
                CHECK (direction IN ('two_way', 'forward')),
            travel_minutes INTEGER NOT NULL DEFAULT 10 CHECK (travel_minutes >= 5),
            resource_id INTEGER NOT NULL UNIQUE,
            is_workface INTEGER NOT NULL DEFAULT 0,
            available_from TEXT,
            available_until TEXT,
            activation_event_id INTEGER,
            deactivation_event_id INTEGER,
            notes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (from_node_id) REFERENCES path_nodes(id),
            FOREIGN KEY (to_node_id) REFERENCES path_nodes(id),
            FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE,
            CHECK (from_node_id <> to_node_id)
        );

        CREATE TABLE IF NOT EXISTS path_segment_calendar_exceptions (
            segment_id INTEGER NOT NULL,
            exception_date TEXT NOT NULL,
            is_available INTEGER NOT NULL CHECK (is_available IN (0, 1)),
            name TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (segment_id, exception_date),
            FOREIGN KEY (segment_id) REFERENCES path_segments(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS route_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            default_trade TEXT NOT NULL DEFAULT '杂工',
            notes TEXT NOT NULL DEFAULT '',
            alternative_route_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS route_template_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id INTEGER NOT NULL,
            segment_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            reverse_travel INTEGER NOT NULL DEFAULT 0 CHECK (reverse_travel IN (0, 1)),
            duration_minutes INTEGER NOT NULL CHECK (duration_minutes >= 5),
            demand_amount REAL NOT NULL DEFAULT 1 CHECK (demand_amount > 0),
            FOREIGN KEY (route_id) REFERENCES route_templates(id) ON DELETE CASCADE,
            FOREIGN KEY (segment_id) REFERENCES path_segments(id),
            UNIQUE (route_id, sort_order)
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
        CREATE INDEX IF NOT EXISTS idx_dependencies_successor ON dependencies(successor_id);
        CREATE INDEX IF NOT EXISTS idx_task_resources_resource
            ON task_resource_demands(resource_id);
        """
        with self.session() as connection:
            connection.executescript(schema)
            dependency_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(dependencies)").fetchall()
            }
            if "lag_days" not in dependency_columns:
                connection.execute(
                    "ALTER TABLE dependencies ADD COLUMN lag_days INTEGER NOT NULL DEFAULT 0"
                )
            if "lag_unit" not in dependency_columns:
                connection.execute(
                    "ALTER TABLE dependencies ADD COLUMN lag_unit TEXT NOT NULL DEFAULT 'calendar_day'"
                )
            if "constraint_category" not in dependency_columns:
                connection.execute(
                    "ALTER TABLE dependencies ADD COLUMN constraint_category TEXT NOT NULL DEFAULT 'process'"
                )
            if "source_type" not in dependency_columns:
                connection.execute(
                    "ALTER TABLE dependencies ADD COLUMN source_type TEXT NOT NULL DEFAULT 'manual'"
                )
            if "template_relation_id" not in dependency_columns:
                connection.execute(
                    "ALTER TABLE dependencies ADD COLUMN template_relation_id INTEGER"
                )
            if "work_template_step_relation_id" not in dependency_columns:
                connection.execute(
                    "ALTER TABLE dependencies ADD COLUMN work_template_step_relation_id INTEGER"
                )
            task_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "duration_days" not in task_columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN duration_days REAL")
            if "trade" not in task_columns:
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN trade TEXT NOT NULL DEFAULT '杂工'"
                )
            task_migrations = {
                "duration_minutes": "ALTER TABLE tasks ADD COLUMN duration_minutes INTEGER",
                "task_type": "ALTER TABLE tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'work'",
                "calendar_type": "ALTER TABLE tasks ADD COLUMN calendar_type TEXT NOT NULL DEFAULT 'working'",
                "priority": "ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 50",
                "schedule_reason": "ALTER TABLE tasks ADD COLUMN schedule_reason TEXT NOT NULL DEFAULT ''",
                "calculated_crew_id": "ALTER TABLE tasks ADD COLUMN calculated_crew_id INTEGER",
                "event_status": "ALTER TABLE tasks ADD COLUMN event_status TEXT NOT NULL DEFAULT 'derived'",
                "event_time": "ALTER TABLE tasks ADD COLUMN event_time TEXT",
                "has_noise": "ALTER TABLE tasks ADD COLUMN has_noise INTEGER NOT NULL DEFAULT 0",
                "process_template_id": "ALTER TABLE tasks ADD COLUMN process_template_id INTEGER",
                "work_template_step_id": "ALTER TABLE tasks ADD COLUMN work_template_step_id INTEGER",
                "template_instance_id": "ALTER TABLE tasks ADD COLUMN template_instance_id INTEGER",
                "route_template_id": "ALTER TABLE tasks ADD COLUMN route_template_id INTEGER",
                "spatial_occupancy_mode": "ALTER TABLE tasks ADD COLUMN spatial_occupancy_mode TEXT NOT NULL DEFAULT 'reduce'",
                "traffic_reduction": "ALTER TABLE tasks ADD COLUMN traffic_reduction REAL NOT NULL DEFAULT 1",
            }
            for column, statement in task_migrations.items():
                if column not in task_columns:
                    connection.execute(statement)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_process_template ON tasks(process_template_id)"
            )
            step_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(work_template_steps)"
                ).fetchall()
            }
            if "event_status" not in step_columns:
                connection.execute(
                    "ALTER TABLE work_template_steps ADD COLUMN event_status TEXT NOT NULL DEFAULT 'derived'"
                )
            settings_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(schedule_settings)"
                ).fetchall()
            }
            if "weekend_working" not in settings_columns:
                connection.execute(
                    """ALTER TABLE schedule_settings
                       ADD COLUMN weekend_working INTEGER NOT NULL DEFAULT 0"""
                )
            if "ignore_noise_restrictions" not in settings_columns:
                connection.execute(
                    """ALTER TABLE schedule_settings
                       ADD COLUMN ignore_noise_restrictions INTEGER NOT NULL DEFAULT 0"""
                )
            settings_migrations = {
                "work_sessions": "ALTER TABLE schedule_settings ADD COLUMN work_sessions TEXT NOT NULL DEFAULT '08:00-12:00,13:00-17:00'",
                "required_finish": "ALTER TABLE schedule_settings ADD COLUMN required_finish TEXT",
                "status_date": "ALTER TABLE schedule_settings ADD COLUMN status_date TEXT",
                "optimization_goal": "ALTER TABLE schedule_settings ADD COLUMN optimization_goal TEXT NOT NULL DEFAULT 'stable'",
                "overtime_lunch": "ALTER TABLE schedule_settings ADD COLUMN overtime_lunch INTEGER NOT NULL DEFAULT 0",
                "overtime_night": "ALTER TABLE schedule_settings ADD COLUMN overtime_night INTEGER NOT NULL DEFAULT 0",
                "overtime_holiday": "ALTER TABLE schedule_settings ADD COLUMN overtime_holiday INTEGER NOT NULL DEFAULT 0",
                "crew_max_daily_hours": "ALTER TABLE schedule_settings ADD COLUMN crew_max_daily_hours REAL NOT NULL DEFAULT 8",
                "crew_max_consecutive_days": "ALTER TABLE schedule_settings ADD COLUMN crew_max_consecutive_days INTEGER NOT NULL DEFAULT 0",
                "resource_leveling_mode": "ALTER TABLE schedule_settings ADD COLUMN resource_leveling_mode TEXT NOT NULL DEFAULT 'delay'",
                "auto_recalculate": "ALTER TABLE schedule_settings ADD COLUMN auto_recalculate INTEGER NOT NULL DEFAULT 1",
            }
            for column, statement in settings_migrations.items():
                if column not in settings_columns:
                    connection.execute(statement)
            route_step_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(route_template_steps)"
                ).fetchall()
            }
            if "reverse_travel" not in route_step_columns:
                connection.execute(
                    """ALTER TABLE route_template_steps
                       ADD COLUMN reverse_travel INTEGER NOT NULL DEFAULT 0"""
                )
            path_segment_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(path_segments)"
                ).fetchall()
            }
            path_segment_migrations = {
                "is_workface": "ALTER TABLE path_segments ADD COLUMN is_workface INTEGER NOT NULL DEFAULT 0",
                "available_from": "ALTER TABLE path_segments ADD COLUMN available_from TEXT",
                "available_until": "ALTER TABLE path_segments ADD COLUMN available_until TEXT",
                "activation_event_id": "ALTER TABLE path_segments ADD COLUMN activation_event_id INTEGER",
                "deactivation_event_id": "ALTER TABLE path_segments ADD COLUMN deactivation_event_id INTEGER",
            }
            for column, statement in path_segment_migrations.items():
                if column not in path_segment_columns:
                    connection.execute(statement)
            route_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(route_templates)"
                ).fetchall()
            }
            if "alternative_route_id" not in route_columns:
                connection.execute(
                    "ALTER TABLE route_templates ADD COLUMN alternative_route_id INTEGER"
                )
            connection.execute(
                "UPDATE tasks SET duration_days = duration WHERE duration_days IS NULL"
            )
            connection.execute(
                """UPDATE tasks
                   SET duration_minutes = CAST(ROUND(duration_days * 480) AS INTEGER)
                   WHERE duration_minutes IS NULL"""
            )
            connection.execute(
                "INSERT OR IGNORE INTO schedule_settings(id, project_start) VALUES(1, ?)",
                (date.today().isoformat(),),
            )
            if not connection.execute(
                "SELECT 1 FROM trade_definitions LIMIT 1"
            ).fetchone():
                for sort_order, trade in enumerate(TRADES):
                    connection.execute(
                        """INSERT INTO trade_definitions(name, color, sort_order)
                           VALUES (?, ?, ?)""",
                        (trade, TRADE_COLORS[trade], sort_order),
                    )
            existing_trades = connection.execute(
                """SELECT trade AS name FROM tasks
                   UNION SELECT default_trade FROM process_templates
                   UNION SELECT default_trade FROM work_template_steps
                   UNION SELECT trade FROM crew_skills
                   UNION SELECT default_trade FROM route_templates"""
            ).fetchall()
            next_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM trade_definitions"
            ).fetchone()[0]
            for row in existing_trades:
                trade = normalized_trade(row["name"])
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO trade_definitions(name, color, sort_order)
                       VALUES (?, '#607D8B', ?)""",
                    (trade, next_order),
                )
                if cursor.rowcount:
                    next_order += 1
            connection.execute(
                """DELETE FROM task_resource_demands
                   WHERE resource_id IN (
                       SELECT id FROM resources WHERE resource_type = 'crew'
                   )"""
            )
            self._remove_legacy_internal_segment_dependencies(connection)

    def trades(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT name, color, sort_order FROM trade_definitions ORDER BY sort_order, name"
            ).fetchall()
            return [dict(row) for row in rows]

    def trade_names(self) -> list[str]:
        return [str(row["name"]) for row in self.trades()]

    def trade_color_map(self) -> dict[str, str]:
        return {str(row["name"]): str(row["color"]) for row in self.trades()}

    def add_trade(self, name: str, color: str) -> None:
        clean_name = normalized_trade(name)
        if not name.strip():
            raise ValueError("工种名称不能为空。")
        if (
            not color.startswith("#")
            or len(color) != 7
            or any(character not in "0123456789abcdefABCDEF" for character in color[1:])
        ):
            raise ValueError("工种颜色无效。")
        with self.session() as connection:
            next_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM trade_definitions"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO trade_definitions(name, color, sort_order) VALUES (?, ?, ?)",
                (clean_name, color.upper(), next_order),
            )

    def update_trade(self, old_name: str, name: str, color: str) -> None:
        clean_name = normalized_trade(name)
        if not name.strip():
            raise ValueError("工种名称不能为空。")
        if (
            not color.startswith("#")
            or len(color) != 7
            or any(character not in "0123456789abcdefABCDEF" for character in color[1:])
        ):
            raise ValueError("工种颜色无效。")
        if old_name == "杂工" and clean_name != old_name:
            raise ValueError("“杂工”是默认工种，只能修改颜色，不能改名。")
        with self.session() as connection:
            current = connection.execute(
                "SELECT sort_order FROM trade_definitions WHERE name = ?", (old_name,)
            ).fetchone()
            if current is None:
                raise ValueError("工种不存在。")
            if clean_name != old_name and connection.execute(
                "SELECT 1 FROM trade_definitions WHERE name = ?", (clean_name,)
            ).fetchone():
                raise ValueError("已经存在同名工种。")
            if clean_name != old_name:
                for table, column in (
                    ("tasks", "trade"),
                    ("process_templates", "default_trade"),
                    ("work_template_steps", "default_trade"),
                    ("crew_skills", "trade"),
                    ("route_templates", "default_trade"),
                ):
                    connection.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                        (clean_name, old_name),
                    )
                connection.execute(
                    "DELETE FROM trade_definitions WHERE name = ?", (old_name,)
                )
                connection.execute(
                    """INSERT INTO trade_definitions(name, color, sort_order)
                       VALUES (?, ?, ?)""",
                    (clean_name, color.upper(), int(current["sort_order"])),
                )
            else:
                connection.execute(
                    "UPDATE trade_definitions SET color = ? WHERE name = ?",
                    (color.upper(), old_name),
                )

    def delete_trade(self, name: str) -> None:
        if name == "杂工":
            raise ValueError("“杂工”是没有指定专业时的默认工种，不能删除。")
        with self.session() as connection:
            references = 0
            for table, column in (
                ("tasks", "trade"),
                ("process_templates", "default_trade"),
                ("work_template_steps", "default_trade"),
                ("crew_skills", "trade"),
                ("route_templates", "default_trade"),
            ):
                references += int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (name,)
                    ).fetchone()[0]
                )
            if references:
                raise ValueError(f"该工种仍被 {references} 处任务、模板或班组使用，不能删除。")
            connection.execute("DELETE FROM trade_definitions WHERE name = ?", (name,))

    @staticmethod
    def _remove_legacy_internal_segment_dependencies(
        connection: sqlite3.Connection,
    ) -> None:
        """Remove only the old auto-created A1→A2 sibling segment chains."""
        connection.execute(
            """DELETE FROM dependencies
               WHERE id IN (
                   SELECT d.id
                   FROM dependencies d
                   JOIN tasks p ON p.id = d.predecessor_id
                   JOIN tasks s ON s.id = d.successor_id
                   JOIN tasks parent ON parent.id = p.parent_id
                   WHERE p.parent_id IS NOT NULL
                     AND p.parent_id = s.parent_id
                     AND p.name LIKE parent.name || '（%段）'
                     AND s.name LIKE parent.name || '（%段）'
                     AND d.relation_type = 'FS'
                     AND d.lag_days = 0
                     AND d.source_type = 'manual'
                     AND (
                         s.sort_order > p.sort_order
                         OR (s.sort_order = p.sort_order AND s.id > p.id)
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM tasks middle
                         WHERE middle.parent_id = p.parent_id
                           AND (
                               middle.sort_order > p.sort_order
                               OR (middle.sort_order = p.sort_order AND middle.id > p.id)
                           )
                           AND (
                               middle.sort_order < s.sort_order
                               OR (middle.sort_order = s.sort_order AND middle.id < s.id)
                           )
                     )
               )"""
        )

    def process_templates(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT * FROM process_templates ORDER BY name, id"
            ).fetchall()
            return [dict(row) for row in rows]

    def add_process_template(
        self, name: str, default_trade: str = "杂工", notes: str = ""
    ) -> int:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("工艺名称不能为空。")
        with self.session() as connection:
            cursor = connection.execute(
                """INSERT INTO process_templates(name, default_trade, notes)
                   VALUES (?, ?, ?)""",
                (clean_name, normalized_trade(default_trade), notes.strip()),
            )
            return int(cursor.lastrowid)

    def update_process_template(
        self, template_id: int, name: str, default_trade: str, notes: str = ""
    ) -> None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("工艺名称不能为空。")
        with self.session() as connection:
            connection.execute(
                """UPDATE process_templates
                   SET name = ?, default_trade = ?, notes = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (clean_name, normalized_trade(default_trade), notes.strip(), template_id),
            )

    def delete_process_template(self, template_id: int) -> None:
        with self.session() as connection:
            connection.execute(
                """DELETE FROM dependencies
                   WHERE source_type = 'work_template'
                     AND work_template_step_relation_id IN (
                         SELECT id FROM work_template_step_dependencies
                         WHERE template_id = ?
                     )""",
                (template_id,),
            )
            relation_ids = [
                int(row["id"])
                for row in connection.execute(
                    """SELECT id FROM process_template_dependencies
                       WHERE predecessor_template_id = ? OR successor_template_id = ?""",
                    (template_id, template_id),
                ).fetchall()
            ]
            if relation_ids:
                placeholders = ",".join("?" for _ in relation_ids)
                connection.execute(
                    f"DELETE FROM dependencies WHERE source_type = 'process_template' AND template_relation_id IN ({placeholders})",
                    relation_ids,
                )
            connection.execute(
                "UPDATE tasks SET process_template_id = NULL WHERE process_template_id = ?",
                (template_id,),
            )
            connection.execute(
                "DELETE FROM process_template_dependencies WHERE predecessor_template_id = ? OR successor_template_id = ?",
                (template_id, template_id),
            )
            connection.execute(
                "DELETE FROM work_template_step_dependencies WHERE template_id = ?",
                (template_id,),
            )
            connection.execute(
                "UPDATE tasks SET work_template_step_id = NULL WHERE work_template_step_id IN (SELECT id FROM work_template_steps WHERE template_id = ?)",
                (template_id,),
            )
            connection.execute("DELETE FROM work_template_steps WHERE template_id = ?", (template_id,))
            connection.execute("DELETE FROM process_templates WHERE id = ?", (template_id,))

    def process_template_dependencies(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT r.*, p.name AS predecessor_name, s.name AS successor_name
                   FROM process_template_dependencies r
                   JOIN process_templates p ON p.id = r.predecessor_template_id
                   JOIN process_templates s ON s.id = r.successor_template_id
                   ORDER BY r.id"""
            ).fetchall()
            return [dict(row) for row in rows]

    def add_process_template_dependency(
        self,
        predecessor_template_id: int,
        successor_template_id: int,
        relation_type: str = "FS",
        lag_days: int = 0,
        lag_unit: str = "calendar_day",
        constraint_category: str = "process",
    ) -> int:
        if predecessor_template_id == successor_template_id:
            raise ValueError("前后工艺不能相同。")
        if relation_type not in {"FS", "SS", "FF", "SF"}:
            raise ValueError("未知的关系类型。")
        if lag_days < 0:
            raise ValueError("间隔不能小于 0。")
        with self.session() as connection:
            graph: dict[int, set[int]] = {}
            for row in connection.execute(
                """SELECT predecessor_template_id, successor_template_id
                   FROM process_template_dependencies"""
            ).fetchall():
                graph.setdefault(int(row["predecessor_template_id"]), set()).add(
                    int(row["successor_template_id"])
                )
            pending = [successor_template_id]
            reached: set[int] = set()
            while pending:
                current = int(pending.pop())
                if current in reached:
                    continue
                reached.add(current)
                pending.extend(graph.get(current, set()))
            if predecessor_template_id in reached:
                raise ValueError("该工艺关系会形成循环。")
            cursor = connection.execute(
                """INSERT INTO process_template_dependencies
                   (predecessor_template_id, successor_template_id, relation_type,
                    lag_days, lag_unit, constraint_category)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    predecessor_template_id,
                    successor_template_id,
                    relation_type,
                    lag_days,
                    self._validate_lag_unit(lag_unit),
                    self._validate_constraint_category(constraint_category),
                ),
            )
            return int(cursor.lastrowid)

    def delete_process_template_dependency(self, relation_id: int) -> None:
        with self.session() as connection:
            connection.execute(
                "DELETE FROM dependencies WHERE source_type = 'process_template' AND template_relation_id = ?",
                (relation_id,),
            )
            connection.execute(
                "DELETE FROM process_template_dependencies WHERE id = ?", (relation_id,)
            )

    def work_template_steps(self, template_id: int) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT * FROM work_template_steps WHERE template_id = ?
                   ORDER BY sort_order, id""",
                (template_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def add_work_template_step(
        self,
        template_id: int,
        name: str,
        task_type: str = "work",
        role: str = "execution",
        default_trade: str = "杂工",
        duration_minutes: int = 480,
        calendar_type: str = "working",
        has_noise: bool = False,
        notes: str = "",
        event_status: str = "derived",
    ) -> int:
        self._validate_work_template_step(name, task_type, role, duration_minutes, calendar_type)
        if task_type == "milestone":
            duration_minutes = 0
            if event_status not in {"derived", "pending"}:
                raise ValueError("模板事件默认状态只能是前序推导或待发生。")
        else:
            event_status = "derived"
        with self.session() as connection:
            if connection.execute(
                "SELECT 1 FROM process_templates WHERE id = ?", (template_id,)
            ).fetchone() is None:
                raise ValueError("所属作业模板不存在。")
            sort_order = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM work_template_steps WHERE template_id = ?",
                    (template_id,),
                ).fetchone()[0]
            )
            cursor = connection.execute(
                """INSERT INTO work_template_steps
                   (template_id, name, task_type, role, default_trade, duration_minutes,
                    calendar_type, has_noise, sort_order, notes, event_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    template_id, name.strip(), task_type, role,
                    normalized_trade(default_trade), int(duration_minutes), calendar_type,
                    int(bool(has_noise) and task_type != "milestone"), sort_order,
                    notes.strip(),
                    event_status,
                ),
            )
            return int(cursor.lastrowid)

    def update_work_template_step(
        self,
        step_id: int,
        name: str,
        task_type: str,
        role: str,
        default_trade: str,
        duration_minutes: int,
        calendar_type: str,
        has_noise: bool,
        notes: str = "",
        event_status: str = "derived",
    ) -> None:
        self._validate_work_template_step(name, task_type, role, duration_minutes, calendar_type)
        if task_type == "milestone":
            duration_minutes = 0
            if event_status not in {"derived", "pending"}:
                raise ValueError("模板事件默认状态只能是前序推导或待发生。")
        else:
            event_status = "derived"
        with self.session() as connection:
            connection.execute(
                """UPDATE work_template_steps
                   SET name = ?, task_type = ?, role = ?, default_trade = ?,
                       duration_minutes = ?, calendar_type = ?, has_noise = ?, notes = ?,
                       event_status = ?
                   WHERE id = ?""",
                (
                    name.strip(), task_type, role, normalized_trade(default_trade),
                    int(duration_minutes), calendar_type,
                    int(bool(has_noise) and task_type != "milestone"), notes.strip(),
                    event_status, step_id,
                ),
            )

    @staticmethod
    def _validate_work_template_step(
        name: str, task_type: str, role: str, duration_minutes: int, calendar_type: str
    ) -> None:
        if not name.strip():
            raise ValueError("步骤名称不能为空。")
        if task_type not in {"work", "logistics", "inspection", "wait", "milestone"}:
            raise ValueError("未知的步骤类型。")
        if role not in {"prerequisite", "execution", "postcondition", "output"}:
            raise ValueError("未知的步骤角色。")
        if calendar_type not in {"working", "elapsed"}:
            raise ValueError("未知的时间类型。")
        if task_type != "milestone" and int(duration_minutes) < 5:
            raise ValueError("非事件步骤不能少于 5 分钟。")

    def delete_work_template_step(self, step_id: int) -> None:
        with self.session() as connection:
            relation_ids = [
                int(row["id"])
                for row in connection.execute(
                    """SELECT id FROM work_template_step_dependencies
                       WHERE predecessor_step_id = ? OR successor_step_id = ?""",
                    (step_id, step_id),
                ).fetchall()
            ]
            if relation_ids:
                placeholders = ",".join("?" for _ in relation_ids)
                connection.execute(
                    f"DELETE FROM dependencies WHERE source_type = 'work_template' AND work_template_step_relation_id IN ({placeholders})",
                    relation_ids,
                )
            connection.execute(
                "DELETE FROM work_template_step_dependencies WHERE predecessor_step_id = ? OR successor_step_id = ?",
                (step_id, step_id),
            )
            connection.execute(
                "UPDATE tasks SET work_template_step_id = NULL WHERE work_template_step_id = ?",
                (step_id,),
            )
            connection.execute("DELETE FROM work_template_steps WHERE id = ?", (step_id,))

    def work_template_step_dependencies(self, template_id: int) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT r.*, p.name AS predecessor_name, s.name AS successor_name
                   FROM work_template_step_dependencies r
                   JOIN work_template_steps p ON p.id = r.predecessor_step_id
                   JOIN work_template_steps s ON s.id = r.successor_step_id
                   WHERE r.template_id = ? ORDER BY r.id""",
                (template_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def add_work_template_step_dependency(
        self,
        template_id: int,
        predecessor_step_id: int,
        successor_step_id: int,
        relation_type: str = "FS",
        lag_days: int = 0,
        lag_unit: str = "calendar_day",
        constraint_category: str = "process",
    ) -> int:
        if predecessor_step_id == successor_step_id:
            raise ValueError("前后步骤不能相同。")
        if relation_type not in {"FS", "SS", "FF", "SF"} or lag_days < 0:
            raise ValueError("步骤关系参数无效。")
        with self.session() as connection:
            step_rows = connection.execute(
                "SELECT id, template_id FROM work_template_steps WHERE id IN (?, ?)",
                (predecessor_step_id, successor_step_id),
            ).fetchall()
            if len(step_rows) != 2 or any(int(row["template_id"]) != template_id for row in step_rows):
                raise ValueError("两个步骤必须属于同一个作业模板。")
            graph: dict[int, set[int]] = {}
            for row in connection.execute(
                """SELECT predecessor_step_id, successor_step_id
                   FROM work_template_step_dependencies WHERE template_id = ?""",
                (template_id,),
            ).fetchall():
                graph.setdefault(int(row["predecessor_step_id"]), set()).add(
                    int(row["successor_step_id"])
                )
            pending = [successor_step_id]
            reached: set[int] = set()
            while pending:
                current = int(pending.pop())
                if current in reached:
                    continue
                reached.add(current)
                pending.extend(graph.get(current, set()))
            if predecessor_step_id in reached:
                raise ValueError("该步骤关系会形成循环。")
            cursor = connection.execute(
                """INSERT INTO work_template_step_dependencies
                   (template_id, predecessor_step_id, successor_step_id, relation_type,
                    lag_days, lag_unit, constraint_category)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    template_id, predecessor_step_id, successor_step_id, relation_type,
                    lag_days, self._validate_lag_unit(lag_unit),
                    self._validate_constraint_category(constraint_category),
                ),
            )
            return int(cursor.lastrowid)

    def delete_work_template_step_dependency(self, relation_id: int) -> None:
        with self.session() as connection:
            connection.execute(
                "DELETE FROM dependencies WHERE source_type = 'work_template' AND work_template_step_relation_id = ?",
                (relation_id,),
            )
            connection.execute(
                "DELETE FROM work_template_step_dependencies WHERE id = ?", (relation_id,)
            )

    def create_work_template_instance(
        self,
        template_id: int,
        instance_name: str,
        parent_id: int | None = None,
        constraint_start: str | None = None,
    ) -> int:
        clean_name = instance_name.strip()
        if not clean_name:
            raise ValueError("作业实例名称不能为空。")
        with self.session() as connection:
            template = connection.execute(
                "SELECT * FROM process_templates WHERE id = ?", (template_id,)
            ).fetchone()
            if template is None:
                raise ValueError("作业模板不存在。")
            steps = connection.execute(
                "SELECT * FROM work_template_steps WHERE template_id = ? ORDER BY sort_order, id",
                (template_id,),
            ).fetchall()
            if not steps:
                raise ValueError("该作业模板还没有步骤。")
            if parent_id is not None and constraint_start is None:
                parent = connection.execute(
                    "SELECT constraint_start FROM tasks WHERE id = ?", (parent_id,)
                ).fetchone()
                if parent:
                    constraint_start = parent["constraint_start"]
            next_order = int(connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM tasks"
            ).fetchone()[0])
            total_minutes = sum(int(step["duration_minutes"]) for step in steps)
            root_cursor = connection.execute(
                """INSERT INTO tasks
                   (parent_id, name, trade, duration, duration_days, duration_minutes,
                    task_type, calendar_type, priority, constraint_start, sort_order,
                    notes, process_template_id)
                   VALUES (?, ?, ?, ?, ?, ?, 'work', 'working', 50, ?, ?, ?, ?)""",
                (
                    parent_id, clean_name, normalized_trade(template["default_trade"]),
                    max(1, math.ceil(total_minutes / 480)), total_minutes / 480,
                    total_minutes, constraint_start, next_order,
                    f"由作业模板“{template['name']}”创建", template_id,
                ),
            )
            root_id = int(root_cursor.lastrowid)
            connection.execute(
                "UPDATE tasks SET template_instance_id = ? WHERE id = ?", (root_id, root_id)
            )
            for index, step in enumerate(steps, start=1):
                minutes = int(step["duration_minutes"])
                connection.execute(
                    """INSERT INTO tasks
                       (parent_id, name, trade, duration, duration_days, duration_minutes,
                        task_type, event_status, has_noise, calendar_type, priority,
                        constraint_start, sort_order, notes, process_template_id,
                        work_template_step_id, template_instance_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 50, ?, ?, ?, ?, ?, ?)""",
                    (
                        root_id, step["name"], step["default_trade"],
                        max(1, math.ceil(minutes / 480)), minutes / 480, minutes,
                        step["task_type"], step["event_status"], step["has_noise"],
                        step["calendar_type"],
                        constraint_start, index * 10, step["notes"], template_id,
                        step["id"], root_id,
                    ),
                )
        self.sync_work_template_dependencies()
        return root_id

    def sync_work_template_dependencies(self) -> int:
        with self.session() as connection:
            connection.execute("DELETE FROM dependencies WHERE source_type = 'work_template'")
            rules = connection.execute(
                "SELECT * FROM work_template_step_dependencies ORDER BY id"
            ).fetchall()
            task_rows = connection.execute(
                """SELECT id, template_instance_id, work_template_step_id
                   FROM tasks WHERE template_instance_id IS NOT NULL
                     AND work_template_step_id IS NOT NULL"""
            ).fetchall()
            task_by_instance_step = {
                (int(row["template_instance_id"]), int(row["work_template_step_id"])): int(row["id"])
                for row in task_rows
            }
            instances_by_template: dict[int, set[int]] = {}
            for row in connection.execute(
                """SELECT id, process_template_id FROM tasks
                   WHERE template_instance_id = id AND process_template_id IS NOT NULL"""
            ).fetchall():
                instances_by_template.setdefault(int(row["process_template_id"]), set()).add(int(row["id"]))
            generated = 0
            for rule in rules:
                for instance_id in instances_by_template.get(int(rule["template_id"]), set()):
                    predecessor_id = task_by_instance_step.get((instance_id, int(rule["predecessor_step_id"])))
                    successor_id = task_by_instance_step.get((instance_id, int(rule["successor_step_id"])))
                    if predecessor_id is None or successor_id is None:
                        continue
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO dependencies
                           (predecessor_id, successor_id, relation_type, lag_days, lag_unit,
                            constraint_category, source_type, work_template_step_relation_id)
                           VALUES (?, ?, ?, ?, ?, ?, 'work_template', ?)""",
                        (
                            predecessor_id, successor_id, rule["relation_type"], rule["lag_days"],
                            rule["lag_unit"], rule["constraint_category"], rule["id"],
                        ),
                    )
                    generated += max(0, cursor.rowcount)
            return generated

    def sync_process_template_dependencies(self) -> int:
        """Rebuild instance constraints inherited from the process library."""
        with self.session() as connection:
            connection.execute("DELETE FROM dependencies WHERE source_type = 'process_template'")
            rules = connection.execute(
                "SELECT * FROM process_template_dependencies ORDER BY id"
            ).fetchall()
            tasks = connection.execute(
                """SELECT id, parent_id, process_template_id, sort_order,
                          template_instance_id, work_template_step_id
                   FROM tasks WHERE process_template_id IS NOT NULL
                   ORDER BY sort_order, id"""
            ).fetchall()
            by_scope_and_template: dict[tuple[int | None, int], list[sqlite3.Row]] = {}
            for task in tasks:
                key = (task["parent_id"], int(task["process_template_id"]))
                by_scope_and_template.setdefault(key, []).append(task)

            generated = 0
            scopes = {task["parent_id"] for task in tasks}
            for rule in rules:
                for scope in scopes:
                    predecessors = by_scope_and_template.get(
                        (scope, int(rule["predecessor_template_id"])), []
                    )
                    successors = by_scope_and_template.get(
                        (scope, int(rule["successor_template_id"])), []
                    )
                    for predecessor, successor in zip(predecessors, successors):
                        predecessor_children = connection.execute(
                            "SELECT id FROM tasks WHERE parent_id = ? ORDER BY sort_order, id",
                            (predecessor["id"],),
                        ).fetchall()
                        successor_children = connection.execute(
                            "SELECT id FROM tasks WHERE parent_id = ? ORDER BY sort_order, id",
                            (successor["id"],),
                        ).fetchall()
                        is_workflow_pair = (
                            predecessor["template_instance_id"] == predecessor["id"]
                            and successor["template_instance_id"] == successor["id"]
                        )
                        pairs = (
                            [(predecessor_children[-1], successor_children[0])]
                            if is_workflow_pair and predecessor_children and successor_children
                            else
                            list(zip(predecessor_children, successor_children))
                            if predecessor_children and successor_children
                            and len(predecessor_children) == len(successor_children)
                            else [(predecessor, successor)]
                            if not predecessor_children and not successor_children
                            else []
                        )
                        for left, right in pairs:
                            cursor = connection.execute(
                                """INSERT OR IGNORE INTO dependencies
                                   (predecessor_id, successor_id, relation_type, lag_days,
                                    lag_unit, constraint_category, source_type,
                                    template_relation_id)
                                   VALUES (?, ?, ?, ?, ?, ?, 'process_template', ?)""",
                                (
                                    left["id"], right["id"], rule["relation_type"],
                                    rule["lag_days"], rule["lag_unit"],
                                    rule["constraint_category"], rule["id"],
                                ),
                            )
                            generated += max(0, cursor.rowcount)
            return generated

    def settings(self) -> dict:
        with self.session() as connection:
            row = connection.execute("SELECT * FROM schedule_settings WHERE id = 1").fetchone()
            return dict(row)

    def update_settings(
        self,
        plan_name: str,
        project_start: str,
        weekend_working: bool = False,
        ignore_noise_restrictions: bool = False,
        work_sessions: str = "08:00-12:00,13:00-17:00",
        required_finish: str | None = None,
        status_date: str | None = None,
        optimization_goal: str = "stable",
        overtime_lunch: bool = False,
        overtime_night: bool = False,
        overtime_holiday: bool = False,
        crew_max_daily_hours: float = 8,
        crew_max_consecutive_days: int = 0,
        resource_leveling_mode: str = "delay",
        auto_recalculate: bool = True,
    ) -> None:
        if optimization_goal not in {"earliest", "stable", "continuity"}:
            raise ValueError("未知的排程优化目标。")
        if resource_leveling_mode not in {"delay", "allow_overload"}:
            raise ValueError("未知的资源平衡方式。")
        with self.session() as connection:
            connection.execute(
                """UPDATE schedule_settings
                   SET plan_name = ?, project_start = ?, weekend_working = ?,
                       ignore_noise_restrictions = ?, work_sessions = ?,
                       required_finish = ?, status_date = ?, optimization_goal = ?,
                       overtime_lunch = ?, overtime_night = ?, overtime_holiday = ?,
                       crew_max_daily_hours = ?, crew_max_consecutive_days = ?,
                       resource_leveling_mode = ?, auto_recalculate = ? WHERE id = 1""",
                (
                    plan_name,
                    project_start,
                    int(weekend_working),
                    int(ignore_noise_restrictions),
                    work_sessions,
                    required_finish,
                    status_date,
                    optimization_goal,
                    int(overtime_lunch),
                    int(overtime_night),
                    int(overtime_holiday),
                    max(0.01, float(crew_max_daily_hours)),
                    max(0, int(crew_max_consecutive_days)),
                    resource_leveling_mode,
                    int(auto_recalculate),
                ),
            )

    def tasks(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY sort_order, id").fetchall()
            items = [self._task_dict(row) for row in rows]
            self._attach_process_templates(connection, items)
            self._attach_resources(connection, items)
            children: dict[int, list[dict]] = {}
            for item in items:
                if item["parent_id"] is not None:
                    children.setdefault(int(item["parent_id"]), []).append(item)
            for item in items:
                if item["id"] in children:
                    item["duration"] = sum(float(child["duration"]) for child in children[item["id"]])
                    item["duration_minutes"] = sum(
                        int(child.get("duration_minutes") or 0)
                        for child in children[item["id"]]
                    )
            return items

    def task(self, task_id: int) -> dict | None:
        with self.session() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            item = self._task_dict(row)
            self._attach_process_templates(connection, [item])
            self._attach_resources(connection, [item])
            children = connection.execute(
                "SELECT duration, duration_days, duration_minutes FROM tasks WHERE parent_id = ?",
                (task_id,),
            ).fetchall()
            if children:
                item["duration_minutes"] = sum(
                    int(child["duration_minutes"] or round(float(child["duration_days"] or child["duration"]) * 480))
                    for child in children
                )
                item["duration"] = item["duration_minutes"] / 480
            return item

    @staticmethod
    def _task_dict(row: sqlite3.Row) -> dict:
        item = dict(row)
        minutes = item.get("duration_minutes")
        item["duration"] = (
            int(minutes) / 480
            if minutes is not None
            else float(item.get("duration_days") or item["duration"])
        )
        item["crew_required"] = item.get("task_type", "work") in {"work", "logistics"}
        item.setdefault("resources", [])
        item.setdefault("process_name", "")
        return item

    @staticmethod
    def _attach_process_templates(
        connection: sqlite3.Connection, tasks: list[dict]
    ) -> None:
        template_ids = sorted(
            {
                int(task["process_template_id"])
                for task in tasks
                if task.get("process_template_id") is not None
            }
        )
        names: dict[int, str] = {}
        step_names: dict[int, str] = {}
        if template_ids:
            placeholders = ",".join("?" for _ in template_ids)
            rows = connection.execute(
                f"SELECT id, name FROM process_templates WHERE id IN ({placeholders})",
                template_ids,
            ).fetchall()
            names = {int(row["id"]): str(row["name"]) for row in rows}
        step_ids = sorted(
            {
                int(task["work_template_step_id"])
                for task in tasks
                if task.get("work_template_step_id") is not None
            }
        )
        if step_ids:
            placeholders = ",".join("?" for _ in step_ids)
            rows = connection.execute(
                f"SELECT id, name FROM work_template_steps WHERE id IN ({placeholders})",
                step_ids,
            ).fetchall()
            step_names = {int(row["id"]): str(row["name"]) for row in rows}
        for task in tasks:
            template_id = task.get("process_template_id")
            task["process_name"] = (
                names.get(int(template_id), "") if template_id is not None else ""
            )
            template_name = task["process_name"]
            instance_id = task.get("template_instance_id")
            step_id = task.get("work_template_step_id")
            if not template_name:
                task["template_source"] = ""
            elif instance_id is not None and int(instance_id) == int(task["id"]):
                task["template_source"] = f"模板实例 · {template_name}"
            elif instance_id is not None and step_id is not None:
                step_name = step_names.get(int(step_id), "")
                task["template_source"] = (
                    f"继承步骤 · {template_name} / {step_name}"
                    if step_name else f"继承步骤 · {template_name}"
                )
            else:
                task["template_source"] = f"继承工艺 · {template_name}"

    @staticmethod
    def _attach_resources(connection: sqlite3.Connection, tasks: list[dict]) -> None:
        if not tasks:
            return
        ids = [int(task["id"]) for task in tasks]
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""SELECT tr.task_id, tr.demand_amount,
                       r.id, r.name, r.resource_type, r.capacity, r.enabled, r.notes,
                       ps.id AS path_segment_id, ps.is_workface,
                       ps.available_from, ps.available_until,
                       ps.activation_event_id, ps.deactivation_event_id
                FROM task_resource_demands tr
                JOIN resources r ON r.id = tr.resource_id
                LEFT JOIN path_segments ps ON ps.resource_id = r.id
                WHERE tr.task_id IN ({placeholders})
                ORDER BY r.resource_type, r.name""",
            ids,
        ).fetchall()
        by_task: dict[int, list[dict]] = {}
        skill_rows = connection.execute(
            f"""SELECT resource_id, trade FROM crew_skills
                WHERE resource_id IN (
                    SELECT resource_id FROM task_resource_demands
                    WHERE task_id IN ({placeholders})
                )
                ORDER BY resource_id, trade""",
            ids,
        ).fetchall()
        skills_by_resource: dict[int, list[str]] = {}
        for skill_row in skill_rows:
            skills_by_resource.setdefault(int(skill_row["resource_id"]), []).append(
                str(skill_row["trade"])
            )
        path_calendar_rows = connection.execute(
            f"""SELECT ps.resource_id, pc.exception_date, pc.is_available
                FROM path_segment_calendar_exceptions pc
                JOIN path_segments ps ON ps.id = pc.segment_id
                WHERE ps.resource_id IN (
                    SELECT resource_id FROM task_resource_demands
                    WHERE task_id IN ({placeholders})
                )
                ORDER BY ps.resource_id, pc.exception_date""",
            ids,
        ).fetchall()
        availability_by_resource: dict[int, dict[str, bool]] = {}
        for calendar_row in path_calendar_rows:
            availability_by_resource.setdefault(int(calendar_row["resource_id"]), {})[
                str(calendar_row["exception_date"])
            ] = bool(calendar_row["is_available"])
        for row in rows:
            resource = dict(row)
            task_id = int(resource.pop("task_id"))
            resource["skills"] = skills_by_resource.get(int(resource["id"]), [])
            resource["availability_exceptions"] = availability_by_resource.get(
                int(resource["id"]), {}
            )
            by_task.setdefault(task_id, []).append(resource)
        for task in tasks:
            task["resources"] = by_task.get(int(task["id"]), [])
        calculated_crew_ids = {
            int(task["calculated_crew_id"])
            for task in tasks
            if task.get("calculated_crew_id") is not None
        }
        crew_by_id: dict[int, dict] = {}
        if calculated_crew_ids:
            crew_placeholders = ",".join("?" for _ in calculated_crew_ids)
            crew_rows = connection.execute(
                f"""SELECT id, name, resource_type, capacity, enabled, notes
                    FROM resources WHERE id IN ({crew_placeholders})""",
                sorted(calculated_crew_ids),
            ).fetchall()
            crew_by_id = {int(row["id"]): dict(row) for row in crew_rows}
            crew_calendar_rows = connection.execute(
                f"""SELECT resource_id, exception_date, is_working
                    FROM crew_calendar_exceptions
                    WHERE resource_id IN ({crew_placeholders})
                    ORDER BY resource_id, exception_date""",
                sorted(calculated_crew_ids),
            ).fetchall()
            for row in crew_calendar_rows:
                crew_by_id[int(row["resource_id"])].setdefault(
                    "availability_exceptions", {}
                )[str(row["exception_date"])] = bool(row["is_working"])
            for crew in crew_by_id.values():
                crew.setdefault("availability_exceptions", {})
        for task in tasks:
            crew_id = task.get("calculated_crew_id")
            task["assigned_crew"] = (
                crew_by_id.get(int(crew_id)) if crew_id is not None else None
            )

    def add_task(
        self,
        name: str,
        duration: float,
        parent_id: int | None,
        constraint_start: str | None,
        notes: str,
        trade: str = "杂工",
        apply_trade_to_children: bool = False,
        task_type: str = "work",
        calendar_type: str = "working",
        priority: int = 50,
        resource_ids: list[int] | None = None,
        event_status: str = "derived",
        event_time: str | None = None,
        has_noise: bool = False,
        process_template_id: int | None = None,
        spatial_occupancy_mode: str = "reduce",
        traffic_reduction: float = 1,
    ) -> int:
        duration_minutes = round(float(duration) * 480)
        if task_type == "milestone":
            duration_minutes = 0
        elif duration_minutes <= 0:
            raise ValueError("非里程碑活动的工期必须大于0。")
        selected_trade = normalized_trade(trade)
        event_status, event_time = self._normalise_event_fields(
            task_type, event_status, event_time
        )
        if spatial_occupancy_mode not in {"normal", "reduce", "close"}:
            raise ValueError("未知的空间占用方式。")
        with self.session() as connection:
            if parent_id is not None:
                parent_constraint = connection.execute(
                    "SELECT constraint_start, process_template_id FROM tasks WHERE id = ?",
                    (parent_id,),
                ).fetchone()
                if parent_constraint is not None:
                    if constraint_start is None:
                        constraint_start = parent_constraint["constraint_start"]
                    if process_template_id is None:
                        process_template_id = parent_constraint["process_template_id"]
            self._validate_process_template(connection, process_template_id)
            self._validate_task_resources(connection, selected_trade, resource_ids or [])
            next_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM tasks"
            ).fetchone()[0]
            cursor = connection.execute(
                """INSERT INTO tasks
                   (name, trade, duration, duration_days, duration_minutes, task_type,
                    event_status, event_time, has_noise, calendar_type, priority, parent_id,
                    constraint_start, notes, sort_order, process_template_id,
                    spatial_occupancy_mode, traffic_reduction)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    selected_trade,
                    max(1, math.ceil(float(duration))),
                    duration_minutes / 480,
                    duration_minutes,
                    task_type,
                    event_status,
                    event_time,
                    int(bool(has_noise) and task_type != "milestone"),
                    calendar_type,
                    int(priority),
                    parent_id,
                    constraint_start,
                    notes,
                    next_order,
                    process_template_id,
                    spatial_occupancy_mode,
                    max(0, float(traffic_reduction)),
                ),
            )
            task_id = int(cursor.lastrowid)
            self._replace_task_resources(connection, task_id, resource_ids or [])
            return task_id

    def update_task(
        self,
        task_id: int,
        name: str,
        duration: float,
        parent_id: int | None,
        constraint_start: str | None,
        notes: str,
        trade: str = "杂工",
        apply_trade_to_children: bool = False,
        task_type: str = "work",
        calendar_type: str = "working",
        priority: int = 50,
        resource_ids: list[int] | None = None,
        event_status: str = "derived",
        event_time: str | None = None,
        has_noise: bool = False,
        process_template_id: int | None = None,
        spatial_occupancy_mode: str = "reduce",
        traffic_reduction: float = 1,
    ) -> None:
        if parent_id == task_id or parent_id in self.descendant_ids(task_id):
            raise ValueError("任务不能移动到自身或自己的子任务下。")
        duration_minutes = round(float(duration) * 480)
        if task_type == "milestone":
            duration_minutes = 0
        elif duration_minutes <= 0:
            raise ValueError("非里程碑活动的工期必须大于0。")
        selected_trade = normalized_trade(trade)
        event_status, event_time = self._normalise_event_fields(
            task_type, event_status, event_time
        )
        if spatial_occupancy_mode not in {"normal", "reduce", "close"}:
            raise ValueError("未知的空间占用方式。")
        with self.session() as connection:
            self._validate_process_template(connection, process_template_id)
            effective_resource_ids = resource_ids
            if effective_resource_ids is None:
                effective_resource_ids = [
                    int(row["resource_id"])
                    for row in connection.execute(
                        "SELECT resource_id FROM task_resource_demands WHERE task_id = ?",
                        (task_id,),
                    ).fetchall()
                ]
            self._validate_task_resources(
                connection, selected_trade, effective_resource_ids
            )
            children = connection.execute(
                "SELECT id, duration, duration_days FROM tasks WHERE parent_id = ? ORDER BY sort_order, id",
                (task_id,),
            ).fetchall()
            if children and apply_trade_to_children:
                descendant_rows = connection.execute(
                    """WITH RECURSIVE descendants(id) AS (
                           SELECT id FROM tasks WHERE parent_id = ?
                           UNION ALL
                           SELECT t.id FROM tasks t JOIN descendants d ON t.parent_id = d.id
                       )
                       SELECT id FROM descendants""",
                    (task_id,),
                ).fetchall()
                for descendant in descendant_rows:
                    descendant_resources = [
                        int(row["resource_id"])
                        for row in connection.execute(
                            "SELECT resource_id FROM task_resource_demands WHERE task_id = ?",
                            (int(descendant["id"]),),
                        ).fetchall()
                    ]
                    self._validate_task_resources(
                        connection, selected_trade, descendant_resources
                    )
            child_durations: list[float] = []
            if children:
                current_total = sum(
                    float(child["duration_days"] or child["duration"]) for child in children
                )
                if abs(float(duration) - current_total) > 1e-6:
                    child_durations = self._distribute_duration(duration, len(children))
            connection.execute(
                """UPDATE tasks
                   SET name = ?, trade = ?, duration = ?, duration_days = ?, duration_minutes = ?,
                       task_type = ?, event_status = ?, event_time = ?, has_noise = ?, calendar_type = ?,
                       priority = ?, parent_id = ?, constraint_start = ?,
                       notes = ?, process_template_id = ?, spatial_occupancy_mode = ?,
                       traffic_reduction = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    name,
                    selected_trade,
                    max(1, math.ceil(float(duration))),
                    duration_minutes / 480,
                    duration_minutes,
                    task_type,
                    event_status,
                    event_time,
                    int(bool(has_noise) and task_type != "milestone"),
                    calendar_type,
                    int(priority),
                    parent_id,
                    constraint_start,
                    notes,
                    process_template_id,
                    spatial_occupancy_mode,
                    max(0, float(traffic_reduction)),
                    task_id,
                ),
            )
            for child, child_duration in zip(children, child_durations):
                child_minutes = round(child_duration * 480)
                connection.execute(
                    """UPDATE tasks SET duration = ?, duration_days = ?, duration_minutes = ?,
                       updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (
                        max(1, math.ceil(child_duration)),
                        child_minutes / 480,
                        child_minutes,
                        child["id"],
                    ),
                )
            if children:
                connection.execute(
                    """WITH RECURSIVE descendants(id) AS (
                           SELECT id FROM tasks WHERE parent_id = ?
                           UNION ALL
                           SELECT t.id FROM tasks t
                           JOIN descendants d ON t.parent_id = d.id
                       )
                       UPDATE tasks
                       SET constraint_start = ?, process_template_id = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id IN (SELECT id FROM descendants)""",
                    (task_id, constraint_start, process_template_id),
                )
            if children and apply_trade_to_children:
                connection.execute(
                    """WITH RECURSIVE descendants(id) AS (
                           SELECT id FROM tasks WHERE parent_id = ?
                           UNION ALL
                           SELECT t.id FROM tasks t JOIN descendants d ON t.parent_id = d.id
                       )
                       UPDATE tasks SET trade = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id IN (SELECT id FROM descendants)""",
                    (task_id, selected_trade),
                )
            if resource_ids is not None:
                self._replace_task_resources(connection, task_id, resource_ids)

    @staticmethod
    def _validate_process_template(
        connection: sqlite3.Connection, process_template_id: int | None
    ) -> None:
        if process_template_id is None:
            return
        exists = connection.execute(
            "SELECT 1 FROM process_templates WHERE id = ?", (process_template_id,)
        ).fetchone()
        if exists is None:
            raise ValueError("所选工艺不存在或已被删除。")

    @staticmethod
    def _normalise_event_fields(
        task_type: str, event_status: str, event_time: str | None
    ) -> tuple[str, str | None]:
        if task_type != "milestone":
            return "derived", None
        if event_status not in {"derived", "pending", "planned", "occurred"}:
            raise ValueError("未知的事件状态。")
        clean_time = str(event_time).strip() if event_time else None
        if event_status in {"planned", "occurred"} and not clean_time:
            raise ValueError("预计发生和已发生事件必须填写发生时间。")
        if event_status in {"derived", "pending"}:
            clean_time = None
        if clean_time:
            try:
                datetime.fromisoformat(clean_time)
            except ValueError as error:
                raise ValueError("事件时间格式无效。") from error
        return event_status, clean_time

    @staticmethod
    def _replace_task_resources(
        connection: sqlite3.Connection, task_id: int, resource_ids: list[int]
    ) -> None:
        connection.execute(
            "DELETE FROM task_resource_demands WHERE task_id = ?", (task_id,)
        )
        unique_ids = sorted({int(resource_id) for resource_id in resource_ids})
        connection.executemany(
            """INSERT INTO task_resource_demands(task_id, resource_id, demand_amount)
               VALUES (?, ?, 1)""",
            [(task_id, resource_id) for resource_id in unique_ids],
        )

    @staticmethod
    def _validate_task_resources(
        connection: sqlite3.Connection, trade: str, resource_ids: list[int]
    ) -> None:
        unique_ids = sorted({int(resource_id) for resource_id in resource_ids})
        if not unique_ids:
            return
        placeholders = ",".join("?" for _ in unique_ids)
        rows = connection.execute(
            f"SELECT id, name, resource_type FROM resources WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchall()
        if len(rows) != len(unique_ids):
            raise ValueError("所选资源不存在或已被删除。")
        crew_rows = [row for row in rows if row["resource_type"] == "crew"]
        if crew_rows:
            raise ValueError("执行班组由排程器自动计算，不能作为任务占用资源手动指定。")

    @staticmethod
    def _distribute_duration(total_duration: float, segment_count: int) -> list[float]:
        segment_duration = float(total_duration) / segment_count
        if segment_duration < 1 / 96:
            raise ValueError(
                f"总工期过短：{segment_count} 个施工段每段不能少于 5 分钟。"
            )
        durations = [round(segment_duration, 6) for _ in range(segment_count)]
        durations[-1] = round(float(total_duration) - sum(durations[:-1]), 6)
        return durations

    def delete_task(self, task_id: int) -> None:
        with self.session() as connection:
            connection.execute(
                "UPDATE path_segments SET activation_event_id = NULL WHERE activation_event_id = ?",
                (task_id,),
            )
            connection.execute(
                "UPDATE path_segments SET deactivation_event_id = NULL WHERE deactivation_event_id = ?",
                (task_id,),
            )
            connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def move_task(
        self,
        task_id: int,
        new_parent_id: int | None,
        before_task_id: int | None = None,
    ) -> None:
        """Move a task in the hierarchy and renumber affected sibling groups."""
        with self.session() as connection:
            task = connection.execute(
                "SELECT id, parent_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise ValueError("要移动的任务不存在。")
            old_parent_id = task["parent_id"]

            if new_parent_id is not None:
                if new_parent_id == task_id:
                    raise ValueError("任务不能移动到自身下面。")
                parent = connection.execute(
                    "SELECT id FROM tasks WHERE id = ?", (new_parent_id,)
                ).fetchone()
                if parent is None:
                    raise ValueError("目标任务不存在。")
                descendant = connection.execute(
                    """WITH RECURSIVE descendants(id) AS (
                           SELECT id FROM tasks WHERE parent_id = ?
                           UNION ALL
                           SELECT t.id FROM tasks t JOIN descendants d ON t.parent_id = d.id
                       ) SELECT 1 FROM descendants WHERE id = ?""",
                    (task_id, new_parent_id),
                ).fetchone()
                if descendant:
                    raise ValueError("任务不能移动到自己的下级任务中。")
                target_dependencies = connection.execute(
                    """SELECT COUNT(*) FROM dependencies
                       WHERE predecessor_id = ? OR successor_id = ?""",
                    (new_parent_id, new_parent_id),
                ).fetchone()[0]
                if target_dependencies:
                    raise ValueError(
                        "目标任务已有任务关系，不能直接变成汇总任务。请先调整该任务的关系。"
                    )

            connection.execute(
                """UPDATE tasks SET parent_id = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (new_parent_id, task_id),
            )
            if new_parent_id is not None:
                inherited_constraint = connection.execute(
                    "SELECT constraint_start FROM tasks WHERE id = ?", (new_parent_id,)
                ).fetchone()["constraint_start"]
                if inherited_constraint:
                    connection.execute(
                        """WITH RECURSIVE moved_tree(id) AS (
                               SELECT ?
                               UNION ALL
                               SELECT t.id FROM tasks t
                               JOIN moved_tree m ON t.parent_id = m.id
                           )
                           UPDATE tasks
                           SET constraint_start = ?, updated_at = CURRENT_TIMESTAMP
                           WHERE id IN (SELECT id FROM moved_tree)""",
                        (task_id, inherited_constraint),
                    )

            def renumber(parent_id: int | None, moving_id: int | None = None) -> None:
                siblings = [
                    int(row["id"])
                    for row in connection.execute(
                        """SELECT id FROM tasks WHERE parent_id IS ? AND id <> ?
                           ORDER BY sort_order, id""",
                        (parent_id, moving_id if moving_id is not None else -1),
                    ).fetchall()
                ]
                if moving_id is not None:
                    if before_task_id in siblings:
                        siblings.insert(siblings.index(before_task_id), moving_id)
                    else:
                        siblings.append(moving_id)
                for index, sibling_id in enumerate(siblings, start=1):
                    connection.execute(
                        "UPDATE tasks SET sort_order = ? WHERE id = ?",
                        (index * 10, sibling_id),
                    )

            if old_parent_id != new_parent_id:
                renumber(old_parent_id)
            renumber(new_parent_id, task_id)

    def descendant_ids(self, task_id: int) -> set[int]:
        with self.session() as connection:
            rows = connection.execute(
                """WITH RECURSIVE descendants(id) AS (
                       SELECT id FROM tasks WHERE parent_id = ?
                       UNION ALL
                       SELECT t.id FROM tasks t JOIN descendants d ON t.parent_id = d.id
                   ) SELECT id FROM descendants""",
                (task_id,),
            ).fetchall()
            return {int(row[0]) for row in rows}

    def leaf_tasks(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT t.* FROM tasks t
                   WHERE NOT EXISTS (SELECT 1 FROM tasks c WHERE c.parent_id = t.id)
                   ORDER BY t.sort_order, t.id"""
            ).fetchall()
            items = [self._task_dict(row) for row in rows]
            self._attach_process_templates(connection, items)
            self._attach_resources(connection, items)
            return items

    def direct_children(self, parent_id: int) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE parent_id = ? ORDER BY sort_order, id",
                (parent_id,),
            ).fetchall()
            items = [self._task_dict(row) for row in rows]
            self._attach_process_templates(connection, items)
            self._attach_resources(connection, items)
            return items

    def flow_summaries(self) -> list[dict]:
        tasks = self.tasks()
        child_ids = {task["parent_id"] for task in tasks if task["parent_id"] is not None}
        summaries: list[dict] = []
        for task in tasks:
            children = [row for row in tasks if row["parent_id"] == task["id"]]
            if len(children) >= 2 and not any(child["id"] in child_ids for child in children):
                item = dict(task)
                item["segment_count"] = len(children)
                summaries.append(item)
        return summaries

    def split_task(self, task_id: int, segment_count: int) -> tuple[list[int], list[float]]:
        with self.session() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("任务不存在。")
            if connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE parent_id = ?", (task_id,)
            ).fetchone()[0]:
                raise ValueError("该任务已经包含施工段。")
            if segment_count < 2:
                raise ValueError("施工段数量不能少于 2。")
            total_duration = float(task["duration_days"] or task["duration"])
            durations = self._distribute_duration(total_duration, segment_count)
            inherited_resources = [
                int(row["resource_id"])
                for row in connection.execute(
                    "SELECT resource_id FROM task_resource_demands WHERE task_id = ?",
                    (task_id,),
                ).fetchall()
            ]
            child_ids: list[int] = []
            for index, duration in enumerate(durations, start=1):
                duration_minutes = round(duration * 480)
                cursor = connection.execute(
                    """INSERT INTO tasks
                       (parent_id, name, trade, duration, duration_days, duration_minutes,
                        task_type, has_noise, calendar_type, priority, constraint_start,
                        process_template_id, spatial_occupancy_mode, traffic_reduction,
                        sort_order, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')""",
                    (
                        task_id,
                        f"{task['name']}（{index}段）",
                        normalized_trade(task["trade"]),
                        max(1, math.ceil(duration)),
                        duration_minutes / 480,
                        duration_minutes,
                        task["task_type"],
                        task["has_noise"],
                        task["calendar_type"],
                        task["priority"],
                        task["constraint_start"],
                        task["process_template_id"],
                        task["spatial_occupancy_mode"],
                        task["traffic_reduction"],
                        index * 10,
                    ),
                )
                child_id = int(cursor.lastrowid)
                child_ids.append(child_id)
                self._replace_task_resources(connection, child_id, inherited_resources)

            # The original task becomes a summary and can no longer participate
            # directly in scheduling. Preserve its external relationships by
            # moving incoming links to the first segment and outgoing links to
            # the last segment. Relation type and lag stay unchanged.
            connection.execute(
                "UPDATE dependencies SET successor_id = ? WHERE successor_id = ?",
                (child_ids[0], task_id),
            )
            connection.execute(
                "UPDATE dependencies SET predecessor_id = ? WHERE predecessor_id = ?",
                (child_ids[-1], task_id),
            )

            return child_ids, durations

    def create_flow_relations(
        self,
        predecessor_summary_id: int,
        successor_summary_id: int,
        minimum_lag: int,
        calendar=None,
    ) -> tuple[list[int], int]:
        if predecessor_summary_id == successor_summary_id:
            raise ValueError("前后工序不能相同。")
        if minimum_lag < 0:
            raise ValueError("最小间隔不能小于 0。")
        predecessor_segments = self.direct_children(predecessor_summary_id)
        successor_segments = self.direct_children(successor_summary_id)
        if len(predecessor_segments) < 2 or len(predecessor_segments) != len(successor_segments):
            raise ValueError("两道工序必须具有相同且不少于 2 个施工段。")

        predecessor_ids_with_children = {
            row["parent_id"] for row in self.tasks() if row["parent_id"] is not None
        }
        if any(row["id"] in predecessor_ids_with_children for row in predecessor_segments):
            raise ValueError("前道工序只能包含一层施工段。")
        if any(row["id"] in predecessor_ids_with_children for row in successor_segments):
            raise ValueError("后道工序只能包含一层施工段。")

        relation_ids: list[int] = []
        with self.session() as connection:
            for predecessor, successor in zip(predecessor_segments, successor_segments):
                cursor = connection.execute(
                    """INSERT INTO dependencies
                       (predecessor_id, successor_id, relation_type, lag_days, lag_unit)
                       VALUES (?, ?, 'FS', ?, 'calendar_day')""",
                    (predecessor["id"], successor["id"], minimum_lag),
                )
                relation_ids.append(int(cursor.lastrowid))
        return relation_ids, 0

    def has_dependencies(self, task_id: int) -> bool:
        with self.session() as connection:
            count = connection.execute(
                """SELECT COUNT(*) FROM dependencies
                   WHERE predecessor_id = ? OR successor_id = ?""",
                (task_id, task_id),
            ).fetchone()[0]
            return bool(count)

    def constraint_overview(self) -> list[dict]:
        """Return every first-version scheduling constraint in one readable list."""
        tasks = self.tasks()
        parent_ids = {
            int(task["parent_id"])
            for task in tasks
            if task.get("parent_id") is not None
        }
        rows: list[dict] = []
        for relation in self.dependencies():
            unit = "工作日" if relation.get("lag_unit") == "workday" else "自然日"
            rows.append(
                {
                    "kind": "temporal",
                    "category": relation.get("constraint_category", "process"),
                    "subject": relation["successor_name"],
                    "rule": (
                        f"{relation['predecessor_name']} → {relation['relation_type']}"
                        f" + {relation['lag_days']} {unit} → {relation['successor_name']}"
                    ),
                    "status": "硬约束",
                }
            )
        for task in tasks:
            if int(task["id"]) in parent_ids:
                continue
            if task.get("constraint_start"):
                rows.append(
                    {
                        "kind": "temporal",
                        "category": "date",
                        "subject": task["name"],
                        "rule": f"不得早于 {task['constraint_start']} 开始",
                        "status": "硬约束",
                    }
                )
            if task.get("has_noise"):
                rows.append(
                    {
                        "kind": "calendar",
                        "category": "noise",
                        "subject": task["name"],
                        "rule": "产生噪音，必须按工作日历执行并避开休息日",
                        "status": "硬约束",
                    }
                )
            if task.get("task_type") == "milestone":
                event_status = task.get("event_status", "derived")
                status_labels = {
                    "derived": "由关系计算",
                    "planned": f"预计 {task.get('event_time') or ''}",
                    "occurred": f"已发生 {task.get('event_time') or ''}",
                    "pending": "未满足，阻断后续",
                }
                rows.append(
                    {
                        "kind": "state",
                        "category": "event",
                        "subject": task["name"],
                        "rule": "事件状态成为后续任务的开始门槛",
                        "status": status_labels.get(event_status, event_status),
                    }
                )
            if task.get("crew_required"):
                rows.append(
                    {
                        "kind": "resource",
                        "category": "crew",
                        "subject": task["name"],
                        "rule": f"需要具备“{task.get('trade', '杂工')}”能力的班组",
                        "status": "排程时自动分配",
                    }
                )
            for resource in task.get("resources", []):
                resource_type = str(resource.get("resource_type", "resource"))
                demand = float(resource.get("demand_amount", 1))
                occupancy_note = ""
                if resource_type == "access" and task.get("task_type") != "logistics":
                    mode = str(task.get("spatial_occupancy_mode", "reduce"))
                    if mode == "normal":
                        demand = 0
                        occupancy_note = "（正常共享）"
                    elif mode == "close":
                        demand = float(resource.get("capacity", 1))
                        occupancy_note = "（完全封闭）"
                    else:
                        demand = float(task.get("traffic_reduction", 1))
                        occupancy_note = "（部分占道）"
                rows.append(
                    {
                        "kind": (
                            "spatial"
                            if resource_type in {"workface", "access"}
                            else "resource"
                        ),
                        "category": resource_type,
                        "subject": task["name"],
                        "rule": (
                            f"占用 {resource['name']}{occupancy_note}，需求 {demand:g}"
                            f" / 容量 {resource.get('capacity', 1):g}"
                        ),
                        "status": "可用" if resource.get("enabled", 1) else "资源已停用",
                    }
                )
        event_names = {int(task["id"]): task["name"] for task in tasks}
        for segment in self.path_segments():
            conditions: list[str] = []
            if segment.get("available_from"):
                conditions.append(f"{segment['available_from']} 起开放")
            if segment.get("available_until"):
                conditions.append(f"{segment['available_until']} 后停用")
            if segment.get("activation_event_id"):
                conditions.append(
                    f"“{event_names.get(int(segment['activation_event_id']), '开放事件')}”后开放"
                )
            if segment.get("deactivation_event_id"):
                conditions.append(
                    f"“{event_names.get(int(segment['deactivation_event_id']), '停用事件')}”时停用"
                )
            if segment.get("is_workface"):
                conditions.insert(0, "兼作施工任务作业面")
            if conditions:
                rows.append(
                    {
                        "kind": "spatial",
                        "category": "dynamic_path",
                        "subject": segment["name"],
                        "rule": "；".join(conditions),
                        "status": "可用" if segment.get("enabled", 1) else "已停用",
                    }
                )
        for crew in self.resources("crew"):
            for exception in self.crew_calendar_exceptions(int(crew["id"])):
                rows.append(
                    {
                        "kind": "calendar",
                        "category": "crew_calendar",
                        "subject": crew["name"],
                        "rule": f"{exception['exception_date']} "
                        + ("班组上班" if exception["is_working"] else "班组休息"),
                        "status": exception["name"] or "手动设置",
                    }
                )
        return rows

    def dependencies(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT d.*, p.name AS predecessor_name, s.name AS successor_name
                   FROM dependencies d
                   JOIN tasks p ON p.id = d.predecessor_id
                   JOIN tasks s ON s.id = d.successor_id
                   ORDER BY d.id"""
            ).fetchall()
            return [dict(row) for row in rows]

    def dependency(self, dependency_id: int) -> dict | None:
        with self.session() as connection:
            row = connection.execute(
                "SELECT * FROM dependencies WHERE id = ?", (dependency_id,)
            ).fetchone()
            return dict(row) if row else None

    def add_dependency(
        self,
        predecessor_id: int,
        successor_id: int,
        relation_type: str,
        lag_days: int = 0,
        constraint_category: str = "process",
        lag_unit: str = "calendar_day",
    ) -> int:
        if lag_days < 0:
            raise ValueError("间隔自然日不能小于 0。")
        with self.session() as connection:
            cursor = connection.execute(
                """INSERT INTO dependencies
                   (predecessor_id, successor_id, relation_type, lag_days, lag_unit,
                    constraint_category)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    predecessor_id,
                    successor_id,
                    relation_type,
                    lag_days,
                    self._validate_lag_unit(lag_unit),
                    self._validate_constraint_category(constraint_category),
                ),
            )
            return int(cursor.lastrowid)

    def delete_dependency(self, dependency_id: int) -> None:
        with self.session() as connection:
            connection.execute("DELETE FROM dependencies WHERE id = ?", (dependency_id,))

    def delete_dependencies(self, dependency_ids: list[int]) -> None:
        if not dependency_ids:
            return
        placeholders = ",".join("?" for _ in dependency_ids)
        with self.session() as connection:
            connection.execute(
                f"DELETE FROM dependencies WHERE id IN ({placeholders})", dependency_ids
            )

    def flow_dependency_ids(self, dependency_id: int) -> list[int]:
        """Return all cross-segment relations belonging to the selected flow pair."""
        with self.session() as connection:
            selected = connection.execute(
                """SELECT p.parent_id AS predecessor_parent_id,
                          s.parent_id AS successor_parent_id
                   FROM dependencies d
                   JOIN tasks p ON p.id = d.predecessor_id
                   JOIN tasks s ON s.id = d.successor_id
                   WHERE d.id = ?""",
                (dependency_id,),
            ).fetchone()
            if (
                selected is None
                or selected["predecessor_parent_id"] is None
                or selected["successor_parent_id"] is None
                or selected["predecessor_parent_id"] == selected["successor_parent_id"]
            ):
                raise ValueError("请选择两道分段工序之间的一条流水关系。")
            rows = connection.execute(
                """SELECT d.id FROM dependencies d
                   JOIN tasks p ON p.id = d.predecessor_id
                   JOIN tasks s ON s.id = d.successor_id
                   WHERE p.parent_id = ? AND s.parent_id = ?
                   ORDER BY d.id""",
                (
                    selected["predecessor_parent_id"],
                    selected["successor_parent_id"],
                ),
            ).fetchall()
            return [int(row["id"]) for row in rows]

    def update_dependency(
        self,
        dependency_id: int,
        predecessor_id: int,
        successor_id: int,
        relation_type: str,
        lag_days: int,
        constraint_category: str = "process",
        lag_unit: str = "calendar_day",
    ) -> None:
        if lag_days < 0:
            raise ValueError("间隔自然日不能小于 0。")
        with self.session() as connection:
            connection.execute(
                """UPDATE dependencies
                   SET predecessor_id = ?, successor_id = ?, relation_type = ?,
                       lag_days = ?, lag_unit = ?, constraint_category = ?
                   WHERE id = ?""",
                (
                    predecessor_id,
                    successor_id,
                    relation_type,
                    lag_days,
                    self._validate_lag_unit(lag_unit),
                    self._validate_constraint_category(constraint_category),
                    dependency_id,
                ),
            )

    @staticmethod
    def _validate_lag_unit(value: str) -> str:
        if value not in {"workday", "calendar_day"}:
            raise ValueError("间隔类型只能是工作日或自然日。")
        return value

    @staticmethod
    def _validate_constraint_category(value: str) -> str:
        allowed = {
            "process",
            "material",
            "drawing",
            "inspection",
            "handover",
            "legal",
            "logistics",
        }
        if value not in allowed:
            raise ValueError("未知的约束业务类别。")
        return value

    def save_calculated_dates(
        self,
        values: dict[int, tuple[str, str]],
        reasons: dict[int, str] | None = None,
        crew_assignments: dict[int, int] | None = None,
    ) -> None:
        with self.session() as connection:
            connection.execute(
                """UPDATE tasks
                   SET calculated_start = NULL, calculated_finish = NULL,
                       calculated_crew_id = NULL"""
            )
            connection.executemany(
                """UPDATE tasks SET calculated_start = ?, calculated_finish = ?
                   WHERE id = ?""",
                [(start, finish, task_id) for task_id, (start, finish) in values.items()],
            )
            if reasons is not None:
                connection.execute("UPDATE tasks SET schedule_reason = ''")
                connection.executemany(
                    "UPDATE tasks SET schedule_reason = ? WHERE id = ?",
                    [(reason, task_id) for task_id, reason in reasons.items()],
                )
            if crew_assignments:
                connection.executemany(
                    "UPDATE tasks SET calculated_crew_id = ? WHERE id = ?",
                    [
                        (crew_id, task_id)
                        for task_id, crew_id in crew_assignments.items()
                    ],
                )

    def resources(self, resource_type: str | None = None) -> list[dict]:
        with self.session() as connection:
            if resource_type is None:
                rows = connection.execute(
                    "SELECT * FROM resources ORDER BY resource_type, name, id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM resources WHERE resource_type = ?
                       ORDER BY name, id""",
                    (resource_type,),
                ).fetchall()
            resources = [dict(row) for row in rows]
            skill_rows = connection.execute(
                "SELECT resource_id, trade FROM crew_skills ORDER BY resource_id, trade"
            ).fetchall()
            skills: dict[int, list[str]] = {}
            for row in skill_rows:
                skills.setdefault(int(row["resource_id"]), []).append(str(row["trade"]))
            for resource in resources:
                resource["skills"] = skills.get(int(resource["id"]), [])
            calendar_rows = connection.execute(
                """SELECT resource_id, exception_date, is_working, name
                   FROM crew_calendar_exceptions
                   ORDER BY resource_id, exception_date"""
            ).fetchall()
            availability: dict[int, dict[str, bool]] = {}
            for row in calendar_rows:
                availability.setdefault(int(row["resource_id"]), {})[
                    str(row["exception_date"])
                ] = bool(row["is_working"])
            path_calendar_rows = connection.execute(
                """SELECT ps.resource_id, pc.exception_date, pc.is_available
                   FROM path_segment_calendar_exceptions pc
                   JOIN path_segments ps ON ps.id = pc.segment_id
                   ORDER BY ps.resource_id, pc.exception_date"""
            ).fetchall()
            for row in path_calendar_rows:
                availability.setdefault(int(row["resource_id"]), {})[
                    str(row["exception_date"])
                ] = bool(row["is_available"])
            for resource in resources:
                resource["availability_exceptions"] = availability.get(
                    int(resource["id"]), {}
                )
            segment_rows = connection.execute(
                """SELECT id, resource_id, is_workface, available_from,
                          available_until, activation_event_id, deactivation_event_id
                   FROM path_segments"""
            ).fetchall()
            segment_by_resource = {
                int(row["resource_id"]): dict(row) for row in segment_rows
            }
            for resource in resources:
                segment = segment_by_resource.get(int(resource["id"]))
                resource["path_segment_id"] = int(segment["id"]) if segment else None
                if segment:
                    for key in (
                        "is_workface",
                        "available_from",
                        "available_until",
                        "activation_event_id",
                        "deactivation_event_id",
                    ):
                        resource[key] = segment[key]
            return resources

    def spatial_objects(self) -> list[dict]:
        """Workfaces and access areas are spatial constraints, not labour resources."""
        return [
            item
            for item in self.resources()
            if item["resource_type"] in {"workface", "access"}
        ]

    def operational_resources(self) -> list[dict]:
        """Resources that provide capacity or capability to execute work."""
        return [
            item
            for item in self.resources()
            if item["resource_type"] in {"crew", "equipment", "inspector"}
        ]

    def add_resource(
        self,
        name: str,
        resource_type: str,
        capacity: float = 1,
        notes: str = "",
        skills: list[str] | None = None,
        enabled: bool = True,
    ) -> int:
        if not name.strip():
            raise ValueError("资源名称不能为空。")
        with self.session() as connection:
            cursor = connection.execute(
                """INSERT INTO resources(name, resource_type, capacity, enabled, notes)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    name.strip(),
                    resource_type,
                    float(capacity),
                    int(enabled),
                    notes.strip(),
                ),
            )
            resource_id = int(cursor.lastrowid)
            self._replace_resource_skills(
                connection, resource_id, resource_type, skills or []
            )
            return resource_id

    def update_resource(
        self,
        resource_id: int,
        name: str,
        resource_type: str,
        capacity: float = 1,
        enabled: bool = True,
        notes: str = "",
        skills: list[str] | None = None,
    ) -> None:
        if not name.strip():
            raise ValueError("资源名称不能为空。")
        if capacity <= 0:
            raise ValueError("资源容量必须大于0。")
        with self.session() as connection:
            connection.execute(
                """UPDATE resources
                   SET name = ?, resource_type = ?, capacity = ?, enabled = ?, notes = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    name.strip(),
                    resource_type,
                    float(capacity),
                    int(enabled),
                    notes.strip(),
                    int(resource_id),
                ),
            )
            if resource_type != "crew":
                self._replace_resource_skills(connection, resource_id, resource_type, [])
                connection.execute(
                    "DELETE FROM crew_calendar_exceptions WHERE resource_id = ?",
                    (int(resource_id),),
                )
            elif skills is not None:
                self._replace_resource_skills(connection, resource_id, resource_type, skills)
            if resource_type == "crew" and skills is not None:
                assigned_tasks = connection.execute(
                    """SELECT t.id, t.trade
                       FROM tasks t
                       JOIN task_resource_demands tr ON tr.task_id = t.id
                       WHERE tr.resource_id = ?""",
                    (int(resource_id),),
                ).fetchall()
                for task in assigned_tasks:
                    self._validate_task_resources(
                        connection, normalized_trade(task["trade"]), [int(resource_id)]
                    )

    def _replace_resource_skills(
        self,
        connection: sqlite3.Connection,
        resource_id: int,
        resource_type: str,
        skills: list[str],
    ) -> None:
        connection.execute("DELETE FROM crew_skills WHERE resource_id = ?", (resource_id,))
        if resource_type != "crew":
            return
        unique_skills = []
        available_trades = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM trade_definitions").fetchall()
        }
        for skill in skills:
            skill = str(skill).strip()
            if skill not in available_trades:
                raise ValueError(f"未知工种能力：{skill}。")
            if skill not in unique_skills:
                unique_skills.append(skill)
        connection.executemany(
            "INSERT INTO crew_skills(resource_id, trade) VALUES (?, ?)",
            [(int(resource_id), skill) for skill in unique_skills],
        )

    def delete_resource(self, resource_id: int) -> None:
        with self.session() as connection:
            connection.execute(
                "UPDATE tasks SET calculated_crew_id = NULL WHERE calculated_crew_id = ?",
                (resource_id,),
            )
            connection.execute("DELETE FROM resources WHERE id = ?", (resource_id,))

    def crew_calendar_exceptions(self, resource_id: int) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT resource_id, exception_date, is_working, name
                   FROM crew_calendar_exceptions
                   WHERE resource_id = ?
                   ORDER BY exception_date""",
                (int(resource_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_crew_calendar_exception(
        self,
        resource_id: int,
        exception_date: str,
        is_working: bool,
        name: str = "",
    ) -> None:
        self.save_crew_calendar_exceptions(
            resource_id, [exception_date], is_working, name
        )

    def save_crew_calendar_exceptions(
        self,
        resource_id: int,
        exception_dates: list[str],
        is_working: bool,
        name: str = "",
    ) -> None:
        dates = list(dict.fromkeys(exception_dates))
        if not dates:
            return
        with self.session() as connection:
            resource = connection.execute(
                "SELECT resource_type FROM resources WHERE id = ?", (int(resource_id),)
            ).fetchone()
            if resource is None:
                raise ValueError("班组不存在。")
            if resource["resource_type"] != "crew":
                raise ValueError("只有班组可以设置上班日历。")
            connection.executemany(
                """INSERT INTO crew_calendar_exceptions(
                       resource_id, exception_date, is_working, name
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(resource_id, exception_date) DO UPDATE SET
                       is_working = excluded.is_working,
                       name = excluded.name""",
                [
                    (int(resource_id), exception_date, int(is_working), name.strip())
                    for exception_date in dates
                ],
            )

    def delete_crew_calendar_exception(
        self, resource_id: int, exception_date: str
    ) -> None:
        self.delete_crew_calendar_exceptions(resource_id, [exception_date])

    def delete_crew_calendar_exceptions(
        self, resource_id: int, exception_dates: list[str]
    ) -> None:
        dates = list(dict.fromkeys(exception_dates))
        if not dates:
            return
        with self.session() as connection:
            connection.executemany(
                """DELETE FROM crew_calendar_exceptions
                   WHERE resource_id = ? AND exception_date = ?""",
                [(int(resource_id), exception_date) for exception_date in dates],
            )

    def path_nodes(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute("SELECT * FROM path_nodes ORDER BY name, id").fetchall()
            return [dict(row) for row in rows]

    def add_path_node(self, name: str, node_type: str = "junction", notes: str = "") -> int:
        if not name.strip():
            raise ValueError("路径节点名称不能为空。")
        with self.session() as connection:
            cursor = connection.execute(
                "INSERT INTO path_nodes(name, node_type, notes) VALUES (?, ?, ?)",
                (name.strip(), node_type, notes.strip()),
            )
            return int(cursor.lastrowid)

    def update_path_node(
        self, node_id: int, name: str, node_type: str = "junction", notes: str = ""
    ) -> None:
        if not name.strip():
            raise ValueError("路径节点名称不能为空。")
        with self.session() as connection:
            connection.execute(
                "UPDATE path_nodes SET name = ?, node_type = ?, notes = ? WHERE id = ?",
                (name.strip(), node_type, notes.strip(), int(node_id)),
            )

    def delete_path_node(self, node_id: int) -> None:
        with self.session() as connection:
            used = connection.execute(
                """SELECT COUNT(*) FROM path_segments
                   WHERE from_node_id = ? OR to_node_id = ?""",
                (int(node_id), int(node_id)),
            ).fetchone()[0]
            if used:
                raise ValueError("该节点仍被路段使用，请先删除相关路段。")
            connection.execute("DELETE FROM path_nodes WHERE id = ?", (int(node_id),))

    def path_segments(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT ps.*, f.name AS from_name, t.name AS to_name,
                          r.capacity, r.enabled
                   FROM path_segments ps
                   JOIN path_nodes f ON f.id = ps.from_node_id
                   JOIN path_nodes t ON t.id = ps.to_node_id
                   JOIN resources r ON r.id = ps.resource_id
                   ORDER BY ps.name, ps.id"""
            ).fetchall()
            return [dict(row) for row in rows]

    def add_path_segment(
        self,
        name: str,
        from_node_id: int,
        to_node_id: int,
        direction: str = "two_way",
        capacity: float = 1,
        travel_minutes: int = 10,
        enabled: bool = True,
        notes: str = "",
        is_workface: bool = False,
        available_from: str | None = None,
        available_until: str | None = None,
        activation_event_id: int | None = None,
        deactivation_event_id: int | None = None,
    ) -> int:
        if not name.strip():
            raise ValueError("路段名称不能为空。")
        if int(from_node_id) == int(to_node_id):
            raise ValueError("路段起点和终点不能相同。")
        if int(travel_minutes) < 5:
            raise ValueError("路段通过时间不能少于5分钟。")
        if available_from and available_until and available_from > available_until:
            raise ValueError("路段停用日期不能早于开放日期。")
        with self.session() as connection:
            self._validate_path_events(
                connection, activation_event_id, deactivation_event_id
            )
            resource_cursor = connection.execute(
                """INSERT INTO resources(name, resource_type, capacity, enabled, notes)
                   VALUES (?, 'access', ?, ?, ?)""",
                (name.strip(), float(capacity), int(enabled), notes.strip()),
            )
            resource_id = int(resource_cursor.lastrowid)
            cursor = connection.execute(
                """INSERT INTO path_segments(
                       name, from_node_id, to_node_id, direction, travel_minutes,
                       resource_id, notes, is_workface, available_from, available_until,
                       activation_event_id, deactivation_event_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name.strip(), int(from_node_id), int(to_node_id), direction,
                    int(travel_minutes), resource_id, notes.strip(), int(is_workface),
                    available_from, available_until, activation_event_id,
                    deactivation_event_id,
                ),
            )
            return int(cursor.lastrowid)

    def update_path_segment(
        self,
        segment_id: int,
        name: str,
        from_node_id: int,
        to_node_id: int,
        direction: str = "two_way",
        capacity: float = 1,
        travel_minutes: int = 10,
        enabled: bool = True,
        notes: str = "",
        is_workface: bool = False,
        available_from: str | None = None,
        available_until: str | None = None,
        activation_event_id: int | None = None,
        deactivation_event_id: int | None = None,
    ) -> None:
        if not name.strip() or int(travel_minutes) < 5:
            raise ValueError("请填写路段名称，且通过时间不能少于5分钟。")
        if int(from_node_id) == int(to_node_id):
            raise ValueError("路段起点和终点不能相同。")
        if available_from and available_until and available_from > available_until:
            raise ValueError("路段停用日期不能早于开放日期。")
        with self.session() as connection:
            self._validate_path_events(
                connection, activation_event_id, deactivation_event_id
            )
            row = connection.execute(
                "SELECT resource_id FROM path_segments WHERE id = ?", (int(segment_id),)
            ).fetchone()
            if row is None:
                raise ValueError("路段不存在。")
            connection.execute(
                """UPDATE path_segments SET name = ?, from_node_id = ?, to_node_id = ?,
                       direction = ?, travel_minutes = ?, notes = ?, is_workface = ?,
                       available_from = ?, available_until = ?, activation_event_id = ?,
                       deactivation_event_id = ? WHERE id = ?""",
                (
                    name.strip(), int(from_node_id), int(to_node_id), direction,
                    int(travel_minutes), notes.strip(), int(is_workface), available_from,
                    available_until, activation_event_id, deactivation_event_id,
                    int(segment_id),
                ),
            )
            connection.execute(
                """UPDATE resources SET name = ?, capacity = ?, enabled = ?, notes = ?,
                       updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (name.strip(), float(capacity), int(enabled), notes.strip(), int(row["resource_id"])),
            )

    @staticmethod
    def _validate_path_events(
        connection: sqlite3.Connection,
        activation_event_id: int | None,
        deactivation_event_id: int | None,
    ) -> None:
        event_ids = {
            int(event_id)
            for event_id in (activation_event_id, deactivation_event_id)
            if event_id is not None
        }
        if (
            activation_event_id is not None
            and deactivation_event_id is not None
            and int(activation_event_id) == int(deactivation_event_id)
        ):
            raise ValueError("开放事件和停用事件不能是同一个事件。")
        if not event_ids:
            return
        placeholders = ",".join("?" for _ in event_ids)
        rows = connection.execute(
            f"SELECT id, task_type FROM tasks WHERE id IN ({placeholders})",
            sorted(event_ids),
        ).fetchall()
        if len(rows) != len(event_ids) or any(
            row["task_type"] != "milestone" for row in rows
        ):
            raise ValueError("开放和停用条件必须选择有效的里程碑事件。")

    def delete_path_segment(self, segment_id: int) -> None:
        with self.session() as connection:
            row = connection.execute(
                "SELECT resource_id FROM path_segments WHERE id = ?", (int(segment_id),)
            ).fetchone()
            if row is None:
                return
            route_uses = connection.execute(
                "SELECT COUNT(*) FROM route_template_steps WHERE segment_id = ?",
                (int(segment_id),),
            ).fetchone()[0]
            task_uses = connection.execute(
                "SELECT COUNT(*) FROM task_resource_demands WHERE resource_id = ?",
                (int(row["resource_id"]),),
            ).fetchone()[0]
            if route_uses or task_uses:
                raise ValueError("该路段仍被路线模板或任务使用，不能删除。")
            connection.execute("DELETE FROM path_segments WHERE id = ?", (int(segment_id),))
            connection.execute("DELETE FROM resources WHERE id = ?", (int(row["resource_id"]),))

    def path_segment_calendar_exceptions(self, segment_id: int) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT segment_id, exception_date, is_available, name
                   FROM path_segment_calendar_exceptions WHERE segment_id = ?
                   ORDER BY exception_date""",
                (int(segment_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_path_segment_calendar_exception(
        self, segment_id: int, exception_date: str, is_available: bool, name: str = ""
    ) -> None:
        with self.session() as connection:
            connection.execute(
                """INSERT INTO path_segment_calendar_exceptions(
                       segment_id, exception_date, is_available, name
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(segment_id, exception_date) DO UPDATE SET
                       is_available = excluded.is_available, name = excluded.name""",
                (int(segment_id), exception_date, int(is_available), name.strip()),
            )

    def delete_path_segment_calendar_exception(
        self, segment_id: int, exception_date: str
    ) -> None:
        with self.session() as connection:
            connection.execute(
                """DELETE FROM path_segment_calendar_exceptions
                   WHERE segment_id = ? AND exception_date = ?""",
                (int(segment_id), exception_date),
            )

    def route_templates(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute("SELECT * FROM route_templates ORDER BY name, id").fetchall()
            return [dict(row) for row in rows]

    def add_route_template(
        self,
        name: str,
        trade: str = "杂工",
        notes: str = "",
        alternative_route_id: int | None = None,
    ) -> int:
        if not name.strip():
            raise ValueError("路线名称不能为空。")
        with self.session() as connection:
            if alternative_route_id is not None:
                exists = connection.execute(
                    "SELECT 1 FROM route_templates WHERE id = ?",
                    (int(alternative_route_id),),
                ).fetchone()
                if exists is None:
                    raise ValueError("替代路线不存在或已被删除。")
            cursor = connection.execute(
                """INSERT INTO route_templates(
                       name, default_trade, notes, alternative_route_id
                   ) VALUES (?, ?, ?, ?)""",
                (
                    name.strip(), normalized_trade(trade), notes.strip(),
                    alternative_route_id,
                ),
            )
            return int(cursor.lastrowid)

    def update_route_template(
        self,
        route_id: int,
        name: str,
        trade: str = "杂工",
        notes: str = "",
        alternative_route_id: int | None = None,
    ) -> None:
        if not name.strip():
            raise ValueError("路线名称不能为空。")
        if alternative_route_id is not None and int(alternative_route_id) == int(route_id):
            raise ValueError("路线不能把自身设置为替代路线。")
        with self.session() as connection:
            candidate_id = alternative_route_id
            visited: set[int] = set()
            while candidate_id is not None:
                candidate_id = int(candidate_id)
                if candidate_id == int(route_id) or candidate_id in visited:
                    raise ValueError("路线的替代关系不能形成循环。")
                visited.add(candidate_id)
                row = connection.execute(
                    "SELECT alternative_route_id FROM route_templates WHERE id = ?",
                    (candidate_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("替代路线不存在或已被删除。")
                candidate_id = row["alternative_route_id"]
            connection.execute(
                """UPDATE route_templates SET name = ?, default_trade = ?, notes = ?,
                       alternative_route_id = ?
                   WHERE id = ?""",
                (
                    name.strip(), normalized_trade(trade), notes.strip(),
                    alternative_route_id, int(route_id),
                ),
            )

    def delete_route_template(self, route_id: int) -> None:
        with self.session() as connection:
            connection.execute(
                "UPDATE route_templates SET alternative_route_id = NULL WHERE alternative_route_id = ?",
                (int(route_id),),
            )
            connection.execute("DELETE FROM route_templates WHERE id = ?", (int(route_id),))

    def route_template_steps(self, route_id: int) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT rs.*, ps.name AS segment_name, ps.from_node_id, ps.to_node_id,
                          CASE WHEN rs.reverse_travel = 1 THEN t.name ELSE f.name END AS from_name,
                          CASE WHEN rs.reverse_travel = 1 THEN f.name ELSE t.name END AS to_name
                   FROM route_template_steps rs
                   JOIN path_segments ps ON ps.id = rs.segment_id
                   JOIN path_nodes f ON f.id = ps.from_node_id
                   JOIN path_nodes t ON t.id = ps.to_node_id
                   WHERE rs.route_id = ? ORDER BY rs.sort_order, rs.id""",
                (int(route_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def add_route_template_step(
        self,
        route_id: int,
        segment_id: int,
        duration_minutes: int | None = None,
        demand_amount: float = 1,
        reverse_travel: bool = False,
    ) -> int:
        with self.session() as connection:
            segment = connection.execute(
                """SELECT travel_minutes, from_node_id, to_node_id, direction
                   FROM path_segments WHERE id = ?""",
                (int(segment_id),),
            ).fetchone()
            if segment is None:
                raise ValueError("路段不存在。")
            duration = int(duration_minutes or segment["travel_minutes"])
            if duration < 5:
                raise ValueError("路线步骤不能少于5分钟。")
            if reverse_travel and segment["direction"] != "two_way":
                raise ValueError("单向路段不能反向通行。")
            new_start = int(
                segment["to_node_id"] if reverse_travel else segment["from_node_id"]
            )
            last = connection.execute(
                """SELECT rs.reverse_travel, ps.from_node_id, ps.to_node_id
                   FROM route_template_steps rs
                   JOIN path_segments ps ON ps.id = rs.segment_id
                   WHERE rs.route_id = ? ORDER BY rs.sort_order DESC, rs.id DESC LIMIT 1""",
                (int(route_id),),
            ).fetchone()
            if last is not None:
                last_end = int(
                    last["from_node_id"]
                    if last["reverse_travel"]
                    else last["to_node_id"]
                )
                if last_end != new_start:
                    raise ValueError("新路段的起点必须与上一段路线的终点一致。")
            next_order = connection.execute(
                """SELECT COALESCE(MAX(sort_order), 0) + 10
                   FROM route_template_steps WHERE route_id = ?""",
                (int(route_id),),
            ).fetchone()[0]
            cursor = connection.execute(
                """INSERT INTO route_template_steps(
                       route_id, segment_id, sort_order, reverse_travel,
                       duration_minutes, demand_amount
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    int(route_id), int(segment_id), int(next_order), int(reverse_travel),
                    duration, float(demand_amount),
                ),
            )
            return int(cursor.lastrowid)

    def delete_route_template_step(self, step_id: int) -> None:
        with self.session() as connection:
            connection.execute("DELETE FROM route_template_steps WHERE id = ?", (int(step_id),))

    def create_route_instance(
        self,
        route_id: int,
        name: str,
        parent_id: int | None = None,
        constraint_start: str | None = None,
    ) -> tuple[int, list[int]]:
        if not name.strip():
            raise ValueError("物流作业名称不能为空。")
        with self.session() as connection:
            if parent_id is not None and constraint_start is None:
                parent = connection.execute(
                    "SELECT constraint_start FROM tasks WHERE id = ?", (int(parent_id),)
                ).fetchone()
                constraint_start = parent["constraint_start"] if parent else None
            reference_date = date.fromisoformat(
                constraint_start
                or connection.execute(
                    "SELECT project_start FROM schedule_settings WHERE id = 1"
                ).fetchone()[0]
            )

            def load_route(candidate_id: int):
                candidate = connection.execute(
                    "SELECT * FROM route_templates WHERE id = ?", (int(candidate_id),)
                ).fetchone()
                candidate_steps = connection.execute(
                    """SELECT rs.*, ps.name AS segment_name, ps.resource_id,
                              ps.available_from, ps.available_until,
                              ps.activation_event_id, ps.deactivation_event_id,
                              r.enabled
                       FROM route_template_steps rs
                       JOIN path_segments ps ON ps.id = rs.segment_id
                       JOIN resources r ON r.id = ps.resource_id
                       WHERE rs.route_id = ? ORDER BY rs.sort_order, rs.id""",
                    (int(candidate_id),),
                ).fetchall()
                return candidate, candidate_steps

            def event_date(event_id: int | None) -> date | None:
                if event_id is None:
                    return None
                row = connection.execute(
                    """SELECT event_status, event_time, calculated_finish
                       FROM tasks WHERE id = ?""",
                    (int(event_id),),
                ).fetchone()
                if row is None or row["event_status"] == "pending":
                    return None
                raw = row["event_time"] or row["calculated_finish"]
                return datetime.fromisoformat(raw).date() if raw else None

            def route_available(candidate_steps) -> bool:
                if not candidate_steps:
                    return False
                for step in candidate_steps:
                    if not step["enabled"]:
                        return False
                    if step["available_from"] and reference_date < date.fromisoformat(step["available_from"]):
                        return False
                    if step["available_until"] and reference_date > date.fromisoformat(step["available_until"]):
                        return False
                    activation_id = step["activation_event_id"]
                    if activation_id is not None:
                        activated = event_date(activation_id)
                        if activated is None or activated > reference_date:
                            return False
                    deactivation_id = step["deactivation_event_id"]
                    if deactivation_id is not None:
                        deactivated = event_date(deactivation_id)
                        if deactivated is not None and deactivated <= reference_date:
                            return False
                return True

            selected_route_id = int(route_id)
            visited_routes: set[int] = set()
            while True:
                if selected_route_id in visited_routes:
                    raise ValueError("路线的替代关系中存在循环。")
                visited_routes.add(selected_route_id)
                route, steps = load_route(selected_route_id)
                if route is None:
                    raise ValueError("路线不存在。")
                if route_available(steps):
                    break
                alternative_id = route["alternative_route_id"]
                if alternative_id is None:
                    raise ValueError("首选路线当前不可用，且没有可用的替代路线。")
                selected_route_id = int(alternative_id)
            next_order = int(connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM tasks"
            ).fetchone()[0])

            def insert_task(task_name: str, minutes: int, task_parent: int | None, order: int) -> int:
                cursor = connection.execute(
                    """INSERT INTO tasks(
                           name, trade, duration, duration_days, duration_minutes, task_type,
                           calendar_type, priority, parent_id, constraint_start, notes,
                           sort_order, route_template_id
                       ) VALUES (?, ?, ?, ?, ?, 'logistics', 'working', 50, ?, ?, '', ?, ?)""",
                    (
                        task_name, route["default_trade"], max(1, math.ceil(minutes / 480)),
                        minutes / 480, minutes, task_parent, constraint_start, order,
                        int(selected_route_id),
                    ),
                )
                return int(cursor.lastrowid)

            total_minutes = sum(int(step["duration_minutes"]) for step in steps)
            summary_id = insert_task(name.strip(), total_minutes, parent_id, next_order)
            child_ids: list[int] = []
            for index, step in enumerate(steps, start=1):
                child_id = insert_task(
                    f"{index}. {step['segment_name']}",
                    int(step["duration_minutes"]), summary_id, next_order + index,
                )
                connection.execute(
                    """INSERT INTO task_resource_demands(task_id, resource_id, demand_amount)
                       VALUES (?, ?, ?)""",
                    (child_id, int(step["resource_id"]), float(step["demand_amount"])),
                )
                if child_ids:
                    connection.execute(
                        """INSERT INTO dependencies(
                               predecessor_id, successor_id, relation_type, lag_days,
                               lag_unit, constraint_category, source_type
                           ) VALUES (?, ?, 'FS', 0, 'calendar_day', 'logistics', 'route_template')""",
                        (child_ids[-1], child_id),
                    )
                child_ids.append(child_id)
            return summary_id, child_ids

    def calendar_exceptions(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT * FROM calendar_exceptions ORDER BY exception_date"
            ).fetchall()
            return [dict(row) for row in rows]

    def calendar_exception_map(self) -> dict[str, bool]:
        return {
            row["exception_date"]: bool(row["is_working"])
            for row in self.calendar_exceptions()
        }

    def save_calendar_exception(self, exception_date: str, is_working: bool, name: str) -> None:
        with self.session() as connection:
            connection.execute(
                """INSERT INTO calendar_exceptions(exception_date, is_working, name)
                   VALUES (?, ?, ?)
                   ON CONFLICT(exception_date) DO UPDATE SET
                       is_working = excluded.is_working,
                       name = excluded.name""",
                (exception_date, int(is_working), name),
            )

    def delete_calendar_exception(self, exception_date: str) -> None:
        with self.session() as connection:
            connection.execute(
                "DELETE FROM calendar_exceptions WHERE exception_date = ?", (exception_date,)
            )
