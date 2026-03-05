from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

try:
    from PySide6.QtCore import QObject, QPoint, QSize, Qt, QTimer, Signal, QUrl
    from PySide6.QtGui import QIcon, QPixmap, QDesktopServices
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QComboBox,
        QFileDialog,
        QHBoxLayout,
        QInputDialog,
        QLineEdit,
        QListView,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QSlider,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    from PySide2.QtCore import QObject, QPoint, QSize, Qt, QTimer, Signal, QUrl
    from PySide2.QtGui import QIcon, QPixmap, QDesktopServices
    from PySide2.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QComboBox,
        QFileDialog,
        QHBoxLayout,
        QInputDialog,
        QLineEdit,
        QListView,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QSlider,
        QVBoxLayout,
        QWidget,
    )

from indexer.db import FootageRecord, get_default_db_path, open_default_db
from indexer.scan import run_indexer

try:
    from viewer.preview import PreviewManager
except ImportError:
    from preview import PreviewManager
try:
    from viewer.asset_model import AssetModel
except ImportError:
    from asset_model import AssetModel
try:
    from viewer.delegate import AssetDelegate
except ImportError:
    from delegate import AssetDelegate


_CACHE_ROOT_PRINTED = False
VISIBLE_LIMIT = 30


def get_local_cache_root() -> Path:
    global _CACHE_ROOT_PRINTED
    local_appdata = os.getenv("LOCALAPPDATA")
    if not local_appdata:
        local_appdata = str(Path.home())
    root = Path(local_appdata) / "FootageLibrary" / "thumb_cache"
    root.mkdir(parents=True, exist_ok=True)
    if not _CACHE_ROOT_PRINTED:
        print("THUMB CACHE ROOT:", str(root))
        _CACHE_ROOT_PRINTED = True
    return root


def _load_remap_config() -> dict[str, str]:
    try:
        project_root = Path(__file__).resolve().parent.parent
        remap_path = project_root / "config" / "remap.json"
        if not remap_path.exists():
            return {}
        with remap_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        return {}
    except Exception:
        return {}


_REMAP_RULES: dict[str, str] = _load_remap_config()


def _apply_remap(path: str) -> str:
    try:
        for source, target in _REMAP_RULES.items():
            if path.startswith(source):
                return target + path[len(source):]
    except Exception:
        return path
    return path


# ---------------------------------------------------------------------------
#  Categories Panel — flow-layout of checkable QPushButtons
# ---------------------------------------------------------------------------

class CategoriesPanel(QWidget):
    category_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._buttons: list[QPushButton] = []
        self._active: str | None = None
        self.setMinimumHeight(30)

    def set_categories(self, categories: list[str]) -> None:
        for btn in self._buttons:
            btn.deleteLater()
        self._buttons.clear()
        self._active = None
        for cat in categories:
            btn = QPushButton(cat, self)
            btn.setCheckable(True)
            # Используем аргумент по умолчанию, чтобы слот работал и с clicked(bool),
            # и с вызовом без аргументов в окружении Nuke.
            btn.clicked.connect(lambda checked=False, c=cat: self._on_click(c))  # type: ignore[call-arg]
            btn.show()
            self._buttons.append(btn)
        if self._buttons:
            self._buttons[0].setChecked(True)
            self._active = categories[0]
        self._reflow()

    def _on_click(self, category: str) -> None:
        for btn in self._buttons:
            btn.setChecked(btn.text() == category)
        self._active = category
        self.category_changed.emit(category)

    def current_category(self) -> str | None:
        return self._active

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self) -> None:
        if not self._buttons:
            self.setFixedHeight(30)
            return
        spacing = 4
        x, y, row_h = 0, 0, 0
        w = max(1, self.width())
        for btn in self._buttons:
            bw = btn.sizeHint().width()
            bh = btn.sizeHint().height()
            if x + bw > w and x > 0:
                x = 0
                y += row_h + spacing
                row_h = 0
            btn.setGeometry(x, y, bw, bh)
            x += bw + spacing
            row_h = max(row_h, bh)
        self.setFixedHeight(y + row_h + spacing)


# ---------------------------------------------------------------------------
#  Viewer
# ---------------------------------------------------------------------------

class Viewer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Footage Library Viewer")
        self.resize(800, 600)
        self._data_loaded = False

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All", "video", "sequence", "image"])
        self._filter_combo.currentIndexChanged.connect(self.on_filter_changed)

        self._categories_panel = CategoriesPanel()
        self._categories_panel.category_changed.connect(self._on_category_clicked)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search...")
        self._search_edit.textChanged.connect(self.on_search_changed)

        self.preview_size = 200
        self.item_by_path: dict[str, int] = {}
        self._model = AssetModel(self.preview_size, self)
        self.model = self._model

        self._list = QListView()
        self.list_view = self._list
        self._list.setModel(self._model)
        self._list.setViewMode(QListView.IconMode)
        self._list.setResizeMode(QListView.Adjust)
        self._list.setMovement(QListView.Static)
        self._list.setSpacing(10)
        self._list.setWrapping(True)
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list.setUniformItemSizes(True)
        self._list.setIconSize(QSize(self.preview_size, self.preview_size))
        self._list.setGridSize(QSize(self.preview_size + 30, self.preview_size + 50))
        self._list.setMouseTracking(True)
        self._list.viewport().setMouseTracking(True)
        self._list.doubleClicked.connect(self.on_item_double_clicked)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self.on_list_context_menu)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self.update_preview_queue)
        self._list.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        # Drag & drop
        self._list.setDragEnabled(True)
        self._list.setDragDropMode(QAbstractItemView.DragOnly)
        # Scroll without click
        self._list.setFocusPolicy(Qt.StrongFocus)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._list.viewport().setFocusPolicy(Qt.NoFocus)

        self.preview_manager = PreviewManager()
        self.delegate = AssetDelegate(self.preview_manager, self.list_view)
        self.list_view.setItemDelegate(self.delegate)
        self._base_mouse_move = self.list_view.mouseMoveEvent
        self.list_view.mouseMoveEvent = self._on_mouse_move

        # Layout
        layout = QVBoxLayout()

        layout.addWidget(self._categories_panel)

        filters_row = QHBoxLayout()
        filters_row.addWidget(self._filter_combo)
        layout.addLayout(filters_row)
        layout.addWidget(self._search_edit)

        self._preview_slider = QSlider(Qt.Horizontal)
        self._preview_slider.setMinimum(80)
        self._preview_slider.setMaximum(400)
        self._preview_slider.setValue(self.preview_size)
        self._preview_slider.valueChanged.connect(self._on_preview_size_changed)
        layout.addWidget(self._preview_slider)

        layout.addWidget(self._list)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    # ----- lifecycle -----

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._data_loaded:
            self._populate_categories()
            cat = self._categories_panel.current_category()
            self.load_data(category=cat)
            self._data_loaded = True

    # ----- categories -----

    def _populate_categories(self) -> None:
        db_path = get_default_db_path()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT DISTINCT category FROM footage "
                "WHERE category IS NOT NULL ORDER BY category"
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        categories = [row["category"] for row in rows if row["category"]]
        self._categories_panel.set_categories(categories)

    def _on_category_clicked(self, category: str) -> None:
        # Явно перезагружаем список под выбранную категорию
        asset_type = self._current_asset_type_filter()
        query = self._search_edit.text()
        self.load_data(asset_type=asset_type, category=category, query=query)

    def _current_category_filter(self) -> str | None:
        return self._categories_panel.current_category()

    # ----- filters -----

    def _current_asset_type_filter(self) -> str | None:
        text = self._filter_combo.currentText()
        return None if text == "All" else text

    def _refresh_list(self) -> None:
        asset_type = self._current_asset_type_filter()
        category = self._current_category_filter()
        query = self._search_edit.text()
        self.load_data(asset_type, category, query)

    def _on_preview_size_changed(self, value: int) -> None:
        self.preview_size = value
        self._list.setIconSize(QSize(self.preview_size, self.preview_size))
        self._list.setGridSize(QSize(self.preview_size + 30, self.preview_size + 50))
        self._model.set_icon_size(self.preview_size)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.preview_size = min(400, self.preview_size + 10)
            else:
                self.preview_size = max(80, self.preview_size - 10)
            self._preview_slider.setValue(self.preview_size)
            self._list.setIconSize(QSize(self.preview_size, self.preview_size))
            self._list.setGridSize(QSize(self.preview_size + 30, self.preview_size + 50))
            event.accept()
            return
        super().wheelEvent(event)

    # ----- data -----

    def load_data(
        self,
        asset_type: str | None = None,
        category: str | None = None,
        query: str = "",
    ) -> None:
        self.delegate.clear_hover()
        self._model.set_assets([])
        self._model.clear_previews()
        self.item_by_path.clear()
        self.preview_manager.clear()

        rows = self._fetch_assets(asset_type, category, query, limit=1000)
        assets: list[dict] = []
        for row in rows:
            path = row["path"]
            asset = {
                "id": row["id"],
                "path": path,
                "name": row["name"] if row["name"] else path,
                "extension": row["extension"],
                "asset_type": row["asset_type"],
                "frame_start": row["frame_start"],
                "frame_end": row["frame_end"],
                "is_sequence": row["is_sequence"] == 1,
            }
            assets.append(asset)
        self._model.set_assets(assets)
        for i, asset in enumerate(assets):
            path = str(asset.get("path") or "")
            if path:
                self.item_by_path[path] = i
        self._list.scrollToTop()
        # Ensure list view has focus so wheel scroll works without click.
        self._list.setFocus()
        self.update_preview_queue()

    def _fetch_assets(
        self,
        asset_type: str | None,
        category: str | None,
        query: str,
        limit: int | None,
    ):
        db_path = get_default_db_path()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        like_param = f"%{query}%" if query else "%"
        try:
            sql = "SELECT id, path, name, category, is_sequence, frame_start, frame_end, asset_type, extension FROM footage"
            conditions: list[str] = []
            params: list[object] = []
            if asset_type is not None:
                conditions.append("asset_type = ?")
                params.append(asset_type)
            if category is not None:
                conditions.append("category = ?")
                params.append(category)
            conditions.append("name LIKE ?")
            params.append(like_param)
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            conn.close()

    # ----- preview queue -----

    def get_visible_indexes(self) -> list[int]:
        viewport = self.list_view.viewport().rect()
        top_left = self.list_view.indexAt(viewport.topLeft())
        bottom_right = self.list_view.indexAt(viewport.bottomRight())
        if not top_left.isValid() or not bottom_right.isValid():
            total = self.model.rowCount()
            if total > 0:
                return list(range(min(VISIBLE_LIMIT, total)))
            return []
        start = top_left.row()
        end = bottom_right.row()
        if end < start:
            return []
        visible = list(range(start, end + 1))
        # Ограничиваем количество «видимых» элементов, чтобы не раздувать приоритетное окно.
        if len(visible) <= VISIBLE_LIMIT:
            return visible
        center = (start + end) // 2
        half = VISIBLE_LIMIT // 2
        new_start = max(0, center - half)
        new_end = min(self.model.rowCount() - 1, new_start + VISIBLE_LIMIT - 1)
        return list(range(new_start, new_end + 1))

    def update_preview_queue(self) -> None:
        """Load thumbnails for visible items on demand. No queue, no ffmpeg."""
        indexes = self.get_visible_indexes()
        if not indexes:
            return
        for row in indexes:
            asset = self.model.get_asset(row)
            if not asset:
                continue
            path = asset.get("path")
            if not path:
                continue
            self.preview_manager.get_thumbnail(path, self.preview_size)
        self.list_view.viewport().update()

    def _on_scroll_changed(self, _value: int) -> None:
        self._scroll_timer.start(100)

    def load_visible_previews(self) -> None:
        self.update_preview_queue()

    # ----- mouse / hover -----

    def _on_mouse_move(self, event) -> None:
        pos = event.pos()
        index = self.list_view.indexAt(pos)
        if not index.isValid():
            self.delegate.clear_hover()
            self.list_view.viewport().update()
            self._base_mouse_move(event)
            return
        rect = self.list_view.visualRect(index)
        if rect.width() > 0:
            x_ratio = (pos.x() - rect.x()) / float(rect.width())
            self.delegate.update_hover(index, x_ratio)
            self.list_view.viewport().update()
        self._base_mouse_move(event)

    # ----- resize -----

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.load_visible_previews()

    # ----- filter callbacks -----

    def on_filter_changed(self, index: int) -> None:
        self._refresh_list()

    def on_search_changed(self, text: str) -> None:
        self._refresh_list()

    def on_list_context_menu(self, pos) -> None:
        index = self._list.indexAt(pos)
        if not index.isValid():
            return

        selected_indexes = self._list.selectedIndexes()
        if not selected_indexes:
            selected_indexes = [index]

        menu = QMenu(self)
        copy_path_action = menu.addAction("Copy Path")
        open_folder_action = menu.addAction("Open Folder")

        # При мультивыборе отключаем действия, работающие только с одним элементом.
        if len(selected_indexes) > 1:
            copy_path_action.setEnabled(False)
            open_folder_action.setEnabled(False)

        menu.addSeparator()
        change_category_action = menu.addAction("Change Category")
        global_pos = self._list.mapToGlobal(pos)
        action = menu.exec(global_pos)
        if action is None:
            return

        if action == copy_path_action:
            target_index = selected_indexes[0]
            metadata = self._model.data(target_index, Qt.UserRole) or {}
            path = metadata.get("path")
            if not path:
                return
            # Применяем те же remap‑правила, что и для Nuke, и копируем полный путь к файлу.
            remapped = _apply_remap(str(path))
            clipboard = QApplication.clipboard()
            clipboard.setText(remapped)
            return

        if action == open_folder_action:
            target_index = selected_indexes[0]
            metadata = self._model.data(target_index, Qt.UserRole) or {}
            path = metadata.get("path")
            if not path:
                return
            remapped = _apply_remap(str(path))
            folder = Path(remapped).parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
            return

        if action == change_category_action:
            self._change_category_for_item(index)

    def _change_category_for_item(self, index) -> None:
        selected_indexes = self._list.selectedIndexes()
        if not selected_indexes:
            if not index or not index.isValid():
                return
            selected_indexes = [index]

        footage_ids: list[int] = []
        for idx in selected_indexes:
            metadata = self._model.data(idx, Qt.UserRole) or {}
            footage_id = metadata.get("id")
            if footage_id is None:
                continue
            try:
                footage_ids.append(int(footage_id))
            except (TypeError, ValueError):
                continue

        if not footage_ids:
            return

        db = open_default_db()
        try:
            categories = db.get_all_categories()
        finally:
            db.close()
        new_category, ok = QInputDialog.getItem(
            self, "Change Category", "Category:", categories, 0, True,
        )
        if not ok:
            return
        new_category = new_category.strip()
        if not new_category:
            return
        db = open_default_db()
        try:
            for fid in footage_ids:
                db.update_category_by_id(fid, new_category)
        finally:
            db.close()
        self._populate_categories()
        self._refresh_list()

    def on_item_double_clicked(self, _index) -> None:
        index = self._list.currentIndex()
        if not index.isValid():
            return
        metadata = self._model.data(index, Qt.UserRole) or {}
        path = metadata.get("path")
        if not path:
            return
        try:
            import nuke
            remapped_path = _apply_remap(path)
            nuke_path = Path(remapped_path).as_posix()
            is_sequence = metadata.get("is_sequence")
            frame_start = metadata.get("frame_start")
            frame_end = metadata.get("frame_end")
            if is_sequence and frame_start is not None and frame_end is not None:
                first = int(frame_start)
                last = int(frame_end)
                node = nuke.nodes.Read(
                    file=nuke_path, first=first, last=last,
                    origfirst=first, origlast=last,
                )
            else:
                node = nuke.createNode("Read", f"file {{{nuke_path}}}", inpanel=False)
            node["selected"].setValue(True)
        except ImportError:
            print(path)


def run_standalone() -> None:
    app = QApplication(sys.argv)
    viewer = Viewer()
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_standalone()
