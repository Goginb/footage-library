"""
Preview builder that works from the footage database instead of scanning folders.

Goal:
- Generate previews (up to 32 frames) only for assets registered in the DB table `footage`.
- Layout: asset_folder/preview/<asset_name>/000.jpg..031.jpg (video/sequence) or 000.jpg (image).
- Skip assets whose preview folder already contains at least 32 JPG files.

Usage:
    python build_previews_from_db.py
"""
from __future__ import annotations

import multiprocessing as mp
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

try:
    # Use the same DB helper as the rest of the project.
    from indexer.db import get_default_db_path
except ImportError:
    get_default_db_path = None  # type: ignore[assignment]


VIDEO_EXT = {".mov", ".mp4", ".mxf", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff"}

# Sequence: name_0001.exr, name_0002.exr — group by prefix within a folder
SEQUENCE_REGEX = re.compile(
    r"^(?P<prefix>.*?)(?P<frame>\d{4})\.(?P<ext>exr|dpx|tif|tiff|jpg|jpeg|png)$",
    re.IGNORECASE,
)


def _safe_asset_name(name: str) -> str:
    """Safe folder name for preview/<asset_name>/."""
    s = name.replace("\\", "_").replace("/", "_").strip() or "asset"
    return s[:200]


def _read_paths_from_db() -> List[Path]:
    """Read all asset paths from footage DB."""
    if get_default_db_path is None:
        raise RuntimeError("indexer.db.get_default_db_path not available")
    db_path = get_default_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT path FROM footage")
        rows = cur.fetchall()
    finally:
        conn.close()
    paths: List[Path] = []
    for (p,) in rows:
        try:
            paths.append(Path(p))
        except Exception:
            continue
    return paths


def _group_assets_from_db(paths: Iterable[Path]) -> List[Tuple[str, str, Path | List[Path]]]:
    """
    Group DB paths into assets.

    Returns list of (asset_name, asset_type, payload):
      - video: (name, "video", Path)
      - image: (name, "image", Path)
      - sequence: (name, "sequence", List[Path])  # sorted by frame number
    Grouping is done per-folder + sequence prefix for numbered frames.
    """
    # Mapping (folder, prefix, ext) -> list of (frame_num, Path)
    seq_groups: dict[Tuple[Path, str, str], List[Tuple[int, Path]]] = {}
    videos: List[Tuple[str, Path]] = []
    images: List[Tuple[str, Path]] = []

    for p in paths:
        folder = p.parent
        name = p.name
        ext = p.suffix.lower()
        if ext in VIDEO_EXT:
            videos.append((_safe_asset_name(p.stem), p))
            continue

        if ext in IMAGE_EXT:
            m = SEQUENCE_REGEX.match(name)
            if m:
                prefix = m.group("prefix")
                frame_str = m.group("frame")
                try:
                    frame_num = int(frame_str)
                except ValueError:
                    frame_num = 0
                key = (folder, prefix, ext)
                seq_groups.setdefault(key, []).append((frame_num, p))
            else:
                images.append((_safe_asset_name(p.stem), p))

    assets: List[Tuple[str, str, Path | List[Path]]] = []

    # Videos: one asset per file
    for name, p in videos:
        assets.append((name, "video", p))

    # Single images: one asset per file
    for name, p in images:
        assets.append((name, "image", p))

    # Sequences: group numbered frames
    for (folder, prefix, _ext), frames in seq_groups.items():
        if not frames:
            continue
        frames.sort(key=lambda x: x[0])
        frame_paths = [fp for _, fp in frames]
        base_name = _safe_asset_name(prefix.rstrip("_") or prefix or "sequence")
        name = base_name
        # Avoid duplicates: if some other asset already uses the same name in this folder,
        # append a numeric suffix.
        existing_names = {a[0] for a in assets if isinstance(a[2], Path) and a[2].parent == folder}
        c = 0
        while name in existing_names:
            c += 1
            name = _safe_asset_name(f"{base_name}_{c}")
        assets.append((name, "sequence", frame_paths))

    return assets


# ---------------------------------------------------------------------------
#  Preview generation helpers (shared strategy)
# ---------------------------------------------------------------------------

def _has_32_frames(dest_dir: Path) -> bool:
    """Check if directory already contains at least 32 JPGs."""
    if not dest_dir.exists():
        return False
    jpgs = list(dest_dir.glob("*.jpg"))
    return len(jpgs) >= 32


def _generate_video_frames(source: Path, dest_dir: Path) -> bool:
    """Extract up to 32 evenly spaced frames using ffmpeg into dest_dir/%03d.jpg."""
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
    """Generate 32 evenly spaced JPG frames from the sequence into dest_dir."""
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
        # Always (re)generate frame: we want evenly spaced frames to overwrite any old layout.
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

    if asset_type == "video":
        ok = _generate_video_frames(payload, dest_dir)  # type: ignore[arg-type]
    elif asset_type == "sequence":
        ok = _generate_sequence_frames(payload, dest_dir)  # type: ignore[arg-type]
    else:  # image
        first = payload  # type: ignore[assignment]
        ok = _generate_image_frame(first, dest_dir / "000.jpg")
    return (str(dest_dir), ok)


def build_previews_from_db() -> None:
    """
    Main entry:
    - Reads all paths from the `footage` table in the DB.
    - Groups them into video / image / sequence assets.
    - For each asset, creates preview/<asset_name>/000.jpg..031.jpg (video/sequence)
      or 000.jpg (image) next to the asset folder.
    - Skips assets whose preview folder already contains at least 32 JPGs.
    """
    paths = _read_paths_from_db()
    if not paths:
        print("No footage paths found in database.")
        return

    assets = _group_assets_from_db(paths)
    if not assets:
        print("No assets detected from database paths.")
        return

    # Ask once how to treat existing previews
    overwrite_input = input(
        "Overwrite existing previews in preview/<asset_name>/ ? [y/N]: "
    ).strip().lower()
    overwrite_existing = overwrite_input == "y"

    # Collect jobs: (asset_type, dest_dir, payload)
    jobs: List[Tuple[str, Path, object]] = []
    for asset_name, asset_type, payload in assets:
        if asset_type == "sequence":
            # payload is list[Path]; use first frame to determine folder
            seq_frames: List[Path] = payload  # type: ignore[assignment]
            if not seq_frames:
                continue
            folder = seq_frames[0].parent
        else:
            p: Path = payload  # type: ignore[assignment]
            folder = p.parent
        dest_dir = folder / "preview" / asset_name

        # Если пользователь выбрал НЕ перезаписывать существующие,
        # то ассеты, у которых уже есть >=32 jpg, пропускаем.
        if (not overwrite_existing) and _has_32_frames(dest_dir):
            continue

        jobs.append((asset_type, dest_dir, payload))

    total_jobs = len(jobs)
    if total_jobs == 0:
        print("No missing previews to generate.")
        return

    workers = max(1, (os.cpu_count() or 4))
    mode = "overwrite" if overwrite_existing else "missing-only"
    print(
        f"[Previews] {total_jobs} asset(s) to process, using {workers} workers "
        f"(mode: {mode})..."
    )
    ok_count = 0
    fail_count = 0
    bar_width = 30

    with mp.Pool(workers) as pool:
        for idx, (dest_dir_str, ok) in enumerate(
            pool.imap_unordered(_generate_one, jobs), start=1
        ):
            if ok:
                ok_count += 1
            else:
                fail_count += 1
            # progress bar in console
            progress = idx / total_jobs
            filled = int(bar_width * progress)
            bar = "#" * filled + "-" * (bar_width - filled)
            print(
                f"\r[Previews] [{bar}] {idx}/{total_jobs}  OK:{ok_count} FAIL:{fail_count}",
                end="",
                flush=True,
            )

    print()  # newline after progress bar
    print(f"[Previews] Done. OK: {ok_count}, failed: {fail_count}.")


def main() -> None:
    build_previews_from_db()


if __name__ == "__main__":
    main()

