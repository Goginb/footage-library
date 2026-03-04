from __future__ import annotations

from collections import OrderedDict
from typing import Any

try:
    from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt
    from PySide6.QtGui import QColor, QIcon, QPixmap
except ImportError:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt
    from PySide2.QtGui import QColor, QIcon, QPixmap


class AssetModel(QAbstractListModel):
    def __init__(self, icon_size: int = 120, parent=None) -> None:
        super().__init__(parent)
        self.assets: list[dict[str, Any]] = []
        self.previews: dict[str, QIcon] = {}
        self.preview_pixmaps: dict[str, QPixmap] = {}
        self.path_to_row: dict[str, int] = {}
        self.ram_cache: OrderedDict[str, QIcon] = OrderedDict()
        self.RAM_CACHE_LIMIT = 120
        self._icon_size = icon_size
        self._placeholder_icon = self._make_placeholder_icon(icon_size)

    def _make_placeholder_icon(self, size: int) -> QIcon:
        pm = QPixmap(size, size)
        pm.fill(QColor(128, 128, 128))
        return QIcon(pm)

    def set_icon_size(self, size: int) -> None:
        self._icon_size = size
        self._placeholder_icon = self._make_placeholder_icon(size)
        rebuilt: dict[str, QIcon] = {}
        for path, pixmap in self.preview_pixmaps.items():
            scaled = pixmap.scaled(
                self._icon_size,
                self._icon_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            rebuilt[path] = QIcon(scaled)
        self.previews = rebuilt
        self.ram_cache.clear()
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
        self.previews.clear()
        self.preview_pixmaps.clear()
        self.ram_cache.clear()
        if self.assets:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self.assets) - 1, 0)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DecorationRole])

    def get_cached_icon(self, path: str) -> QIcon | None:
        if path in self.ram_cache:
            icon = self.ram_cache.pop(path)
            self.ram_cache[path] = icon
            return icon
        return None

    def add_ram_cache(self, path: str, icon: QIcon) -> None:
        if not path or icon.isNull():
            return
        if path in self.ram_cache:
            self.ram_cache.pop(path)
        self.ram_cache[path] = icon
        if len(self.ram_cache) > self.RAM_CACHE_LIMIT:
            self.ram_cache.popitem(last=False)

    def placeholder_cache_key(self) -> int:
        return self._placeholder_icon.cacheKey()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self.assets)

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
            icon = self.get_cached_icon(path)
            if icon is not None:
                return icon
            icon = self.previews.get(path)
            if icon is not None:
                self.add_ram_cache(path, icon)
                return icon
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
        row = self.path_to_row.get(path)
        if row is None or icon.isNull():
            return
        self.preview_pixmaps[path] = icon.pixmap(self._icon_size, self._icon_size)
        self.previews[path] = icon
        self.add_ram_cache(path, icon)
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def get_asset(self, row: int) -> dict[str, Any] | None:
        if row < 0 or row >= len(self.assets):
            return None
        return self.assets[row]

    def has_preview(self, path: str) -> bool:
        return path in self.previews
