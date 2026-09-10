from __future__ import annotations

import sqlite3

from PySide6.QtCore import QDate, QDateTime, Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QCheckBox,
    QColorDialog,
    QCompleter,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .database import Database
from .scheduler import (
    CONSTRAINT_CATEGORY_LABELS,
    LAG_UNIT_LABELS,
    RELATION_LABELS,
    crew_actual_work_intervals,
)
from .trades import TRADES
from .work_calendar import WorkCalendar


class ProjectSettingsDialog(QDialog):
    def __init__(self, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("项目设置")
        self.setMinimumWidth(650)

        self.project_start = QDateEdit()
        self.project_start.setCalendarPopup(True)
        self.project_start.setDisplayFormat("yyyy-MM-dd")
        parsed = QDate.fromString(str(settings.get("project_start", "")), "yyyy-MM-dd")
        self.project_start.setDate(parsed if parsed.isValid() else QDate.currentDate())

        self.required_finish_enabled = QCheckBox("设置要求完工日期")
        self.required_finish_enabled.setChecked(bool(settings.get("required_finish")))
        self.required_finish = QDateEdit()
        self.required_finish.setCalendarPopup(True)
        self.required_finish.setDisplayFormat("yyyy-MM-dd")
        required_date = QDate.fromString(
            str(settings.get("required_finish") or ""), "yyyy-MM-dd"
        )
        self.required_finish.setDate(
            required_date if required_date.isValid() else self.project_start.date().addMonths(6)
        )
        self.required_finish.setEnabled(self.required_finish_enabled.isChecked())
        self.required_finish_enabled.toggled.connect(self.required_finish.setEnabled)

        self.status_date_enabled = QCheckBox("启用排程基准日期")
        self.status_date_enabled.setChecked(bool(settings.get("status_date")))
        self.status_date = QDateEdit()
        self.status_date.setCalendarPopup(True)
        self.status_date.setDisplayFormat("yyyy-MM-dd")
        status_date = QDate.fromString(str(settings.get("status_date") or ""), "yyyy-MM-dd")
        self.status_date.setDate(
            status_date if status_date.isValid() else self.project_start.date()
        )
        self.status_date.setEnabled(self.status_date_enabled.isChecked())
        self.status_date_enabled.toggled.connect(self.status_date.setEnabled)

        self.work_sessions = QLineEdit(
            str(settings.get("work_sessions") or "08:00-12:00,13:00-17:00")
        )
        self.work_sessions.setPlaceholderText("08:00-12:00,13:00-17:00")
        self.work_sessions.setToolTip(
            "多个时段用逗号分隔；两班制可填写 06:00-14:00,14:00-22:00"
        )
        self.weekend_working = QCheckBox("允许双休日加班施工")
        self.weekend_working.setChecked(bool(settings.get("weekend_working", 0)))
        self.overtime_lunch = QCheckBox("允许午休时段连续施工")
        self.overtime_lunch.setChecked(bool(settings.get("overtime_lunch", 0)))
        self.overtime_night = QCheckBox("允许夜间加班至 22:00")
        self.overtime_night.setChecked(bool(settings.get("overtime_night", 0)))
        self.overtime_holiday = QCheckBox("允许节假日加班施工")
        self.overtime_holiday.setChecked(bool(settings.get("overtime_holiday", 0)))

        self.crew_max_daily_hours = QDoubleSpinBox()
        self.crew_max_daily_hours.setRange(0.5, 24)
        self.crew_max_daily_hours.setDecimals(1)
        self.crew_max_daily_hours.setSuffix(" 小时")
        self.crew_max_daily_hours.setValue(
            float(settings.get("crew_max_daily_hours", 8))
        )
        self.limit_consecutive_days = QCheckBox("限制连续工作天数")
        saved_consecutive_days = int(settings.get("crew_max_consecutive_days", 0))
        self.limit_consecutive_days.setChecked(saved_consecutive_days > 0)
        self.crew_max_consecutive_days = QSpinBox()
        self.crew_max_consecutive_days.setRange(1, 31)
        self.crew_max_consecutive_days.setSuffix(" 天")
        self.crew_max_consecutive_days.setValue(
            saved_consecutive_days if saved_consecutive_days > 0 else 6
        )
        self.crew_max_consecutive_days.setEnabled(
            self.limit_consecutive_days.isChecked()
        )
        self.limit_consecutive_days.toggled.connect(
            self.crew_max_consecutive_days.setEnabled
        )

        self.optimization_goal = QComboBox()
        self.optimization_goal.addItem("尽量保持原计划（推荐）", "stable")
        self.optimization_goal.addItem("最早完成项目", "earliest")
        self.optimization_goal.addItem("尽量减少班组切换", "continuity")
        self.optimization_goal.setCurrentIndex(
            max(0, self.optimization_goal.findData(settings.get("optimization_goal", "stable")))
        )
        self.resource_leveling_mode = QComboBox()
        self.resource_leveling_mode.addItem("冲突时自动顺延（推荐）", "delay")
        self.resource_leveling_mode.addItem("允许超负荷并显示警告", "allow_overload")
        self.resource_leveling_mode.setCurrentIndex(
            max(
                0,
                self.resource_leveling_mode.findData(
                    settings.get("resource_leveling_mode", "delay")
                ),
            )
        )
        self.schedule_update_mode = QComboBox()
        self.schedule_update_mode.addItem("设置保存后自动重新排期（推荐）", True)
        self.schedule_update_mode.addItem("手动点击“重新排期”按钮", False)
        self.schedule_update_mode.setCurrentIndex(
            max(
                0,
                self.schedule_update_mode.findData(
                    bool(settings.get("auto_recalculate", 1))
                ),
            )
        )
        self.schedule_update_mode.setToolTip(
            "手动模式只保存修改，计划日期保持不变，直到点击项目预计完成旁边的重新排期按钮"
        )

        self.ignore_noise = QCheckBox("无视噪音施工限制")
        self.ignore_noise.setChecked(bool(settings.get("ignore_noise_restrictions", 0)))
        self.ignore_noise.setToolTip(
            "开启后，标记为有噪音的自然连续任务不再强制避开休息日；"
            "任务仍遵守自身时间类型及项目工作制度。"
        )
        noise_hint = QLabel(
            "默认情况下，有噪音任务会强制按施工日历执行。开启后，将按照任务自身选择的"
            "“工作日历累计”或“自然连续时间”排程。"
        )
        noise_hint.setWordWrap(True)
        noise_hint.setStyleSheet("color: #687985;")

        basic_form = QFormLayout()
        basic_form.addRow("无约束任务起始日期", self.project_start)
        basic_form.addRow(self.required_finish_enabled, self.required_finish)
        basic_form.addRow(self.status_date_enabled, self.status_date)
        status_hint = QLabel(
            "基准日前已经排入计划的任务保持原时间；其他任务不得重新排到基准日前。"
        )
        status_hint.setWordWrap(True)
        status_hint.setStyleSheet("color: #687985;")
        basic_form.addRow("", status_hint)

        work_form = QFormLayout()
        work_form.addRow("每日工作时段", self.work_sessions)
        work_form.addRow("双休日", self.weekend_working)
        work_form.addRow("午休加班", self.overtime_lunch)
        work_form.addRow("夜间加班", self.overtime_night)
        work_form.addRow("节假日", self.overtime_holiday)
        work_form.addRow("班组每天最多工作", self.crew_max_daily_hours)
        work_form.addRow(self.limit_consecutive_days, self.crew_max_consecutive_days)
        work_form.addRow("噪音施工", self.ignore_noise)
        work_form.addRow("", noise_hint)

        strategy_form = QFormLayout()
        strategy_form.addRow("排程优化目标", self.optimization_goal)
        strategy_form.addRow("自动资源平衡", self.resource_leveling_mode)
        strategy_form.addRow("设置修改后", self.schedule_update_mode)
        strategy_hint = QLabel(
            "允许超负荷时，任务不会因资源冲突顺延，但会在排程原因中明确标出超负荷资源。"
        )
        strategy_hint.setWordWrap(True)
        strategy_hint.setStyleSheet("color: #687985;")
        strategy_form.addRow("", strategy_hint)

        tabs = QTabWidget()
        for title, form in (
            ("基本控制", basic_form),
            ("工作时间与加班", work_form),
            ("排程策略", strategy_form),
        ):
            page = QWidget()
            page.setLayout(form)
            tabs.addTab(page, title)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            sessions = WorkCalendar.parse_sessions(self.work_sessions.text())
            WorkCalendar(sessions=sessions)
        except ValueError as error:
            QMessageBox.warning(self, "工作时段无效", str(error))
            return
        if (
            self.required_finish_enabled.isChecked()
            and self.required_finish.date() < self.project_start.date()
        ):
            QMessageBox.warning(self, "日期无效", "要求完工日期不能早于项目起始日期。")
            return
        if (
            self.status_date_enabled.isChecked()
            and self.status_date.date() < self.project_start.date()
        ):
            QMessageBox.warning(self, "日期无效", "排程基准日期不能早于项目起始日期。")
            return
        self.accept()

    def data(self) -> dict:
        return {
            "project_start": self.project_start.date().toString("yyyy-MM-dd"),
            "required_finish": (
                self.required_finish.date().toString("yyyy-MM-dd")
                if self.required_finish_enabled.isChecked()
                else None
            ),
            "status_date": (
                self.status_date.date().toString("yyyy-MM-dd")
                if self.status_date_enabled.isChecked()
                else None
            ),
            "work_sessions": self.work_sessions.text().strip(),
            "weekend_working": self.weekend_working.isChecked(),
            "overtime_lunch": self.overtime_lunch.isChecked(),
            "overtime_night": self.overtime_night.isChecked(),
            "overtime_holiday": self.overtime_holiday.isChecked(),
            "crew_max_daily_hours": self.crew_max_daily_hours.value(),
            "crew_max_consecutive_days": (
                self.crew_max_consecutive_days.value()
                if self.limit_consecutive_days.isChecked()
                else 0
            ),
            "optimization_goal": str(self.optimization_goal.currentData()),
            "resource_leveling_mode": str(self.resource_leveling_mode.currentData()),
            "auto_recalculate": bool(self.schedule_update_mode.currentData()),
            "ignore_noise_restrictions": self.ignore_noise.isChecked(),
        }


class TaskDialog(QDialog):
    def __init__(
        self,
        tasks: list[dict],
        values: dict | None = None,
        fixed_parent_id: int | None = None,
        lock_parent: bool = False,
        resources: list[dict] | None = None,
        process_templates: list[dict] | None = None,
        trades: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.values = values or {}
        self.setWindowTitle("编辑任务/事件" if values else "新增任务/事件")
        self.setMinimumWidth(520)

        self.name = QLineEdit(str(self.values.get("name", "")))
        self.parent_task = QComboBox()
        self.parent_task.addItem("无（顶层任务）", None)
        current_id = self.values.get("id")
        for task in tasks:
            if task["id"] != current_id:
                self.parent_task.addItem(task["name"], task["id"])

        parent_id = fixed_parent_id if fixed_parent_id is not None else self.values.get("parent_id")
        index = self.parent_task.findData(parent_id)
        self.parent_task.setCurrentIndex(max(0, index))
        if fixed_parent_id is not None or lock_parent:
            self.parent_task.setEnabled(False)

        self.task_type = QComboBox()
        for code, label in (
            ("work", "施工活动"),
            ("logistics", "物流活动"),
            ("inspection", "验收活动"),
            ("wait", "等待/养护"),
            ("milestone", "事件/里程碑"),
        ):
            self.task_type.addItem(label, code)
        self.task_type.setCurrentIndex(
            max(0, self.task_type.findData(self.values.get("task_type", "work")))
        )

        self.event_status = QComboBox()
        for code, label in (
            ("derived", "由前序任务推导"),
            ("planned", "预计发生"),
            ("occurred", "已实际发生"),
            ("pending", "尚未发生（阻断后续）"),
        ):
            self.event_status.addItem(label, code)
        self.event_status.setCurrentIndex(
            max(0, self.event_status.findData(self.values.get("event_status", "derived")))
        )
        self.event_time = QDateTimeEdit()
        self.event_time.setCalendarPopup(True)
        self.event_time.setDisplayFormat("yyyy-MM-dd HH:mm")
        parsed_event_time = QDateTime.fromString(
            str(self.values.get("event_time") or ""), "yyyy-MM-ddTHH:mm"
        )
        self.event_time.setDateTime(
            parsed_event_time if parsed_event_time.isValid() else QDateTime.currentDateTime()
        )
        self.event_status.currentIndexChanged.connect(self._sync_event_status)

        self.duration = QDoubleSpinBox()
        self.duration.setRange(0, 999999)
        self.duration.setDecimals(6)
        self.duration_unit = QComboBox()
        self.duration_unit.addItem("工日", 480)
        self.duration_unit.addItem("小时", 60)
        self.duration_unit.addItem("分钟", 1)
        initial_minutes = int(
            self.values.get(
                "duration_minutes", round(float(self.values.get("duration", 1)) * 480)
            )
            or 0
        )
        initial_factor = 480 if initial_minutes % 480 == 0 else 60 if initial_minutes % 60 == 0 else 1
        self.duration_unit.setCurrentIndex(self.duration_unit.findData(initial_factor))
        self._duration_factor = initial_factor
        self.duration.setValue(initial_minutes / initial_factor)
        self._configure_duration_input()
        self.duration_unit.currentIndexChanged.connect(self._change_duration_unit)
        duration_row = QHBoxLayout()
        duration_row.addWidget(self.duration, 1)
        duration_row.addWidget(self.duration_unit)

        self.calendar_type = QComboBox()
        self.calendar_type.addItem("按工作日历累计", "working")
        self.calendar_type.addItem("自然连续时间", "elapsed")
        self.calendar_type.setCurrentIndex(
            max(0, self.calendar_type.findData(self.values.get("calendar_type", "working")))
        )

        self.priority = QSpinBox()
        self.priority.setRange(0, 100)
        self.priority.setValue(int(self.values.get("priority", 50)))
        self.priority.setToolTip("资源冲突时优先安排数值较高的活动")

        self.has_noise = QCheckBox("施工期间产生明显噪音")
        self.has_noise.setChecked(bool(self.values.get("has_noise", 0)))
        self.has_noise.setToolTip("勾选后强制按工作日历排程，自动避开休息日和节假日")

        self.trade = QComboBox()
        self.trade.addItems(trades or list(TRADES))
        trade_index = self.trade.findText(str(self.values.get("trade", "杂工")))
        self.trade.setCurrentIndex(max(0, trade_index))

        self.process_template = QComboBox()
        self.process_template.addItem("无（独立任务）", None)
        self._process_templates = {
            int(template["id"]): dict(template) for template in process_templates or []
        }
        for template in process_templates or []:
            self.process_template.addItem(template["name"], template["id"])
        self.process_template.setCurrentIndex(
            max(
                0,
                self.process_template.findData(self.values.get("process_template_id")),
            )
        )
        self.process_template.currentIndexChanged.connect(self._apply_process_defaults)
        self.apply_trade_to_children = QCheckBox("将所选工种应用到全部下级施工段")
        self.apply_trade_to_children.setVisible(bool(self.values.get("segment_count")))

        self.crew_requirement = QLabel()
        self.crew_requirement.setWordWrap(True)

        self.spatial_resources = QListWidget()
        self.spatial_resources.setMaximumHeight(120)
        self.operational_resources = QListWidget()
        self.operational_resources.setMaximumHeight(120)
        selected_resource_ids = {
            int(resource["id"]) for resource in self.values.get("resources", [])
        }
        resource_labels = {
            "workface": "作业面",
            "crew": "班组",
            "equipment": "设备",
            "access": "通行区域",
            "inspector": "检查人员",
        }
        for resource in resources or []:
            if resource["resource_type"] == "crew":
                continue
            label = (
                "作业面＋路径"
                if resource.get("resource_type") == "access"
                and resource.get("is_workface")
                else resource_labels.get(
                    resource["resource_type"], resource["resource_type"]
                )
            )
            item = QListWidgetItem(f"[{label}] {resource['name']}")
            item.setData(Qt.ItemDataRole.UserRole, int(resource["id"]))
            item.setData(Qt.ItemDataRole.UserRole + 1, dict(resource))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if int(resource["id"]) in selected_resource_ids
                else Qt.CheckState.Unchecked
            )
            target = (
                self.spatial_resources
                if resource["resource_type"] in {"workface", "access"}
                else self.operational_resources
            )
            target.addItem(item)

        self.spatial_occupancy_mode = QComboBox()
        self.spatial_occupancy_mode.addItem("正常共享，不影响通行容量", "normal")
        self.spatial_occupancy_mode.addItem("施工占用部分通行容量", "reduce")
        self.spatial_occupancy_mode.addItem("施工期间完全封闭通行", "close")
        self.spatial_occupancy_mode.setCurrentIndex(
            max(
                0,
                self.spatial_occupancy_mode.findData(
                    self.values.get("spatial_occupancy_mode", "reduce")
                ),
            )
        )
        self.traffic_reduction = QDoubleSpinBox()
        self.traffic_reduction.setRange(0.01, 9999)
        self.traffic_reduction.setValue(
            float(self.values.get("traffic_reduction", 1))
        )
        self.traffic_reduction.setSuffix(" 容量")
        self.spatial_occupancy_mode.currentIndexChanged.connect(
            self._sync_spatial_occupancy
        )

        self.use_constraint = QCheckBox("指定最早开始日期")
        self.use_constraint.setToolTip("汇总任务的日期会同步应用到全部下级任务")
        self.constraint_date = QDateEdit()
        self.constraint_date.setCalendarPopup(True)
        self.constraint_date.setDisplayFormat("yyyy-MM-dd")
        existing_date = self.values.get("constraint_start")
        self.use_constraint.setChecked(bool(existing_date))
        parsed = QDate.fromString(existing_date or "", "yyyy-MM-dd")
        self.constraint_date.setDate(parsed if parsed.isValid() else QDate.currentDate())
        self.constraint_date.setEnabled(self.use_constraint.isChecked())
        self.use_constraint.toggled.connect(self.constraint_date.setEnabled)

        constraint_row = QHBoxLayout()
        constraint_row.addWidget(self.use_constraint)
        constraint_row.addWidget(self.constraint_date)

        self.notes = QPlainTextEdit(str(self.values.get("notes", "")))
        self.notes.setMaximumHeight(90)

        form = QFormLayout()
        form.addRow("任务名称 *", self.name)
        form.addRow("上级任务", self.parent_task)
        form.addRow("活动类型", self.task_type)
        form.addRow("事件状态", self.event_status)
        form.addRow("事件时间", self.event_time)
        form.addRow("继承的工艺模板", self.process_template)
        form.addRow("工种", self.trade)
        form.addRow("所需班组能力", self.crew_requirement)
        form.addRow("", self.apply_trade_to_children)
        form.addRow("工期", duration_row)
        form.addRow("时间类型", self.calendar_type)
        form.addRow("排程优先级", self.priority)
        form.addRow("噪音限制", self.has_noise)
        form.addRow("作业面/通行区域", self.spatial_resources)
        form.addRow("施工对通行影响", self.spatial_occupancy_mode)
        form.addRow("降低通行容量", self.traffic_reduction)
        form.addRow("占用资源", self.operational_resources)
        form.addRow("日期约束", constraint_row)
        form.addRow("备注", self.notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.task_type.currentIndexChanged.connect(self._sync_task_type)
        self.trade.currentTextChanged.connect(self._refresh_crew_requirement)
        self._sync_task_type()
        self._sync_spatial_occupancy()

    def _sync_spatial_occupancy(self) -> None:
        self.traffic_reduction.setEnabled(
            self.spatial_occupancy_mode.currentData() == "reduce"
        )

    def _refresh_crew_requirement(self) -> None:
        if self.task_type.currentData() in {"work", "logistics"}:
            self.crew_requirement.setText(
                f"需要具备“{self.trade.currentText()}”能力的班组；具体班组由排程器自动选择。"
            )
        else:
            self.crew_requirement.setText("此活动类型不要求施工班组。")

    def _sync_task_type(self) -> None:
        milestone = self.task_type.currentData() == "milestone"
        if milestone:
            self.duration.setValue(0)
        elif self.duration.value() <= 0:
            self.duration.setValue(5 / self._duration_factor)
        self.duration.setEnabled(not milestone)
        self.duration_unit.setEnabled(not milestone)
        self.event_status.setEnabled(milestone)
        self.has_noise.setEnabled(not milestone)
        if milestone:
            self.has_noise.setChecked(False)
        self._sync_event_status()
        self._refresh_crew_requirement()

    def _sync_event_status(self) -> None:
        is_event = self.task_type.currentData() == "milestone"
        self.event_time.setEnabled(
            is_event and self.event_status.currentData() in {"planned", "occurred"}
        )

    def _configure_duration_input(self) -> None:
        factor = int(self.duration_unit.currentData())
        self.duration.setSingleStep(5 / factor)
        self.duration.setToolTip("内部按整数分钟保存，非里程碑活动最小5分钟")

    def _change_duration_unit(self) -> None:
        new_factor = int(self.duration_unit.currentData())
        total_minutes = self.duration.value() * self._duration_factor
        self._duration_factor = new_factor
        self._configure_duration_input()
        self.duration.setValue(total_minutes / new_factor)

    def data(self) -> dict:
        resource_ids = []
        for resource_list in (self.spatial_resources, self.operational_resources):
            resource_ids.extend(
                int(resource_list.item(index).data(Qt.ItemDataRole.UserRole))
                for index in range(resource_list.count())
                if resource_list.item(index).checkState() == Qt.CheckState.Checked
            )
        return {
            "name": self.name.text().strip(),
            "duration": self.duration.value() * self._duration_factor / 480,
            "task_type": self.task_type.currentData(),
            "event_status": self.event_status.currentData(),
            "event_time": (
                self.event_time.dateTime().toString("yyyy-MM-ddTHH:mm")
                if self.task_type.currentData() == "milestone"
                and self.event_status.currentData() in {"planned", "occurred"}
                else None
            ),
            "calendar_type": self.calendar_type.currentData(),
            "priority": self.priority.value(),
            "has_noise": self.has_noise.isChecked(),
            "trade": self.trade.currentText(),
            "process_template_id": self.process_template.currentData(),
            "apply_trade_to_children": self.apply_trade_to_children.isChecked(),
            "parent_id": self.parent_task.currentData(),
            "constraint_start": (
                self.constraint_date.date().toString("yyyy-MM-dd")
                if self.use_constraint.isChecked()
                else None
            ),
            "notes": self.notes.toPlainText().strip(),
            "resource_ids": resource_ids,
            "spatial_occupancy_mode": self.spatial_occupancy_mode.currentData(),
            "traffic_reduction": self.traffic_reduction.value(),
        }

    def _accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "请填写任务", "任务名称不能为空。")
            return
        if (
            self.task_type.currentData() != "milestone"
            and self.duration.value() * self._duration_factor < 5 - 1e-7
        ):
            QMessageBox.warning(self, "工期过短", "非里程碑活动的工期不能少于5分钟。")
            return
        self.accept()

    def _apply_process_defaults(self) -> None:
        template_id = self.process_template.currentData()
        if template_id is None:
            return
        template = self._process_templates.get(int(template_id))
        if template:
            index = self.trade.findText(str(template.get("default_trade", "杂工")))
            if index >= 0:
                self.trade.setCurrentIndex(index)


class ProcessTemplateEditDialog(QDialog):
    def __init__(self, trades: list[str], data: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        data = data or {}
        self.setWindowTitle("修改作业模板" if data else "新增作业模板")
        self.setMinimumWidth(500)
        self.name = QLineEdit(str(data.get("name", "")))
        self.name.setPlaceholderText("例如：基层处理、自流平、地板铺装")
        self.trade = QComboBox()
        self.trade.addItems(trades)
        self.trade.setCurrentIndex(max(0, self.trade.findText(str(data.get("default_trade", "")))))
        self.notes = QPlainTextEdit(str(data.get("notes", "")))
        self.notes.setPlaceholderText("适用条件或施工说明（可选）")
        self.notes.setMaximumHeight(100)
        form = QFormLayout()
        form.addRow("模板名称", self.name)
        form.addRow("默认工种", self.trade)
        form.addRow("说明", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "请填写模板", "模板名称不能为空。")
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return self.name.text(), self.trade.currentText(), self.notes.toPlainText()


class WorkTemplateStepEditDialog(QDialog):
    def __init__(self, trades: list[str], data: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        data = data or {}
        self.setWindowTitle("修改模板步骤" if data else "新增模板步骤")
        self.setMinimumWidth(540)
        self.name = QLineEdit(str(data.get("name", "")))
        self.task_type = QComboBox()
        for code, label in (("work", "施工活动"), ("logistics", "搬运/物流"), ("inspection", "审批/验收"), ("wait", "等待/养护"), ("milestone", "事件/条件达成")):
            self.task_type.addItem(label, code)
        self.role = QComboBox()
        for code, label in (("prerequisite", "前置条件"), ("execution", "执行步骤"), ("postcondition", "后置步骤"), ("output", "输出事件")):
            self.role.addItem(label, code)
        self.trade = QComboBox()
        self.trade.addItems(trades)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0, 99999)
        self.duration.setDecimals(2)
        self.duration.setSuffix(" 小时")
        self.duration.setValue(int(data.get("duration_minutes", 480)) / 60)
        self.calendar = QComboBox()
        self.calendar.addItem("工作日历", "working")
        self.calendar.addItem("自然连续", "elapsed")
        self.noise = QCheckBox("该步骤有噪音")
        self.noise.setChecked(bool(data.get("has_noise", False)))
        self.event_status = QComboBox()
        self.event_status.addItem("由前序推导", "derived")
        self.event_status.addItem("待现场确认（阻断后续）", "pending")
        self.notes = QPlainTextEdit(str(data.get("notes", "")))
        self.notes.setMaximumHeight(80)
        for combo, value in ((self.task_type, data.get("task_type", "work")), (self.role, data.get("role", "execution")), (self.calendar, data.get("calendar_type", "working")), (self.event_status, data.get("event_status", "derived"))):
            combo.setCurrentIndex(max(0, combo.findData(value)))
        self.trade.setCurrentIndex(max(0, self.trade.findText(str(data.get("default_trade", "")))))
        self.task_type.currentIndexChanged.connect(self._sync_type)
        form = QFormLayout()
        for label, widget in (("步骤名称", self.name), ("步骤类型", self.task_type), ("步骤角色", self.role), ("默认工种", self.trade), ("时长", self.duration), ("时间类型", self.calendar), ("", self.noise), ("事件默认状态", self.event_status), ("步骤说明", self.notes)):
            form.addRow(label, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._sync_type()

    def _sync_type(self) -> None:
        event = self.task_type.currentData() == "milestone"
        if event:
            self.duration.setValue(0)
        elif self.duration.value() <= 0:
            self.duration.setValue(1)
        for widget in (self.duration, self.trade, self.noise):
            widget.setEnabled(not event)
        self.event_status.setEnabled(event)

    def _accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "请填写步骤", "步骤名称不能为空。")
            return
        self.accept()

    def values(self) -> tuple:
        return (self.name.text(), self.task_type.currentData(), self.role.currentData(), self.trade.currentText(), round(self.duration.value() * 60), self.calendar.currentData(), self.noise.isChecked(), self.notes.toPlainText(), self.event_status.currentData())


class TemplateRelationEditDialog(QDialog):
    def __init__(self, items: list[dict], label: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"新增{label}关系")
        self.setMinimumWidth(520)
        self.predecessor, self.successor = QComboBox(), QComboBox()
        for item in items:
            for combo in (self.predecessor, self.successor):
                combo.addItem(str(item["name"]), int(item["id"]))
        if self.successor.count() > 1:
            self.successor.setCurrentIndex(1)
        self.relation_type = QComboBox()
        for code, text in RELATION_LABELS.items():
            self.relation_type.addItem(text, code)
        self.lag = QSpinBox()
        self.lag.setRange(0, 9999)
        self.lag_unit = QComboBox()
        for code, text in LAG_UNIT_LABELS.items():
            self.lag_unit.addItem(text, code)
        self.category = QComboBox()
        for code, text in CONSTRAINT_CATEGORY_LABELS.items():
            self.category.addItem(text, code)
        form = QFormLayout()
        for text, widget in ((f"前置{label}", self.predecessor), ("关系类型", self.relation_type), ("间隔", self.lag), ("间隔单位", self.lag_unit), ("业务类别", self.category), (f"后续{label}", self.successor)):
            form.addRow(text, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("添加")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> tuple:
        return (self.predecessor.currentData(), self.successor.currentData(), self.relation_type.currentData(), self.lag.value(), self.lag_unit.currentData(), self.category.currentData())


class ProcessLibraryDialog(QDialog):
    changed = Signal()

    def __init__(self, database: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("作业模板库")
        self.resize(1100, 720)
        explanation = QLabel(
            "每个作业模板是一套可复用的工艺定义，模板步骤和步骤关系都从属于它。"
            "从模板创建现场作业后，会生成一个模板实例及其继承步骤。"
        )
        explanation.setWordWrap(True)
        self.inheritance_label = QLabel(
            "继承关系：作业模板  →  模板步骤与规则  →  现场任务实例"
        )
        self.inheritance_label.setStyleSheet(
            "font-weight: 600; color: #24527A; padding: 4px 0;"
        )
        self.template_selector = QComboBox()
        self.template_selector.currentIndexChanged.connect(self._select_from_combo)
        selector = QHBoxLayout()
        selector.addWidget(QLabel("当前模板"))
        selector.addWidget(self.template_selector, 1)
        selector.addStretch()

        self.template_table = self._make_table(["模板名称", "默认工种", "说明"])
        self.template_table.doubleClicked.connect(lambda *_: self._edit_template())
        self.template_table.itemSelectionChanged.connect(self._select_from_table)
        self.step_table = self._make_table(["步骤", "类型", "角色", "默认工种", "时长", "时间类型", "说明"])
        self.step_table.doubleClicked.connect(lambda *_: self._edit_step())
        self.step_rule_table = self._make_table(["业务类别", "前置步骤", "关系", "间隔", "单位", "后续步骤"])
        self.instance_table = self._make_table(
            ["现场任务实例", "继承步骤", "计算开始", "计算完成", "来源说明"]
        )
        self.rule_table = self._make_table(["业务类别", "前置模板", "关系", "间隔", "单位", "后续模板"])

        self.tabs = QTabWidget()
        self.tabs.addTab(self._page((self._button("新增模板", self._add_template, True), self._button("修改模板", self._edit_template), self._button("删除模板", self._delete_template)), self.template_table), "作业模板")
        self.tabs.addTab(self._page((self._button("新增步骤", self._add_step, True), self._button("修改步骤", self._edit_step), self._button("删除步骤", self._delete_step)), self.step_table), "模板步骤")
        self.tabs.addTab(self._page((self._button("新增步骤关系", self._add_step_rule, True), self._button("删除步骤关系", self._delete_step_rule)), self.step_rule_table), "步骤关系")
        self.tabs.addTab(self._page((), self.instance_table), "现场任务实例")
        self.tabs.addTab(self._page((self._button("新增模板衔接", self._add_rule, True), self._button("删除模板衔接", self._delete_rule)), self.rule_table), "模板衔接")

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close_buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addWidget(self.inheritance_label)
        layout.addLayout(selector)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(close_buttons)
        self.refresh()

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _button(text: str, handler, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        if primary:
            button.setObjectName("PrimaryButton")
        button.clicked.connect(handler)
        return button

    @staticmethod
    def _page(buttons: tuple[QPushButton, ...], table: QTableWidget) -> QWidget:
        page = QWidget()
        actions = QHBoxLayout()
        for button in buttons:
            actions.addWidget(button)
        actions.addStretch()
        layout = QVBoxLayout(page)
        layout.addLayout(actions)
        layout.addWidget(table, 1)
        return page

    def refresh(self) -> None:
        current_id = self._current_template_id()
        templates = self.database.process_templates()
        self.template_selector.blockSignals(True)
        self.template_selector.clear()
        for template in templates:
            self.template_selector.addItem(str(template["name"]), int(template["id"]))
        index = self.template_selector.findData(current_id)
        self.template_selector.setCurrentIndex(index if index >= 0 else (0 if templates else -1))
        self.template_selector.blockSignals(False)
        current_id = self._current_template_id()
        self.template_table.blockSignals(True)
        self.template_table.setRowCount(len(templates))
        selected_row = -1
        for row, template in enumerate(templates):
            if int(template["id"]) == current_id:
                selected_row = row
            self._set_row(self.template_table, row, template["id"], (template["name"], template["default_trade"], template.get("notes", "")))
        if selected_row >= 0:
            self.template_table.selectRow(selected_row)
        self.template_table.blockSignals(False)
        rules = self.database.process_template_dependencies()
        self.rule_table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            self._set_row(self.rule_table, row, rule["id"], (CONSTRAINT_CATEGORY_LABELS.get(rule["constraint_category"], "其他"), rule["predecessor_name"], RELATION_LABELS[rule["relation_type"]], rule["lag_days"], LAG_UNIT_LABELS[rule["lag_unit"]], rule["successor_name"]))
        self.template_table.resizeColumnsToContents()
        self.rule_table.resizeColumnsToContents()
        self._refresh_steps()

    def _current_template_id(self) -> int | None:
        value = self.template_selector.currentData()
        return int(value) if value is not None else None

    def _select_from_combo(self) -> None:
        template_id = self._current_template_id()
        self.template_table.blockSignals(True)
        for row in range(self.template_table.rowCount()):
            item = self.template_table.item(row, 0)
            if item and int(item.data(Qt.ItemDataRole.UserRole)) == template_id:
                self.template_table.selectRow(row)
                break
        self.template_table.blockSignals(False)
        self._refresh_steps()

    def _select_from_table(self) -> None:
        index = self.template_selector.findData(self._selected_id(self.template_table))
        if index >= 0:
            self.template_selector.setCurrentIndex(index)

    def _refresh_steps(self) -> None:
        template_id = self._current_template_id()
        steps = self.database.work_template_steps(template_id) if template_id else []
        template_name = self.template_selector.currentText().strip()
        quoted_name = f"“{template_name}”" if template_name else "当前模板"
        self.tabs.setTabText(1, f"{quoted_name}的步骤")
        self.tabs.setTabText(2, f"{quoted_name}的步骤关系")
        self.tabs.setTabText(3, f"{quoted_name}的现场实例")
        types = {"work": "施工", "logistics": "搬运/物流", "inspection": "审批/验收", "wait": "等待/养护", "milestone": "事件"}
        roles = {"prerequisite": "前置条件", "execution": "执行步骤", "postcondition": "后置步骤", "output": "输出事件"}
        self.step_table.setRowCount(len(steps))
        for row, step in enumerate(steps):
            minutes = int(step["duration_minutes"])
            duration = "0（事件）" if step["task_type"] == "milestone" else f"{minutes / 60:g}小时"
            self._set_row(self.step_table, row, step["id"], (step["name"], types.get(step["task_type"], step["task_type"]), roles.get(step["role"], step["role"]), step["default_trade"], duration, "自然连续" if step["calendar_type"] == "elapsed" else "工作日历", step.get("notes", "")))
        relations = self.database.work_template_step_dependencies(template_id) if template_id else []
        self.step_rule_table.setRowCount(len(relations))
        for row, relation in enumerate(relations):
            self._set_row(self.step_rule_table, row, relation["id"], (CONSTRAINT_CATEGORY_LABELS.get(relation["constraint_category"], "其他"), relation["predecessor_name"], RELATION_LABELS[relation["relation_type"]], relation["lag_days"], LAG_UNIT_LABELS[relation["lag_unit"]], relation["successor_name"]))
        all_tasks = self.database.tasks()
        instances = [
            task for task in all_tasks
            if template_id is not None
            and task.get("process_template_id") == template_id
            and task.get("template_instance_id") == task.get("id")
        ]
        children_by_instance: dict[int, int] = {}
        for task in all_tasks:
            instance_id = task.get("template_instance_id")
            if instance_id is not None and task.get("work_template_step_id") is not None:
                children_by_instance[int(instance_id)] = (
                    children_by_instance.get(int(instance_id), 0) + 1
                )
        self.instance_table.setRowCount(len(instances))
        for row, instance in enumerate(instances):
            inherited_count = children_by_instance.get(int(instance["id"]), 0)
            self._set_row(self.instance_table, row, instance["id"], (
                instance["name"], f"{inherited_count}/{len(steps)} 项",
                instance.get("calculated_start") or "—",
                instance.get("calculated_finish") or "—",
                f"继承自作业模板“{template_name}”",
            ))
        self.step_table.resizeColumnsToContents()
        self.step_rule_table.resizeColumnsToContents()
        self.instance_table.resizeColumnsToContents()

    @staticmethod
    def _set_row(table: QTableWidget, row: int, row_id: int, values: tuple) -> None:
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setData(Qt.ItemDataRole.UserRole, int(row_id))
            table.setItem(row, column, item)

    @staticmethod
    def _selected_id(table: QTableWidget) -> int | None:
        rows = table.selectionModel().selectedRows()
        if not rows:
            return None
        item = table.item(rows[0].row(), 0)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _add_template(self) -> None:
        dialog = ProcessTemplateEditDialog(self.database.trade_names(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            template_id = self.database.add_process_template(*dialog.values())
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法保存模板", str(error))
            return
        self.changed.emit()
        self.refresh()
        self.template_selector.setCurrentIndex(self.template_selector.findData(template_id))

    def _edit_template(self) -> None:
        template_id = self._selected_id(self.template_table) or self._current_template_id()
        if template_id is None:
            QMessageBox.information(self, "请选择模板", "请先选择要修改的作业模板。")
            return
        template = next(row for row in self.database.process_templates() if int(row["id"]) == template_id)
        dialog = ProcessTemplateEditDialog(self.database.trade_names(), template, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.database.update_process_template(template_id, *dialog.values())
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法保存模板", str(error))
            return
        self.changed.emit()
        self.refresh()

    def _delete_template(self) -> None:
        template_id = self._selected_id(self.template_table)
        if template_id is None:
            QMessageBox.information(self, "请选择模板", "请先选择要删除的作业模板。")
            return
        if QMessageBox.question(self, "确认删除", "删除后，任务会解除所属模板，相关模板关系也会删除。是否继续？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.database.delete_process_template(template_id)
        self.changed.emit()
        self.refresh()

    def _add_step(self) -> None:
        template_id = self._current_template_id()
        if template_id is None:
            QMessageBox.information(self, "请选择模板", "请先选择步骤所属的作业模板。")
            return
        dialog = WorkTemplateStepEditDialog(self.database.trade_names(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.database.add_work_template_step(template_id, *dialog.values())
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法添加步骤", str(error))
            return
        self.changed.emit()
        self._refresh_steps()

    def _edit_step(self) -> None:
        step_id, template_id = self._selected_id(self.step_table), self._current_template_id()
        if step_id is None or template_id is None:
            QMessageBox.information(self, "请选择步骤", "请先选择要修改的模板步骤。")
            return
        step = next(row for row in self.database.work_template_steps(template_id) if int(row["id"]) == step_id)
        dialog = WorkTemplateStepEditDialog(self.database.trade_names(), step, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.database.update_work_template_step(step_id, *dialog.values())
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法保存步骤", str(error))
            return
        self.changed.emit()
        self._refresh_steps()

    def _delete_step(self) -> None:
        step_id = self._selected_id(self.step_table)
        if step_id is None:
            QMessageBox.information(self, "请选择步骤", "请先选择要删除的模板步骤。")
            return
        self.database.delete_work_template_step(step_id)
        self.changed.emit()
        self._refresh_steps()

    def _add_step_rule(self) -> None:
        template_id = self._current_template_id()
        steps = self.database.work_template_steps(template_id) if template_id else []
        if len(steps) < 2:
            QMessageBox.information(self, "步骤不足", "请先在当前模板中添加至少两个步骤。")
            return
        dialog = TemplateRelationEditDialog(steps, "步骤", self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.database.add_work_template_step_dependency(template_id, *dialog.values())
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法添加步骤关系", str(error))
            return
        self.changed.emit()
        self._refresh_steps()

    def _delete_step_rule(self) -> None:
        relation_id = self._selected_id(self.step_rule_table)
        if relation_id is None:
            QMessageBox.information(self, "请选择关系", "请先选择要删除的步骤关系。")
            return
        self.database.delete_work_template_step_dependency(relation_id)
        self.changed.emit()
        self._refresh_steps()

    def _add_rule(self) -> None:
        templates = self.database.process_templates()
        if len(templates) < 2:
            QMessageBox.information(self, "模板不足", "请先建立至少两个作业模板。")
            return
        dialog = TemplateRelationEditDialog(templates, "模板", self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.database.add_process_template_dependency(*dialog.values())
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法添加模板衔接", str(error))
            return
        self.changed.emit()
        self.refresh()

    def _delete_rule(self) -> None:
        relation_id = self._selected_id(self.rule_table)
        if relation_id is None:
            QMessageBox.information(self, "请选择关系", "请先选择要删除的模板衔接。")
            return
        self.database.delete_process_template_dependency(relation_id)
        self.changed.emit()
        self.refresh()


class FlowRelationDialog(QDialog):
    def __init__(self, summaries: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("建立流水关系")
        self.setMinimumWidth(540)

        self.predecessor = QComboBox()
        self.successor = QComboBox()
        for summary in summaries:
            label = f"{summary['name']}（{summary['segment_count']}段）"
            self.predecessor.addItem(label, summary["id"])
            self.successor.addItem(label, summary["id"])
        if self.successor.count() > 1:
            self.successor.setCurrentIndex(1)

        self.minimum_lag = QSpinBox()
        self.minimum_lag.setRange(0, 9999)
        self.minimum_lag.setSuffix(" 自然日")

        explanation = QLabel(
            "软件会逐段建立工艺前后关系。最终是否连续由班组、作业面、设备和其他约束共同决定；"
            "出现间断时会保留真实结果。"
        )
        explanation.setWordWrap(True)

        form = QFormLayout()
        form.addRow("前道工序", self.predecessor)
        form.addRow("后道工序", self.successor)
        form.addRow("每段最小间隔", self.minimum_lag)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("建立流水关系")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def data(self) -> tuple[int, int, int]:
        return (
            int(self.predecessor.currentData()),
            int(self.successor.currentData()),
            self.minimum_lag.value(),
        )

    def _accept(self) -> None:
        if self.predecessor.currentData() == self.successor.currentData():
            QMessageBox.warning(self, "无法建立", "前道工序和后道工序不能相同。")
            return
        predecessor_text = self.predecessor.currentText()
        successor_text = self.successor.currentText()
        predecessor_count = predecessor_text.rsplit("（", 1)[-1]
        successor_count = successor_text.rsplit("（", 1)[-1]
        if predecessor_count != successor_count:
            QMessageBox.warning(self, "施工段不一致", "两道工序的施工段数量必须相同。")
            return
        self.accept()


class TradeManagerDialog(QDialog):
    changed = Signal()

    def __init__(self, database: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.editing_name: str | None = None
        self.selected_color = "#607D8B"
        self.setWindowTitle("工种与颜色")
        self.resize(650, 520)

        explanation = QLabel(
            "这里维护本工程可使用的工种。改名会同步更新已有任务、模板和班组；"
            "颜色用于任务表和横道图。"
        )
        explanation.setWordWrap(True)
        self.name = QLineEdit()
        self.name.setPlaceholderText("例如：防水工、焊工、消防安装工")
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._choose_color)
        self._update_color_button()
        self.save_button = QPushButton("新增工种")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self._save)
        cancel_edit = QPushButton("取消编辑")
        cancel_edit.clicked.connect(self._clear_editor)

        editor = QHBoxLayout()
        editor.addWidget(QLabel("工种名称"))
        editor.addWidget(self.name, 1)
        editor.addWidget(QLabel("显示颜色"))
        editor.addWidget(self.color_button)
        editor.addWidget(self.save_button)
        editor.addWidget(cancel_edit)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["工种", "横道图颜色"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda *_: self._load_selected())

        edit_button = QPushButton("编辑所选")
        edit_button.clicked.connect(self._load_selected)
        delete_button = QPushButton("删除所选")
        delete_button.clicked.connect(self._delete_selected)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.addWidget(edit_button)
        footer.addWidget(delete_button)
        footer.addStretch()
        footer.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addLayout(editor)
        layout.addWidget(self.table, 1)
        layout.addLayout(footer)
        self._refresh()

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.selected_color), self, "选择工种颜色")
        if color.isValid():
            self.selected_color = color.name().upper()
            self._update_color_button()

    def _update_color_button(self) -> None:
        color = QColor(self.selected_color)
        foreground = "#FFFFFF" if color.lightness() < 145 else "#263238"
        self.color_button.setText(self.selected_color)
        self.color_button.setStyleSheet(
            f"background: {self.selected_color}; color: {foreground};"
            " border: 1px solid #89949a; min-width: 92px;"
        )

    def _refresh(self) -> None:
        rows = self.database.trades()
        self.table.setRowCount(len(rows))
        for row_index, trade in enumerate(rows):
            name_item = QTableWidgetItem(str(trade["name"]))
            color_item = QTableWidgetItem(str(trade["color"]).upper())
            color = QColor(str(trade["color"]))
            color_item.setBackground(color)
            color_item.setForeground(
                QColor("#FFFFFF" if color.lightness() < 145 else "#263238")
            )
            self.table.setItem(row_index, 0, name_item)
            self.table.setItem(row_index, 1, color_item)
        self.table.resizeColumnsToContents()

    def _selected_name(self) -> str | None:
        row = self.table.currentRow()
        if row < 0 or self.table.item(row, 0) is None:
            return None
        return self.table.item(row, 0).text()

    def _load_selected(self) -> None:
        selected = self._selected_name()
        if selected is None:
            QMessageBox.information(self, "请选择工种", "请先选择要编辑的工种。")
            return
        trade = next(row for row in self.database.trades() if row["name"] == selected)
        self.editing_name = selected
        self.name.setText(selected)
        self.selected_color = str(trade["color"]).upper()
        self._update_color_button()
        self.save_button.setText("保存修改")

    def _clear_editor(self) -> None:
        self.editing_name = None
        self.name.clear()
        self.selected_color = "#607D8B"
        self._update_color_button()
        self.save_button.setText("新增工种")
        self.table.clearSelection()

    def _save(self) -> None:
        try:
            if self.editing_name is None:
                self.database.add_trade(self.name.text(), self.selected_color)
            else:
                self.database.update_trade(
                    self.editing_name, self.name.text(), self.selected_color
                )
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法保存工种", str(error))
            return
        self.changed.emit()
        self._clear_editor()
        self._refresh()

    def _delete_selected(self) -> None:
        selected = self._selected_name()
        if selected is None:
            QMessageBox.information(self, "请选择工种", "请先选择要删除的工种。")
            return
        answer = QMessageBox.question(
            self,
            "确认删除工种",
            f"确定删除“{selected}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.database.delete_trade(selected)
        except ValueError as error:
            QMessageBox.warning(self, "无法删除工种", str(error))
            return
        self.changed.emit()
        self._clear_editor()
        self._refresh()


class ResourceEditDialog(QDialog):
    """Focused add/edit form opened from the resource list."""

    TYPE_LABELS = {
        "workface": "作业面",
        "crew": "班组",
        "equipment": "设备",
        "access": "通行区域",
        "inspector": "检查人员",
    }

    def __init__(
        self,
        allowed_types: tuple[str, ...],
        values: dict | None = None,
        trades: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.values = values or {}
        self.is_spatial = set(allowed_types).issubset({"workface", "access"})
        entity_name = "空间" if self.is_spatial else "施工资源"
        self.setWindowTitle(("编辑" if values else "新增") + entity_name)
        self.resize(520, 420 if not self.is_spatial else 300)

        self.name = QLineEdit(str(self.values.get("name", "")))
        self.resource_type = QComboBox()
        for code in allowed_types:
            self.resource_type.addItem(self.TYPE_LABELS[code], code)
        self.resource_type.setCurrentIndex(
            max(0, self.resource_type.findData(self.values.get("resource_type")))
        )
        self.capacity = QDoubleSpinBox()
        self.capacity.setRange(0.01, 9999)
        self.capacity.setValue(float(self.values.get("capacity", 1)))
        self.enabled = QCheckBox("可用于排程")
        self.enabled.setChecked(bool(self.values.get("enabled", 1)))
        self.notes = QLineEdit(str(self.values.get("notes", "")))
        self.skills = QListWidget()
        self.skills.setMaximumHeight(125)
        selected_skills = set(self.values.get("skills", []))
        for trade in trades or list(TRADES):
            item = QListWidgetItem(trade)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if trade in selected_skills
                else Qt.CheckState.Unchecked
            )
            self.skills.addItem(item)
        self.availability_hint = QLabel()
        self.availability_hint.setWordWrap(True)

        form = QFormLayout()
        form.addRow("名称", self.name)
        form.addRow("类型", self.resource_type)
        form.addRow("并行容量", self.capacity)
        form.addRow("班组能力", self.skills)
        form.addRow("", self.enabled)
        form.addRow("备注", self.notes)
        form.addRow("日期可用性", self.availability_hint)
        if self.is_spatial:
            self.skills.setVisible(False)
            skills_label = form.labelForField(self.skills)
            if skills_label is not None:
                skills_label.setVisible(False)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch()
        layout.addWidget(buttons)
        self.resource_type.currentIndexChanged.connect(self._sync_resource_type)
        self._sync_resource_type()
        self.name.setFocus()

    def _selected_skills(self) -> list[str]:
        return [
            self.skills.item(index).text()
            for index in range(self.skills.count())
            if self.skills.item(index).checkState() == Qt.CheckState.Checked
        ]

    def _sync_resource_type(self) -> None:
        is_crew = self.resource_type.currentData() == "crew"
        self.skills.setEnabled(is_crew)
        self.skills.setToolTip(
            "一个班组可以勾选多个工种能力。"
            if is_crew
            else "只有班组需要设置工种能力。"
        )
        self.availability_hint.setText(
            "班组默认跟随项目工作日历；保存后可在资源列表中维护具体上班日期。"
            if is_crew
            else (
                "当前按该空间在计划期内所有日期均可用。"
                if self.is_spatial
                else "当前按该资源在计划期内所有日期均可用。"
            )
        )

    def _accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "请填写名称", "名称不能为空。")
            return
        if self.resource_type.currentData() == "crew" and not self._selected_skills():
            QMessageBox.warning(self, "请设置班组能力", "班组至少需要勾选一个工种能力。")
            return
        self.accept()

    def data(self) -> dict:
        resource_type = str(self.resource_type.currentData())
        return {
            "name": self.name.text().strip(),
            "resource_type": resource_type,
            "capacity": self.capacity.value(),
            "enabled": self.enabled.isChecked(),
            "notes": self.notes.text().strip(),
            "skills": self._selected_skills() if resource_type == "crew" else [],
        }


class ResourceManagerDialog(QDialog):
    changed = Signal()

    TYPE_LABELS = {
        "workface": "作业面",
        "crew": "班组",
        "equipment": "设备",
        "access": "通行区域",
        "inspector": "检查人员",
    }

    def __init__(
        self,
        database: Database,
        parent: QWidget | None = None,
        allowed_types: tuple[str, ...] | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.database = database
        self.allowed_types = allowed_types or (
            "crew",
            "equipment",
            "inspector",
        )
        self.is_spatial_manager = set(self.allowed_types).issubset({"workface", "access"})
        self.entity_name = "空间" if self.is_spatial_manager else "资源"
        self.setWindowTitle(title or ("作业面与空间" if self.is_spatial_manager else "施工资源"))
        self.resize(850, 540)

        self.category_tabs: QTabBar | None = None
        if not self.is_spatial_manager and "equipment" in self.allowed_types:
            self.category_tabs = QTabBar()
            self.category_tabs.addTab("人员与班组")
            self.category_tabs.addTab("设备")
            self.category_tabs.setExpanding(False)
            self.category_tabs.currentChanged.connect(self._change_category)

        self.add_button = QPushButton(f"新增{self.entity_name}")
        self.add_button.setObjectName("PrimaryButton")
        self.add_button.clicked.connect(self._add)
        self.edit_button = QPushButton("编辑所选")
        self.edit_button.clicked.connect(self._edit_selected)
        delete = QPushButton("删除所选")
        delete.clicked.connect(self._delete)
        self.delete_button = delete
        self.crew_calendar_button = QPushButton("班组上班日历")
        self.crew_calendar_button.setToolTip("选择一个已保存的班组后维护每日上班状态")
        self.crew_calendar_button.clicked.connect(self._manage_crew_calendar)
        self.crew_schedule_button = QPushButton("查看实际排班")
        self.crew_schedule_button.setToolTip(
            "查看所选班组在当前排程中真正执行任务的日期和时间段"
        )
        self.crew_schedule_button.clicked.connect(self._view_crew_schedule)
        actions = QHBoxLayout()
        actions.addWidget(self.add_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(delete)
        if not self.is_spatial_manager:
            actions.addWidget(self.crew_calendar_button)
            actions.addWidget(self.crew_schedule_button)
        actions.addStretch()

        self.table = QTableWidget(0, 5 if self.is_spatial_manager else 6)
        self.table.setHorizontalHeaderLabels(
            ["名称", "类型", "并行容量", "状态", "备注"]
            if self.is_spatial_manager
            else ["名称", "类型", "班组能力", "并行容量", "状态", "备注"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._load_selected)
        self.table.doubleClicked.connect(lambda *_: self._edit_selected())

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        if self.category_tabs is not None:
            layout.addWidget(self.category_tabs)
        layout.addLayout(actions)
        layout.addWidget(self.table, 1)
        layout.addWidget(close)
        self._update_category_actions()
        self._refresh()

    def _active_allowed_types(self) -> tuple[str, ...]:
        if self.category_tabs is None:
            return self.allowed_types
        category = (
            ("crew", "inspector")
            if self.category_tabs.currentIndex() == 0
            else ("equipment",)
        )
        return tuple(item for item in category if item in self.allowed_types)

    def _change_category(self, _index: int) -> None:
        self._update_category_actions()
        self._refresh()

    def _update_category_actions(self) -> None:
        if self.category_tabs is None:
            self.add_button.setText(f"新增{self.entity_name}")
        elif self.category_tabs.currentIndex() == 0:
            self.add_button.setText("新增人员/班组")
        else:
            self.add_button.setText("新增设备")
        if not self.is_spatial_manager:
            equipment_only = (
                self.category_tabs is not None
                and self.category_tabs.currentIndex() == 1
            )
            self.crew_calendar_button.setVisible(not equipment_only)
            self.crew_schedule_button.setVisible(not equipment_only)
            if hasattr(self, "table"):
                self.table.setColumnCount(5 if equipment_only else 6)
                self.table.setHorizontalHeaderLabels(
                    ["名称", "类型", "并行容量", "状态", "备注"]
                    if equipment_only
                    else ["名称", "类型", "班组能力", "并行容量", "状态", "备注"]
                )

    def _refresh(self) -> None:
        rows = [
            row
            for row in self.database.resources()
            if row["resource_type"] in self._active_allowed_types()
            and row.get("path_segment_id") is None
        ]
        self._resources = rows
        self.table.setRowCount(len(rows))
        equipment_only = (
            not self.is_spatial_manager
            and self.category_tabs is not None
            and self.category_tabs.currentIndex() == 1
        )
        for row_index, resource in enumerate(rows):
            base_values = (
                resource["name"],
                self.TYPE_LABELS.get(resource["resource_type"], resource["resource_type"]),
            )
            values = (
                base_values
                + (
                    f"{float(resource['capacity']):g}",
                    "可用" if resource["enabled"] else "停用",
                    resource["notes"],
                )
                if self.is_spatial_manager or equipment_only
                else base_values
                + (
                    (
                        "、".join(resource.get("skills", [])) or "能力未设置"
                        if resource["resource_type"] == "crew"
                        else "—"
                    ),
                    f"{float(resource['capacity']):g}",
                    "可用" if resource["enabled"] else "停用",
                    resource["notes"],
                )
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, int(resource["id"]))
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()
        self._load_selected()

    def _selected_resource(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0 or self.table.item(row, 0) is None:
            return None
        resource_id = int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        return next(
            (
                resource
                for resource in getattr(self, "_resources", [])
                if int(resource["id"]) == resource_id
            ),
            None,
        )

    def _load_selected(self) -> None:
        resource = self._selected_resource()
        selected = resource is not None
        is_crew = selected and resource["resource_type"] == "crew"
        self.edit_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)
        self.crew_calendar_button.setEnabled(bool(is_crew))
        self.crew_schedule_button.setEnabled(bool(is_crew))

    def _add(self) -> None:
        dialog = ResourceEditDialog(
            self._active_allowed_types(),
            trades=self.database.trade_names(),
            parent=self,
        )
        if not dialog.exec():
            return
        values = dialog.data()
        try:
            self.database.add_resource(
                values["name"],
                values["resource_type"],
                values["capacity"],
                values["notes"],
                values["skills"],
                values["enabled"],
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, f"无法新增{self.entity_name}", str(error))
            return
        self.changed.emit()
        self._refresh()

    def _edit_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            QMessageBox.information(
                self, f"请选择{self.entity_name}", f"请先选择要编辑的{self.entity_name}。"
            )
            return
        dialog = ResourceEditDialog(
            self._active_allowed_types(),
            resource,
            trades=self.database.trade_names(),
            parent=self,
        )
        if not dialog.exec():
            return
        values = dialog.data()
        try:
            self.database.update_resource(
                int(resource["id"]),
                values["name"],
                values["resource_type"],
                values["capacity"],
                values["enabled"],
                values["notes"],
                values["skills"],
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, f"无法保存{self.entity_name}", str(error))
            return
        self.changed.emit()
        self._refresh()

    def _manage_crew_calendar(self) -> None:
        resource = self._selected_resource()
        if resource is None or resource["resource_type"] != "crew":
            QMessageBox.information(self, "请选择班组", "请先选择一个已保存的班组。")
            return
        dialog = CrewCalendarDialog(
            self.database,
            int(resource["id"]),
            str(resource["name"]),
            self,
        )
        dialog.exec()
        if dialog.has_changes:
            self.changed.emit()

    def _view_crew_schedule(self) -> None:
        resource = self._selected_resource()
        if resource is None or resource["resource_type"] != "crew":
            QMessageBox.information(self, "请选择班组", "请先选择一个已保存的班组。")
            return
        CrewActualScheduleDialog(self.database, resource, self).exec()

    def _delete(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            QMessageBox.information(
                self, f"请选择{self.entity_name}", f"请先选择要删除的{self.entity_name}。"
            )
            return
        answer = QMessageBox.question(
            self,
            f"确认删除{self.entity_name}",
            f"删除后，所有任务对该{self.entity_name}的占用都会一并解除。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.database.delete_resource(int(resource["id"]))
        self.changed.emit()
        self._refresh()


class ConstraintOverviewDialog(QDialog):
    """Read-only explanation of all constraints currently entering the scheduler."""

    def __init__(self, database: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("排程约束总览")
        self.resize(1050, 560)

        explanation = QLabel(
            "这里统一显示时间关系、事件状态、班组能力、作业面和设备容量。"
            "时间关系在“约束关系”中维护，其余约束在任务/事件或作业面与资源中维护。"
        )
        explanation.setWordWrap(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "输入任务、事件、班组、作业面、路径或规则内容"
        )
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("全部约束类型", None)
        self.result_count = QLabel()
        clear_search = QPushButton("清除查询")
        clear_search.clicked.connect(self._clear_filter)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("查询"))
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.kind_filter)
        search_row.addWidget(clear_search)
        search_row.addWidget(self.result_count)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["约束大类", "业务类别", "约束对象", "规则", "当前状态"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)

        kind_labels = {
            "temporal": "时间",
            "state": "事件/状态",
            "resource": "能力/资源",
            "spatial": "空间",
            "calendar": "工作日历",
        }
        for code, label in kind_labels.items():
            self.kind_filter.addItem(label, code)
        self.kind_filter.currentIndexChanged.connect(self._apply_filter)
        category_labels = CONSTRAINT_CATEGORY_LABELS | {
            "date": "日期",
            "event": "事件",
            "crew": "班组能力",
            "crew_calendar": "班组日历",
            "workface": "作业面",
            "access": "通行区域",
            "equipment": "设备",
            "inspector": "检查人员",
            "noise": "噪音限制",
        }
        rows = database.constraint_overview()
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                kind_labels.get(row["kind"], row["kind"]),
                category_labels.get(row["category"], row["category"]),
                row["subject"],
                row["rule"],
                row["status"],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row["kind"])
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()
        self._apply_filter()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addLayout(search_row)
        layout.addWidget(self.table)
        layout.addWidget(buttons)

    def _apply_filter(self, *_args) -> None:
        query = self.search.text().strip().casefold()
        selected_kind = self.kind_filter.currentData()
        matched = 0
        for row in range(self.table.rowCount()):
            kind_item = self.table.item(row, 0)
            kind_matches = (
                selected_kind is None
                or kind_item.data(Qt.ItemDataRole.UserRole) == selected_kind
            )
            text_matches = not query or any(
                query in self.table.item(row, column).text().casefold()
                for column in range(self.table.columnCount())
                if self.table.item(row, column) is not None
            )
            visible = kind_matches and text_matches
            self.table.setRowHidden(row, not visible)
            if visible:
                matched += 1
        self.result_count.setText(f"找到 {matched} 条")

    def _clear_filter(self) -> None:
        self.search.clear()
        self.kind_filter.setCurrentIndex(0)
        self._apply_filter()


class RelationsDialog(QDialog):
    changed = Signal()

    def __init__(self, database: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.last_error: str | None = None
        self.editing_id: int | None = None
        self.setWindowTitle("约束关系")
        self.resize(1180, 520)

        explanation = QLabel(
            "任务和事件使用同一套时间约束；业务类别说明这条关系为什么存在。"
            "FS：前项完成后后项开始　SS：同时开始　FF：同时完成　SF：前项开始后后项完成。"
            "编辑时只选一行；删除时可用 Ctrl/Shift 多选。"
            "选中任意一条跨工序的逐段关系，可删除整组流水关系。"
        )
        explanation.setWordWrap(True)

        self.predecessor = QComboBox()
        self.successor = QComboBox()
        self.relation_type = QComboBox()
        for code, label in RELATION_LABELS.items():
            self.relation_type.addItem(label, code)
        self.lag_days = QSpinBox()
        self.lag_days.setRange(0, 9999)
        self.lag_unit = QComboBox()
        for code, label in LAG_UNIT_LABELS.items():
            self.lag_unit.addItem(label, code)
        self.constraint_category = QComboBox()
        for code, label in CONSTRAINT_CATEGORY_LABELS.items():
            self.constraint_category.addItem(label, code)

        self.save_button = QPushButton("添加关系")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self._save_relation)
        self.cancel_edit_button = QPushButton("取消编辑")
        self.cancel_edit_button.clicked.connect(self._reset_editor)
        self.cancel_edit_button.setVisible(False)
        edit_button = QPushButton("编辑选中关系")
        edit_button.clicked.connect(self._load_selected)
        delete_button = QPushButton("批量删除选中关系")
        delete_button.clicked.connect(self._delete)
        delete_flow_button = QPushButton("删除整组流水关系")
        delete_flow_button.clicked.connect(self._delete_flow_group)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("类别"))
        add_row.addWidget(self.constraint_category)
        add_row.addWidget(QLabel("前置任务"))
        add_row.addWidget(self.predecessor, 1)
        add_row.addWidget(QLabel("关系"))
        add_row.addWidget(self.relation_type)
        add_row.addWidget(QLabel("间隔"))
        add_row.addWidget(self.lag_days)
        add_row.addWidget(self.lag_unit)
        add_row.addWidget(QLabel("后续任务"))
        add_row.addWidget(self.successor, 1)
        add_row.addWidget(self.save_button)
        add_row.addWidget(self.cancel_edit_button)

        self.task_search = QLineEdit()
        self.task_search.setPlaceholderText("输入任务名称，搜索其前置或后续关系")
        self.task_search.setClearButtonEnabled(True)
        self.task_search.textChanged.connect(self._apply_search_filter)
        self.task_search.returnPressed.connect(self._jump_to_next_match)
        search_button = QPushButton("定位下一条")
        search_button.clicked.connect(self._jump_to_next_match)
        clear_search_button = QPushButton("显示全部")
        clear_search_button.clicked.connect(self.task_search.clear)
        self.search_result = QLabel()
        self.search_result.setStyleSheet("color: #607783;")
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("搜索任务"))
        search_row.addWidget(self.task_search, 1)
        search_row.addWidget(search_button)
        search_row.addWidget(clear_search_button)
        search_row.addWidget(self.search_result)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["来源", "业务类别", "前置任务/事件", "关系类型", "间隔", "单位", "后续任务/事件"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda *_: self._load_selected())

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close_buttons.rejected.connect(self.reject)

        footer = QHBoxLayout()
        footer.addWidget(edit_button)
        select_all_button = QPushButton("全选")
        select_all_button.clicked.connect(self.table.selectAll)
        footer.addWidget(select_all_button)
        footer.addWidget(delete_button)
        footer.addWidget(delete_flow_button)
        footer.addStretch()
        footer.addWidget(close_buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addLayout(add_row)
        layout.addLayout(search_row)
        layout.addWidget(self.table)
        layout.addLayout(footer)
        self.refresh()

    def refresh(self) -> None:
        leaf_tasks = self.database.leaf_tasks()
        task_names = sorted({str(task["name"]) for task in leaf_tasks})
        completer = QCompleter(task_names, self.task_search)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.task_search.setCompleter(completer)
        for combo in (self.predecessor, self.successor):
            current = combo.currentData()
            combo.clear()
            for task in leaf_tasks:
                combo.addItem(task["name"], task["id"])
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)

        rows = self.database.dependencies()
        self.table.setRowCount(len(rows))
        for row_index, relation in enumerate(rows):
            values = (
                (
                    "模板步骤继承"
                    if relation.get("source_type") == "work_template"
                    else "模板衔接继承"
                    if relation.get("source_type") == "process_template"
                    else "手工"
                ),
                CONSTRAINT_CATEGORY_LABELS.get(
                    relation.get("constraint_category", "process"), "其他"
                ),
                relation["predecessor_name"],
                RELATION_LABELS[relation["relation_type"]],
                str(relation["lag_days"]),
                LAG_UNIT_LABELS.get(relation.get("lag_unit", "calendar_day"), "自然日"),
                relation["successor_name"],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, relation["id"])
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()
        self._apply_search_filter()

    def _matching_rows(self) -> list[int]:
        query = self.task_search.text().strip().casefold()
        if not query:
            return list(range(self.table.rowCount()))
        matches: list[int] = []
        for row in range(self.table.rowCount()):
            predecessor = self.table.item(row, 2).text().casefold()
            successor = self.table.item(row, 6).text().casefold()
            if query in predecessor or query in successor:
                matches.append(row)
        return matches

    def _apply_search_filter(self) -> None:
        matches = set(self._matching_rows())
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, row not in matches)
        if self.task_search.text().strip():
            self.search_result.setText(f"找到 {len(matches)} 条关系")
        else:
            self.search_result.setText(f"共 {self.table.rowCount()} 条关系")

    def _jump_to_next_match(self) -> None:
        if not self.task_search.text().strip():
            QMessageBox.information(self, "请输入任务", "请先输入需要查找的任务名称。")
            return
        matches = self._matching_rows()
        if not matches:
            QMessageBox.information(self, "没有找到", "没有找到包含该任务的关系。")
            return
        selected = self.table.selectionModel().selectedRows()
        current_row = selected[0].row() if selected else -1
        next_row = next((row for row in matches if row > current_row), matches[0])
        self.table.clearSelection()
        self.table.selectRow(next_row)
        self.table.setCurrentCell(next_row, 0)
        self.table.scrollToItem(self.table.item(next_row, 0))

    def _save_relation(self) -> None:
        predecessor = self.predecessor.currentData()
        successor = self.successor.currentData()
        if predecessor is None or successor is None:
            QMessageBox.information(self, "没有可选任务", "请先建立至少两个最末级任务。")
            return
        if predecessor == successor:
            QMessageBox.warning(self, "无法保存", "前置任务和后续任务不能相同。")
            return
        if self._would_create_cycle(int(predecessor), int(successor)):
            QMessageBox.warning(
                self,
                "无法保存",
                "这条关系会形成循环，请调整前置任务或后续任务。",
            )
            return
        relation_type = self.relation_type.currentData()
        lag_days = self.lag_days.value()
        if self.editing_id is None:
            try:
                relation_id = self.database.add_dependency(
                    predecessor,
                    successor,
                    relation_type,
                    lag_days,
                    self.constraint_category.currentData(),
                    self.lag_unit.currentData(),
                )
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, "无法添加", "这两个任务之间已经存在关系。")
                return
            self.changed.emit()
            if self.last_error:
                self.database.delete_dependency(relation_id)
                error = self.last_error
                self.changed.emit()
                QMessageBox.warning(self, "无法添加", error)
                return
        else:
            old = self.database.dependency(self.editing_id)
            if old is None:
                self._reset_editor()
                self.refresh()
                return
            try:
                self.database.update_dependency(
                    self.editing_id,
                    predecessor,
                    successor,
                    relation_type,
                    lag_days,
                    self.constraint_category.currentData(),
                    self.lag_unit.currentData(),
                )
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, "无法修改", "这两个任务之间已经存在关系。")
                return
            self.changed.emit()
            if self.last_error:
                error = self.last_error
                self.database.update_dependency(
                    old["id"],
                    old["predecessor_id"],
                    old["successor_id"],
                    old["relation_type"],
                    old["lag_days"],
                    old.get("constraint_category", "process"),
                    old.get("lag_unit", "calendar_day"),
                )
                self.changed.emit()
                QMessageBox.warning(self, "无法修改", error)
                return
        self._reset_editor()
        self.refresh()

    def _would_create_cycle(self, predecessor_id: int, successor_id: int) -> bool:
        """Check relation loops without running the full project scheduler."""
        outgoing: dict[int, list[int]] = {}
        for relation in self.database.dependencies():
            if self.editing_id is not None and int(relation["id"]) == self.editing_id:
                continue
            outgoing.setdefault(int(relation["predecessor_id"]), []).append(
                int(relation["successor_id"])
            )

        pending = [successor_id]
        visited: set[int] = set()
        while pending:
            task_id = pending.pop()
            if task_id == predecessor_id:
                return True
            if task_id in visited:
                continue
            visited.add(task_id)
            pending.extend(outgoing.get(task_id, ()))
        return False

    def _load_selected(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "请选择关系", "请先选择要编辑的任务关系。")
            return
        if len(selected) > 1:
            QMessageBox.information(self, "只能编辑一条", "编辑关系时请只选择一行。")
            return
        item = self.table.item(selected[0].row(), 0)
        relation = self.database.dependency(int(item.data(Qt.ItemDataRole.UserRole)))
        if relation is None:
            self.refresh()
            return
        if relation.get("source_type") in {"process_template", "work_template"}:
            QMessageBox.information(
                self, "由作业模板库管理",
                "这条关系由作业模板继承生成，请在“作业模板库”中修改规则。",
            )
            return
        self.editing_id = relation["id"]
        self.predecessor.setCurrentIndex(
            self.predecessor.findData(relation["predecessor_id"])
        )
        self.successor.setCurrentIndex(self.successor.findData(relation["successor_id"]))
        self.relation_type.setCurrentIndex(
            self.relation_type.findData(relation["relation_type"])
        )
        self.lag_days.setValue(relation["lag_days"])
        self.constraint_category.setCurrentIndex(
            self.constraint_category.findData(
                relation.get("constraint_category", "process")
            )
        )
        self.lag_unit.setCurrentIndex(
            self.lag_unit.findData(relation.get("lag_unit", "calendar_day"))
        )
        self.save_button.setText("保存修改")
        self.cancel_edit_button.setVisible(True)

    def _reset_editor(self) -> None:
        self.editing_id = None
        self.save_button.setText("添加关系")
        self.cancel_edit_button.setVisible(False)
        self.lag_days.setValue(0)
        self.constraint_category.setCurrentIndex(0)
        self.lag_unit.setCurrentIndex(0)
        self.table.clearSelection()

    def _delete(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "请选择关系", "请先选择要删除的任务关系。")
            return
        dependency_ids = sorted(
            {
                int(self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
                for index in selected
            }
        )
        generated = [
            dependency_id for dependency_id in dependency_ids
            if (self.database.dependency(dependency_id) or {}).get("source_type")
            in {"process_template", "work_template"}
        ]
        if generated:
            QMessageBox.information(
                self, "包含继承关系",
                "选中内容包含模板继承关系。请到“作业模板库”修改或删除模板规则。",
            )
            return
        answer = QMessageBox.question(
            self,
            "确认批量删除",
            f"确定删除选中的 {len(dependency_ids)} 条任务关系吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.database.delete_dependencies(dependency_ids)
        if self.editing_id in dependency_ids:
            self._reset_editor()
        self.changed.emit()
        self.refresh()

    def _delete_flow_group(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if len(selected) != 1:
            QMessageBox.information(
                self, "请选择一条流水关系", "请在要删除的流水关系组中选择任意一行。"
            )
            return
        dependency_id = int(
            self.table.item(selected[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        )
        if (self.database.dependency(dependency_id) or {}).get("source_type") in {
            "process_template", "work_template"
        }:
            QMessageBox.information(
                self, "由作业模板库管理", "模板继承关系不能作为手工流水关系删除。"
            )
            return
        try:
            dependency_ids = self.database.flow_dependency_ids(dependency_id)
        except ValueError as error:
            QMessageBox.warning(self, "不是流水关系", str(error))
            return
        answer = QMessageBox.question(
            self,
            "确认删除整组流水关系",
            f"将一次删除这两道工序之间的 {len(dependency_ids)} 条逐段关系，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.database.delete_dependencies(dependency_ids)
        if self.editing_id in dependency_ids:
            self._reset_editor()
        self.changed.emit()
        self.refresh()


class WorkCalendarDialog(QDialog):
    changed = Signal()

    def __init__(self, database: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("工作日历")
        self.resize(920, 680)
        self.weekend_working = bool(database.settings().get("weekend_working", 0))
        self._formatted_dates: set[QDate] = set()
        self._exceptions: dict[str, dict] = {}

        default_rule = (
            "当前规则：周一至周日均为工作日。"
            if self.weekend_working
            else "当前规则：周一至周五工作，周六、周日休息。"
        )
        description = QLabel(
            default_rule + "点击日期后可改成施工日或不施工日；单日设置优先于默认规则。"
        )
        description.setWordWrap(True)

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setSelectedDate(QDate.currentDate())
        self.calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        self.calendar.selectionChanged.connect(self._load_selected_day)
        self.calendar.currentPageChanged.connect(lambda *_: self._paint_calendar())

        legend = QHBoxLayout()
        for text, color, foreground in (
            ("正常施工日", "#e8f5e9", "#245c32"),
            ("默认休息日", "#eef1f3", "#69777e"),
            ("手动设为施工", "#bfe8c6", "#145c2e"),
            ("手动设为不施工", "#ffcaca", "#9c1c1c"),
        ):
            item = QLabel(f"■ {text}")
            item.setStyleSheet(
                f"color: {foreground}; background: {color}; padding: 5px 8px;"
                " border-radius: 4px;"
            )
            legend.addWidget(item)
        legend.addStretch()

        self.selected_date_label = QLabel()
        self.selected_date_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.selected_status = QLabel()
        self.selected_status.setWordWrap(True)
        self.exception_name = QLineEdit()
        self.exception_name.setPlaceholderText("例如：现场停工、国庆节、调休上班")
        rest_button = QPushButton("设为不施工")
        rest_button.setObjectName("PrimaryButton")
        rest_button.clicked.connect(lambda: self._save_selected_day(False))
        work_button = QPushButton("设为施工日")
        work_button.clicked.connect(lambda: self._save_selected_day(True))
        restore_button = QPushButton("恢复默认规则")
        restore_button.clicked.connect(self._restore_selected_day)

        editor = QVBoxLayout()
        editor.addWidget(self.selected_date_label)
        editor.addWidget(self.selected_status)
        editor.addSpacing(8)
        editor.addWidget(QLabel("当天说明（可选）"))
        editor.addWidget(self.exception_name)
        editor.addWidget(rest_button)
        editor.addWidget(work_button)
        editor.addWidget(restore_button)
        editor.addStretch()

        calendar_row = QHBoxLayout()
        calendar_row.addWidget(self.calendar, 3)
        calendar_row.addLayout(editor, 2)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["已修改日期", "星期", "施工状态", "说明"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda *_: self._select_table_day())

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close_buttons.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.addWidget(QLabel("下表列出手动修改过的日期；双击可在月历中定位。"))
        footer.addStretch()
        footer.addWidget(close_buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(description)
        layout.addLayout(legend)
        layout.addLayout(calendar_row, 3)
        layout.addWidget(self.table, 2)
        layout.addLayout(footer)
        self.refresh()

    def refresh(self) -> None:
        rows = self.database.calendar_exceptions()
        self._exceptions = {row["exception_date"]: row for row in rows}
        self.table.setRowCount(len(rows))
        week_names = "一二三四五六日"
        for row_index, row in enumerate(rows):
            qdate = QDate.fromString(row["exception_date"], "yyyy-MM-dd")
            values = (
                row["exception_date"],
                "星期" + week_names[qdate.dayOfWeek() - 1],
                "工作日" if row["is_working"] else "非工作日",
                row["name"],
            )
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self._paint_calendar()
        self._load_selected_day()

    def _save_selected_day(self, is_working: bool) -> None:
        self.database.save_calendar_exception(
            self.calendar.selectedDate().toString("yyyy-MM-dd"),
            is_working,
            self.exception_name.text().strip(),
        )
        self.changed.emit()
        self.refresh()

    def _restore_selected_day(self) -> None:
        day = self.calendar.selectedDate().toString("yyyy-MM-dd")
        self.database.delete_calendar_exception(day)
        self.changed.emit()
        self.refresh()

    def _load_selected_day(self) -> None:
        selected = self.calendar.selectedDate()
        day = selected.toString("yyyy-MM-dd")
        week_names = "一二三四五六日"
        self.selected_date_label.setText(
            f"{day}　星期{week_names[selected.dayOfWeek() - 1]}"
        )
        exception = self._exceptions.get(day)
        default_working = self.weekend_working or selected.dayOfWeek() <= 5
        if exception is None:
            self.selected_status.setText(
                "当前：按默认规则施工"
                if default_working
                else "当前：按默认规则休息，不施工"
            )
            self.exception_name.clear()
        else:
            self.selected_status.setText(
                "当前：手动设为施工日"
                if exception["is_working"]
                else "当前：手动设为不施工日"
            )
            self.exception_name.setText(exception["name"])

    def _select_table_day(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        selected = QDate.fromString(self.table.item(row, 0).text(), "yyyy-MM-dd")
        if selected.isValid():
            self.calendar.setSelectedDate(selected)
            self.calendar.showSelectedDate()

    def _paint_calendar(self) -> None:
        for day in self._formatted_dates:
            self.calendar.setDateTextFormat(day, QTextCharFormat())
        self._formatted_dates.clear()

        year = self.calendar.yearShown()
        month = self.calendar.monthShown()
        first = QDate(year, month, 1)
        for day_number in range(1, first.daysInMonth() + 1):
            day = QDate(year, month, day_number)
            exception = self._exceptions.get(day.toString("yyyy-MM-dd"))
            default_working = self.weekend_working or day.dayOfWeek() <= 5
            text_format = QTextCharFormat()
            if exception is not None and exception["is_working"]:
                text_format.setBackground(QColor("#bfe8c6"))
                text_format.setForeground(QColor("#145c2e"))
                text_format.setFontWeight(700)
            elif exception is not None:
                text_format.setBackground(QColor("#ffcaca"))
                text_format.setForeground(QColor("#9c1c1c"))
                text_format.setFontWeight(700)
            elif default_working:
                text_format.setBackground(QColor("#e8f5e9"))
                text_format.setForeground(QColor("#263238"))
            else:
                text_format.setBackground(QColor("#eef1f3"))
                text_format.setForeground(QColor("#89949a"))
            self.calendar.setDateTextFormat(day, text_format)
            self._formatted_dates.add(day)


class CrewActualScheduleDialog(QDialog):
    """Read-only view of one crew's productive time in the current schedule."""

    def __init__(
        self,
        database: Database,
        crew: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"实际排班 - {crew['name']}")
        self.resize(1180, 680)

        settings = database.settings()
        calendar = WorkCalendar.from_settings(
            settings, database.calendar_exception_map()
        )
        calendar = calendar.limited_daily_hours(
            min(float(settings.get("crew_max_daily_hours", 8)), calendar.HOURS_PER_DAY)
        )
        self.rows = crew_actual_work_intervals(
            database.tasks(),
            crew,
            calendar,
            max_consecutive_days=int(
                settings.get("crew_max_consecutive_days", 0)
            ),
        )
        self.hours_by_date: dict[str, float] = {}
        for row in self.rows:
            raw_date = row["start"].strftime("%Y-%m-%d")
            self.hours_by_date[raw_date] = (
                self.hours_by_date.get(raw_date, 0) + float(row["hours"])
            )

        description = QLabel(
            "这里显示当前排程真正分配给该班组的工作时间，不是考勤记录。"
            "休息日、夜间和午休不会计入工作日历任务；自然连续任务按连续占用时间显示。"
        )
        description.setWordWrap(True)

        total_hours = sum(float(row["hours"]) for row in self.rows)
        working_days = {row["start"].date() for row in self.rows}
        if self.rows:
            summary_text = (
                f"共 {len(working_days)} 个实际工作日，{total_hours:g} 小时；"
                f"从 {self.rows[0]['start']:%Y-%m-%d %H:%M} "
                f"到 {self.rows[-1]['finish']:%Y-%m-%d %H:%M}"
            )
        else:
            summary_text = "当前排程尚未给该班组安排工作。"
        summary = QLabel(summary_text)
        summary.setStyleSheet("font-size: 15px; font-weight: 600;")

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setMinimumWidth(390)
        self.calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        if self.rows:
            first_date = self.rows[0]["start"].date()
            self.calendar.setSelectedDate(
                QDate(first_date.year, first_date.month, first_date.day)
            )
        self._paint_calendar()

        calendar_legend = QHBoxLayout()
        for text, color, foreground in (
            ("全天排班", "#a9dfb5", "#175c2c"),
            ("部分时段", "#ffe3a1", "#795400"),
        ):
            item = QLabel(f"■ {text}")
            item.setStyleSheet(
                f"color: {foreground}; background: {color}; padding: 5px 8px;"
                " border-radius: 4px;"
            )
            calendar_legend.addWidget(item)
        calendar_legend.addStretch()

        self.day_summary = QLabel()
        self.day_summary.setWordWrap(True)
        self.day_summary.setStyleSheet("font-size: 14px; font-weight: 600;")
        show_all = QPushButton("查看全部日期")
        show_all.setToolTip("取消按日筛选，显示该班组的全部实际排班")
        show_all.clicked.connect(self._show_all_rows)

        calendar_side = QVBoxLayout()
        calendar_side.addWidget(self.calendar)
        calendar_side.addLayout(calendar_legend)
        calendar_side.addWidget(self.day_summary)
        calendar_side.addWidget(show_all)
        calendar_side.addStretch()

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["日期", "星期", "实际时间段", "工时", "执行任务", "作业面/路径"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        content = QHBoxLayout()
        content.addLayout(calendar_side, 2)
        content.addWidget(self.table, 3)

        self.calendar.selectionChanged.connect(self._show_selected_day)
        if self.rows:
            self._show_selected_day()
        else:
            self.day_summary.setText("当前没有实际排班日期。")
            self._refresh_table([])

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close_buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(description)
        layout.addWidget(summary)
        layout.addLayout(content, 1)
        layout.addWidget(close_buttons)

    def _paint_calendar(self) -> None:
        for raw_date, hours in self.hours_by_date.items():
            day = QDate.fromString(raw_date, "yyyy-MM-dd")
            text_format = QTextCharFormat()
            if hours >= 8 - 1e-9:
                text_format.setBackground(QColor("#a9dfb5"))
                text_format.setForeground(QColor("#175c2c"))
            else:
                text_format.setBackground(QColor("#ffe3a1"))
                text_format.setForeground(QColor("#795400"))
            text_format.setFontWeight(700)
            self.calendar.setDateTextFormat(day, text_format)

    def _show_selected_day(self) -> None:
        raw_date = self.calendar.selectedDate().toString("yyyy-MM-dd")
        rows = [
            row for row in self.rows if row["start"].strftime("%Y-%m-%d") == raw_date
        ]
        hours = sum(float(row["hours"]) for row in rows)
        self.day_summary.setText(
            f"{raw_date}：{hours:g} 小时，{len({row['task_id'] for row in rows})} 项任务"
            if rows
            else f"{raw_date}：当天没有实际排班"
        )
        self._refresh_table(rows)

    def _show_all_rows(self) -> None:
        self.day_summary.setText(
            f"全部排班：{len(self.hours_by_date)} 个工作日，"
            f"{sum(float(row['hours']) for row in self.rows):g} 小时"
        )
        self._refresh_table(self.rows)

    def _refresh_table(self, rows: list[dict]) -> None:
        self.table.setRowCount(len(rows))
        week_names = "一二三四五六日"
        for row_index, row in enumerate(rows):
            start = row["start"]
            finish = row["finish"]
            values = (
                start.strftime("%Y-%m-%d"),
                "星期" + week_names[start.weekday()],
                f"{start:%H:%M}–{finish:%H:%M}",
                f"{float(row['hours']):g} 小时",
                row["task_name"],
                row["location"] or "—",
            )
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()


class CrewCalendarDialog(QDialog):
    changed = Signal()

    def __init__(
        self,
        database: Database,
        resource_id: int,
        crew_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.database = database
        self.resource_id = int(resource_id)
        self.crew_name = crew_name
        self.has_changes = False
        self._selected_dates: set[str] = {
            QDate.currentDate().toString("yyyy-MM-dd")
        }
        settings = database.settings()
        self.project_calendar = {
            "weekend_working": bool(settings.get("weekend_working", 0)),
            "overtime_holiday": bool(settings.get("overtime_holiday", 0)),
            "exceptions": database.calendar_exception_map(),
        }
        self._formatted_dates: set[QDate] = set()
        self._exceptions: dict[str, dict] = {}
        self.setWindowTitle(f"班组上班日历 - {crew_name}")
        self.resize(920, 680)

        description = QLabel(
            f"{crew_name}默认跟随项目工作日历。可按日期指定班组上班或休息；"
            "班组上班日期仍须同时满足项目施工日历。可连续修改多个日期，"
            "关闭“施工资源”窗口后再统一重新排期。"
        )
        description.setWordWrap(True)

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setSelectedDate(QDate.currentDate())
        self.calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        self.calendar.selectionChanged.connect(self._load_selected_day)
        self.calendar.clicked.connect(self._toggle_selected_date)
        self.calendar.currentPageChanged.connect(lambda *_: self._paint_calendar())

        legend = QHBoxLayout()
        for text, color, foreground in (
            ("按项目日历上班", "#e8f5e9", "#245c32"),
            ("项目默认休息", "#eef1f3", "#69777e"),
            ("班组手动上班", "#bfe8c6", "#145c2e"),
            ("班组手动休息", "#ffcaca", "#9c1c1c"),
            ("已选择", "#2374ab", "#ffffff"),
        ):
            item = QLabel(f"■ {text}")
            item.setStyleSheet(
                f"color: {foreground}; background: {color}; padding: 5px 8px;"
                " border-radius: 4px;"
            )
            legend.addWidget(item)
        legend.addStretch()

        self.multi_select = QCheckBox("多选日期")
        self.multi_select.setChecked(True)
        self.multi_select.setToolTip("开启后，连续点击日期可加入或取消选择")
        self.multi_select.toggled.connect(self._sync_multi_selection)
        self.selection_count = QLabel()
        clear_selection = QPushButton("清空选择")
        clear_selection.clicked.connect(self._clear_selected_dates)
        selection_row = QHBoxLayout()
        selection_row.addWidget(self.multi_select)
        selection_row.addWidget(self.selection_count)
        selection_row.addWidget(clear_selection)
        selection_row.addStretch()

        self.selected_date_label = QLabel()
        self.selected_date_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.selected_status = QLabel()
        self.selected_status.setWordWrap(True)
        self.exception_name = QLineEdit()
        self.exception_name.setPlaceholderText("例如：请假、调班、临时增援")
        self.rest_button = QPushButton("设为班组休息")
        self.rest_button.setObjectName("PrimaryButton")
        self.rest_button.clicked.connect(lambda: self._save_selected_day(False))
        self.work_button = QPushButton("设为班组上班")
        self.work_button.clicked.connect(lambda: self._save_selected_day(True))
        self.restore_button = QPushButton("恢复跟随项目日历")
        self.restore_button.clicked.connect(self._restore_selected_day)

        editor = QVBoxLayout()
        editor.addWidget(self.selected_date_label)
        editor.addWidget(self.selected_status)
        editor.addSpacing(8)
        editor.addWidget(QLabel("当天说明（可选）"))
        editor.addWidget(self.exception_name)
        editor.addWidget(self.rest_button)
        editor.addWidget(self.work_button)
        editor.addWidget(self.restore_button)
        editor.addStretch()

        calendar_row = QHBoxLayout()
        calendar_row.addWidget(self.calendar, 3)
        calendar_row.addLayout(editor, 2)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["已修改日期", "星期", "班组状态", "说明"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda *_: self._select_table_day())

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close_buttons.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.addWidget(QLabel("下表列出该班组手动修改过的日期；双击可在月历中定位。"))
        footer.addStretch()
        footer.addWidget(close_buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(description)
        layout.addLayout(legend)
        layout.addLayout(selection_row)
        layout.addLayout(calendar_row, 3)
        layout.addWidget(self.table, 2)
        layout.addLayout(footer)
        self.refresh()
        self._sync_multi_selection()

    def _project_is_working(self, day: QDate) -> bool:
        raw = day.toString("yyyy-MM-dd")
        exception = self.project_calendar["exceptions"].get(raw)
        if exception is not None:
            return bool(exception) or bool(self.project_calendar["overtime_holiday"])
        return bool(self.project_calendar["weekend_working"]) or day.dayOfWeek() <= 5

    def refresh(self) -> None:
        rows = self.database.crew_calendar_exceptions(self.resource_id)
        self._exceptions = {row["exception_date"]: row for row in rows}
        self.table.setRowCount(len(rows))
        week_names = "一二三四五六日"
        for row_index, row in enumerate(rows):
            qdate = QDate.fromString(row["exception_date"], "yyyy-MM-dd")
            values = (
                row["exception_date"],
                "星期" + week_names[qdate.dayOfWeek() - 1],
                "上班" if row["is_working"] else "休息",
                row["name"],
            )
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        self._paint_calendar()
        self._load_selected_day()

    def _save_selected_day(self, is_working: bool) -> None:
        dates = self._target_dates()
        if not dates:
            return
        self.database.save_crew_calendar_exceptions(
            self.resource_id,
            dates,
            is_working,
            self.exception_name.text().strip(),
        )
        self.has_changes = True
        self.changed.emit()
        self.refresh()
        if self.multi_select.isChecked():
            self._clear_selected_dates()

    def _restore_selected_day(self) -> None:
        dates = self._target_dates()
        if not dates:
            return
        self.database.delete_crew_calendar_exceptions(
            self.resource_id,
            dates,
        )
        self.has_changes = True
        self.changed.emit()
        self.refresh()

    def _target_dates(self) -> list[str]:
        if self.multi_select.isChecked():
            return sorted(self._selected_dates)
        return [self.calendar.selectedDate().toString("yyyy-MM-dd")]

    def _toggle_selected_date(self, day: QDate) -> None:
        if not self.multi_select.isChecked():
            return
        raw = day.toString("yyyy-MM-dd")
        if raw in self._selected_dates:
            self._selected_dates.remove(raw)
        else:
            self._selected_dates.add(raw)
        self._sync_multi_selection()

    def _clear_selected_dates(self) -> None:
        self._selected_dates.clear()
        self._sync_multi_selection()

    def _sync_multi_selection(self) -> None:
        multiple = self.multi_select.isChecked()
        if not multiple:
            self._selected_dates.clear()
        count = len(self._selected_dates)
        self.selection_count.setText(
            f"已选择 {count} 天，可一次性设置" if multiple else "当前为单日设置"
        )
        enabled = not multiple or count > 0
        self.rest_button.setEnabled(enabled)
        self.work_button.setEnabled(enabled)
        self.restore_button.setEnabled(enabled)
        self._paint_calendar()

    def _load_selected_day(self) -> None:
        selected = self.calendar.selectedDate()
        raw = selected.toString("yyyy-MM-dd")
        week_names = "一二三四五六日"
        self.selected_date_label.setText(
            f"{raw}　星期{week_names[selected.dayOfWeek() - 1]}"
        )
        exception = self._exceptions.get(raw)
        project_working = self._project_is_working(selected)
        if exception is None:
            self.selected_status.setText(
                "当前：跟随项目日历，班组上班"
                if project_working
                else "当前：项目休息日，班组不施工"
            )
            self.exception_name.clear()
        else:
            status = "手动设为班组上班" if exception["is_working"] else "手动设为班组休息"
            if exception["is_working"] and not project_working:
                status += "；但项目当天不施工"
            self.selected_status.setText("当前：" + status)
            self.exception_name.setText(exception["name"])

    def _select_table_day(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        selected = QDate.fromString(self.table.item(row, 0).text(), "yyyy-MM-dd")
        if selected.isValid():
            self.calendar.setSelectedDate(selected)
            self.calendar.showSelectedDate()

    def _paint_calendar(self) -> None:
        for day in self._formatted_dates:
            self.calendar.setDateTextFormat(day, QTextCharFormat())
        self._formatted_dates.clear()
        first = QDate(self.calendar.yearShown(), self.calendar.monthShown(), 1)
        for day_number in range(1, first.daysInMonth() + 1):
            day = QDate(first.year(), first.month(), day_number)
            raw = day.toString("yyyy-MM-dd")
            exception = self._exceptions.get(raw)
            project_working = self._project_is_working(day)
            text_format = QTextCharFormat()
            if raw in self._selected_dates:
                text_format.setBackground(QColor("#2374ab"))
                text_format.setForeground(QColor("#ffffff"))
                text_format.setFontWeight(700)
            elif exception is not None and exception["is_working"]:
                text_format.setBackground(QColor("#bfe8c6"))
                text_format.setForeground(QColor("#145c2e"))
                text_format.setFontWeight(700)
            elif exception is not None:
                text_format.setBackground(QColor("#ffcaca"))
                text_format.setForeground(QColor("#9c1c1c"))
                text_format.setFontWeight(700)
            elif project_working:
                text_format.setBackground(QColor("#e8f5e9"))
                text_format.setForeground(QColor("#263238"))
            else:
                text_format.setBackground(QColor("#eef1f3"))
                text_format.setForeground(QColor("#89949a"))
            self.calendar.setDateTextFormat(day, text_format)
            self._formatted_dates.add(day)


class PathNodeEditDialog(QDialog):
    NODE_TYPES = {
        "junction": "交叉点",
        "entrance": "出入口",
        "loading": "装卸点",
        "elevator": "电梯口",
        "stair": "楼梯口",
        "workface": "作业面入口",
    }

    def __init__(
        self, values: dict | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.values = values or {}
        self.setWindowTitle("编辑路径节点" if values else "新增路径节点")
        self.setMinimumWidth(460)

        self.name = QLineEdit(str(self.values.get("name", "")))
        self.name.setPlaceholderText("例如：工地大门、1号电梯口、三层走廊交叉点")
        self.node_type = QComboBox()
        for code, label in self.NODE_TYPES.items():
            self.node_type.addItem(label, code)
        self.node_type.setCurrentIndex(
            max(0, self.node_type.findData(self.values.get("node_type", "junction")))
        )
        self.notes = QLineEdit(str(self.values.get("notes", "")))

        form = QFormLayout()
        form.addRow("节点名称", self.name)
        form.addRow("节点类型", self.node_type)
        form.addRow("备注", self.notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.name.setFocus()

    def _accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "请填写节点名称", "节点名称不能为空。")
            return
        self.accept()

    def data(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "node_type": str(self.node_type.currentData()),
            "notes": self.notes.text().strip(),
        }


class PathSegmentEditDialog(QDialog):
    def __init__(
        self,
        nodes: list[dict],
        events: list[dict],
        values: dict | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.values = values or {}
        self.setWindowTitle("编辑通行路段" if values else "新增通行路段")
        self.resize(560, 620)
        self.name = QLineEdit(str(self.values.get("name", "")))
        self.from_node = QComboBox(); self.to_node = QComboBox()
        for node in nodes:
            label = str(node["name"]); node_id = int(node["id"])
            self.from_node.addItem(label, node_id); self.to_node.addItem(label, node_id)
        self.from_node.setCurrentIndex(max(0, self.from_node.findData(self.values.get("from_node_id"))))
        self.to_node.setCurrentIndex(max(0, self.to_node.findData(self.values.get("to_node_id"))))
        if not values and self.to_node.count() > 1: self.to_node.setCurrentIndex(1)
        self.direction = QComboBox(); self.direction.addItem("双向通行", "two_way"); self.direction.addItem("仅起点至终点", "forward")
        self.direction.setCurrentIndex(max(0, self.direction.findData(self.values.get("direction", "two_way"))))
        self.capacity = QDoubleSpinBox(); self.capacity.setRange(0.01, 9999); self.capacity.setValue(float(self.values.get("capacity", 1)))
        self.minutes = QSpinBox(); self.minutes.setRange(5, 10080); self.minutes.setSuffix(" 分钟"); self.minutes.setValue(int(self.values.get("travel_minutes", 10)))
        self.enabled = QCheckBox("可用于排程"); self.enabled.setChecked(bool(self.values.get("enabled", 1)))
        self.is_workface = QCheckBox("同时作为施工任务的作业面"); self.is_workface.setChecked(bool(self.values.get("is_workface", 0)))
        self.from_enabled = QCheckBox("指定开放日期"); self.from_date = QDateEdit(); self.from_date.setCalendarPopup(True); self.from_date.setDisplayFormat("yyyy-MM-dd")
        self.from_enabled.setChecked(bool(self.values.get("available_from"))); self.from_date.setEnabled(self.from_enabled.isChecked()); self.from_enabled.toggled.connect(self.from_date.setEnabled)
        if self.values.get("available_from"): self.from_date.setDate(QDate.fromString(self.values["available_from"], "yyyy-MM-dd"))
        self.until_enabled = QCheckBox("指定停用日期"); self.until_date = QDateEdit(); self.until_date.setCalendarPopup(True); self.until_date.setDisplayFormat("yyyy-MM-dd")
        self.until_enabled.setChecked(bool(self.values.get("available_until"))); self.until_date.setEnabled(self.until_enabled.isChecked()); self.until_enabled.toggled.connect(self.until_date.setEnabled)
        if self.values.get("available_until"): self.until_date.setDate(QDate.fromString(self.values["available_until"], "yyyy-MM-dd"))
        self.activation_event = QComboBox(); self.deactivation_event = QComboBox()
        for combo in (self.activation_event, self.deactivation_event):
            combo.addItem("无（不受事件控制）", None)
            for event in events: combo.addItem(str(event["name"]), int(event["id"]))
        self.activation_event.setCurrentIndex(max(0, self.activation_event.findData(self.values.get("activation_event_id"))))
        self.deactivation_event.setCurrentIndex(max(0, self.deactivation_event.findData(self.values.get("deactivation_event_id"))))
        self.notes = QLineEdit(str(self.values.get("notes", "")))
        form = QFormLayout(); form.addRow("路段名称", self.name); form.addRow("起点", self.from_node); form.addRow("终点", self.to_node); form.addRow("方向", self.direction); form.addRow("通行容量", self.capacity); form.addRow("标准通过时间", self.minutes); form.addRow("", self.enabled); form.addRow("空间角色", self.is_workface)
        open_row = QHBoxLayout(); open_row.addWidget(self.from_enabled); open_row.addWidget(self.from_date)
        close_row = QHBoxLayout(); close_row.addWidget(self.until_enabled); close_row.addWidget(self.until_date)
        form.addRow("固定开放时间", open_row); form.addRow("固定停用时间", close_row); form.addRow("开放事件", self.activation_event); form.addRow("停用事件", self.deactivation_event); form.addRow("备注", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存"); buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消"); buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.name.text().strip(): QMessageBox.warning(self, "请填写名称", "路段名称不能为空。"); return
        if self.from_node.currentData() is None or self.to_node.currentData() is None: QMessageBox.warning(self, "需要路径节点", "请先建立至少两个路径节点。"); return
        if self.from_node.currentData() == self.to_node.currentData(): QMessageBox.warning(self, "节点重复", "路段起点和终点不能相同。"); return
        if self.from_enabled.isChecked() and self.until_enabled.isChecked() and self.from_date.date() > self.until_date.date(): QMessageBox.warning(self, "日期无效", "路段停用日期不能早于开放日期。"); return
        self.accept()

    def data(self) -> dict:
        return {"name": self.name.text().strip(), "from_node_id": int(self.from_node.currentData()), "to_node_id": int(self.to_node.currentData()), "direction": str(self.direction.currentData()), "capacity": self.capacity.value(), "travel_minutes": self.minutes.value(), "enabled": self.enabled.isChecked(), "notes": self.notes.text().strip(), "is_workface": self.is_workface.isChecked(), "available_from": self.from_date.date().toString("yyyy-MM-dd") if self.from_enabled.isChecked() else None, "available_until": self.until_date.date().toString("yyyy-MM-dd") if self.until_enabled.isChecked() else None, "activation_event_id": self.activation_event.currentData(), "deactivation_event_id": self.deactivation_event.currentData()}


class RouteEditDialog(QDialog):
    def __init__(self, trades: list[str], routes: list[dict], values: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent); self.values = values or {}; self.setWindowTitle("编辑运输路线" if values else "新增运输路线"); self.setMinimumWidth(500)
        self.name = QLineEdit(str(self.values.get("name", ""))); self.trade = QComboBox(); self.trade.addItems(trades); self.trade.setCurrentIndex(max(0, self.trade.findText(str(self.values.get("default_trade", "杂工")))))
        self.alternative = QComboBox(); self.alternative.addItem("无替代路线", None)
        current_id = self.values.get("id")
        for route in routes:
            if int(route["id"]) != current_id: self.alternative.addItem(str(route["name"]), int(route["id"]))
        self.alternative.setCurrentIndex(max(0, self.alternative.findData(self.values.get("alternative_route_id"))))
        self.notes = QLineEdit(str(self.values.get("notes", "")))
        form = QFormLayout(); form.addRow("路线名称", self.name); form.addRow("运输工种", self.trade); form.addRow("不可用时改走", self.alternative); form.addRow("备注", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存"); buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消"); buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.name.text().strip(): QMessageBox.warning(self, "请填写名称", "路线名称不能为空。"); return
        self.accept()

    def data(self) -> dict:
        return {"name": self.name.text().strip(), "trade": self.trade.currentText(), "alternative_route_id": self.alternative.currentData(), "notes": self.notes.text().strip()}


class RouteStepEditDialog(QDialog):
    def __init__(self, segments: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent); self.segments = {int(item["id"]): item for item in segments}; self.setWindowTitle("添加路线步骤"); self.setMinimumWidth(540)
        self.segment = QComboBox()
        for item in segments: self.segment.addItem(f"{item['name']}（{item['from_name']}→{item['to_name']}）", int(item["id"]))
        self.reverse = QComboBox(); self.reverse.addItem("按路段方向", False); self.reverse.addItem("反向通行", True)
        self.minutes = QSpinBox(); self.minutes.setRange(5, 10080); self.minutes.setSuffix(" 分钟"); self.demand = QDoubleSpinBox(); self.demand.setRange(0.01, 9999); self.demand.setValue(1)
        self.segment.currentIndexChanged.connect(self._sync_segment); self._sync_segment()
        form = QFormLayout(); form.addRow("通行路段", self.segment); form.addRow("方向", self.reverse); form.addRow("耗时", self.minutes); form.addRow("占用量", self.demand)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); buttons.button(QDialogButtonBox.StandardButton.Save).setText("添加"); buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消"); buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addWidget(buttons)

    def _sync_segment(self) -> None:
        item = self.segments.get(self.segment.currentData()); self.minutes.setValue(int(item["travel_minutes"]) if item else 10); two_way = bool(item and item["direction"] == "two_way"); self.reverse.setEnabled(two_way)
        if not two_way: self.reverse.setCurrentIndex(0)

    def _accept(self) -> None:
        if self.segment.currentData() is None: QMessageBox.warning(self, "没有路段", "请先建立通行路段。"); return
        self.accept()

    def data(self) -> dict:
        return {"segment_id": int(self.segment.currentData()), "duration_minutes": self.minutes.value(), "demand_amount": self.demand.value(), "reverse_travel": bool(self.reverse.currentData())}


class PathNetworkDialog(QDialog):
    changed = Signal()
    NODE_TYPES = PathNodeEditDialog.NODE_TYPES

    def __init__(self, database: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.editing_segment_id: int | None = None
        self.editing_route_id: int | None = None
        self.setWindowTitle("现场空间系统")
        self.resize(1080, 720)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_spaces_tab(), "空间与作业面")
        self.tabs.addTab(self._build_nodes_tab(), "路径节点")
        self.tabs.addTab(self._build_segments_tab(), "通行路段")
        self.tabs.addTab(self._build_routes_tab(), "运输路线")
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        description = QLabel(
            "作业面、空间、路径节点和通行路段统一组成现场空间系统；"
            "运输路线按顺序组合路段，并可自动生成分段物流任务。"
            "走廊等复合空间可在通行路段中设置为同时承担作业面角色。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(close)
        self.refresh_all()

    @staticmethod
    def _selected_id(table: QTableWidget) -> int | None:
        row = table.currentRow()
        if row < 0 or table.item(row, 0) is None:
            return None
        value = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    def _build_spaces_tab(self) -> QWidget:
        tab = QWidget()
        add = QPushButton("新增空间/作业面")
        add.setObjectName("PrimaryButton")
        add.clicked.connect(self._add_space)
        edit = QPushButton("编辑所选")
        edit.clicked.connect(self._edit_space)
        delete = QPushButton("删除所选")
        delete.clicked.connect(self._delete_space)
        actions = QHBoxLayout()
        actions.addWidget(add)
        actions.addWidget(edit)
        actions.addWidget(delete)
        actions.addStretch()

        self.space_table = QTableWidget(0, 5)
        self.space_table.setHorizontalHeaderLabels(
            ["名称", "空间类型", "并行容量", "状态", "备注"]
        )
        self.space_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.space_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.space_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.space_table.horizontalHeader().setStretchLastSection(True)
        self.space_table.doubleClicked.connect(lambda *_: self._edit_space())

        hint = QLabel(
            "这里维护任务持续占用的作业面和普通通行区域；"
            "兼具通行与施工功能的走廊等空间，请在“通行路段”中设置复合角色。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #687985;")
        layout = QVBoxLayout(tab)
        layout.addWidget(hint)
        layout.addLayout(actions)
        layout.addWidget(self.space_table, 1)
        return tab

    def _build_nodes_tab(self) -> QWidget:
        tab = QWidget()
        add = QPushButton("新增节点")
        add.setObjectName("PrimaryButton")
        add.clicked.connect(self._add_node)
        edit = QPushButton("编辑所选")
        edit.clicked.connect(self._edit_node)
        delete = QPushButton("删除所选")
        delete.clicked.connect(self._delete_node)
        actions = QHBoxLayout()
        actions.addWidget(add); actions.addWidget(edit); actions.addWidget(delete); actions.addStretch()
        self.node_table = QTableWidget(0, 3)
        self.node_table.setHorizontalHeaderLabels(["节点名称", "类型", "备注"])
        self.node_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.node_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.node_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.node_table.doubleClicked.connect(lambda *_: self._edit_node())
        layout = QVBoxLayout(tab)
        layout.addLayout(actions); layout.addWidget(self.node_table, 1)
        return tab

    def _build_segments_tab(self) -> QWidget:
        tab = QWidget()
        add = QPushButton("新增通行路段"); add.setObjectName("PrimaryButton"); add.clicked.connect(self._add_segment_dialog)
        edit = QPushButton("编辑所选"); edit.clicked.connect(self._edit_segment_dialog)
        delete = QPushButton("删除所选"); delete.clicked.connect(self._delete_segment)
        self.segment_calendar = QPushButton("路段通行日历"); self.segment_calendar.clicked.connect(self._open_segment_calendar); self.segment_calendar.setEnabled(False)
        actions = QHBoxLayout()
        actions.addWidget(add); actions.addWidget(edit); actions.addWidget(delete); actions.addWidget(self.segment_calendar); actions.addStretch()
        self.segment_table = QTableWidget(0, 12)
        self.segment_table.setHorizontalHeaderLabels(["空间/路段", "起点", "终点", "方向", "容量", "通过时间", "角色", "开放日期", "停用日期", "开放事件", "停用事件", "状态"])
        self.segment_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.segment_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.segment_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.segment_table.itemSelectionChanged.connect(self._sync_segment_selection)
        self.segment_table.doubleClicked.connect(lambda *_: self._edit_segment_dialog())
        layout = QVBoxLayout(tab)
        layout.addLayout(actions); layout.addWidget(self.segment_table, 1)
        return tab

    def _build_routes_tab(self) -> QWidget:
        tab = QWidget()
        add = QPushButton("新增运输路线"); add.setObjectName("PrimaryButton"); add.clicked.connect(self._add_route_dialog)
        edit = QPushButton("编辑所选"); edit.clicked.connect(self._edit_route_dialog)
        delete = QPushButton("删除路线"); delete.clicked.connect(self._delete_route)
        self.create_transport = QPushButton("从所选路线生成物流作业"); self.create_transport.clicked.connect(self._create_transport); self.create_transport.setEnabled(False)
        route_actions = QHBoxLayout()
        route_actions.addWidget(add); route_actions.addWidget(edit); route_actions.addWidget(delete); route_actions.addWidget(self.create_transport); route_actions.addStretch()
        self.route_table = QTableWidget(0, 4)
        self.route_table.setHorizontalHeaderLabels(["路线名称", "运输工种", "替代路线", "备注"])
        self.route_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.route_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.route_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.route_table.itemSelectionChanged.connect(self._select_route)
        self.route_table.doubleClicked.connect(lambda *_: self._edit_route_dialog())
        add_step = QPushButton("添加路线步骤"); add_step.clicked.connect(self._add_step_dialog)
        delete_step = QPushButton("删除所选步骤"); delete_step.clicked.connect(self._delete_step)
        step_actions = QHBoxLayout(); step_actions.addWidget(QLabel("所选路线的路段顺序")); step_actions.addStretch(); step_actions.addWidget(add_step); step_actions.addWidget(delete_step)
        self.step_table = QTableWidget(0, 6)
        self.step_table.setHorizontalHeaderLabels(["顺序", "路段", "起点", "终点", "耗时", "占用量"])
        self.step_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.step_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.step_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout = QVBoxLayout(tab)
        layout.addLayout(route_actions); layout.addWidget(self.route_table, 1)
        layout.addLayout(step_actions); layout.addWidget(self.step_table, 1)
        return tab

    def refresh_all(self) -> None:
        spaces = [
            resource
            for resource in self.database.resources()
            if resource["resource_type"] in {"workface", "access"}
            and resource.get("path_segment_id") is None
        ]
        self._spaces = spaces
        self.space_table.setRowCount(len(spaces))
        space_type_labels = {"workface": "作业面", "access": "通行区域"}
        for row_index, space in enumerate(spaces):
            values = (
                space["name"],
                space_type_labels.get(space["resource_type"], space["resource_type"]),
                f"{float(space['capacity']):g}",
                "可用" if space["enabled"] else "停用",
                space["notes"],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, int(space["id"]))
                self.space_table.setItem(row_index, column, item)
        self.space_table.resizeColumnsToContents()
        nodes = self.database.path_nodes()
        events = [
            task
            for task in self.database.tasks()
            if task.get("task_type") == "milestone"
        ]
        self.node_table.setRowCount(len(nodes))
        for row_index, node in enumerate(nodes):
            for column, value in enumerate((node["name"], self.NODE_TYPES.get(node["node_type"], node["node_type"]), node["notes"])):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, int(node["id"])); self.node_table.setItem(row_index, column, item)
        self.node_table.resizeColumnsToContents()
        segments = self.database.path_segments()
        self.segment_table.setRowCount(len(segments))
        for row_index, segment in enumerate(segments):
            event_names = {int(event["id"]): event["name"] for event in events}
            values = (segment["name"], segment["from_name"], segment["to_name"], "双向" if segment["direction"] == "two_way" else "单向", f"{segment['capacity']:g}", f"{segment['travel_minutes']}分钟", "作业面＋路径" if segment.get("is_workface") else "路径", segment.get("available_from") or "—", segment.get("available_until") or "—", event_names.get(segment.get("activation_event_id"), "—"), event_names.get(segment.get("deactivation_event_id"), "—"), "可用" if segment["enabled"] else "停用")
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, int(segment["id"])); self.segment_table.setItem(row_index, column, item)
        self.segment_table.resizeColumnsToContents()
        routes = self.database.route_templates()
        route_names = {int(route["id"]): route["name"] for route in routes}
        selected_route_id = self.editing_route_id
        self.route_table.setRowCount(len(routes))
        for row_index, route in enumerate(routes):
            for column, value in enumerate((route["name"], route["default_trade"], route_names.get(route.get("alternative_route_id"), "—"), route["notes"])):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, int(route["id"])); self.route_table.setItem(row_index, column, item)
            if int(route["id"]) == selected_route_id:
                self.route_table.selectRow(row_index)
        self.route_table.resizeColumnsToContents()
        self._sync_segment_selection()
        self._select_route()
        self._refresh_steps()

    def _selected_space(self) -> dict | None:
        resource_id = self._selected_id(self.space_table)
        if resource_id is None:
            return None
        return next(
            (
                resource
                for resource in getattr(self, "_spaces", [])
                if int(resource["id"]) == resource_id
            ),
            None,
        )

    def _add_space(self) -> None:
        dialog = ResourceEditDialog(("workface", "access"), parent=self)
        if not dialog.exec():
            return
        values = dialog.data()
        try:
            self.database.add_resource(
                values["name"],
                values["resource_type"],
                values["capacity"],
                values["notes"],
                values["skills"],
                values["enabled"],
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法新增空间", str(error))
            return
        self.changed.emit()
        self.refresh_all()

    def _edit_space(self) -> None:
        space = self._selected_space()
        if space is None:
            QMessageBox.information(self, "请选择空间", "请先选择要编辑的空间或作业面。")
            return
        dialog = ResourceEditDialog(("workface", "access"), space, parent=self)
        if not dialog.exec():
            return
        values = dialog.data()
        try:
            self.database.update_resource(
                int(space["id"]),
                values["name"],
                values["resource_type"],
                values["capacity"],
                values["enabled"],
                values["notes"],
                values["skills"],
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法保存空间", str(error))
            return
        self.changed.emit()
        self.refresh_all()

    def _delete_space(self) -> None:
        space = self._selected_space()
        if space is None:
            QMessageBox.information(self, "请选择空间", "请先选择要删除的空间或作业面。")
            return
        answer = QMessageBox.question(
            self,
            "确认删除空间",
            f"删除“{space['name']}”后，任务对该空间的占用也会解除。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.database.delete_resource(int(space["id"]))
        self.changed.emit()
        self.refresh_all()

    def _add_node(self) -> None:
        dialog = PathNodeEditDialog(parent=self)
        if not dialog.exec():
            return
        values = dialog.data()
        try:
            self.database.add_path_node(
                values["name"], values["node_type"], values["notes"]
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法新增节点", str(error))
            return
        self.changed.emit()
        self.refresh_all()

    def _edit_node(self) -> None:
        node_id = self._selected_id(self.node_table)
        if node_id is None:
            QMessageBox.information(self, "请选择节点", "请先选择要编辑的路径节点。")
            return
        node = next(item for item in self.database.path_nodes() if int(item["id"]) == node_id)
        dialog = PathNodeEditDialog(node, self)
        if not dialog.exec():
            return
        values = dialog.data()
        try:
            self.database.update_path_node(
                node_id, values["name"], values["node_type"], values["notes"]
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法保存节点", str(error))
            return
        self.changed.emit()
        self.refresh_all()

    def _delete_node(self) -> None:
        node_id = self._selected_id(self.node_table)
        if node_id is None:
            QMessageBox.information(self, "请选择节点", "请先选择要删除的路径节点。")
            return
        node = next(item for item in self.database.path_nodes() if int(item["id"]) == node_id)
        answer = QMessageBox.question(
            self,
            "确认删除节点",
            f"确定删除“{node['name']}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.database.delete_path_node(node_id)
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法删除节点", str(error))
            return
        self.changed.emit()
        self.refresh_all()

    def _selected_segment(self) -> dict | None:
        segment_id = self._selected_id(self.segment_table)
        return next((item for item in self.database.path_segments() if int(item["id"]) == segment_id), None) if segment_id is not None else None

    def _segment_dialog_data(self) -> tuple[list[dict], list[dict]]:
        nodes = self.database.path_nodes()
        events = [task for task in self.database.tasks() if task.get("task_type") == "milestone"]
        return nodes, events

    def _add_segment_dialog(self) -> None:
        nodes, events = self._segment_dialog_data()
        dialog = PathSegmentEditDialog(nodes, events, parent=self)
        if not dialog.exec(): return
        values = dialog.data()
        try: self.database.add_path_segment(**values)
        except (ValueError, sqlite3.IntegrityError) as error: QMessageBox.warning(self, "无法新增路段", str(error)); return
        self.changed.emit(); self.refresh_all()

    def _edit_segment_dialog(self) -> None:
        segment = self._selected_segment()
        if segment is None: QMessageBox.information(self, "请选择路段", "请先选择要编辑的通行路段。"); return
        nodes, events = self._segment_dialog_data()
        dialog = PathSegmentEditDialog(nodes, events, segment, self)
        if not dialog.exec(): return
        values = dialog.data()
        try: self.database.update_path_segment(int(segment["id"]), **values)
        except (ValueError, sqlite3.IntegrityError) as error: QMessageBox.warning(self, "无法保存路段", str(error)); return
        self.changed.emit(); self.refresh_all()

    def _sync_segment_selection(self) -> None:
        self.editing_segment_id = self._selected_id(self.segment_table)
        self.segment_calendar.setEnabled(self.editing_segment_id is not None)

    def _delete_segment(self) -> None:
        segment = self._selected_segment()
        if segment is None: QMessageBox.information(self, "请选择路段", "请先选择要删除的通行路段。"); return
        answer = QMessageBox.question(self, "确认删除路段", f"确定删除“{segment['name']}”吗？相关路线步骤也会一并删除。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes: return
        try: self.database.delete_path_segment(int(segment["id"]))
        except (ValueError, sqlite3.IntegrityError) as error: QMessageBox.warning(self, "无法删除路段", str(error)); return
        self.changed.emit(); self.editing_segment_id = None; self.refresh_all()

    def _open_segment_calendar(self) -> None:
        segment = self._selected_segment()
        if segment is None: return
        dialog = PathSegmentCalendarDialog(self.database, int(segment["id"]), str(segment["name"]), self); dialog.changed.connect(self.changed.emit); dialog.exec()

    def _selected_route(self) -> dict | None:
        route_id = self._selected_id(self.route_table)
        return next((item for item in self.database.route_templates() if int(item["id"]) == route_id), None) if route_id is not None else None

    def _select_route(self) -> None:
        route = self._selected_route(); self.editing_route_id = int(route["id"]) if route else None; self.create_transport.setEnabled(route is not None); self._refresh_steps()

    def _add_route_dialog(self) -> None:
        dialog = RouteEditDialog(self.database.trade_names(), self.database.route_templates(), parent=self)
        if not dialog.exec(): return
        values = dialog.data()
        try: route_id = self.database.add_route_template(values["name"], values["trade"], values["notes"], values["alternative_route_id"])
        except (ValueError, sqlite3.IntegrityError) as error: QMessageBox.warning(self, "无法新增路线", str(error)); return
        self.changed.emit(); self.editing_route_id = route_id; self.refresh_all()

    def _edit_route_dialog(self) -> None:
        route = self._selected_route()
        if route is None: QMessageBox.information(self, "请选择路线", "请先选择要编辑的运输路线。"); return
        dialog = RouteEditDialog(self.database.trade_names(), self.database.route_templates(), route, self)
        if not dialog.exec(): return
        values = dialog.data()
        try: self.database.update_route_template(int(route["id"]), values["name"], values["trade"], values["notes"], values["alternative_route_id"])
        except (ValueError, sqlite3.IntegrityError) as error: QMessageBox.warning(self, "无法保存路线", str(error)); return
        self.changed.emit(); self.editing_route_id = int(route["id"]); self.refresh_all()

    def _delete_route(self) -> None:
        route = self._selected_route()
        if route is None: QMessageBox.information(self, "请选择路线", "请先选择要删除的运输路线。"); return
        answer = QMessageBox.question(self, "确认删除路线", f"确定删除“{route['name']}”及其全部路线步骤吗？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes: return
        self.database.delete_route_template(int(route["id"])); self.changed.emit(); self.editing_route_id = None; self.refresh_all()

    def _add_step_dialog(self) -> None:
        if self.editing_route_id is None: QMessageBox.information(self, "请选择路线", "请先选择一条运输路线。"); return
        dialog = RouteStepEditDialog(self.database.path_segments(), self)
        if not dialog.exec(): return
        values = dialog.data()
        try: self.database.add_route_template_step(self.editing_route_id, **values)
        except (ValueError, sqlite3.IntegrityError) as error: QMessageBox.warning(self, "无法添加步骤", str(error)); return
        self.changed.emit(); self._refresh_steps()

    def _delete_step(self) -> None:
        step_id = self._selected_id(self.step_table)
        if step_id is None: return
        self.database.delete_route_template_step(step_id); self.changed.emit(); self._refresh_steps()

    def _refresh_steps(self) -> None:
        rows = self.database.route_template_steps(self.editing_route_id) if self.editing_route_id is not None else []
        self.step_table.setRowCount(len(rows))
        for row_index, step in enumerate(rows):
            values = (row_index + 1, step["segment_name"], step["from_name"], step["to_name"], f"{step['duration_minutes']}分钟", f"{step['demand_amount']:g}")
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, int(step["id"])); self.step_table.setItem(row_index, column, item)
        self.step_table.resizeColumnsToContents()

    def _create_transport(self) -> None:
        route = self._selected_route()
        if route is None: return
        name, accepted = QInputDialog.getText(self, "生成物流作业", "物流作业名称", text=str(route["name"]))
        if not accepted or not name.strip(): return
        try: summary_id, child_ids = self.database.create_route_instance(int(route["id"]), name.strip())
        except (ValueError, sqlite3.IntegrityError) as error: QMessageBox.warning(self, "无法生成物流作业", str(error)); return
        self.changed.emit(); QMessageBox.information(self, "已生成", f"已生成1个物流汇总任务和{len(child_ids)}个分段运输任务。\n任务编号：{summary_id}")


class PathSegmentCalendarDialog(QDialog):
    changed = Signal()

    def __init__(self, database: Database, segment_id: int, segment_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database; self.segment_id = int(segment_id); self._exceptions: dict[str, dict] = {}; self._formatted_dates: set[QDate] = set()
        self.setWindowTitle(f"路段通行日历 - {segment_name}"); self.resize(850, 620)
        self.calendar = QCalendarWidget(); self.calendar.setGridVisible(True); self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader); self.calendar.selectionChanged.connect(self._load_day); self.calendar.currentPageChanged.connect(lambda *_: self._paint())
        self.status = QLabel(); self.status.setWordWrap(True)
        self.name = QLineEdit(); self.name.setPlaceholderText("例如：材料堆放、封路、临时开放")
        close_button = QPushButton("设为不可通行"); close_button.setObjectName("PrimaryButton"); close_button.clicked.connect(lambda: self._save(False))
        open_button = QPushButton("设为可通行"); open_button.clicked.connect(lambda: self._save(True))
        restore = QPushButton("恢复默认可通行"); restore.clicked.connect(self._restore)
        editor = QVBoxLayout(); editor.addWidget(self.status); editor.addWidget(QLabel("当天说明")); editor.addWidget(self.name); editor.addWidget(close_button); editor.addWidget(open_button); editor.addWidget(restore); editor.addStretch()
        row = QHBoxLayout(); row.addWidget(self.calendar, 3); row.addLayout(editor, 2)
        self.table = QTableWidget(0, 3); self.table.setHorizontalHeaderLabels(["日期", "通行状态", "说明"]); self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.table.doubleClicked.connect(lambda *_: self._select_table_day())
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); close.button(QDialogButtonBox.StandardButton.Close).setText("关闭"); close.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addWidget(QLabel("默认可通行；设置为不可通行后，使用该路段的任务会自动顺延。")); layout.addLayout(row, 2); layout.addWidget(self.table, 1); layout.addWidget(close)
        self.refresh()

    def refresh(self) -> None:
        rows = self.database.path_segment_calendar_exceptions(self.segment_id); self._exceptions = {item["exception_date"]: item for item in rows}; self.table.setRowCount(len(rows))
        for row_index, item in enumerate(rows):
            for column, value in enumerate((item["exception_date"], "可通行" if item["is_available"] else "不可通行", item["name"])): self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents(); self._paint(); self._load_day()

    def _save(self, available: bool) -> None:
        self.database.save_path_segment_calendar_exception(self.segment_id, self.calendar.selectedDate().toString("yyyy-MM-dd"), available, self.name.text()); self.changed.emit(); self.refresh()

    def _restore(self) -> None:
        self.database.delete_path_segment_calendar_exception(self.segment_id, self.calendar.selectedDate().toString("yyyy-MM-dd")); self.changed.emit(); self.refresh()

    def _load_day(self) -> None:
        raw = self.calendar.selectedDate().toString("yyyy-MM-dd"); item = self._exceptions.get(raw)
        if item is None: self.status.setText(f"{raw}：默认可通行"); self.name.clear()
        else: self.status.setText(f"{raw}：" + ("手动设为可通行" if item["is_available"] else "手动设为不可通行")); self.name.setText(item["name"])

    def _select_table_day(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            day = QDate.fromString(self.table.item(row, 0).text(), "yyyy-MM-dd")
            if day.isValid(): self.calendar.setSelectedDate(day); self.calendar.showSelectedDate()

    def _paint(self) -> None:
        for day in self._formatted_dates: self.calendar.setDateTextFormat(day, QTextCharFormat())
        self._formatted_dates.clear(); first = QDate(self.calendar.yearShown(), self.calendar.monthShown(), 1)
        for number in range(1, first.daysInMonth() + 1):
            day = QDate(first.year(), first.month(), number); item = self._exceptions.get(day.toString("yyyy-MM-dd")); fmt = QTextCharFormat()
            if item is not None and not item["is_available"]: fmt.setBackground(QColor("#ffcaca")); fmt.setForeground(QColor("#9c1c1c")); fmt.setFontWeight(700)
            elif item is not None: fmt.setBackground(QColor("#bfe8c6")); fmt.setForeground(QColor("#145c2e")); fmt.setFontWeight(700)
            else: fmt.setBackground(QColor("#e8f5e9")); fmt.setForeground(QColor("#263238"))
            self.calendar.setDateTextFormat(day, fmt); self._formatted_dates.add(day)
