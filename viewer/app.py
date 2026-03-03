from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)

from indexer.db import FootageRecord, get_default_db_path, open_default_db


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


class Viewer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Footage Library Viewer")
        self.resize(800, 600)

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All", "video", "sequence", "image"])
        self._filter_combo.currentIndexChanged.connect(self.on_filter_changed)

        self._category_filter = QComboBox()
        self._category_filter.addItem("All Categories")
        self._category_filter.currentIndexChanged.connect(self.on_category_changed)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search...")
        self._search_edit.textChanged.connect(self.on_search_changed)

        self._list_widget = QListWidget()
        self._list_widget.itemDoubleClicked.connect(self.on_item_double_clicked)

        layout = QVBoxLayout()
        filters_row = QHBoxLayout()
        filters_row.addWidget(self._filter_combo)
        filters_row.addWidget(self._category_filter)
        layout.addLayout(filters_row)
        layout.addWidget(self._search_edit)
        layout.addWidget(self._list_widget)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self._populate_category_filter()
        self._refresh_list()

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

    def load_data(
        self,
        asset_type: str | None = None,
        category: str | None = None,
        query: str = "",
    ) -> None:
        db_path = get_default_db_path()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        like_param = f"%{query}%" if query else "%"

        try:
            sql = """
                SELECT path, name
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

            # name LIKE is always applied (search box)
            conditions.append("name LIKE ?")
            params.append(like_param)

            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            sql += " LIMIT 1000"

            cur.execute(sql, params)

            rows = cur.fetchall()
        finally:
            conn.close()

        self._list_widget.clear()
        for row in rows:
            name = row["name"] if row["name"] else row["path"]
            item = QListWidgetItem(name)
            # Store full path in data.
            item.setData(Qt.UserRole, row["path"])
            self._list_widget.addItem(item)

    def on_filter_changed(self, index: int) -> None:
        self._refresh_list()

    def on_category_changed(self, index: int) -> None:
        self._refresh_list()

    def on_search_changed(self, text: str) -> None:
        self._refresh_list()

    def on_item_double_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if not path:
            return

        # Используем нормализованный путь для запроса к БД
        db_path_value = str(Path(path))

        try:
            import nuke

            # Remap path for Nuke using config, затем нормализуем в POSIX-вид
            remapped_path = _apply_remap(path)
            nuke_path = Path(remapped_path).as_posix()

            # Lookup sequence info in the database
            db_file_path = get_default_db_path()
            conn = sqlite3.connect(db_file_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT is_sequence, frame_start, frame_end
                    FROM footage
                    WHERE path = ?
                    """,
                    (db_path_value,),
                )
                row = cur.fetchone()
            finally:
                conn.close()

            if row is not None and row["is_sequence"] == 1:
                first = row["frame_start"]
                last = row["frame_end"]
                if first is None:
                    first = 0
                if last is None:
                    last = first

                node = nuke.nodes.Read(
                    file=nuke_path,
                    first=first,
                    last=last,
                    origfirst=first,
                    origlast=last,
                )
            else:
                # Non-sequence: create Read with file set at creation time (GUI-like behavior)
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

