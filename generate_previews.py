"""
Generate thumbnails for assets. Run once before using the Viewer.
Viewer never generates; it only reads thumb_cache/<hash>/thumb.jpg
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

VIDEO_EXT = {".mov", ".mp4", ".mxf", ".avi"}


def get_thumb_cache_root() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA")
    if not local_appdata:
        local_appdata = str(Path.home())
    root = Path(local_appdata) / "FootageLibrary" / "thumb_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def asset_hash(asset_path: str) -> str:
    return hashlib.md5(asset_path.encode("utf-8")).hexdigest()


def get_thumb_path(asset_path: str) -> Path:
    """Path where thumb.jpg is stored for this asset."""
    h = asset_hash(asset_path)
    return get_thumb_cache_root() / h / "thumb.jpg"


def generate_preview(asset_path: str) -> bool:
    """
    Generate thumb_cache/<hash>/thumb.jpg for the asset.
    Returns True if thumbnail exists after (created or already present).
    """
    path = Path(asset_path)
    if not path.exists():
        return False

    h = asset_hash(asset_path)
    cache_dir = get_thumb_cache_root() / h
    thumb = cache_dir / "thumb.jpg"
    if thumb.exists():
        return True

    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()

    if ext in VIDEO_EXT:
        cmd = [
            "ffmpeg", "-y", "-i", asset_path,
            "-vf", "scale=256:-1",
            "-frames:v", "1",
            str(thumb),
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            return False
        return thumb.exists()

    # Image: load, resize to 256, save
    try:
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage, QImageReader
    except ImportError:
        from PySide2.QtCore import QSize
        from PySide2.QtGui import QImage, QImageReader

    try:
        reader = QImageReader(asset_path)
        reader.setAutoTransform(True)
        try:
            reader.setScaledSize(QSize(256, 256))
        except Exception:
            pass
        image = reader.read()
        if image.isNull():
            return False
        return image.save(str(thumb), "JPG")
    except Exception:
        return False


def build_previews(asset_list: list[str]) -> None:
    """Generate thumbnails for all assets. Run once before using viewer."""
    for asset_path in asset_list:
        generate_preview(asset_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "--from-db":
        # Build all previews from footage database. Run once before using viewer.
        try:
            from indexer.db import get_default_db_path
            import sqlite3
        except ImportError:
            print("Need indexer.db to use --from-db")
            sys.exit(1)
        db_path = get_default_db_path()
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT path FROM footage")
        paths = [row[0] for row in cur.fetchall()]
        conn.close()
        print("Building previews for", len(paths), "assets...")
        build_previews(paths)
        print("Done.")
        sys.exit(0)
    if len(sys.argv) < 2:
        print("Usage: python generate_previews.py <path1> [path2 ...]")
        print("       python generate_previews.py --from-db   # build all from database")
        sys.exit(1)
    for p in sys.argv[1:]:
        if generate_preview(p):
            print("OK:", p)
        else:
            print("SKIP/FAIL:", p)
