from __future__ import annotations

import struct
import zlib
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from .work_calendar import WorkCalendar
from .trades import MIXED_TRADE_COLOR, TRADE_COLORS, normalized_trade
from .scheduler import critical_path, crew_actual_work_intervals, schedule_sort_key


CRITICAL_COLOR = "#D32F2F"


class GanttWidget(QAbstractScrollArea):
    day_width_changed = Signal(float)
    ROW_HEIGHT = 28
    HEADER_HEIGHT = 54
    LABEL_WIDTH = 250
    DAY_WIDTH = 96

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.all_tasks: list[dict] = []
        self.tasks: list[dict] = []
        self.dependencies: list[dict] = []
        self.project_start = date.today()
        self.calendar = WorkCalendar()
        self.collapsed_ids: set[int] = set()
        self.manually_expanded_ids: set[int] = set()
        self.max_depth: int | None = None
        self.show_critical_path = False
        self.critical_task_ids: set[int] = set()
        self.critical_relations: set[tuple[int, int, str]] = set()
        self.crew_intervals_by_task: dict[int, list[tuple[datetime, datetime]]] = {}
        self.trade_colors = dict(TRADE_COLORS)
        self.day_width = 96.0
        self._wheel_zoom_remainder = 0.0
        self._middle_panning = False
        self._pan_origin = QPointF()
        self._pan_scroll_origin = (0, 0)
        self.setMinimumHeight(260)
        self.setToolTip(
            "滚轮：缩放；Ctrl＋滚轮：上下滚动；Shift＋滚轮：左右滚动；"
            "按住中键拖动：上下左右平移\n点击汇总工序前的三角形可展开或收起施工段"
        )

    def set_data(
        self,
        tasks: list[dict],
        dependencies: list[dict],
        project_start: str,
        calendar: WorkCalendar | None = None,
        crew_max_daily_hours: float | None = None,
        crew_max_consecutive_days: int = 0,
        trade_colors: dict[str, str] | None = None,
    ) -> None:
        self.project_start = date.fromisoformat(project_start)
        self.calendar = calendar or WorkCalendar()
        self.dependencies = dependencies
        self.all_tasks = tasks
        self.trade_colors = dict(trade_colors or TRADE_COLORS)
        self.crew_intervals_by_task = defaultdict(list)
        crews = {
            int(task["assigned_crew"]["id"]): task["assigned_crew"]
            for task in tasks
            if task.get("assigned_crew")
        }
        crew_calendar = self.calendar
        if crew_max_daily_hours is not None:
            crew_calendar = crew_calendar.limited_daily_hours(
                min(float(crew_max_daily_hours), crew_calendar.HOURS_PER_DAY)
            )
        for crew in crews.values():
            for row in crew_actual_work_intervals(
                tasks,
                crew,
                crew_calendar,
                max_consecutive_days=crew_max_consecutive_days,
            ):
                self.crew_intervals_by_task[int(row["task_id"])].append(
                    (row["start"], row["finish"])
                )
        self.critical_task_ids, self.critical_relations = critical_path(
            tasks, dependencies, self.calendar, crew_max_daily_hours
        )
        valid_ids = {int(task["id"]) for task in tasks}
        self.collapsed_ids.intersection_update(valid_ids)
        self.manually_expanded_ids.intersection_update(valid_ids)
        self._rebuild_visible_tasks()

    def _is_effectively_collapsed(self, task: dict) -> bool:
        task_id = int(task["id"])
        return task_id in self.collapsed_ids or (
            self.max_depth is not None
            and int(task.get("depth", 0)) >= self.max_depth
            and task_id not in self.manually_expanded_ids
        )

    def _rebuild_visible_tasks(self) -> None:
        self.tasks = self._flatten(self.all_tasks)
        self._update_scrollbars()
        self.viewport().update()

    def _flatten(self, tasks: list[dict]) -> list[dict]:
        children: dict[int | None, list[dict]] = defaultdict(list)
        for task in tasks:
            children[task["parent_id"]].append(task)
        for siblings in children.values():
            siblings.sort(key=schedule_sort_key)
        result: list[dict] = []
        trade_cache: dict[int, set[str]] = {}

        def descendant_trades(task: dict) -> set[str]:
            task_id = int(task["id"])
            if task_id in trade_cache:
                return trade_cache[task_id]
            task_children = children.get(task_id, [])
            if not task_children:
                trades = {normalized_trade(task.get("trade"))}
            else:
                trades = set().union(*(descendant_trades(child) for child in task_children))
            trade_cache[task_id] = trades
            return trades

        def visit(parent_id: int | None, depth: int) -> None:
            for task in children[parent_id]:
                item = dict(task)
                item["depth"] = depth
                item["is_summary"] = bool(children.get(task["id"]))
                trades = descendant_trades(task)
                item["display_trade"] = next(iter(trades)) if len(trades) == 1 else None
                result.append(item)
                if not self._is_effectively_collapsed(item):
                    visit(task["id"], depth + 1)

        visit(None, 0)
        return result

    def set_max_depth(self, depth: int | None) -> None:
        self.max_depth = depth
        self.collapsed_ids.clear()
        self.manually_expanded_ids.clear()
        self._rebuild_visible_tasks()

    def set_show_critical_path(self, enabled: bool) -> None:
        self.show_critical_path = bool(enabled)
        self.viewport().update()

    def expand_all(self) -> None:
        self.collapsed_ids.clear()
        self.manually_expanded_ids.clear()
        self.max_depth = None
        self._rebuild_visible_tasks()

    def collapse_all(self) -> None:
        self.manually_expanded_ids.clear()
        self.collapsed_ids = {
            int(task["id"])
            for task in self.all_tasks
            if any(child["parent_id"] == task["id"] for child in self.all_tasks)
        }
        self._rebuild_visible_tasks()

    def set_day_width(self, width: float, anchor_x: float | None = None) -> None:
        # Quarter-pixel scale units make both wheel and slider zoom feel continuous,
        # especially in the compact week view.
        width = max(5.0, min(240.0, round(float(width) * 4) / 4))
        if width == self.day_width:
            return
        old_width = self.day_width
        old_scroll = self.horizontalScrollBar().value()
        if anchor_x is None or anchor_x < self.LABEL_WIDTH:
            anchor_x = (self.LABEL_WIDTH + self.viewport().width()) / 2
        time_position = max(0, old_scroll + anchor_x - self.LABEL_WIDTH) / old_width
        self.day_width = width
        self._update_scrollbars()
        new_scroll = self.LABEL_WIDTH + time_position * self.day_width - anchor_x
        self.horizontalScrollBar().setValue(max(0, round(new_scroll)))
        self.viewport().update()
        self.day_width_changed.emit(self.day_width)

    def wheelEvent(self, event) -> None:
        pixels = event.pixelDelta()
        angle = event.angleDelta()
        modifiers = event.modifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            # Ctrl takes precedence if both modifiers are held.
            vertical = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            bar = self.verticalScrollBar() if vertical else self.horizontalScrollBar()
            delta = pixels.y() or pixels.x()
            if not delta:
                delta = (angle.y() or angle.x()) / 120 * self.ROW_HEIGHT * 3
            bar.setValue(bar.value() - round(delta))
            self._wheel_zoom_remainder = 0.0
        else:
            pixel_delta = pixels.y() or pixels.x()
            if pixel_delta:
                zoom_delta = pixel_delta / 24
            else:
                zoom_delta = (angle.y() or angle.x()) / 120 * 2
            self._wheel_zoom_remainder += zoom_delta
            quarter_steps = int(self._wheel_zoom_remainder * 4)
            change = quarter_steps / 4
            self._wheel_zoom_remainder -= change
            if change:
                self.set_day_width(self.day_width + change, event.position().x())
        event.accept()

    def _date_range(self) -> tuple[date, date]:
        starts = [self.project_start]
        finishes = [self.project_start + timedelta(days=13)]
        for task in self.tasks:
            if task.get("calculated_start"):
                starts.append(datetime.fromisoformat(task["calculated_start"]).date())
                finishes.append(datetime.fromisoformat(task["calculated_finish"]).date())
        return min(starts) - timedelta(days=1), max(finishes) + timedelta(days=2)

    def _update_scrollbars(self) -> None:
        width, height = self.full_content_size()
        self.horizontalScrollBar().setRange(0, max(0, width - self.viewport().width()))
        self.horizontalScrollBar().setPageStep(self.viewport().width())
        self.verticalScrollBar().setRange(0, max(0, height - self.viewport().height()))
        self.verticalScrollBar().setPageStep(self.viewport().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_scrollbars()

    def full_content_size(self) -> tuple[int, int]:
        first, last = self._date_range()
        width = math.ceil(
            self.LABEL_WIDTH + ((last - first).days + 1) * self.day_width
        )
        height = self.HEADER_HEIGHT + len(self.tasks) * self.ROW_HEIGHT
        return width, height

    def _display_intervals(self, task: dict) -> list[tuple[datetime, datetime]]:
        """Use natural spans for summaries/elapsed tasks and sessions for leaf work."""
        if not task.get("calculated_start"):
            return []
        start = datetime.fromisoformat(task["calculated_start"])
        finish = datetime.fromisoformat(task["calculated_finish"])
        if task.get("is_summary") or task.get("calendar_type") == "elapsed":
            return [(start, finish)]
        if start == finish:
            return [(start, finish)]
        crew_intervals = self.crew_intervals_by_task.get(int(task["id"]))
        if crew_intervals:
            return crew_intervals
        return self.calendar.work_intervals_between(start, finish)

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        self._paint_chart(
            painter,
            self.horizontalScrollBar().value(),
            self.verticalScrollBar().value(),
            self.viewport().width(),
            self.viewport().height(),
            frozen_panes=True,
        )

    def _paint_chart(
        self,
        painter: QPainter,
        x_offset: int,
        y_offset: int,
        view_width: int,
        view_height: int,
        frozen_panes: bool,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(QRect(0, 0, view_width, view_height), QColor("#ffffff"))
        first, last = self._date_range()

        painter.translate(-x_offset, -y_offset)
        total_width = math.ceil(
            self.LABEL_WIDTH + ((last - first).days + 1) * self.day_width
        )
        total_height = self.HEADER_HEIGHT + len(self.tasks) * self.ROW_HEIGHT
        painter.fillRect(QRect(0, 0, total_width, self.HEADER_HEIGHT), QColor("#eaf0f4"))

        painter.setPen(QColor("#263238"))
        painter.setFont(QFont(painter.font().family(), 10, QFont.Weight.Bold))
        painter.drawText(
            QRect(12, 0, self.LABEL_WIDTH - 12, self.HEADER_HEIGHT),
            Qt.AlignmentFlag.AlignVCenter,
            "任务",
        )

        current = first
        day_index = 0
        weekly_mode = self.day_width < 30
        while current <= last:
            x = self.LABEL_WIDTH + day_index * self.day_width
            if not self.calendar.is_working_day(current):
                painter.fillRect(
                    QRectF(x, self.HEADER_HEIGHT, self.day_width, total_height),
                    QColor("#f5f7f8"),
                )
            if weekly_mode:
                if current.weekday() == 0:
                    painter.setPen(QPen(QColor("#9aabb5"), 1.3))
                    painter.drawLine(round(x), 0, round(x), total_height)
                    painter.setPen(QColor("#344955"))
                    painter.setFont(QFont(painter.font().family(), 7, QFont.Weight.Bold))
                    label_width = max(35, self.day_width * 7)
                    painter.drawText(
                        QRectF(x + 2, 3, label_width - 2, 24),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        current.strftime("%m/%d"),
                    )
                    painter.setFont(QFont(painter.font().family(), 7))
                    painter.drawText(
                        QRectF(x + 2, 25, label_width - 2, 20),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        "周一",
                    )
            else:
                painter.setPen(QColor("#cfd8df"))
                painter.drawLine(round(x), 0, round(x), total_height)
                painter.setPen(QColor("#3c4b55"))
                painter.setFont(QFont(painter.font().family(), 8))
                painter.drawText(
                    QRectF(x, 3, self.day_width, 24),
                    Qt.AlignmentFlag.AlignCenter,
                    current.strftime("%m/%d"),
                )
                painter.drawText(
                    QRectF(x, 25, self.day_width, 22),
                    Qt.AlignmentFlag.AlignCenter,
                    "一二三四五六日"[current.weekday()],
                )
            current += timedelta(days=1)
            day_index += 1

        today = date.today()
        if first <= today <= last:
            today_x = self.LABEL_WIDTH + (today - first).days * self.day_width
            painter.setPen(QPen(QColor("#d04444"), 2))
            painter.drawLine(round(today_x), 0, round(today_x), total_height)

        bar_rects: dict[int, tuple[QRect, QRect]] = {}
        for row, task in enumerate(self.tasks):
            y = self.HEADER_HEIGHT + row * self.ROW_HEIGHT
            if row % 2:
                painter.fillRect(QRect(0, y, total_width, self.ROW_HEIGHT), QColor("#fafbfc"))
            painter.setPen(QColor("#e0e6ea"))
            painter.drawLine(0, y + self.ROW_HEIGHT, total_width, y + self.ROW_HEIGHT)

            is_critical = self.show_critical_path and int(task["id"]) in self.critical_task_ids
            painter.setPen(QColor(CRITICAL_COLOR if is_critical else "#263238"))
            weight = (
                QFont.Weight.Bold
                if task["is_summary"] or is_critical
                else QFont.Weight.Normal
            )
            painter.setFont(QFont(painter.font().family(), 9, weight))
            label_x = 12 + task["depth"] * 20
            if task["is_summary"]:
                arrow_x = label_x
                arrow_y = y + self.ROW_HEIGHT // 2
                if self._is_effectively_collapsed(task):
                    triangle = QPolygon([QPoint(arrow_x, arrow_y - 5), QPoint(arrow_x, arrow_y + 5), QPoint(arrow_x + 7, arrow_y)])
                else:
                    triangle = QPolygon([QPoint(arrow_x, arrow_y - 3), QPoint(arrow_x + 10, arrow_y - 3), QPoint(arrow_x + 5, arrow_y + 5)])
                painter.setBrush(QColor("#526975"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPolygon(triangle)
                label_x += 14
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QColor(CRITICAL_COLOR if is_critical else "#263238"))
            painter.drawText(
                QRect(label_x, y, self.LABEL_WIDTH - label_x - 5, self.ROW_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter,
                task["name"],
            )

            if not task.get("calculated_start"):
                continue
            first_time = datetime.combine(first, time.min)
            intervals = self._display_intervals(task)
            if not intervals:
                continue
            bars: list[QRect] = []
            for interval_start, interval_finish in intervals:
                start_days = (interval_start - first_time).total_seconds() / 86400
                finish_days = (interval_finish - first_time).total_seconds() / 86400
                bar_x = round(self.LABEL_WIDTH + start_days * self.day_width)
                raw_width = round((finish_days - start_days) * self.day_width)
                minimum_width = 5 if interval_start == interval_finish else 2
                bars.append(QRect(bar_x, y + 6, max(minimum_width, raw_width), 16))
            bar_rects[task["id"]] = (bars[0], bars[-1])
            display_trade = task.get("display_trade")
            color = QColor(
                CRITICAL_COLOR
                if is_critical
                else self.trade_colors.get(display_trade, MIXED_TRADE_COLOR)
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            for bar in bars:
                painter.drawRoundedRect(bar, 3, 3)
            label_bar = max(bars, key=lambda item: item.width())
            if label_bar.width() >= 18:
                painter.setPen(QColor("#ffffff"))
                weight = QFont.Weight.Bold if task["is_summary"] else QFont.Weight.Normal
                painter.setFont(QFont(painter.font().family(), 8, weight))
                text_rect = label_bar.adjusted(4, 0, -4, 0)
                label = painter.fontMetrics().elidedText(
                    task["name"],
                    Qt.TextElideMode.ElideRight,
                    text_rect.width(),
                )
                painter.drawText(
                    text_rect,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )

        painter.setBrush(Qt.BrushStyle.NoBrush)
        for relation in self.dependencies:
            pred_bars = bar_rects.get(relation["predecessor_id"])
            succ_bars = bar_rects.get(relation["successor_id"])
            if not pred_bars or not succ_bars:
                continue
            relation_key = (
                int(relation["predecessor_id"]),
                int(relation["successor_id"]),
                str(relation["relation_type"]),
            )
            relation_is_critical = (
                self.show_critical_path and relation_key in self.critical_relations
            )
            relation_color = QColor(
                CRITICAL_COLOR if relation_is_critical else "#a54d35"
            )
            painter.setPen(QPen(relation_color, 2.4 if relation_is_critical else 1.4))
            code = relation["relation_type"]
            pred = pred_bars[-1] if code[0] == "F" else pred_bars[0]
            succ = succ_bars[0] if code[1] == "S" else succ_bars[-1]
            start = QPoint(pred.right() if code[0] == "F" else pred.left(), pred.center().y())
            end = QPoint(succ.left() if code[1] == "S" else succ.right(), succ.center().y())
            middle_x = (start.x() + end.x()) // 2
            painter.drawLine(start, QPoint(middle_x, start.y()))
            painter.drawLine(QPoint(middle_x, start.y()), QPoint(middle_x, end.y()))
            painter.drawLine(QPoint(middle_x, end.y()), end)
            lag_days = float(relation.get("lag_days", 0))
            if lag_days:
                painter.setFont(QFont(painter.font().family(), 8))
                painter.drawText(
                    QRect(middle_x + 3, min(start.y(), end.y()) - 16, 80, 16),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"+{lag_days:g}自然日",
                )
            direction = 1 if end.x() >= middle_x else -1
            arrow = QPolygon(
                [
                    end,
                    QPoint(end.x() - 6 * direction, end.y() - 4),
                    QPoint(end.x() - 6 * direction, end.y() + 4),
                ]
            )
            painter.setBrush(relation_color)
            painter.drawPolygon(arrow)
            painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.setPen(QColor("#b9c5cc"))
        painter.drawLine(self.LABEL_WIDTH, 0, self.LABEL_WIDTH, total_height)

        if frozen_panes:
            # Redraw the two frozen panes after the scrolling content.  The
            # opaque overlays also keep bars and dependency lines from sliding
            # underneath the fixed task-name column and date header.
            painter.resetTransform()
            self._draw_frozen_header(painter, x_offset, first, last)
            self._draw_frozen_labels(painter, y_offset)
            painter.setPen(QColor("#b9c5cc"))
            painter.drawLine(self.LABEL_WIDTH, 0, self.LABEL_WIDTH, view_height)
            painter.drawLine(0, self.HEADER_HEIGHT, view_width, self.HEADER_HEIGHT)

    @staticmethod
    def _write_png_chunk(file, chunk_type: bytes, data: bytes) -> None:
        file.write(struct.pack(">I", len(data)))
        file.write(chunk_type)
        file.write(data)
        file.write(struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF))

    def export_full_image(self, path: str | Path) -> Path:
        """Export every currently visible hierarchy row and the full date range."""
        width, height = self.full_content_size()
        if not self.tasks:
            raise ValueError("当前没有可导出的任务。")
        if width > 60000:
            raise ValueError("图片过宽，请先适当缩小横向比例后再导出。")

        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb",
                prefix=output.stem + "_",
                suffix=".tmp",
                dir=output.parent,
                delete=False,
            ) as file:
                temporary_path = Path(file.name)
                file.write(b"\x89PNG\r\n\x1a\n")
                self._write_png_chunk(
                    file,
                    b"IHDR",
                    struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0),
                )
                compressor = zlib.compressobj(level=6)
                chunk_height = 128
                for top in range(0, height, chunk_height):
                    current_height = min(chunk_height, height - top)
                    image = QImage(width, current_height, QImage.Format.Format_RGBA8888)
                    if image.isNull():
                        raise OSError("无法创建导出图片，请缩小横向比例后重试。")
                    image.fill(QColor("#ffffff"))
                    painter = QPainter(image)
                    painter.translate(0, -top)
                    self._paint_chart(
                        painter,
                        0,
                        0,
                        width,
                        height,
                        frozen_panes=False,
                    )
                    painter.end()
                    pixels = image.constBits()
                    bytes_per_line = image.bytesPerLine()
                    row_bytes = width * 4
                    for row in range(current_height):
                        start = row * bytes_per_line
                        compressed = compressor.compress(
                            b"\x00" + bytes(pixels[start : start + row_bytes])
                        )
                        if compressed:
                            self._write_png_chunk(file, b"IDAT", compressed)
                remaining = compressor.flush()
                if remaining:
                    self._write_png_chunk(file, b"IDAT", remaining)
                self._write_png_chunk(file, b"IEND", b"")
            temporary_path.replace(output)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
        return output

    def _draw_frozen_header(
        self, painter: QPainter, x_offset: int, first: date, last: date
    ) -> None:
        painter.fillRect(
            QRect(0, 0, self.viewport().width(), self.HEADER_HEIGHT),
            QColor("#eaf0f4"),
        )
        painter.save()
        painter.setClipRect(
            QRect(
                self.LABEL_WIDTH,
                0,
                max(0, self.viewport().width() - self.LABEL_WIDTH),
                self.HEADER_HEIGHT,
            )
        )
        painter.translate(-x_offset, 0)
        weekly_mode = self.day_width < 30
        current = first
        day_index = 0
        while current <= last:
            x = self.LABEL_WIDTH + day_index * self.day_width
            if weekly_mode:
                if current.weekday() == 0:
                    painter.setPen(QPen(QColor("#9aabb5"), 1.3))
                    painter.drawLine(round(x), 0, round(x), self.HEADER_HEIGHT)
                    painter.setPen(QColor("#344955"))
                    painter.setFont(QFont(painter.font().family(), 7, QFont.Weight.Bold))
                    label_width = max(35, self.day_width * 7)
                    painter.drawText(
                        QRectF(x + 2, 3, label_width - 2, 24),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        current.strftime("%m/%d"),
                    )
                    painter.setFont(QFont(painter.font().family(), 7))
                    painter.drawText(
                        QRectF(x + 2, 25, label_width - 2, 20),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        "周一",
                    )
            else:
                painter.setPen(QColor("#cfd8df"))
                painter.drawLine(round(x), 0, round(x), self.HEADER_HEIGHT)
                painter.setPen(QColor("#3c4b55"))
                painter.setFont(QFont(painter.font().family(), 8))
                painter.drawText(
                    QRectF(x, 3, self.day_width, 24),
                    Qt.AlignmentFlag.AlignCenter,
                    current.strftime("%m/%d"),
                )
                painter.drawText(
                    QRectF(x, 25, self.day_width, 22),
                    Qt.AlignmentFlag.AlignCenter,
                    "一二三四五六日"[current.weekday()],
                )
            current += timedelta(days=1)
            day_index += 1
        today = date.today()
        if first <= today <= last:
            today_x = self.LABEL_WIDTH + (today - first).days * self.day_width
            painter.setPen(QPen(QColor("#d04444"), 2))
            painter.drawLine(round(today_x), 0, round(today_x), self.HEADER_HEIGHT)
        painter.restore()

        painter.setPen(QColor("#263238"))
        painter.setFont(QFont(painter.font().family(), 10, QFont.Weight.Bold))
        painter.drawText(
            QRect(12, 0, self.LABEL_WIDTH - 12, self.HEADER_HEIGHT),
            Qt.AlignmentFlag.AlignVCenter,
            "任务",
        )

    def _draw_frozen_labels(self, painter: QPainter, y_offset: int) -> None:
        painter.save()
        painter.setClipRect(
            QRect(
                0,
                self.HEADER_HEIGHT,
                self.LABEL_WIDTH,
                max(0, self.viewport().height() - self.HEADER_HEIGHT),
            )
        )
        painter.translate(0, -y_offset)
        for row, task in enumerate(self.tasks):
            y = self.HEADER_HEIGHT + row * self.ROW_HEIGHT
            background = QColor("#fafbfc") if row % 2 else QColor("#ffffff")
            painter.fillRect(QRect(0, y, self.LABEL_WIDTH, self.ROW_HEIGHT), background)
            painter.setPen(QColor("#e0e6ea"))
            painter.drawLine(0, y + self.ROW_HEIGHT, self.LABEL_WIDTH, y + self.ROW_HEIGHT)

            is_critical = self.show_critical_path and int(task["id"]) in self.critical_task_ids
            painter.setPen(QColor(CRITICAL_COLOR if is_critical else "#263238"))
            weight = (
                QFont.Weight.Bold
                if task["is_summary"] or is_critical
                else QFont.Weight.Normal
            )
            painter.setFont(QFont(painter.font().family(), 9, weight))
            label_x = 12 + task["depth"] * 20
            if task["is_summary"]:
                arrow_x = label_x
                arrow_y = y + self.ROW_HEIGHT // 2
                if self._is_effectively_collapsed(task):
                    triangle = QPolygon(
                        [
                            QPoint(arrow_x, arrow_y - 5),
                            QPoint(arrow_x, arrow_y + 5),
                            QPoint(arrow_x + 7, arrow_y),
                        ]
                    )
                else:
                    triangle = QPolygon(
                        [
                            QPoint(arrow_x, arrow_y - 3),
                            QPoint(arrow_x + 10, arrow_y - 3),
                            QPoint(arrow_x + 5, arrow_y + 5),
                        ]
                    )
                painter.setBrush(QColor("#526975"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPolygon(triangle)
                label_x += 14
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QColor(CRITICAL_COLOR if is_critical else "#263238"))
            painter.drawText(
                QRect(label_x, y, self.LABEL_WIDTH - label_x - 5, self.ROW_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter,
                task["name"],
            )
        painter.restore()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_panning = True
            self._pan_origin = event.position()
            self._pan_scroll_origin = (
                self.horizontalScrollBar().value(),
                self.verticalScrollBar().value(),
            )
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        x = event.position().x()
        y = event.position().y() + self.verticalScrollBar().value()
        if (
            event.button() == Qt.MouseButton.LeftButton
            and x < self.LABEL_WIDTH
            and y >= self.HEADER_HEIGHT
        ):
            row = int((y - self.HEADER_HEIGHT) // self.ROW_HEIGHT)
            if 0 <= row < len(self.tasks) and self.tasks[row]["is_summary"]:
                task_id = int(self.tasks[row]["id"])
                if task_id in self.collapsed_ids:
                    self.collapsed_ids.remove(task_id)
                    self.manually_expanded_ids.add(task_id)
                elif self._is_effectively_collapsed(self.tasks[row]):
                    self.manually_expanded_ids.add(task_id)
                else:
                    self.collapsed_ids.add(task_id)
                    self.manually_expanded_ids.discard(task_id)
                self._rebuild_visible_tasks()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._middle_panning:
            delta = event.position() - self._pan_origin
            self.horizontalScrollBar().setValue(
                self._pan_scroll_origin[0] - round(delta.x())
            )
            self.verticalScrollBar().setValue(
                self._pan_scroll_origin[1] - round(delta.y())
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._middle_panning:
            self._middle_panning = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)
