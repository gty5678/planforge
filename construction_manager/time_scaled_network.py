from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from .network_data import assign_time_lanes, collapsed_network_data
from .scheduler import critical_path
from .work_calendar import WorkCalendar


CRITICAL_COLOR = "#D32F2F"
ACTIVITY_COLOR = "#2374AB"
RELATION_COLOR = "#657985"


class TimeScaledNetworkWidget(QAbstractScrollArea):
    """A time-true, activity-on-node precedence network chart.

    The horizontal position of every activity endpoint is derived directly from
    its calculated timestamp.  Lanes are only used to separate concurrent work;
    they do not change the time meaning of the diagram.
    """

    day_width_changed = Signal(float)
    HEADER_HEIGHT = 82
    LANE_HEIGHT = 76
    LEFT_MARGIN = 54
    RIGHT_MARGIN = 80

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.all_tasks: list[dict] = []
        self.tasks: list[dict] = []
        self.dependencies: list[dict] = []
        self.calendar = WorkCalendar()
        self.project_start = date.today()
        self.day_width = 84.0
        self.show_critical_path = True
        self.critical_task_ids: set[int] = set()
        self.critical_relations: set[tuple[int, int, str]] = set()
        self.critical_edge_pairs: set[tuple[int, int]] = set()
        self.lane_by_id: dict[int, int] = {}
        self._activity_hit_rects: dict[int, QRectF] = {}
        self.setMouseTracking(True)
        self.setMinimumHeight(300)
        self.setToolTip(
            "横轴为实际日历时间；活动节点表示实际起止，折线表示紧前关系；"
            "红色表示关键路径。滚轮缩放，Ctrl＋滚轮上下滚动，Shift＋滚轮左右滚动。"
        )

    def set_data(
        self,
        tasks: list[dict],
        dependencies: list[dict],
        project_start: str,
        calendar: WorkCalendar | None = None,
        crew_max_daily_hours: float | None = None,
    ) -> None:
        self.all_tasks = tasks
        self.tasks, self.dependencies, source_to_display = collapsed_network_data(
            tasks, dependencies
        )
        self.project_start = date.fromisoformat(project_start)
        self.calendar = calendar or WorkCalendar()
        raw_driving_relations: set[tuple[int, int, str]] = set()
        raw_critical_task_ids, raw_critical_relations = critical_path(
            tasks,
            dependencies,
            self.calendar,
            crew_max_daily_hours,
            driving_relations_out=raw_driving_relations,
        )
        display_ids = {int(task["id"]) for task in self.tasks}
        self.critical_task_ids = {
            source_to_display.get(task_id, task_id)
            for task_id in raw_critical_task_ids
            if source_to_display.get(task_id, task_id) in display_ids
        }
        self.critical_relations = {
            (
                source_to_display.get(left, left),
                source_to_display.get(right, right),
                code,
            )
            for left, right, code in raw_critical_relations
            if source_to_display.get(left, left)
            != source_to_display.get(right, right)
        }
        self.critical_edge_pairs = {
            (left, right) for left, right, _ in self.critical_relations
        }
        driver_labels = {
            "resource": "资源驱动",
            "availability": "开放事件",
        }
        mapped_drivers: set[tuple[int, int, str]] = set()
        for left, right, kind in raw_driving_relations:
            mapped_left = source_to_display.get(left, left)
            mapped_right = source_to_display.get(right, right)
            if (
                mapped_left == mapped_right
                or mapped_left not in display_ids
                or mapped_right not in display_ids
                or (mapped_left, mapped_right) in self.critical_edge_pairs
            ):
                continue
            mapped_drivers.add((mapped_left, mapped_right, kind))
        for left, right, kind in sorted(mapped_drivers):
            self.dependencies.append(
                {
                    "predecessor_id": left,
                    "successor_id": right,
                    "relation_type": "FS",
                    "lag_days": 0,
                    "lag_unit": "calendar_day",
                    "critical_driver": True,
                    "display_label": driver_labels.get(kind, "控制关系"),
                }
            )
            self.critical_edge_pairs.add((left, right))
        self._assign_lanes()
        self._update_scrollbars()
        self.viewport().update()

    def _assign_lanes(self) -> None:
        """Greedily share a lane only when activity labels cannot collide."""
        # The clearance keeps endpoint symbols, relation labels and names apart.
        self.lane_by_id = assign_time_lanes(self.tasks)

    def set_show_critical_path(self, enabled: bool) -> None:
        self.show_critical_path = bool(enabled)
        self.viewport().update()

    def set_day_width(self, width: float, anchor_x: float | None = None) -> None:
        width = max(12.0, min(240.0, round(float(width))))
        if width == self.day_width:
            return
        old_width = self.day_width
        old_scroll = self.horizontalScrollBar().value()
        anchor_x = self.viewport().width() / 2 if anchor_x is None else anchor_x
        time_position = max(0.0, old_scroll + anchor_x - self.LEFT_MARGIN) / old_width
        self.day_width = width
        self._update_scrollbars()
        self.horizontalScrollBar().setValue(
            max(0, round(self.LEFT_MARGIN + time_position * width - anchor_x))
        )
        self.viewport().update()
        self.day_width_changed.emit(width)

    def wheelEvent(self, event) -> None:
        modifiers = event.modifiers()
        delta = event.pixelDelta().y() or event.angleDelta().y()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - round(delta / 2))
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - round(delta / 2))
        else:
            steps = delta / (40 if event.pixelDelta().y() else 120)
            self.set_day_width(self.day_width + steps * 4, event.position().x())
        event.accept()

    def _date_range(self) -> tuple[date, date]:
        starts = [self.project_start]
        finishes = [self.project_start + timedelta(days=13)]
        for task in self.tasks:
            starts.append(datetime.fromisoformat(task["calculated_start"]).date())
            finishes.append(datetime.fromisoformat(task["calculated_finish"]).date())
        return min(starts) - timedelta(days=1), max(finishes) + timedelta(days=2)

    def full_content_size(self) -> tuple[int, int]:
        first, last = self._date_range()
        lane_count = max(self.lane_by_id.values(), default=-1) + 1
        width = math.ceil(
            self.LEFT_MARGIN + ((last - first).days + 1) * self.day_width + self.RIGHT_MARGIN
        )
        height = self.HEADER_HEIGHT + max(1, lane_count) * self.LANE_HEIGHT + 30
        return width, height

    def _update_scrollbars(self) -> None:
        width, height = self.full_content_size()
        self.horizontalScrollBar().setRange(0, max(0, width - self.viewport().width()))
        self.horizontalScrollBar().setPageStep(self.viewport().width())
        self.verticalScrollBar().setRange(0, max(0, height - self.viewport().height()))
        self.verticalScrollBar().setPageStep(self.viewport().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_scrollbars()

    def _time_x(self, value: datetime, first: date) -> float:
        origin = datetime.combine(first, time.min)
        days = (value - origin).total_seconds() / 86400
        return self.LEFT_MARGIN + days * self.day_width

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        self._paint_network(
            painter,
            self.horizontalScrollBar().value(),
            self.verticalScrollBar().value(),
            self.viewport().width(),
            self.viewport().height(),
        )

    def _paint_network(
        self,
        painter: QPainter,
        x_offset: int,
        y_offset: int,
        view_width: int,
        view_height: int,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(QRectF(0, 0, view_width, view_height), QColor("#ffffff"))
        painter.translate(-x_offset, -y_offset)
        first, last = self._date_range()
        total_width, total_height = self.full_content_size()

        # Calendar grid and the two-level, engineering-style time ruler.
        painter.fillRect(QRectF(0, 0, total_width, self.HEADER_HEIGHT), QColor("#eaf0f4"))
        current = first
        while current <= last:
            x = self.LEFT_MARGIN + (current - first).days * self.day_width
            if not self.calendar.is_working_day(current):
                painter.fillRect(
                    QRectF(x, self.HEADER_HEIGHT, self.day_width, total_height),
                    QColor("#f4f6f7"),
                )
            painter.setPen(QPen(QColor("#d4dde3"), 1))
            painter.drawLine(QPointF(x, 48), QPointF(x, total_height))
            if self.day_width >= 34:
                painter.setPen(QColor("#344955"))
                painter.setFont(QFont(painter.font().family(), 8))
                painter.drawText(
                    QRectF(x, 50, self.day_width, 29),
                    Qt.AlignmentFlag.AlignCenter,
                    current.strftime("%m/%d"),
                )
            elif current.weekday() == 0:
                painter.setPen(QColor("#344955"))
                painter.drawText(
                    QRectF(x + 2, 52, 80, 24),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    current.strftime("%m/%d"),
                )
            current += timedelta(days=1)

        # Month bands make long schedules unambiguous.
        cursor = date(first.year, first.month, 1)
        while cursor <= last:
            next_month = (
                date(cursor.year + 1, 1, 1)
                if cursor.month == 12
                else date(cursor.year, cursor.month + 1, 1)
            )
            band_start = max(first, cursor)
            band_finish = min(last + timedelta(days=1), next_month)
            x1 = self.LEFT_MARGIN + (band_start - first).days * self.day_width
            x2 = self.LEFT_MARGIN + (band_finish - first).days * self.day_width
            painter.setPen(QPen(QColor("#9fb0bb"), 1))
            painter.drawRect(QRectF(x1, 20, x2 - x1, 28))
            painter.setPen(QColor("#263238"))
            painter.setFont(QFont(painter.font().family(), 9, QFont.Weight.Bold))
            painter.drawText(
                QRectF(x1, 20, x2 - x1, 28),
                Qt.AlignmentFlag.AlignCenter,
                f"{cursor.year}年{cursor.month}月",
            )
            cursor = next_month

        painter.setPen(QPen(QColor("#aab8c0"), 1))
        painter.drawLine(QPointF(0, self.HEADER_HEIGHT), QPointF(total_width, self.HEADER_HEIGHT))

        points: dict[int, tuple[QPointF, QPointF]] = {}
        hit_rects: dict[int, QRectF] = {}
        for task in self.tasks:
            task_id = int(task["id"])
            lane = self.lane_by_id[task_id]
            y = self.HEADER_HEIGHT + lane * self.LANE_HEIGHT + 42
            start = datetime.fromisoformat(task["calculated_start"])
            finish = datetime.fromisoformat(task["calculated_finish"])
            x1 = self._time_x(start, first)
            x2 = self._time_x(finish, first)
            if abs(x2 - x1) < 5:
                x2 = x1 + 5
            points[task_id] = (QPointF(x1, y), QPointF(x2, y))
            is_critical = self.show_critical_path and task_id in self.critical_task_ids
            color = QColor(CRITICAL_COLOR if is_critical else ACTIVITY_COLOR)

            if task.get("task_type") == "milestone":
                diamond = QPolygonF(
                    [QPointF(x1, y - 7), QPointF(x1 + 7, y), QPointF(x1, y + 7), QPointF(x1 - 7, y)]
                )
                painter.setPen(QPen(color, 2.5 if is_critical else 1.8))
                painter.setBrush(color)
                painter.drawPolygon(diamond)
                label_center = x1
                hit_rect = QRectF(x1 - 9, y - 10, 18, 20)
            else:
                node_rect = QRectF(min(x1, x2), y - 9, max(5.0, abs(x2 - x1)), 18)
                painter.setPen(QPen(color, 2.8 if is_critical else 1.8))
                painter.setBrush(QColor("#FDECEC" if is_critical else "#E7F2F9"))
                painter.drawRoundedRect(node_rect, 4, 4)
                label_center = (x1 + x2) / 2
                hit_rect = QRectF(min(x1, x2) - 5, y - 15, abs(x2 - x1) + 10, 30)
            hit_rects[task_id] = hit_rect

            painter.setPen(color)
            painter.setFont(QFont(painter.font().family(), 8, QFont.Weight.Bold if is_critical else QFont.Weight.Normal))
            name = str(task["name"])
            duration = float(task.get("duration", 0))
            caption = f"{task_id}  {name}  ({duration:g}工日)"
            text_width = min(260.0, max(110.0, painter.fontMetrics().horizontalAdvance(caption) + 12.0))
            painter.drawText(
                QRectF(label_center - text_width / 2, y - 31, text_width, 22),
                Qt.AlignmentFlag.AlignCenter,
                painter.fontMetrics().elidedText(caption, Qt.TextElideMode.ElideRight, int(text_width)),
            )

        # Draw precedence links after activities so their endpoint semantics stay visible.
        for relation in self.dependencies:
            predecessor_id = int(relation["predecessor_id"])
            successor_id = int(relation["successor_id"])
            if predecessor_id not in points or successor_id not in points:
                continue
            relation_type = str(relation.get("relation_type", "FS"))
            pred_start, pred_finish = points[predecessor_id]
            succ_start, succ_finish = points[successor_id]
            start_point = pred_finish if relation_type[0] == "F" else pred_start
            end_point = succ_start if relation_type[1] == "S" else succ_finish
            relation_key = (predecessor_id, successor_id, relation_type)
            is_critical = self.show_critical_path and (
                relation_key in self.critical_relations
                or bool(relation.get("critical_driver"))
            )
            color = QColor(CRITICAL_COLOR if is_critical else RELATION_COLOR)
            pen = QPen(color, 2.4 if is_critical else 1.2)
            if not is_critical:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            bend_y = min(start_point.y(), end_point.y()) - 15
            if abs(start_point.y() - end_point.y()) < 2:
                bend_y = start_point.y() + 17
            path = QPainterPath(start_point)
            path.lineTo(QPointF(start_point.x(), bend_y))
            path.lineTo(QPointF(end_point.x(), bend_y))
            path.lineTo(end_point)
            painter.drawPath(path)
            painter.setBrush(color)
            approach = 1 if end_point.y() >= bend_y else -1
            painter.drawPolygon(
                QPolygonF(
                    [
                        end_point,
                        QPointF(end_point.x() - 4, end_point.y() - 7 * approach),
                        QPointF(end_point.x() + 4, end_point.y() - 7 * approach),
                    ]
                )
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            lag = float(relation.get("lag_days", 0))
            lag_unit = "自然日" if str(relation.get("lag_unit", "calendar_day")) == "calendar_day" else "工作日"
            collapsed_count = int(relation.get("collapsed_count", 1))
            label = str(relation.get("display_label") or relation_type)
            if lag:
                label += f" {lag:+g}{lag_unit}"
            if collapsed_count > 1:
                label += f" ×{collapsed_count}"
            painter.setPen(color)
            painter.setFont(QFont(painter.font().family(), 7))
            painter.drawText(
                QRectF((start_point.x() + end_point.x()) / 2 - 55, bend_y - 15, 110, 15),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

        # Project boundary markers, virtual nodes and the missing boundary links
        # make the controlling chain traceable from start through to completion.
        if self.tasks:
            start_time = min(datetime.fromisoformat(task["calculated_start"]) for task in self.tasks)
            finish_time = max(datetime.fromisoformat(task["calculated_finish"]) for task in self.tasks)
            for value, caption, color_name in (
                (start_time, "项目开始", "#2E7D32"),
                (finish_time, "项目完成", CRITICAL_COLOR),
            ):
                x = self._time_x(value, first)
                painter.setPen(QPen(QColor(color_name), 1.5, Qt.PenStyle.DashDotLine))
                painter.drawLine(QPointF(x, self.HEADER_HEIGHT), QPointF(x, total_height - 12))
                painter.setPen(QColor(color_name))
                painter.setFont(QFont(painter.font().family(), 8, QFont.Weight.Bold))
                painter.drawText(QRectF(x + 4, self.HEADER_HEIGHT + 2, 90, 20), caption)

            if self.show_critical_path and self.critical_task_ids:
                virtual_y = self.HEADER_HEIGHT + 17
                project_start_point = QPointF(self._time_x(start_time, first), virtual_y)
                project_finish_point = QPointF(self._time_x(finish_time, first), virtual_y)
                incoming_ids = {right for _, right in self.critical_edge_pairs}
                outgoing_ids = {left for left, _ in self.critical_edge_pairs}
                root_ids = self.critical_task_ids - incoming_ids
                terminal_ids = self.critical_task_ids - outgoing_ids

                def draw_boundary_link(start_point: QPointF, end_point: QPointF) -> None:
                    painter.setPen(QPen(QColor(CRITICAL_COLOR), 2.2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    bend_y = min(start_point.y(), end_point.y()) + 12
                    path = QPainterPath(start_point)
                    path.lineTo(QPointF(start_point.x(), bend_y))
                    path.lineTo(QPointF(end_point.x(), bend_y))
                    path.lineTo(end_point)
                    painter.drawPath(path)
                    direction = 1 if end_point.y() >= bend_y else -1
                    painter.setBrush(QColor(CRITICAL_COLOR))
                    painter.drawPolygon(
                        QPolygonF(
                            [
                                end_point,
                                QPointF(end_point.x() - 4, end_point.y() - 7 * direction),
                                QPointF(end_point.x() + 4, end_point.y() - 7 * direction),
                            ]
                        )
                    )

                for task_id in sorted(root_ids):
                    if task_id in points:
                        draw_boundary_link(project_start_point, points[task_id][0])
                for task_id in sorted(terminal_ids):
                    if task_id in points:
                        draw_boundary_link(points[task_id][1], project_finish_point)

                painter.setPen(QPen(QColor(CRITICAL_COLOR), 2.4))
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(project_start_point, 6, 6)
                painter.setBrush(QColor(CRITICAL_COLOR))
                painter.drawEllipse(project_finish_point, 6, 6)

        self._activity_hit_rects = hit_rects

        # Frozen title and ruler baseline stay readable while scrolling.
        painter.resetTransform()
        painter.fillRect(QRectF(0, 0, view_width, 20), QColor("#dce7ed"))
        painter.setPen(QColor("#17324d"))
        painter.setFont(QFont(painter.font().family(), 9, QFont.Weight.Bold))
        painter.drawText(
            QRectF(10, 0, view_width - 20, 20),
            Qt.AlignmentFlag.AlignVCenter,
            "单代号时标网络图（分段汇总、未分段保留）",
        )

    def mouseMoveEvent(self, event) -> None:
        point = QPointF(
            event.position().x() + self.horizontalScrollBar().value(),
            event.position().y() + self.verticalScrollBar().value(),
        )
        task_by_id = {int(task["id"]): task for task in self.tasks}
        for task_id, rect in self._activity_hit_rects.items():
            if rect.contains(point):
                task = task_by_id[task_id]
                critical = "是" if task_id in self.critical_task_ids else "否"
                self.setToolTip(
                    f"{task['name']}\n开始：{task['calculated_start']}\n完成：{task['calculated_finish']}"
                    f"\n关键活动：{critical}"
                )
                return
        super().mouseMoveEvent(event)

    def export_full_image(self, path: str | Path) -> Path:
        output = Path(path)
        width, height = self.full_content_size()
        if width > 30000 or height > 30000 or width * height > 180_000_000:
            raise ValueError("时标网络图尺寸过大，请先缩小横向比例后再导出。")
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        self._paint_network(painter, 0, 0, width, height)
        painter.end()
        output.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(output), "PNG"):
            raise OSError(f"无法写入图片：{output}")
        return output
