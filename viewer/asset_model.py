from __future__ import annotations

from typing import Any

try:
    from PySide6.QtCore import QAbstractListModel, QMimeData, QModelIndex, Qt
    from PySide6.QtGui import QColor, QIcon, QPixmap
except ImportError:
    from PySide2.QtCore import QAbstractListModel, QMimeData, QModelIndex, Qt
    from PySide2.QtGui import QColor, QIcon, QPixmap


class AssetModel(QAbstractListModel):
    def __init__(self, icon_size: int = 120, parent=None) -> None:
        super().__init__(parent)
        self.assets: list[dict[str, Any]] = []
        self.path_to_row: dict[str, int] = {}
        self._icon_size = icon_size
        self._placeholder_icon = self._make_placeholder_icon(icon_size)

    def _make_placeholder_icon(self, size: int) -> QIcon:
        pm = QPixmap(size, size)
        pm.fill(QColor(128, 128, 128))
        return QIcon(pm)

    def set_icon_size(self, size: int) -> None:
        self._icon_size = size
        self._placeholder_icon = self._make_placeholder_icon(size)
        if self.assets:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self.assets) - 1, 0)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DecorationRole])

    def set_assets(self, assets: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self.assets = assets
        self.path_to_row = {}
        for i, asset in enumerate(assets):
            path = str(asset.get("path") or "")
            if path:
                self.path_to_row[path] = i
        self.endResetModel()

    def clear_previews(self) -> None:
        # В новой архитектуре модель не хранит превью,
        # но событие нужно, чтобы плитки могли перерисоваться с placeholder.
        if self.assets:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self.assets) - 1, 0)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DecorationRole])

    # ----- Qt model interface -----

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self.assets)

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:  # type: ignore[override]
        default = super().flags(index)
        if index.isValid():
            return default | Qt.ItemIsDragEnabled
        return default

    def mimeTypes(self) -> list[str]:
        return ["text/plain"]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:
        data = QMimeData()
        paths: list[str] = []
        for idx in indexes:
            if idx.isValid():
                row = idx.row()
                if 0 <= row < len(self.assets):
                    p = self.assets[row].get("path", "")
                    if p:
                        paths.append(str(p))
        data.setText("\n".join(paths))
        return data

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self.assets):
            return None

        asset = self.assets[row]
        path = str(asset.get("path") or "")

        if role == Qt.DisplayRole:
            return asset.get("name") or path
        if role == Qt.DecorationRole:
            # Модель всегда отдаёт только placeholder.
            # Реальные превью и hover-кадры рисует delegate из PreviewManager/atlas.
            return self._placeholder_icon
        if role == Qt.UserRole:
            return {
                "id": asset.get("id"),
                "path": asset.get("path"),
                "name": asset.get("name"),
                "extension": asset.get("extension"),
                "asset_type": asset.get("asset_type"),
                "frame_start": asset.get("frame_start"),
                "frame_end": asset.get("frame_end"),
                "is_sequence": asset.get("is_sequence"),
            }
        return None

    def set_preview(self, path: str, icon: QIcon) -> None:
        # Больше не используется: превью живут в PreviewManager/atlas,
        # а не в модели.
        return

    def get_asset(self, row: int) -> dict[str, Any] | None:
        if row < 0 or row >= len(self.assets):
            return None
        return self.assets[row]

    def has_preview(self, path: str) -> bool:
        # Совместимость со старым интерфейсом. Фактический статус кэша
        # теперь определяется в PreviewManager/atlas.
        return False
