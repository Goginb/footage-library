"""
Viewer preview: read-only. Loads existing thumbnails only.
First tries in-folder preview/<asset_name>/thumb.jpg, then thumb_cache/<hash>/thumb.jpg.
Viewer NEVER generates previews; use build_previews.py or generate_previews.py.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPixmap
except ImportError:
    from PySide2.QtCore import Qt
    from PySide2.QtGui import QImage, QPixmap


def find_preview_for_asset(asset_path: str) -> str | None:
    """
    Resolve in-folder preview path: asset_folder/preview/<asset_name>/thumb.jpg.
    For sequence paths (e.g. exp_0001.exr) also tries preview/<prefix>/thumb.jpg.
    Returns path string if file exists, else None.
    """
    asset_path = Path(asset_path)
    asset_folder = asset_path.parent
    asset_name = asset_path.stem

    base_dir = asset_folder / "preview"

    # Prefer multi-frame layout 000.jpg (new builder).
    preview_path = base_dir / asset_name / "000.jpg"
    if preview_path.exists():
        return str(preview_path)
    # Backward-compatible: thumb.jpg layout.
    legacy = base_dir / asset_name / "thumb.jpg"
    if legacy.exists():
        return str(legacy)

    # Sequence: build_previews uses prefix (exp_0001 -> exp)
    name_no_frame = re.sub(r"_\d{4}$", "", asset_name)
    if name_no_frame != asset_name:
        preview_path = base_dir / name_no_frame / "000.jpg"
        if preview_path.exists():
            return str(preview_path)
        legacy = base_dir / name_no_frame / "thumb.jpg"
        if legacy.exists():
            return str(legacy)
    return None


def get_preview_dir_for_asset(asset_path: str) -> Path | None:
    """
    Return directory that contains preview frames for the asset.

    Приоритет:
      1) Глобальный корень превью: <LIB_ROOT>/preview/<rel>/<asset_name>/
         где LIB_ROOT определяется эвристикой по пути (Z:\\_Library\\Something\\...).
      2) Старый локальный layout: asset_folder/preview/<asset_name>/ (для обратной совместимости).
    """
    p = Path(asset_path)
    asset_folder = p.parent
    asset_name = p.stem

    parts = p.parts
    # Попробуем определить корень библиотеки вида Z:\_Library\Something
    if len(parts) >= 3 and parts[1].lower() == "_library":
        lib_root = Path(parts[0]) / parts[1] / parts[2]
    elif len(parts) >= 2:
        lib_root = Path(parts[0]) / parts[1]
    else:
        lib_root = p.parent

    try:
        rel = asset_folder.relative_to(lib_root)
    except ValueError:
        rel = Path(".")

    preview_root = lib_root / "preview"

    # Основной случай: preview/<rel>/<asset_name>/
    d = preview_root / rel / asset_name
    if d.exists():
        return d

    # Секвенции: exp_0001 -> exp (preview/<rel>/exp/)
    name_no_frame = re.sub(r"_\d{4}$", "", asset_name)
    if name_no_frame != asset_name:
        d2 = preview_root / rel / name_no_frame
        if d2.exists():
            return d2

    # Fallback: старый локальный layout рядом с ассетом
    base_dir = asset_folder / "preview"
    d_local = base_dir / asset_name
    if d_local.exists():
        return d_local
    if name_no_frame != asset_name:
        d_local2 = base_dir / name_no_frame
        if d_local2.exists():
            return d_local2

    return None


def get_thumb_cache_root() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA")
    if not local_appdata:
        local_appdata = str(Path.home())
    root = Path(local_appdata) / "FootageLibrary" / "thumb_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _asset_hash(asset_path: str) -> str:
    return hashlib.md5(asset_path.encode("utf-8")).hexdigest()


def get_thumb_path(asset_path: str) -> Path:
    """Path to thumb.jpg for this asset."""
    h = _asset_hash(asset_path)
    return get_thumb_cache_root() / h / "thumb.jpg"


# ---------------------------------------------------------------------------
#  PreviewManager — RAM cache, load on demand. No queue, no ffmpeg.
# ---------------------------------------------------------------------------

class PreviewManager:
    def __init__(self) -> None:
        self.cache: dict[str, QPixmap] = {}
        self.hover_cache: dict[tuple[str, int], QPixmap] = {}

    def clear(self) -> None:
        self.cache.clear()
        self.hover_cache.clear()

    def get_thumbnail(self, path: str, icon_size: int = 200) -> QPixmap | None:
        """
        Return pixmap for asset path. Load from disk if not in cache.
        Tries in-folder preview/<asset_name>/thumb.jpg first, then thumb_cache/<hash>/thumb.jpg.
        Viewer never generates; only loads existing jpg and caches.
        """
        if not path:
            return None
        if path in self.cache:
            pm = self.cache[path]
            if pm.isNull():
                return None
            return pm

        # STEP 1–2: Prefer in-folder preview
        preview_file = find_preview_for_asset(path)
        if preview_file:
            image = QImage(preview_file)
            if not image.isNull():
                pixmap = QPixmap.fromImage(image)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        icon_size, icon_size,
                        Qt.KeepAspectRatio, Qt.SmoothTransformation,
                    )
                    self.cache[path] = scaled
                    return scaled

        # STEP 3: Fallback to central cache thumb_cache/<hash>/thumb.jpg
        thumb_path = get_thumb_path(path)
        if not thumb_path.exists():
            return None

        image = QImage(str(thumb_path))
        if image.isNull():
            return None
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return None
        scaled = pixmap.scaled(
            icon_size, icon_size,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.cache[path] = scaled
        return scaled

    def get_hover_frame(self, path: str, frame_index: int, icon_size: int) -> QPixmap | None:
        """
        Return specific hover frame (0..N) for asset if it exists.
        Does not generate anything, only reads preview/<asset>/NNN.jpg.
        """
        if not path:
            return None
        if frame_index < 0:
            return None

        key = (path, frame_index)
        if key in self.hover_cache:
            pm = self.hover_cache[key]
            return None if pm.isNull() else pm

        preview_dir = get_preview_dir_for_asset(path)
        if preview_dir is None:
            return None

        frame_file = preview_dir / f"{frame_index:03d}.jpg"
        if not frame_file.exists():
            return None

        image = QImage(str(frame_file))
        if image.isNull():
            return None
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return None

        scaled = pixmap.scaled(
            icon_size, icon_size,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.hover_cache[key] = scaled
        return scaled
