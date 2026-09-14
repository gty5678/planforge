from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QTabWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .database import Database
from .dialogs import (
    ConstraintOverviewDialog,
    FlowRelationDialog,
    PathNetworkDialog,
    ProjectSettingsDialog,
    ProcessLibraryDialog,
    RelationsDialog,
    ResourceManagerDialog,
    TaskDialog,
    TradeManagerDialog,
    WorkCalendarDialog,
)
from .gantt import GanttWidget
from .time_scaled_network import TimeScaledNetworkWidget
from .icons import make_icon
from .domain import ScheduleModel
from .scheduler import ScheduleError, schedule_sort_key
from .scheduling import ScheduleOptions, SchedulingEngine
from .work_calendar import WorkCalendar
from .trades import normalized_trade
from .task_tree import DraggableTaskTree


STYLE = """
QMainWindow, QWidget { background: #f4f6f8; color: #263238; font-size: 14px; }
QLabel#Title { font-size: 22px; font-weight: 700; color: #17324d; }
QLabel#ProjectFinish {
    background: #17324d; color: white; border-radius: 7px;
    padding: 5px 14px; font-size: 15px; font-weight: 700;
}
QLineEdit, QDateEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: white; border: 1px solid #cbd6dd; border-radius: 5px; padding: 7px;
}
QPushButton { background: white; border: 1px solid #c5d0d8; border-radius: 5px; padding: 8px 13px; }
QPushButton:hover { background: #edf3f7; }
QPushButton:disabled { background: #eceff1; color: #9aa5ab; border-color: #d8dee2; }
QPushButton#PrimaryButton { background: #2374ab; color: white; border-color: #2374ab; }
QTreeWidget, QTableWidget {
    background: white; border: 1px solid #d7e0e5; border-radius: 5px;
    alternate-background-color: #f8fafb;
}
QHeaderView::section {
    background: #eaf0f4; border: none; border-right: 1px solid #d5dee4;
    padding: 8px; font-weight: 600;
}
QStatusBar { background: white; }
"""


class MainWindow(QMainWindow):
    def _decorate_button(
        self,
        button: QPushButton,
        icon_name: str,
        tooltip: str,
        *,
        icon_only: bool = True,
    ) -> None:
        icon_color = "#ffffff" if button.objectName() == "PrimaryButton" else "#315B76"
        button.setIcon(make_icon(icon_name, icon_color))
        button.setIconSize(QSize(20, 20))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        if icon_only:
            button.setText("")
            button.setFixedSize(38, 38)

    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database
        self.setWindowTitle("施工排程")
        self.resize(1320, 820)
        self.setMinimumSize(1000, 650)
        self.setStyleSheet(STYLE)

        self.project_settings = database.settings()
        recalculate = QPushButton("重新排期")
        recalculate.setObjectName("PrimaryButton")
        recalculate.clicked.connect(self.recalculate)
        self._decorate_button(
            recalculate,
            "refresh",
            "按照当前工序关系、班组、作业面和路径约束重新排期",
            icon_only=False,
        )
        recalculate.setFixedHeight(48)

        project_settings_button = QPushButton("项目设置")
        project_settings_button.clicked.connect(self.manage_project_settings)
        self._decorate_button(
            project_settings_button,
            "settings",
            "设置项目日期、工作时段、加班、班组工时上限和排程策略",
        )

        trades_button = QPushButton("工种与颜色")
        trades_button.clicked.connect(self.manage_trades)
        self._decorate_button(
            trades_button,
            "palette",
            "新增、改名、删除工种并设置任务表和横道图颜色",
        )

        self.add_task_button = QPushButton("新增任务/事件")
        self.add_task_button.setObjectName("PrimaryButton")
        self.add_task_button.setToolTip("有选中任务时新增同级任务；未选中时新增顶层任务")
        self.add_task_button.clicked.connect(self.add_task)
        self._decorate_button(
            self.add_task_button,
            "task_add",
            "新增任务或事件；有选中任务时新增同级任务",
        )
        self.add_subtask_button = QPushButton("新增子任务")
        self.add_subtask_button.clicked.connect(self.add_subtask)
        self._decorate_button(
            self.add_subtask_button,
            "subtask_add",
            "在当前选中任务下新增子任务",
        )
        self.split_task_button = QPushButton("划分施工段")
        self.split_task_button.clicked.connect(self.split_selected_task)
        self._decorate_button(
            self.split_task_button,
            "split",
            "把选中的末级任务划分为多个独立施工段",
        )
        self.delete_task_button = QPushButton("删除任务")
        self.delete_task_button.clicked.connect(self.delete_task)
        self._decorate_button(
            self.delete_task_button,
            "delete",
            "删除当前选中的任务及其下级任务",
        )
        relations = QPushButton("约束关系")
        relations.clicked.connect(self.manage_relations)
        self._decorate_button(
            relations,
            "relations",
            "维护任务之间的 FS、SS、FF、SF 约束关系",
        )
        process_library = QPushButton("作业模板库")
        process_library.clicked.connect(self.manage_process_library)
        self._decorate_button(
            process_library,
            "templates",
            "维护标准作业模板、模板步骤和模板关系",
        )
        create_from_template = QPushButton("从模板创建作业")
        create_from_template.clicked.connect(self.create_from_work_template)
        self._decorate_button(
            create_from_template,
            "template_add",
            "从标准作业模板生成完整任务和事件链",
        )
        constraint_overview = QPushButton("约束总览")
        constraint_overview.clicked.connect(self.show_constraint_overview)
        self._decorate_button(
            constraint_overview,
            "overview",
            "查看时间、事件、班组、空间、设备和日历约束",
        )
        calendar_button = QPushButton("工作日历")
        calendar_button.clicked.connect(self.manage_calendar)
        self._decorate_button(
            calendar_button,
            "calendar",
            "维护项目施工日、休息日、节假日和调休日期",
        )
        flow_relation = QPushButton("建立流水关系")
        flow_relation.clicked.connect(self.create_flow_relation)
        self._decorate_button(
            flow_relation,
            "flow",
            "在两道分段工序之间建立对应施工段的流水关系",
        )
        resources_button = QPushButton("施工资源")
        resources_button.clicked.connect(self.manage_resources)
        self._decorate_button(
            resources_button,
            "resources",
            "维护班组、设备、检查人员及班组上班日历",
        )
        path_network_button = QPushButton("现场空间")
        path_network_button.clicked.connect(self.manage_path_network)
        self._decorate_button(
            path_network_button,
            "workface",
            "统一维护作业面、空间、路径节点、通行路段和运输路线",
        )

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("项目工具"))
        for button in (
            project_settings_button,
            trades_button,
            process_library,
            resources_button,
            path_network_button,
            calendar_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch()
        self.project_finish_label = QLabel("项目预计完成\n—")
        self.project_finish_label.setObjectName("ProjectFinish")
        self.project_finish_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.project_finish_label.setMinimumWidth(235)
        self.project_finish_label.setFixedHeight(48)
        self.project_finish_label.setToolTip("重新排程后显示全部末级任务中最晚的完成时间")
        toolbar.addWidget(recalculate)
        toolbar.addWidget(self.project_finish_label)

        self.tree = DraggableTaskTree()
        self.tree.setColumnCount(14)
        self.tree.setHeaderLabels(
            ["任务名称", "类型", "模板继承", "噪音", "工种", "执行班组", "工期（汇总按自然日）", "作业面/通行", "占用资源", "约束开始", "计算开始", "计算完成", "排程原因", "备注"]
        )
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree_expanded_ids: set[int] = set()
        self._restoring_tree_state = False
        self.tree.itemExpanded.connect(self._remember_tree_expanded)
        self.tree.itemCollapsed.connect(self._remember_tree_collapsed)
        self.tree.task_move_requested.connect(self._move_task_from_tree)
        self.tree.itemSelectionChanged.connect(self._update_task_action_state)
        self.tree.doubleClicked.connect(lambda *_: self.edit_task())

        task_actions = QHBoxLayout()
        task_actions.setContentsMargins(0, 0, 0, 0)
        task_actions.addWidget(QLabel("任务操作"))
        for button in (
            self.add_task_button,
            self.add_subtask_button,
            self.split_task_button,
            self.delete_task_button,
            relations,
            constraint_overview,
            create_from_template,
            flow_relation,
        ):
            task_actions.addWidget(button)
        collapse_tree = QPushButton("全部收起")
        collapse_tree.clicked.connect(self._collapse_tree)
        self._decorate_button(
            collapse_tree,
            "collapse",
            "收起任务表中的全部下级任务",
        )
        task_actions.addWidget(QLabel("层级"))
        self.tree_level = QComboBox()
        self.tree_level.addItem("全部层级", None)
        for level in range(1, 7):
            self.tree_level.addItem(f"默认展开前 {level} 层", level - 1)
        self.tree_level.setToolTip(
            "一次性设置任务表的展开深度；之后仍可逐项继续展开或收起"
        )
        self.tree_level.activated.connect(lambda *_: self._apply_tree_depth())
        task_actions.addWidget(self.tree_level)
        task_actions.addWidget(collapse_tree)
        task_actions.addStretch()
        hint = QLabel("拖到任务中间成为子任务；拖到行间保持同级")
        hint.setStyleSheet("color: #687985;")
        task_actions.addWidget(hint)

        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(6)
        tree_layout.addLayout(task_actions)
        tree_layout.addWidget(self.tree, 1)

        self.gantt = GanttWidget()
        gantt_controls = QHBoxLayout()
        gantt_controls.setContentsMargins(0, 0, 0, 0)
        gantt_controls.addWidget(QLabel("横道图显示"))
        self.gantt_level = QComboBox()
        self.gantt_level.addItem("全部层级", None)
        for level in range(1, 7):
            self.gantt_level.addItem(f"默认展开前 {level} 层", level - 1)
        self.gantt_level.setToolTip(
            "设置横道图默认展开深度；仍可点击汇总任务前的三角形继续展开下级"
        )
        self.gantt_level.activated.connect(
            lambda *_: self.gantt.set_max_depth(self.gantt_level.currentData())
        )
        gantt_controls.addWidget(self.gantt_level)
        expand_gantt = QPushButton("全部展开")
        expand_gantt.clicked.connect(self._expand_gantt)
        self._decorate_button(
            expand_gantt,
            "expand",
            "展开横道图中的全部任务层级",
        )
        collapse_gantt = QPushButton("全部收起")
        collapse_gantt.clicked.connect(self.gantt.collapse_all)
        self._decorate_button(
            collapse_gantt,
            "collapse",
            "收起横道图中的全部任务层级",
        )
        export_gantt = QPushButton("导出完整横道图图片")
        export_gantt.clicked.connect(self.export_gantt_image)
        self._decorate_button(
            export_gantt,
            "export",
            "按当前层级、展开状态和缩放比例导出完整横道图图片",
        )
        gantt_controls.addWidget(expand_gantt)
        gantt_controls.addWidget(collapse_gantt)
        gantt_controls.addWidget(export_gantt)
        self.critical_path_toggle = QCheckBox("显示关键路径")
        self.critical_path_toggle.setToolTip("以红色显示控制项目最终完成日期的工序和任务关系")
        self.critical_path_toggle.toggled.connect(self.gantt.set_show_critical_path)
        gantt_controls.addWidget(self.critical_path_toggle)
        gantt_controls.addStretch()
        gantt_controls.addWidget(QLabel("横向缩放"))
        gantt_controls.addWidget(QLabel("缩小"))
        self.gantt_zoom = QSlider(Qt.Orientation.Horizontal)
        self.gantt_zoom.setRange(20, 960)
        self.gantt_zoom.setValue(384)
        self.gantt_zoom.setSingleStep(1)
        self.gantt_zoom.setPageStep(8)
        self.gantt_zoom.setFixedWidth(260)
        self.gantt_zoom.setToolTip(
            "支持四分之一像素的细腻缩放；横道图滚轮缩放；"
            "Ctrl＋滚轮上下滚动；Shift＋滚轮左右滚动；"
            "按住中键拖动可上下左右平移。缩小时自动切换为周视图"
        )
        self.gantt_zoom.valueChanged.connect(
            lambda value: self.gantt.set_day_width(value / 4)
        )
        self.gantt.day_width_changed.connect(
            lambda width: self.gantt_zoom.setValue(round(width * 4))
        )
        gantt_controls.addWidget(self.gantt_zoom)
        gantt_controls.addWidget(QLabel("放大"))

        gantt_panel = QWidget()
        gantt_layout = QVBoxLayout(gantt_panel)
        gantt_layout.setContentsMargins(0, 0, 0, 0)
        gantt_layout.setSpacing(6)
        gantt_layout.addLayout(gantt_controls)
        self.trade_legend = QLabel()
        self.trade_legend.setStyleSheet("color: #52616b; padding-left: 4px;")
        self.trade_legend.setWordWrap(True)
        gantt_layout.addWidget(self.trade_legend)
        gantt_layout.addWidget(self.gantt, 1)

        self.time_network = TimeScaledNetworkWidget()
        network_controls = QHBoxLayout()
        network_controls.setContentsMargins(0, 0, 0, 0)
        network_controls.addWidget(QLabel("时标网络图显示"))
        self.network_critical_toggle = QCheckBox("突出关键路径")
        self.network_critical_toggle.setChecked(True)
        self.network_critical_toggle.setToolTip("关键活动和控制性工序关系以红色粗线显示")
        self.network_critical_toggle.toggled.connect(
            self.time_network.set_show_critical_path
        )
        network_controls.addWidget(self.network_critical_toggle)
        export_network = QPushButton("导出完整时标网络图")
        export_network.clicked.connect(self.export_time_network_image)
        self._decorate_button(
            export_network,
            "export",
            "导出包含全部活动、时间刻度、逻辑关系和关键路径的 PNG 图片",
            icon_only=False,
        )
        network_controls.addWidget(export_network)
        network_controls.addStretch()
        network_controls.addWidget(QLabel("横向缩放"))
        network_controls.addWidget(QLabel("缩小"))
        self.network_zoom = QSlider(Qt.Orientation.Horizontal)
        self.network_zoom.setRange(12, 240)
        self.network_zoom.setValue(84)
        self.network_zoom.setFixedWidth(240)
        self.network_zoom.setToolTip("缩放时间轴；图中所有活动端点始终对应实际日期和时间")
        self.network_zoom.valueChanged.connect(self.time_network.set_day_width)
        self.time_network.day_width_changed.connect(
            lambda width: self.network_zoom.setValue(round(width))
        )
        network_controls.addWidget(self.network_zoom)
        network_controls.addWidget(QLabel("放大"))

        network_panel = QWidget()
        network_layout = QVBoxLayout(network_panel)
        network_layout.setContentsMargins(0, 0, 0, 0)
        network_layout.setSpacing(6)
        network_layout.addLayout(network_controls)
        network_legend = QLabel(
            '<span style="color:#2374AB;">▰</span> 普通活动节点　'
            '<span style="color:#657985;">┄┄▶</span> 逻辑关系（标注 FS/SS/FF/SF 与时距）　'
            '<span style="color:#D32F2F;">▰ ━▶</span> 连续关键控制链（含资源/开放事件）　'
            '<span style="color:#f4f6f7; background:#9aa5ab;"> 非工作日 </span>'
        )
        network_legend.setStyleSheet("color: #52616b; padding-left: 4px;")
        network_legend.setWordWrap(True)
        network_layout.addWidget(network_legend)
        network_layout.addWidget(self.time_network, 1)

        content_tabs = QTabWidget()
        content_tabs.setDocumentMode(True)
        content_tabs.addTab(tree_panel, "任务表")
        content_tabs.addTab(gantt_panel, "横道图")
        content_tabs.addTab(network_panel, "时标网络图")
        content_tabs.setToolTip("在任务明细表和横道图之间切换")
        self.content_tabs = content_tabs

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.addLayout(toolbar)
        layout.addWidget(content_tabs, 1)
        self.setCentralWidget(central)
        self.deadline_status = QLabel()
        self.statusBar().addPermanentWidget(self.deadline_status)
        self.statusBar().showMessage(f"数据保存在：{database.path}")
        if bool(self.project_settings.get("auto_recalculate", 1)):
            self.recalculate()
        else:
            self.refresh()
        self._update_task_action_state()

    def selected_task_id(self) -> int | None:
        items = self.tree.selectedItems()
        return int(items[0].data(0, Qt.ItemDataRole.UserRole)) if items else None

    def export_gantt_image(self) -> None:
        name = "计划横道图"
        for character in '<>:"/\\|?*':
            name = name.replace(character, "_")
        default_name = name.rstrip(". ") + "_横道图.png"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出完整横道图图片",
            str(Path.home() / "Desktop" / default_name),
            "PNG 图片 (*.png)",
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            output = self.gantt.export_full_image(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        width, height = self.gantt.full_content_size()
        QMessageBox.information(
            self,
            "导出完成",
            f"完整横道图已导出到：\n{output}\n\n图片尺寸：{width} × {height} 像素",
        )
        self.statusBar().showMessage(f"完整横道图图片已导出：{output}", 6000)

    def export_time_network_image(self) -> None:
        default_name = "计划_时标网络图.png"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出完整时标网络图",
            str(Path.home() / "Desktop" / default_name),
            "PNG 图片 (*.png)",
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            output = self.time_network.export_full_image(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        width, height = self.time_network.full_content_size()
        QMessageBox.information(
            self,
            "导出完成",
            f"完整时标网络图已导出到：\n{output}\n\n图片尺寸：{width} × {height} 像素",
        )
        self.statusBar().showMessage(f"完整时标网络图已导出：{output}", 6000)

    def _update_task_action_state(self) -> None:
        enabled = self.selected_task_id() is not None
        for button in (
            self.add_subtask_button,
            self.split_task_button,
            self.delete_task_button,
        ):
            button.setEnabled(enabled)

    def _select_tree_task(self, task_id: int) -> None:
        item = getattr(self, "tree_items", {}).get(task_id)
        if item is None:
            return
        parent = item.parent()
        while parent is not None:
            parent_id = parent.data(0, Qt.ItemDataRole.UserRole)
            if parent_id is not None:
                self.tree_expanded_ids.add(int(parent_id))
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.setCurrentItem(item)
        item.setSelected(True)
        self.tree.scrollToItem(item)

    def _expand_gantt(self) -> None:
        self.gantt_level.setCurrentIndex(0)
        self.gantt.expand_all()

    def _remember_tree_expanded(self, item: QTreeWidgetItem) -> None:
        if self._restoring_tree_state:
            return
        task_id = item.data(0, Qt.ItemDataRole.UserRole)
        if task_id is not None:
            self.tree_expanded_ids.add(int(task_id))

    def _remember_tree_collapsed(self, item: QTreeWidgetItem) -> None:
        if self._restoring_tree_state:
            return
        task_id = item.data(0, Qt.ItemDataRole.UserRole)
        if task_id is not None:
            self.tree_expanded_ids.discard(int(task_id))

    def _apply_tree_depth(self) -> None:
        if not hasattr(self, "tree_items"):
            return
        max_depth = self.tree_level.currentData()
        self._restoring_tree_state = True
        expanded_ids: set[int] = set()
        for task_id, item in self.tree_items.items():
            if item.childCount() == 0:
                continue
            depth = 0
            parent = item.parent()
            while parent is not None:
                depth += 1
                parent = parent.parent()
            should_expand = max_depth is None or depth < int(max_depth)
            item.setExpanded(should_expand)
            if should_expand:
                expanded_ids.add(int(task_id))
        self.tree_expanded_ids = expanded_ids
        self._restoring_tree_state = False

    def _collapse_tree(self) -> None:
        self.tree_expanded_ids.clear()
        self.tree.collapseAll()

    def _move_task_from_tree(
        self, task_id: int, new_parent_id: int | None, before_task_id: int | None
    ) -> None:
        try:
            self.database.move_task(task_id, new_parent_id, before_task_id)
        except ValueError as error:
            QMessageBox.warning(self, "无法移动任务", str(error))
            self.refresh()
            return
        if new_parent_id is not None:
            self.tree_expanded_ids.add(new_parent_id)
        self._after_schedule_change()
        self._select_tree_task(task_id)
        destination = (
            self.database.task(new_parent_id)["name"]
            if new_parent_id is not None and self.database.task(new_parent_id)
            else "顶层"
        )
        self.statusBar().showMessage(f"任务层级已更新，当前位置：{destination}", 5000)

    def add_task(self) -> None:
        selected_id = self.selected_task_id()
        selected = self.database.task(selected_id) if selected_id is not None else None
        parent_id = selected["parent_id"] if selected else None
        dialog = TaskDialog(
            self.database.tasks(),
            fixed_parent_id=parent_id,
            lock_parent=True,
            resources=self.database.resources(),
            process_templates=self.database.process_templates(),
            trades=self.database.trade_names(),
            parent=self,
        )
        if dialog.exec():
            self._save_new(dialog.data())

    def add_subtask(self) -> None:
        parent_id = self.selected_task_id()
        if parent_id is None:
            QMessageBox.information(self, "请选择上级任务", "请先选择一个任务作为上级任务。")
            return
        if self.database.has_dependencies(parent_id):
            QMessageBox.warning(
                self,
                "无法添加子任务",
                "该任务已经参与任务关系，请先删除相关关系再添加子任务。",
            )
            return
        dialog = TaskDialog(
            self.database.tasks(),
            fixed_parent_id=parent_id,
            lock_parent=True,
            resources=self.database.resources(),
            process_templates=self.database.process_templates(),
            trades=self.database.trade_names(),
            parent=self,
        )
        if dialog.exec():
            self._save_new(dialog.data())

    def _save_new(self, values: dict) -> None:
        if values["parent_id"] and self.database.has_dependencies(values["parent_id"]):
            QMessageBox.warning(
                self,
                "无法设置上级任务",
                "所选上级任务已经参与任务关系，请先删除它的相关关系。",
            )
            return
        task_id = self.database.add_task(**values)
        if values["parent_id"] is not None:
            self.tree_expanded_ids.add(int(values["parent_id"]))
        self._after_schedule_change()
        self._select_tree_task(task_id)

    def split_selected_task(self) -> None:
        task_id = self.selected_task_id()
        if task_id is None:
            QMessageBox.information(self, "请选择工序", "请先选择需要划分施工段的工序。")
            return
        task = self.database.task(task_id)
        if task.get("task_type") == "milestone":
            QMessageBox.information(self, "事件不能分段", "事件没有持续时间，不能划分施工段。")
            return
        had_dependencies = self.database.has_dependencies(task_id)
        segment_count, accepted = QInputDialog.getInt(
            self,
            "划分施工段",
            f"“{task['name']}”总工期为 {task['duration']} 工日。\n请输入施工段数量：",
            2,
            2,
            min(9999, max(2, int(float(task["duration"]) * 96))),
            1,
        )
        if not accepted:
            return
        try:
            _, durations = self.database.split_task(task_id, segment_count)
        except ValueError as error:
            QMessageBox.warning(self, "无法划分", str(error))
            return
        self._after_schedule_change()
        QMessageBox.information(
            self,
            "划分完成",
            f"已生成 {segment_count} 个独立施工段；不会自动建立段内先后关系。"
            "\n各段可由不同班组并行，实际安排由资源和工艺约束决定。\n各段估算工期："
            + "、".join(f"{duration:g}工日（{duration * 8:g}小时）" for duration in durations)
            + ("\n原任务关系已自动迁移到首段和末段。" if had_dependencies else ""),
        )

    def edit_task(self) -> None:
        task_id = self.selected_task_id()
        if task_id is None:
            QMessageBox.information(self, "请选择任务", "请先选择要编辑的任务。")
            return
        task = self.database.task(task_id)
        segment_count = len(self.database.direct_children(task_id))
        task["segment_count"] = segment_count
        dialog = TaskDialog(
            self.database.tasks(), task, resources=self.database.resources(),
            process_templates=self.database.process_templates(),
            trades=self.database.trade_names(), parent=self
        )
        if not dialog.exec():
            return
        values = dialog.data()
        duration_changed = abs(float(values["duration"]) - float(task["duration"])) > 1e-6
        constraint_changed = values["constraint_start"] != task.get("constraint_start")
        if values["parent_id"] and self.database.has_dependencies(values["parent_id"]):
            QMessageBox.warning(
                self,
                "无法设置上级任务",
                "所选上级任务已经参与任务关系，请先删除它的相关关系。",
            )
            return
        try:
            self.database.update_task(task_id=task_id, **values)
        except ValueError as error:
            QMessageBox.warning(self, "无法保存", str(error))
            return
        self._after_schedule_change()
        if segment_count and duration_changed:
            self.statusBar().showMessage(
                f"汇总工序总工期已重新分配到 {segment_count} 个施工段", 5000
            )
        elif segment_count and constraint_changed:
            self.statusBar().showMessage(
                "汇总任务的最早开始日期已同步到全部下级任务", 5000
            )

    def delete_task(self) -> None:
        task_id = self.selected_task_id()
        if task_id is None:
            QMessageBox.information(self, "请选择任务", "请先选择要删除的任务。")
            return
        task = self.database.task(task_id)
        answer = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除“{task['name']}”吗？其子任务和相关任务关系也会一并删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.database.delete_task(task_id)
            self._after_schedule_change()

    def manage_relations(self) -> None:
        dialog = RelationsDialog(self.database, self)
        self._run_batched_settings_dialog(dialog)

    def manage_process_library(self) -> None:
        dialog = ProcessLibraryDialog(self.database, self)
        self._run_batched_settings_dialog(dialog)

    def create_from_work_template(self) -> None:
        templates = [
            template for template in self.database.process_templates()
            if self.database.work_template_steps(int(template["id"]))
        ]
        if not templates:
            QMessageBox.information(
                self, "没有可用模板", "请先在作业模板库中建立模板并添加步骤。"
            )
            return
        names = [str(template["name"]) for template in templates]
        selected_name, accepted = QInputDialog.getItem(
            self, "从模板创建作业", "选择作业模板", names, 0, False
        )
        if not accepted:
            return
        template = templates[names.index(selected_name)]
        instance_name, accepted = QInputDialog.getText(
            self, "作业实例名称", "填写现场作业名称", text=selected_name
        )
        if not accepted or not instance_name.strip():
            return
        parent_id = self.selected_task_id()
        if parent_id is not None and self.database.has_dependencies(parent_id):
            QMessageBox.warning(
                self, "无法作为上级任务",
                "当前选中任务已经参与关系。请先选择一个未参与关系的汇总任务，或取消选择后创建顶层作业。",
            )
            return
        try:
            root_id = self.database.create_work_template_instance(
                int(template["id"]), instance_name, parent_id=parent_id
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法创建作业", str(error))
            return
        if parent_id is not None:
            self.tree_expanded_ids.add(parent_id)
        self.tree_expanded_ids.add(root_id)
        self._after_schedule_change()
        self._select_tree_task(root_id)
        self.statusBar().showMessage("已从模板生成完整任务/事件实例链", 5000)

    def show_constraint_overview(self) -> None:
        ConstraintOverviewDialog(self.database, self).exec()

    def manage_calendar(self) -> None:
        dialog = WorkCalendarDialog(self.database, self)
        self._run_batched_settings_dialog(dialog)

    def manage_project_settings(self) -> None:
        dialog = ProjectSettingsDialog(self.project_settings, self)
        if not dialog.exec():
            return
        values = dialog.data()
        self.database.update_settings(
            self.project_settings.get("plan_name", "工程进度计划"),
            values["project_start"],
            weekend_working=values["weekend_working"],
            ignore_noise_restrictions=values["ignore_noise_restrictions"],
            work_sessions=values["work_sessions"],
            required_finish=values["required_finish"],
            status_date=values["status_date"],
            optimization_goal=values["optimization_goal"],
            overtime_lunch=values["overtime_lunch"],
            overtime_night=values["overtime_night"],
            overtime_holiday=values["overtime_holiday"],
            crew_max_daily_hours=values["crew_max_daily_hours"],
            crew_max_consecutive_days=values["crew_max_consecutive_days"],
            resource_leveling_mode=values["resource_leveling_mode"],
            auto_recalculate=values["auto_recalculate"],
        )
        self.project_settings = self.database.settings()
        self._after_schedule_change(show_success=True)

    def manage_trades(self) -> None:
        dialog = TradeManagerDialog(self.database, self)
        self._run_batched_settings_dialog(dialog)

    def manage_resources(self) -> None:
        dialog = ResourceManagerDialog(
            self.database,
            self,
            allowed_types=("crew", "equipment", "inspector"),
            title="施工资源",
        )
        self._run_batched_settings_dialog(dialog)

    def manage_workfaces(self) -> None:
        self.manage_path_network()

    def manage_path_network(self) -> None:
        dialog = PathNetworkDialog(self.database, self)
        self._run_batched_settings_dialog(dialog)

    def _run_batched_settings_dialog(self, dialog) -> None:
        """Save freely inside a settings window, then schedule at most once."""
        needs_recalculate = False

        def mark_changed() -> None:
            nonlocal needs_recalculate
            needs_recalculate = True

        dialog.changed.connect(mark_changed)
        dialog.exec()
        if needs_recalculate:
            self._after_schedule_change()

    def _work_calendar(self) -> WorkCalendar:
        return WorkCalendar.from_settings(
            self.project_settings, self.database.calendar_exception_map()
        )

    def create_flow_relation(self) -> None:
        summaries = self.database.flow_summaries()
        if len(summaries) < 2:
            QMessageBox.information(
                self,
                "需要两道分段工序",
                "请先选择工序并使用“划分施工段”，至少准备两道分段工序。",
            )
            return
        dialog = FlowRelationDialog(summaries, self)
        if not dialog.exec():
            return
        predecessor_id, successor_id, minimum_lag = dialog.data()
        try:
            self.database.create_flow_relations(
                predecessor_id,
                successor_id,
                minimum_lag,
                self._work_calendar(),
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            message = (
                "这些施工段之间已经存在关系，请先在约束关系窗口中检查。"
                if isinstance(error, sqlite3.IntegrityError)
                else str(error)
            )
            QMessageBox.warning(self, "无法建立流水关系", message)
            return

        self._after_schedule_change()
        QMessageBox.information(
            self,
            "流水关系已建立",
            f"每个施工段均已设置不少于 {minimum_lag} 自然日的硬性间隔（包含双休日）。"
            "\n最终是否连续由班组、作业面、设备和其他约束决定。",
        )

    def _calculate_and_store(self) -> None:
        self.database.sync_process_template_dependencies()
        self.database.sync_work_template_dependencies()
        tasks = self.database.tasks()
        tasks.sort(key=schedule_sort_key)
        dependencies = self.database.dependencies()
        start = str(self.project_settings["project_start"])
        calendar = self._work_calendar()
        result = SchedulingEngine.calculate(
            ScheduleModel(
                tasks=tasks,
                dependencies=dependencies,
                project_start=start,
                calendar=calendar,
                available_resources=self.database.resources(),
            ),
            ScheduleOptions(
                optimization_goal=str(
                    self.project_settings.get("optimization_goal", "stable")
                ),
                status_date=self.project_settings.get("status_date"),
                crew_max_daily_hours=float(
                    self.project_settings.get("crew_max_daily_hours", 8)
                ),
                crew_max_consecutive_days=int(
                    self.project_settings.get("crew_max_consecutive_days", 0)
                ),
                resource_leveling_mode=str(
                    self.project_settings.get("resource_leveling_mode", "delay")
                ),
            ),
        )
        self.database.save_calculated_dates(
            result.dates, result.reasons, result.crew_assignments
        )

    def recalculate(self, show_success: bool = False) -> None:
        try:
            self._calculate_and_store()
        except ScheduleError as error:
            QMessageBox.warning(self, "无法排期", str(error))
            return
        self.refresh()
        self.project_finish_label.setStyleSheet("")
        if show_success:
            self.statusBar().showMessage("计划设置已保存，日期已重新计算", 4000)

    def _after_schedule_change(self, show_success: bool = False) -> None:
        if bool(self.project_settings.get("auto_recalculate", 1)):
            self.recalculate(show_success=show_success)
            return
        self.refresh()
        current_finish = self.project_finish_label.text().split("\n", 1)
        finish_text = current_finish[1] if len(current_finish) > 1 else "—"
        self.project_finish_label.setText(f"项目预计完成（待重排）\n{finish_text}")
        self.project_finish_label.setStyleSheet(
            "background: #D9822B; color: white; border-radius: 7px;"
            " padding: 5px 14px; font-size: 15px; font-weight: 700;"
        )
        self.deadline_status.setText("排程结果待更新")
        self.statusBar().showMessage(
            "修改已保存；当前为手动排程，请点击右上角“重新排期”更新日期",
            6000,
        )

    def refresh(self) -> None:
        selected_task_id = self.selected_task_id()
        tasks = self.database.tasks()
        tasks.sort(key=schedule_sort_key)
        dependencies = self.database.dependencies()
        calendar = self._work_calendar()
        self._restoring_tree_state = True
        self.tree.clear()
        by_parent: dict[int | None, list[dict]] = defaultdict(list)
        for task in tasks:
            by_parent[task["parent_id"]].append(task)

        trade_cache: dict[int, set[str]] = {}
        item_by_id: dict[int, QTreeWidgetItem] = {}

        def descendant_trades(task: dict) -> set[str]:
            task_id = int(task["id"])
            if task_id in trade_cache:
                return trade_cache[task_id]
            children = by_parent.get(task_id, [])
            if not children:
                trades = {normalized_trade(task.get("trade"))}
            else:
                trades = set().union(*(descendant_trades(child) for child in children))
            trade_cache[task_id] = trades
            return trades

        def add_children(parent_id: int | None, parent_item: QTreeWidgetItem | None) -> None:
            type_labels = {
                "work": "施工",
                "logistics": "物流",
                "inspection": "验收",
                "wait": "等待/养护",
                "milestone": "事件",
            }
            event_status_labels = {
                "derived": "前序推导",
                "planned": "预计发生",
                "occurred": "已发生",
                "pending": "待发生",
            }
            for task in by_parent[parent_id]:
                is_summary = bool(by_parent.get(task["id"]))
                if is_summary and task.get("calculated_start"):
                    start = datetime.fromisoformat(task["calculated_start"])
                    finish = datetime.fromisoformat(task["calculated_finish"])
                    elapsed = WorkCalendar.natural_days_between(start.date(), finish.date())
                    duration_text = f"{elapsed} 自然日"
                elif task.get("task_type") == "milestone":
                    duration_text = "0（里程碑）"
                else:
                    minutes = int(task.get("duration_minutes") or round(float(task["duration"]) * 480))
                    if minutes < 60:
                        duration_text = f"{minutes}分钟"
                    elif minutes % 60 == 0:
                        duration_text = f"{minutes / 60:g}小时 / {minutes / 480:g}工日"
                    else:
                        duration_text = f"{minutes // 60}小时{minutes % 60}分钟"
                task_trades = descendant_trades(task)
                trade_text = next(iter(task_trades)) if len(task_trades) == 1 else "混合工种"
                spatial_resources = [
                    resource["name"]
                    for resource in task.get("resources", [])
                    if resource.get("resource_type") in {"workface", "access"}
                ]
                occupied_resources = [
                    resource["name"]
                    for resource in task.get("resources", [])
                    if resource.get("resource_type") in {"equipment", "inspector"}
                ]
                assigned_crew = task.get("assigned_crew")
                if assigned_crew:
                    crew_text = assigned_crew["name"]
                elif not is_summary and task.get("crew_required"):
                    crew_text = "待计算"
                else:
                    crew_text = "—"
                values = [
                    task["name"],
                    f"汇总任务（{len(by_parent[task['id']])}项）"
                    if is_summary
                    else (
                        f"事件·{event_status_labels.get(task.get('event_status', 'derived'), '前序推导')}"
                        if task.get("task_type") == "milestone"
                        else type_labels.get(task.get("task_type", "work"), "活动")
                    ),
                    task.get("template_source") or "—",
                    "有" if task.get("has_noise") and not is_summary else "—",
                    trade_text,
                    crew_text,
                    duration_text,
                    "、".join(spatial_resources),
                    "、".join(occupied_resources),
                    task.get("constraint_start") or "",
                    task.get("calculated_start") or "",
                    task.get("calculated_finish") or "",
                    task.get("schedule_reason") or "",
                    task.get("notes") or "",
                ]
                item = QTreeWidgetItem(values)
                item.setData(0, Qt.ItemDataRole.UserRole, task["id"])
                item_by_id[int(task["id"])] = item
                if is_summary:
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                if parent_item:
                    parent_item.addChild(item)
                else:
                    self.tree.addTopLevelItem(item)
                add_children(task["id"], item)

        add_children(None, None)
        self.tree_items = item_by_id
        summary_ids = {task_id for task_id, item in item_by_id.items() if item.childCount()}
        self.tree_expanded_ids.intersection_update(summary_ids)
        for task_id in self.tree_expanded_ids:
            item_by_id[task_id].setExpanded(True)
        self._restoring_tree_state = False
        if selected_task_id is not None:
            self._select_tree_task(selected_task_id)
        else:
            self.tree.clearSelection()
            self.tree.setCurrentItem(None)
        self._update_task_action_state()
        for column in range(14):
            self.tree.resizeColumnToContents(column)
        minimum_widths = (220, 90, 110, 60, 80, 120, 120, 150, 140, 110, 130, 130, 180, 180)
        for column, minimum in enumerate(minimum_widths):
            self.tree.setColumnWidth(column, max(minimum, self.tree.columnWidth(column)))
        start = str(self.project_settings["project_start"])
        trade_colors = self.database.trade_color_map()
        legend_html = "　".join(
            f'<span style="color:{color};">■</span> {trade}'
            for trade, color in trade_colors.items()
        )
        self.trade_legend.setText(
            "工种颜色："
            + legend_html
            + '　深蓝色：混合工种汇总　<span style="color:#D32F2F;">■</span> 关键路径（开启时）'
        )
        self.gantt.set_data(
            tasks,
            dependencies,
            start,
            calendar,
            crew_max_daily_hours=float(
                self.project_settings.get("crew_max_daily_hours", 8)
            ),
            crew_max_consecutive_days=int(
                self.project_settings.get("crew_max_consecutive_days", 0)
            ),
            trade_colors=trade_colors,
        )
        self.time_network.set_data(
            tasks,
            dependencies,
            start,
            calendar,
            crew_max_daily_hours=float(
                self.project_settings.get("crew_max_daily_hours", 8)
            ),
        )
        self._update_project_finish_display(tasks)
        self._update_deadline_status(tasks)

    def _update_project_finish_display(self, tasks: list[dict]) -> None:
        parent_ids = {
            int(task["parent_id"])
            for task in tasks
            if task.get("parent_id") is not None
        }
        leaf_tasks = [task for task in tasks if int(task["id"]) not in parent_ids]
        dated_tasks = [task for task in leaf_tasks if task.get("calculated_finish")]
        unscheduled_count = len(leaf_tasks) - len(dated_tasks)
        if not dated_tasks:
            self.project_finish_label.setText("项目预计完成\n尚无排程结果")
            self.project_finish_label.setStyleSheet(
                "background: #687985; color: white; border-radius: 7px; "
                "padding: 5px 14px; font-size: 15px; font-weight: 700;"
            )
            self.project_finish_label.setToolTip("当前没有已计算完成时间的末级任务")
            return

        planned_finish = max(
            datetime.fromisoformat(str(task["calculated_finish"]))
            for task in dated_tasks
        )
        latest_names = [
            str(task["name"])
            for task in dated_tasks
            if datetime.fromisoformat(str(task["calculated_finish"])) == planned_finish
        ]
        detail = f"{planned_finish:%Y-%m-%d %H:%M}"
        background = "#17324d"
        status_tip = ""
        required_finish = self.project_settings.get("required_finish")
        if required_finish:
            target_date = datetime.fromisoformat(str(required_finish)).date()
            difference = (planned_finish.date() - target_date).days
            if difference > 0:
                detail += f"　延期 {difference} 天"
                background = "#b42318"
                status_tip = f"\n要求完工：{target_date:%Y-%m-%d}，预计延期 {difference} 天"
            elif difference < 0:
                detail += f"　提前 {-difference} 天"
                background = "#18733c"
                status_tip = f"\n要求完工：{target_date:%Y-%m-%d}，预计提前 {-difference} 天"
            else:
                detail += "　按期"
                background = "#18733c"
                status_tip = f"\n要求完工：{target_date:%Y-%m-%d}，预计按期完成"
        if unscheduled_count:
            detail += f"　{unscheduled_count} 项待排"
            background = "#a15c00"

        self.project_finish_label.setText(f"项目预计完成\n{detail}")
        self.project_finish_label.setStyleSheet(
            f"background: {background}; color: white; border-radius: 7px; "
            "padding: 5px 14px; font-size: 15px; font-weight: 700;"
        )
        tooltip = "最晚完成任务：" + "、".join(latest_names)
        if unscheduled_count:
            tooltip += f"\n另有 {unscheduled_count} 个末级任务尚未排入计划"
        self.project_finish_label.setToolTip(tooltip + status_tip)

    def _update_deadline_status(self, tasks: list[dict]) -> None:
        required_finish = self.project_settings.get("required_finish")
        dated_finishes = [
            datetime.fromisoformat(str(task["calculated_finish"]))
            for task in tasks
            if task.get("calculated_finish")
        ]
        if not required_finish or not dated_finishes:
            self.deadline_status.clear()
            self.deadline_status.setToolTip("")
            return
        planned_finish = max(dated_finishes)
        target_date = datetime.fromisoformat(str(required_finish)).date()
        difference = (planned_finish.date() - target_date).days
        if difference > 0:
            self.deadline_status.setText(f"预计延期 {difference} 天")
            self.deadline_status.setStyleSheet(
                "color: #b42318; font-weight: 700; padding: 0 10px;"
            )
        elif difference < 0:
            self.deadline_status.setText(f"预计提前 {-difference} 天")
            self.deadline_status.setStyleSheet(
                "color: #18733c; font-weight: 700; padding: 0 10px;"
            )
        else:
            self.deadline_status.setText("预计按期完成")
            self.deadline_status.setStyleSheet(
                "color: #18733c; font-weight: 700; padding: 0 10px;"
            )
        self.deadline_status.setToolTip(
            f"当前预计完成：{planned_finish:%Y-%m-%d %H:%M}；"
            f"要求完工：{target_date:%Y-%m-%d}"
        )
