from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem


class DraggableTaskTree(QTreeWidget):
    """A tree that requests persistent hierarchy moves instead of moving UI rows only."""

    task_move_requested = Signal(int, object, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self._dragged_task_id: int | None = None

    @staticmethod
    def _task_id(item: QTreeWidgetItem | None) -> int | None:
        if item is None:
            return None
        value = item.data(0, Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    def _next_sibling_id(self, target: QTreeWidgetItem, source_id: int) -> int | None:
        parent = target.parent()
        if parent is None:
            start = self.indexOfTopLevelItem(target) + 1
            candidates = (self.topLevelItem(index) for index in range(start, self.topLevelItemCount()))
        else:
            start = parent.indexOfChild(target) + 1
            candidates = (parent.child(index) for index in range(start, parent.childCount()))
        for candidate in candidates:
            candidate_id = self._task_id(candidate)
            if candidate_id != source_id:
                return candidate_id
        return None

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.itemAt(event.position().toPoint()) is None
        ):
            self.clearSelection()
            self.setCurrentItem(None)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.clearSelection()
            self.setCurrentItem(None)
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions) -> None:
        # currentItem can change while hovering during a drag, so remember the
        # source before the drag begins.
        self._dragged_task_id = self._task_id(self.currentItem())
        try:
            super().startDrag(supported_actions)
        finally:
            self._dragged_task_id = None

    def dropEvent(self, event) -> None:
        source_id = self._dragged_task_id or self._task_id(self.currentItem())
        if source_id is None:
            event.ignore()
            return

        target = self.itemAt(event.position().toPoint())
        position = self.dropIndicatorPosition()
        new_parent_id: int | None
        before_task_id: int | None = None

        if target is None or position == QAbstractItemView.DropIndicatorPosition.OnViewport:
            new_parent_id = None
        elif position == QAbstractItemView.DropIndicatorPosition.OnItem:
            new_parent_id = self._task_id(target)
        else:
            new_parent_id = self._task_id(target.parent())
            if position == QAbstractItemView.DropIndicatorPosition.AboveItem:
                before_task_id = self._task_id(target)
            else:
                before_task_id = self._next_sibling_id(target, source_id)

        event.acceptProposedAction()
        # Rebuilding the tree from a direct signal while Qt is still completing
        # its drop can invalidate the source item. Defer persistence one event turn.
        QTimer.singleShot(
            0,
            lambda: self.task_move_requested.emit(
                source_id, new_parent_id, before_task_id
            ),
        )
