from __future__ import annotations

from collections import OrderedDict, deque
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
try:
    from PySide6.QtGui import QPixmap
except ImportError:
    from PySide2.QtGui import QPixmap

VIDEO_EXT = {".mov", ".mp4", ".mxf", ".avi"}


def get_local_cache_root() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA")
    if not local_appdata:
        local_appdata = str(Path.home())
    root = Path(local_appdata) / "FootageLibrary" / "thumb_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


SERVER_CACHE_ROOT = Path(r"\\server\vfx_library\thumb_cache")
LOCAL_CACHE_ROOT = get_local_cache_root()
INDEX_FILE = os.path.join(get_local_cache_root(), "index.json")
preview_index: dict[str, dict] = {}


def load_preview_index() -> None:
    global preview_index
    try:
        if os.path.exists(INDEX_FILE):
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            preview_index = data if isinstance(data, dict) else {}
        else:
            preview_index = {}
    except Exception:
        preview_index = {}


def save_preview_index() -> None:
    try:
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(preview_index, f, ensure_ascii=True)
    except Exception:
        pass


def _asset_hash(asset_path: str) -> str:
    return hashlib.md5(asset_path.encode("utf-8")).hexdigest()


def _update_index(asset_path: str, frame_count: int) -> None:
    h = _asset_hash(asset_path)
    preview_index[h] = {
        "frames": int(frame_count),
        "updated": time.time(),
    }
    save_preview_index()


def _has_jpg(cache_dir: Path) -> bool:
    if not cache_dir.exists() or not cache_dir.is_dir():
        return False
    return any(cache_dir.glob("*.jpg"))


def preview_exists(asset_path: str) -> bool:
    h = _asset_hash(asset_path)
    if h in preview_index:
        return True

    server_dir = SERVER_CACHE_ROOT / h
    local_dir = LOCAL_CACHE_ROOT / h

    if _has_jpg(server_dir):
        frame_count = len(list(server_dir.glob("*.jpg")))
        _update_index(asset_path, frame_count)
        return True
    if _has_jpg(local_dir):
        frame_count = len(list(local_dir.glob("*.jpg")))
        _update_index(asset_path, frame_count)
        return True
    return False


def get_preview_cache_dir(asset_path: str) -> Path:
    h = _asset_hash(asset_path)
    server_dir = SERVER_CACHE_ROOT / h
    local_dir = LOCAL_CACHE_ROOT / h

    if _has_jpg(server_dir):
        return server_dir
    if _has_jpg(local_dir):
        return local_dir
    return local_dir


def generate_video_preview(video_path: str) -> str | None:
    cache_dir = get_preview_cache_dir(video_path)

    frame_files = sorted(cache_dir.glob("*.jpg")) if cache_dir.exists() else []
    if frame_files:
        _update_index(video_path, len(frame_files))
        first = cache_dir / "000.jpg"
        return str(first if first.exists() else frame_files[0])

    # No server/local jpg found: generate preview only in local cache.
    h = _asset_hash(video_path)
    local_cache_dir = LOCAL_CACHE_ROOT / h
    local_cache_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = str(local_cache_dir / "%03d.jpg")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        "fps=1",
        "-start_number",
        "0",
        "-frames:v",
        "3",
        output_pattern,
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return None

    first = local_cache_dir / "000.jpg"
    if first.exists():
        _update_index(video_path, len(list(local_cache_dir.glob("*.jpg"))))
        return str(first)

    frame_files = sorted(local_cache_dir.glob("*.jpg"))
    if frame_files:
        _update_index(video_path, len(frame_files))
        return str(frame_files[0])
    return None


load_preview_index()


class PreviewManager:
    def __init__(self, thread_pool, start_task, is_cached, max_retry: int = 2) -> None:
        self.thread_pool = thread_pool
        self.start_task = start_task
        self.is_cached = is_cached
        self.max_retry = max_retry
        self.queue = deque()
        self.loading: set[str] = set()
        self.retry_count: dict[str, int] = {}
        self.pending_assets: dict[str, dict] = {}
        self.thumbnail_cache = OrderedDict()
        self.frames_cache = OrderedDict()
        self.THUMB_CACHE_LIMIT = 200
        self.FRAMES_CACHE_LIMIT = 80

    def clear(self) -> None:
        self.queue.clear()
        self.loading.clear()
        self.retry_count.clear()
        self.pending_assets.clear()
        self.thumbnail_cache.clear()
        self.frames_cache.clear()

    def enqueue(self, asset) -> None:
        path = asset.get("path") if isinstance(asset, dict) else None
        if not path:
            return
        if self.is_cached(path):
            return
        if path in self.loading:
            return
        if self.retry_count.get(path, 0) > self.max_retry:
            return

        self.queue.append(asset)
        self.pending_assets[path] = asset
        self.loading.add(path)
        self.process_queue()

    def process_queue(self) -> None:
        while self.queue and self.thread_pool.activeThreadCount() < self.thread_pool.maxThreadCount():
            asset = self.queue.popleft()
            self.start_task(asset)

    def mark_loaded(self, path: str, success: bool) -> None:
        if not path:
            return
        self.loading.discard(path)

        if success:
            self.retry_count.pop(path, None)
            self.pending_assets.pop(path, None)
        else:
            attempt = self.retry_count.get(path, 0) + 1
            self.retry_count[path] = attempt
            if attempt <= self.max_retry and path in self.pending_assets:
                self.queue.append(self.pending_assets[path])
                self.loading.add(path)
            else:
                self.pending_assets.pop(path, None)
        self.process_queue()

    def set_thumbnail(self, path: str, pixmap: QPixmap) -> None:
        if not path or pixmap.isNull():
            return
        if path in self.thumbnail_cache:
            self.thumbnail_cache.pop(path)
        self.thumbnail_cache[path] = pixmap
        if len(self.thumbnail_cache) > self.THUMB_CACHE_LIMIT:
            self.thumbnail_cache.popitem(last=False)

    def get_thumbnail(self, path: str) -> QPixmap | None:
        return self.thumbnail_cache.get(path)

    def get_frames(self, path: str) -> list[QPixmap]:
        if not path:
            return []
        cached = self.frames_cache.get(path)
        if cached is not None:
            self.frames_cache.pop(path)
            self.frames_cache[path] = cached
            return cached

        cache_dir = get_preview_cache_dir(path)
        frame_files = sorted(cache_dir.glob("*.jpg"))[:12]
        frames: list[QPixmap] = []
        for frame_file in frame_files:
            pm = QPixmap(str(frame_file))
            if not pm.isNull():
                frames.append(pm)

        if path in self.frames_cache:
            self.frames_cache.pop(path)
        self.frames_cache[path] = frames
        if len(self.frames_cache) > self.FRAMES_CACHE_LIMIT:
            self.frames_cache.popitem(last=False)
        if frames and path not in self.thumbnail_cache:
            self.thumbnail_cache[path] = frames[0]
            if len(self.thumbnail_cache) > self.THUMB_CACHE_LIMIT:
                self.thumbnail_cache.popitem(last=False)
        return frames
