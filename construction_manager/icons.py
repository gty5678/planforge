from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def make_icon(name: str, color: str = "#315B76") -> QIcon:
    """Draw a small, consistent icon set for construction scheduling actions."""
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def line(x1: float, y1: float, x2: float, y2: float) -> None:
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def rect(x: float, y: float, w: float, h: float, radius: float = 2) -> None:
        painter.drawRoundedRect(QRectF(x, y, w, h), radius, radius)

    def circle(x: float, y: float, radius: float = 4) -> None:
        painter.drawEllipse(QPointF(x, y), radius, radius)

    def plus(x: float, y: float, size: float = 6) -> None:
        line(x - size, y, x + size, y)
        line(x, y - size, x, y + size)

    if name == "save":
        rect(9, 7, 30, 34, 3); rect(15, 8, 17, 11, 1); rect(15, 27, 18, 13, 1)
    elif name == "refresh":
        painter.drawArc(QRectF(9, 9, 30, 30), 35 * 16, 275 * 16)
        painter.setBrush(QColor(color)); painter.drawPolygon(QPolygonF([QPointF(36, 7), QPointF(41, 16), QPointF(31, 15)]))
    elif name == "task_add":
        rect(8, 8, 25, 32, 3); line(14, 17, 27, 17); line(14, 24, 25, 24); plus(36, 34, 7)
    elif name == "subtask_add":
        circle(11, 12, 3); line(11, 15, 11, 34); line(11, 23, 23, 23); line(11, 34, 23, 34); plus(31, 28, 7)
    elif name == "split":
        rect(6, 13, 36, 22, 3); line(18, 14, 18, 34); line(30, 14, 30, 34)
    elif name == "edit":
        rect(7, 8, 25, 32, 3); line(14, 17, 26, 17); line(14, 24, 22, 24)
        path = QPainterPath(QPointF(22, 35)); path.lineTo(37, 20); path.lineTo(42, 25); path.lineTo(27, 40); path.closeSubpath(); painter.drawPath(path)
    elif name == "delete":
        line(11, 14, 37, 14); line(18, 9, 30, 9); rect(14, 14, 20, 26, 2); line(21, 20, 21, 34); line(27, 20, 27, 34)
    elif name == "relations":
        circle(10, 14); circle(38, 14); circle(24, 36); line(14, 14, 34, 14); line(36, 18, 27, 32); line(21, 32, 12, 18)
    elif name == "templates":
        rect(7, 8, 27, 22, 3); rect(13, 16, 27, 22, 3); line(20, 23, 33, 23); line(20, 29, 31, 29)
    elif name == "template_add":
        rect(7, 8, 24, 29, 3); line(13, 17, 25, 17); line(13, 24, 23, 24); plus(36, 34, 7)
    elif name == "overview":
        rect(8, 7, 32, 34, 3)
        for y in (16, 24, 32): circle(15, y, 2); line(21, y, 34, y)
    elif name == "flow":
        rect(5, 9, 12, 8, 2); rect(19, 21, 12, 8, 2); rect(33, 33, 10, 8, 2)
        line(17, 13, 25, 13); line(25, 13, 25, 20); line(31, 25, 38, 25); line(38, 25, 38, 32)
    elif name == "workface":
        rect(7, 8, 34, 32, 2); line(24, 8, 24, 40); line(7, 24, 41, 24); circle(15, 16, 2); circle(33, 32, 2)
    elif name == "resources":
        circle(17, 15, 6); circle(32, 17, 5); painter.drawArc(QRectF(7, 22, 22, 18), 0, 180 * 16); painter.drawArc(QRectF(23, 24, 18, 15), 0, 180 * 16)
    elif name == "palette":
        painter.drawEllipse(QRectF(7, 8, 34, 31))
        circle(17, 17, 2.5); circle(27, 14, 2.5); circle(35, 21, 2.5); circle(16, 29, 2.5)
        painter.setBrush(QColor("#f4f6f8")); painter.drawEllipse(QRectF(27, 27, 13, 12))
    elif name == "path":
        circle(9, 36, 4); circle(24, 13, 4); circle(40, 31, 4); line(12, 33, 21, 17); line(28, 15, 38, 27)
    elif name == "calendar":
        rect(7, 9, 34, 32, 3); line(7, 18, 41, 18); line(15, 6, 15, 13); line(33, 6, 33, 13)
        for x in (15, 24, 33):
            for y in (25, 33): circle(x, y, 1.5)
    elif name == "settings":
        line(9, 14, 39, 14); line(9, 24, 39, 24); line(9, 34, 39, 34)
        circle(18, 14, 4); circle(31, 24, 4); circle(22, 34, 4)
    elif name == "expand":
        line(10, 17, 24, 31); line(24, 31, 38, 17)
    elif name == "collapse":
        line(10, 31, 24, 17); line(24, 17, 38, 31)
    elif name == "export":
        rect(7, 8, 25, 27, 3); line(11, 30, 18, 23); line(18, 23, 24, 29); line(31, 20, 31, 41); line(24, 34, 31, 41); line(38, 34, 31, 41)
    else:
        circle(24, 24, 14)

    painter.end()
    return QIcon(pixmap)
