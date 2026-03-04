from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

try:
    from PySide6.QtCore import QObject, QPoint, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
    from PySide6.QtGui import QIcon, QImageReader, QPixmap
    from PySide6.QtWidgets import (
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
    from PySide2.QtCore import QObject, QPoint, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
    from PySide2.QtGui import QIcon, QImageReader, QPixmap
    from PySide2.QtWidgets import (
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
    from viewer.preview import PreviewManager, VIDEO_EXT, generate_video_preview
except ImportError:
    from preview import PreviewManager, VIDEO_EXT, generate_video_preview
try:
    from viewer.asset_model import AssetModel
except ImportError:
    from asset_model import AssetModel
try:
    from viewer.delegate import AssetDelegate
except ImportError:
    from delegate import AssetDelegate

import inspect
print("DB MODULE USED BY VIEWER:", inspect.getfile(open_default_db))


_CACHE_ROOT_PRINTED = False
MAX_PREVIEW_FRAMES = 120
MAX_PREVIEW_PER_PASS = 12
PREFETCH = 30
_DEBUG_LOG_PATH = Path(__file__).resolve().parent.parent / "debug-0006b8.log"
_DEBUG_SESSION_ID = "0006b8"


def _write_debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
    run_id: str = "pre-fix",
) -> None:
    try:
        payload = {
            "sessionId": _DEBUG_SESSION_ID,
            "id": f"log_{int(time.time() * 1000)}_{time.perf_counter_ns()}",
            "timestamp": int(time.time() * 1000),
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


def get_local_cache_root() -> Path:
    """
    Return the root directory for local thumbnail cache in LOCALAPPDATA.
    """
    global _CACHE_ROOT_PRINTED

    local_appdata = os.getenv("LOCALAPPDATA")
    if not local_appdata:
        # Fallback to user home if LOCALAPPDATA is not set (very rare)
        local_appdata = str(Path.home())

    root = Path(local_appdata) / "FootageLibrary" / "thumb_cache"
    root.mkdir(parents=True, exist_ok=True)

    if not _CACHE_ROOT_PRINTED:
        print("THUMB CACHE ROOT:", str(root))
        _CACHE_ROOT_PRINTED = True

    return root


def get_preview_cache_dir(asset_path: str) -> Path:
    """
    Return the cache directory for a specific asset, based on md5 of its path.
    """
    h = hashlib.md5(asset_path.encode("utf-8")).hexdigest()
    return get_local_cache_root() / h


def preview_exists(asset_path: str) -> bool:
    """
    Return True if a cached preview (jpg) exists for the given asset path.
    """
    cache_dir = get_preview_cache_dir(asset_path)
    if not cache_dir.exists() or not cache_dir.is_dir():
        return False
    return any(cache_dir.glob("*.jpg"))


def generate_sequence_preview(asset_path: str, frame_start: int, frame_end: int) -> None:
    """
    Generate preview thumbnails for an image sequence and store them in the local cache.
    Thumbnails are saved as JPG files named 000.jpg, 001.jpg, ... in the sequence cache dir.
    """
    try:
        total_frames = max(0, int(frame_end) - int(frame_start) + 1)
    except Exception:
        print("generate_sequence_preview: invalid frame range", frame_start, frame_end)
        return

    if total_frames <= 0:
        print("generate_sequence_preview: no frames to process")
        return

    # Determine which frame numbers to sample
    if total_frames <= MAX_PREVIEW_FRAMES:
        frame_numbers = list(range(int(frame_start), int(frame_end) + 1))
    else:
        step = total_frames / float(MAX_PREVIEW_FRAMES)
        frame_numbers_set = set()
        for i in range(MAX_PREVIEW_FRAMES):
            idx = int(frame_start + i * step)
            if idx > frame_end:
                idx = frame_end
            frame_numbers_set.add(idx)
        frame_numbers = sorted(frame_numbers_set)

    cache_dir = get_preview_cache_dir(asset_path)
    cache_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    for out_index, frame_num in enumerate(frame_numbers):
        try:
            frame_path = asset_path.replace("%04d", f"{int(frame_num):04d}")
        except Exception:
            frame_path = asset_path

        pixmap = QPixmap(frame_path)
        if pixmap.isNull():
            continue

        out_name = f"{out_index:03d}.jpg"
        out_path = cache_dir / out_name
        pixmap.save(str(out_path), "JPG")
        generated += 1

    print(f"generate_sequence_preview: generated {generated} frames")


def _load_remap_config() -> dict[str, str]:
    try:
        project_root = Path(__file__).resolve().parent.parent
        remap_path = project_root / "config" / "remap.json"
        if not remap_path.exists():
            return {}
        with remap_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            # Ensure keys/values are strings
            return {
                str(k): str(v)
                for k, v in data.items()
            }
        return {}
    except Exception:
        # Any error in config loading should not break viewer/Nuke
        return {}


_REMAP_RULES: dict[str, str] = _load_remap_config()


def _apply_remap(path: str) -> str:
    """Apply first matching prefix remap from config; return original on any error."""
    try:
        for source, target in _REMAP_RULES.items():
            if path.startswith(source):
                return target + path[len(source):]
    except Exception:
        return path
    return path


class PreviewLoaderSignals(QObject):
    preview_ready = Signal(str, QPixmap, int)
    task_done = Signal(str, int, bool)


class PreviewLoaderTask(QRunnable):
    def __init__(
        self,
        asset_path: str,
        is_sequence: bool,
        frame_start: int | None,
        frame_end: int | None,
        asset_type: str | None,
        extension: str | None,
        request_id: int,
        get_current_request_id,
    ) -> None:
        super().__init__()
        self.asset_path = asset_path
        self.is_sequence = is_sequence
        self.frame_start = frame_start
        self.frame_end = frame_end
        self.asset_type = asset_type
        self.extension = extension
        self.request_id = request_id
        self.get_current_request_id = get_current_request_id
        self.signals = PreviewLoaderSignals()

    def run(self) -> None:
        success = False
        if self.request_id != self.get_current_request_id():
            self.signals.task_done.emit(self.asset_path, self.request_id, False)
            return

        preview_path = ensure_preview(
            asset_path=self.asset_path,
            is_sequence=self.is_sequence,
            frame_start=self.frame_start,
            frame_end=self.frame_end,
            asset_type=self.asset_type,
            extension=self.extension,
        )

        if not preview_path:
            self.signals.task_done.emit(self.asset_path, self.request_id, False)
            return

        reader = QImageReader(preview_path)
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            self.signals.task_done.emit(self.asset_path, self.request_id, False)
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self.signals.task_done.emit(self.asset_path, self.request_id, False)
            return
        if self.request_id != self.get_current_request_id():
            self.signals.task_done.emit(self.asset_path, self.request_id, False)
            return
        self.signals.preview_ready.emit(self.asset_path, pixmap, self.request_id)
        success = True
        self.signals.task_done.emit(self.asset_path, self.request_id, success)


def _first_cached_preview(asset_path: str) -> str | None:
    cache_dir = get_preview_cache_dir(asset_path)
    frame_files = sorted(cache_dir.glob("*.jpg"))
    if not frame_files:
        return None
    preferred = cache_dir / "000.jpg"
    if preferred.exists():
        return str(preferred)
    return str(frame_files[0])


def generate_image_preview(image_path: str) -> str | None:
    cache_dir = get_preview_cache_dir(image_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / "000.jpg"
    if out_path.exists():
        return str(out_path)

    reader = QImageReader(image_path)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return None
    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull():
        return None
    pixmap.save(str(out_path), "JPG")
    if out_path.exists():
        return str(out_path)
    return None


def ensure_preview(
    asset_path: str,
    is_sequence: bool,
    frame_start: int | None,
    frame_end: int | None,
    asset_type: str | None,
    extension: str | None,
) -> str | None:
    cached = _first_cached_preview(asset_path)
    if cached:
        return cached

    is_video = (asset_type == "video") or ((extension or "").lower() in VIDEO_EXT)
    if is_video:
        return generate_video_preview(asset_path)

    if is_sequence and frame_start is not None and frame_end is not None:
        generate_sequence_preview(asset_path, int(frame_start), int(frame_end))
        return _first_cached_preview(asset_path)

    return generate_image_preview(asset_path)


class Viewer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Footage Library Viewer")
        self.resize(800, 600)
        self._data_loaded = False

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All", "video", "sequence", "image"])
        self._filter_combo.currentIndexChanged.connect(self.on_filter_changed)

        self._category_filter = QComboBox()
        self._category_filter.addItem("All Categories")
        self._category_filter.currentIndexChanged.connect(self.on_category_changed)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search...")
        self._search_edit.textChanged.connect(self.on_search_changed)

        self.preview_size = 200
        self.item_by_path: dict[str, int] = {}
        self.preview_requested: set[str] = set()
        self._preview_request_id = 0
        self._thread_pool = QThreadPool.globalInstance()
        self._thread_pool.setMaxThreadCount(4)
        self.thread_pool = self._thread_pool
        self._lazy_timer = QTimer(self)
        self._lazy_timer.setSingleShot(True)
        self._lazy_timer.timeout.connect(self.load_visible_previews)
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
        self._list.setUniformItemSizes(True)
        self._list.setIconSize(QSize(self.preview_size, self.preview_size))
        self._list.setGridSize(QSize(self.preview_size + 30, self.preview_size + 50))
        self._list.setMouseTracking(True)
        self._list.viewport().setMouseTracking(True)
        self._list.doubleClicked.connect(self.on_item_double_clicked)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self.on_list_context_menu)
        self._list.verticalScrollBar().valueChanged.connect(self.update_preview_queue)
        self.preview_manager = PreviewManager(
            thread_pool=self._thread_pool,
            start_task=self._start_preview_task,
            is_cached=self._is_asset_cached,
            max_retry=2,
        )
        self.delegate = AssetDelegate(self.preview_manager, self.list_view)
        self.list_view.setItemDelegate(self.delegate)
        self._base_mouse_move = self.list_view.mouseMoveEvent
        self.list_view.mouseMoveEvent = self._on_mouse_move

        layout = QVBoxLayout()

        self._rescan_button = QPushButton("Rescan Library")
        self._rescan_button.clicked.connect(self.on_rescan_clicked)
        toolbar_row = QHBoxLayout()
        toolbar_row.addWidget(self._rescan_button)
        layout.addLayout(toolbar_row)

        filters_row = QHBoxLayout()
        filters_row.addWidget(self._filter_combo)
        filters_row.addWidget(self._category_filter)
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._data_loaded:
            self._populate_category_filter()
            self.load_data()
            self._data_loaded = True

    def _populate_category_filter(self) -> None:
        db_path = get_default_db_path()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            cur.execute(
                """
                SELECT DISTINCT category
                FROM footage
                WHERE category IS NOT NULL
                ORDER BY category
                """
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        for row in rows:
            value = row["category"]
            if value:
                self._category_filter.addItem(value)

    def _current_asset_type_filter(self) -> str | None:
        text = self._filter_combo.currentText()
        return None if text == "All" else text

    def _current_category_filter(self) -> str | None:
        text = self._category_filter.currentText()
        if text == "All Categories" or not text:
            return None
        return text

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

    def load_data(
        self,
        asset_type: str | None = None,
        category: str | None = None,
        query: str = "",
    ) -> None:
        self.delegate.clear_hover()

        # region agent log
        _write_debug_log(
            "H3",
            "viewer/app.py:load_data:start",
            "load_data reset begin",
            {
                "listCountBefore": self._model.rowCount(),
                "itemByPathBefore": len(self.item_by_path),
                "previewRequestedBefore": len(self.preview_requested),
                "requestIdBefore": self._preview_request_id,
            },
        )
        # endregion
        self._model.set_assets([])
        self._model.clear_previews()
        self.item_by_path.clear()
        self.preview_requested.clear()
        self.preview_manager.clear()
        self._preview_request_id += 1
        self.thread_pool.clear()

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
        QTimer.singleShot(0, self.update_preview_queue)

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
            sql = """
                SELECT id, path, name, category, is_sequence, frame_start, frame_end, asset_type, extension
                FROM footage
            """
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

    def get_visible_indexes(self) -> list[int]:
        viewport = self.list_view.viewport().rect()
        top_left = self.list_view.indexAt(viewport.topLeft())
        bottom_right = self.list_view.indexAt(viewport.bottomRight())
        if not top_left.isValid() or not bottom_right.isValid():
            return []
        start = top_left.row()
        end = bottom_right.row()
        if end < start:
            return []
        return list(range(start, end + 1))

    def get_priority_indexes(self) -> list[int]:
        visible = self.get_visible_indexes()
        if not visible:
            return []
        start = max(0, visible[0] - PREFETCH)
        end = min(self.model.rowCount() - 1, visible[-1] + PREFETCH)
        if end < start:
            return []
        return list(range(start, end + 1))

    def _is_asset_cached(self, path: str) -> bool:
        return self.model.has_preview(path)

    def _start_preview_task(self, asset: dict) -> None:
        path = asset.get("path")
        if not path:
            return
        request_id = self._preview_request_id
        worker = PreviewLoaderTask(
            asset_path=path,
            is_sequence=bool(asset.get("is_sequence")),
            frame_start=asset.get("frame_start"),
            frame_end=asset.get("frame_end"),
            asset_type=asset.get("asset_type"),
            extension=asset.get("extension"),
            request_id=request_id,
            get_current_request_id=lambda: self._preview_request_id,
        )
        worker.signals.preview_ready.connect(self._on_preview_loaded)
        worker.signals.task_done.connect(self._on_preview_task_done)
        self._thread_pool.start(worker)

    def _on_preview_task_done(self, asset_path: str, request_id: int, success: bool) -> None:
        if request_id != self._preview_request_id:
            self.preview_manager.mark_loaded(asset_path, True)
            return
        if success:
            self.preview_requested.add(asset_path)
        self.preview_manager.mark_loaded(asset_path, success)

    def update_preview_queue(self) -> None:
        indexes = self.get_priority_indexes()
        if not indexes:
            return
        started = 0
        for row in indexes:
            if started >= MAX_PREVIEW_PER_PASS:
                break
            asset = self.model.get_asset(row)
            if asset is None:
                continue
            path = str(asset.get("path") or "")
            if not path:
                continue
            if self.preview_manager.is_cached(path):
                continue
            self.preview_manager.enqueue(asset)
            started += 1

    def load_visible_previews(self) -> None:
        self.update_preview_queue()

    def _on_preview_loaded(self, asset_path: str, pixmap: QPixmap, request_id: int) -> None:
        if request_id != self._preview_request_id:
            return
        if pixmap.isNull():
            return
        scaled = pixmap.scaled(
            self.preview_size,
            self.preview_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.preview_manager.set_thumbnail(asset_path, scaled)
        self._model.set_preview(asset_path, QIcon(scaled))
        index_row = self.item_by_path.get(asset_path)
        if index_row is not None:
            idx = self.model.index(index_row, 0)
            rect = self.list_view.visualRect(idx)
            self.list_view.viewport().update(rect)

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
            self.list_view.viewport().update(rect)
        self._base_mouse_move(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.load_visible_previews()

    def on_filter_changed(self, index: int) -> None:
        self._refresh_list()

    def on_category_changed(self, index: int) -> None:
        self._refresh_list()

    def on_search_changed(self, text: str) -> None:
        self._refresh_list()

    def on_rescan_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select footage folder")
        if not folder:
            return

        folder_path = Path(folder)
        print(f"Rescanning library for: {folder_path}")
        run_indexer([folder_path])
        print("Rescan finished")
        self._refresh_list()

    def on_list_context_menu(self, pos) -> None:
        index = self._list.indexAt(pos)
        if not index.isValid():
            return

        menu = QMenu(self)
        change_category_action = menu.addAction("Change Category")
        global_pos = self._list.mapToGlobal(pos)
        action = menu.exec(global_pos)

        if action == change_category_action:
            self._change_category_for_item(index)

    def _change_category_for_item(self, index) -> None:
        metadata = self._model.data(index, Qt.UserRole) or {}
        footage_id = metadata.get("id")
        if footage_id is None:
            return

        db = open_default_db()
        try:
            categories = db.get_all_categories()
        finally:
            db.close()

        new_category, ok = QInputDialog.getItem(
            self,
            "Change Category",
            "Category:",
            categories,
            0,
            True,  # editable: allow typing new category
        )
        if not ok:
            return

        new_category = new_category.strip()
        if not new_category:
            return

        db = open_default_db()
        try:
            db.update_category_by_id(int(footage_id), new_category)
        finally:
            db.close()

        # Refresh list to reflect updated category and respect filters
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
                    file=nuke_path,
                    first=first,
                    last=last,
                    origfirst=first,
                    origlast=last,
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

