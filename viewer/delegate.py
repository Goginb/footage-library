from __future__ import annotations

try:
    from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QStyledItemDelegate
except ImportError:
    from PySide2.QtCore import QModelIndex, QRect, QSize, Qt
    from PySide2.QtGui import QPainter, QPixmap
    from PySide2.QtWidgets import QStyledItemDelegate


def _fit_pixmap(pixmap: QPixmap, target: QRect) -> tuple[QRect, QPixmap]:
    """Scale pixmap keeping aspect ratio, center inside target rect."""
    scaled = pixmap.scaled(
        target.width(), target.height(),
        Qt.KeepAspectRatio, Qt.SmoothTransformation,
    )
    x = target.x() + (target.width() - scaled.width()) // 2
    y = target.y() + (target.height() - scaled.height()) // 2
    return QRect(x, y, scaled.width(), scaled.height()), scaled


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
        """
        Update hover state based on mouse position over the tile.
        x_ratio is in [0..1]; map it to frame index 0..31.
        """
        if not index.isValid():
            self.hover_index = None
            self.hover_frame = 0
            return

        frame = int(x_ratio * 32)
        if frame < 0:
            frame = 0
        if frame > 31:
            frame = 31

        self.hover_index = index
        self.hover_frame = frame

    def _icon_rect(self, option) -> QRect:
        rect = option.rect
        view = self.parent()
        if view is not None:
            icon_size: QSize = view.iconSize()
            w = min(icon_size.width(), rect.width())
            h = min(icon_size.height(), rect.height())
        else:
            w = rect.width()
            h = max(1, rect.height() * 4 // 5)
        x = rect.x() + (rect.width() - w) // 2
        y = rect.y()
        return QRect(x, y, w, h)

    def paint(self, painter: QPainter, option, index: QModelIndex):
        super().paint(painter, option, index)

        metadata = index.data(Qt.UserRole) or {}
        path = metadata.get("path") if isinstance(metadata, dict) else None
        if not path:
            return

        icon_area = self._icon_rect(option)
        icon_size = icon_area.width() if icon_area.width() > 0 else 200

        # Base static thumbnail (000.jpg or hash-cache)
        base = self.preview_manager.get_thumbnail(path, icon_size)
        if base and not base.isNull():
            dest_rect, scaled = _fit_pixmap(base, icon_area)
            painter.drawPixmap(dest_rect, scaled)

        # Hover frame overlay when mouse is over this index
        if self.hover_index is not None and index == self.hover_index:
            hover_pm = self.preview_manager.get_hover_frame(path, self.hover_frame, icon_size)
            if hover_pm and not hover_pm.isNull():
                dest_rect, scaled = _fit_pixmap(hover_pm, icon_area)
                painter.drawPixmap(dest_rect, scaled)
