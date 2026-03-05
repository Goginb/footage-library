"""
Robust preview builder for VFX footage library.

For every folder that contains footage:
- creates preview/<asset_name>/ with JPG frames
- does NOT modify original files
- can be run repeatedly; skips folders that already have 32 frames

Frame strategy
- VIDEO: up to 32 frames, evenly sampled via ffmpeg, 256px wide
- SEQUENCE: 32 frames, evenly sampled from the sequence, 256px
- IMAGE: single frame 000.jpg, 256px
"""
from __future__ import annotations

import multiprocessing as mp
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# ---------------------------------------------------------------------------
#  Detection
# ---------------------------------------------------------------------------

VIDEO_EXT = {".mov", ".mp4", ".mxf", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff"}

# Sequence: name_0001.exr, name_0002.exr — group by prefix
SEQUENCE_REGEX = re.compile(
    r"^(?P<prefix>.*?)(?P<frame>\d{4})\.(?P<ext>exr|dpx|tif|tiff|jpg|jpeg|png)$",
    re.IGNORECASE,
)

IGNORE_DIRS = {"preview", "_trash", "backup", "temp", ".git", "__pycache__", ".nuke"}


def _safe_asset_name(name: str) -> str:
    """Safe folder name: no path separators or reserved chars."""
    s = name.replace("\\", "_").replace("/", "_").strip() or "asset"
    return s[:200]


def _collect_assets_in_folder(folder: Path) -> List[Tuple[str, str, object]]:
    """
    Returns list of (asset_name, asset_type, payload).

    asset_type:
      - "video": payload is Path to video file
      - "image": payload is Path to image file
      - "sequence": payload is list[Path] of all frames (sorted)
    """
    folder = folder.resolve()
    if not folder.is_dir():
        return []

    # Group sequence frames by (prefix, ext)
    sequences: dict[Tuple[str, str], List[Tuple[int, Path]]] = {}
    single_video: List[Path] = []
    single_image: List[Path] = []

    for p in folder.iterdir():
        if p.is_dir() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext in VIDEO_EXT:
            single_video.append(p)
            continue
        if ext in IMAGE_EXT:
            m = SEQUENCE_REGEX.match(p.name)
            if m:
                prefix = m.group("prefix")
                frame_str = m.group("frame")
                try:
                    frame_num = int(frame_str)
                except ValueError:
                    frame_num = 0
                key = (prefix, ext)
                sequences.setdefault(key, []).append((frame_num, p))
            else:
                single_image.append(p)

    out: List[Tuple[str, str, object]] = []

    # Single video: asset_name = stem
    for p in single_video:
        out.append((_safe_asset_name(p.stem), "video", p))

    # Single image: asset_name = stem
    for p in single_image:
        out.append((_safe_asset_name(p.stem), "image", p))

    # Sequences: asset_name = prefix (strip trailing _)
    for (prefix, _ext), frames in sequences.items():
        if not frames:
            continue
        frames.sort(key=lambda x: x[0])
        frame_paths = [fp for _, fp in frames]
        name = _safe_asset_name(prefix.rstrip("_") or prefix or "sequence")
        base_name = name
        c = 0
        while any(a[0] == name for a in out):
            c += 1
            name = _safe_asset_name(f"{base_name}_{c}")
        out.append((name, "sequence", frame_paths))

    return out


def _walk_folders_with_footage(root: Path) -> List[Path]:
    """All directories that contain at least one asset (video, image, or sequence)."""
    root = root.resolve()
    folders: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        folder = Path(dirpath)
        if folder.name in IGNORE_DIRS:
            continue
        assets = _collect_assets_in_folder(folder)
        if assets:
            folders.append(folder)
    return folders


# ---------------------------------------------------------------------------
#  Preview generation (one job per worker)
# ---------------------------------------------------------------------------

def _has_32_frames(dest_dir: Path) -> bool:
    if not dest_dir.exists():
        return False
    jpgs = list(dest_dir.glob("*.jpg"))
    return len(jpgs) >= 32


def _generate_video_frames(source: Path, dest_dir: Path) -> bool:
    """Extract up to 32 evenly spaced frames using ffmpeg."""
    if not source.exists():
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(dest_dir / "%03d.jpg")
    vf = "select='not(mod(n,ceil(n/32)))',scale=256:-1"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vf",
        vf,
        "-vsync",
        "vfr",
        "-frames:v",
        "32",
        pattern,
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=300,
        )
    except (Exception, subprocess.TimeoutExpired):
        return False
    # We accept even if fewer than 32 frames were written.
    return any(dest_dir.glob("*.jpg"))


def _generate_image_frame(source: Path, dest_file: Path) -> bool:
    """Generate a single 256px JPG from source into dest_file."""
    if not source.exists():
        return False
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        try:
            from PySide6.QtCore import QSize
            from PySide6.QtGui import QImage, QImageReader
        except ImportError:
            from PySide2.QtCore import QSize
            from PySide2.QtGui import QImage, QImageReader
        try:
            reader = QImageReader(str(source))
            reader.setAutoTransform(True)
            try:
                reader.setScaledSize(QSize(256, 256))
            except Exception:
                pass
            image = reader.read()
            if image.isNull():
                return False
            return image.save(str(dest_file), "JPG")
        except Exception:
            return False
    try:
        img = Image.open(source)
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS
        img.thumbnail((256, 256), resample)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(dest_file, "JPEG", quality=85)
        return True
    except Exception:
        return False


def _generate_sequence_frames(frames: List[Path], dest_dir: Path) -> bool:
    """Generate 32 evenly spaced frames from a sequence."""
    n = len(frames)
    if n == 0:
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    success = False
    for i in range(32):
        idx = int(i * n / 32)
        if idx >= n:
            idx = n - 1
        src = frames[idx]
        dest = dest_dir / f"{i:03d}.jpg"
        # If dest already exists, skip to honor "skip existing" semantics.
        if dest.exists():
            success = True
            continue
        if _generate_image_frame(src, dest):
            success = True
    return success


def _generate_one(job: Tuple[str, Path, object]) -> Tuple[str, bool]:
    """
    Worker task: (asset_type, dest_dir, payload) -> (dest_dir_str, success).

    asset_type:
      - "video": payload is Path
      - "image": payload is Path
      - "sequence": payload is List[Path]
    """
    asset_type, dest_dir, payload = job
    if _has_32_frames(dest_dir):
        return (str(dest_dir), True)

    if asset_type == "video":
        ok = _generate_video_frames(payload, dest_dir)  # type: ignore[arg-type]
    elif asset_type == "sequence":
        ok = _generate_sequence_frames(payload, dest_dir)  # type: ignore[arg-type]
    else:  # image
        first = payload  # type: ignore[assignment]
        ok = _generate_image_frame(first, dest_dir / "000.jpg")
    return (str(dest_dir), ok)


def build_previews(root_folder: str | Path) -> None:
    """
    For every folder under root_folder that contains footage, create
    preview/<asset_name>/000.jpg..031.jpg (video/sequence) or 000.jpg (image).

    Skips generation when preview/<asset_name>/ already has at least 32 JPGs.
    Uses multiprocessing with cpu_count workers.
    """
    root = Path(root_folder).resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    # Collect all jobs: (asset_type, dest_dir, payload)
    jobs: List[Tuple[str, Path, object]] = []
    folders = _walk_folders_with_footage(root)
    for folder in folders:
        preview_base = folder / "preview"
        for asset_name, asset_type, payload in _collect_assets_in_folder(folder):
            dest_dir = preview_base / asset_name
            if _has_32_frames(dest_dir):
                continue
            jobs.append((asset_type, dest_dir, payload))

    if not jobs:
        print("No missing previews to generate.")
        return

    workers = max(1, (os.cpu_count() or 4))
    print(f"Generating previews for {len(jobs)} assets using {workers} workers...")
    with mp.Pool(workers) as pool:
        results = pool.map(_generate_one, jobs, chunksize=1)
    ok_count = sum(1 for _, ok in results if ok)
    fail_count = len(results) - ok_count
    print(f"Done. OK: {ok_count}, failed: {fail_count}.")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python build_previews.py <root_folder>")
        print("Example: python build_previews.py Z:/FootageLibrary")
        sys.exit(1)
    root = sys.argv[1]
    build_previews(root)


if __name__ == "__main__":
    main()

