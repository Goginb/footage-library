from __future__ import annotations

try:
    from PySide6.QtCore import QModelIndex, Qt
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QStyledItemDelegate
except ImportError:
    from PySide2.QtCore import QModelIndex, Qt
    from PySide2.QtGui import QPainter
    from PySide2.QtWidgets import QStyledItemDelegate


class AssetDelegate(QStyledItemDelegate):
    def __init__(self, preview_manager, parent=None):
        super().__init__(parent)
        self.preview_manager = preview_manager
        self.hover_index = None
        self.hover_frame = 0

    def clear_hover(self) -> None:
        self.hover_index = None
        self.hover_frame = 0

    def update_hover(self, index, x_ratio: float) -> None:
        self.hover_index = index if index and index.isValid() else None
        if self.hover_index is None:
            return

        metadata = index.data(Qt.UserRole) or {}
        path = metadata.get("path")
        if not path:
            return

        frames = self.preview_manager.get_frames(path)
        if frames:
            x_ratio = max(0.0, min(1.0, float(x_ratio)))
            self.hover_frame = int(x_ratio * (len(frames) - 1))

    def paint(self, painter: QPainter, option, index: QModelIndex):
        rect = option.rect
        metadata = index.data(Qt.UserRole) or {}
        path = metadata.get("path")
        thumbnail = self.preview_manager.get_thumbnail(path) if path else None

        if thumbnail is not None and not thumbnail.isNull():
            painter.drawPixmap(rect, thumbnail)
        else:
            super().paint(painter, option, index)
            return

        if self.hover_index is not None and index == self.hover_index and path:
            frames = self.preview_manager.get_frames(path)
            if frames:
                frame = frames[self.hover_frame % len(frames)]
                painter.drawPixmap(rect, frame)
