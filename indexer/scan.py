from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .db import FootageDatabase, FootageRecord, open_default_db

try:
    from generate_previews import generate_preview, get_thumb_path
except ImportError:  # pragma: no cover - fallback for non-package execution
    generate_preview = None  # type: ignore[assignment]
    get_thumb_path = None  # type: ignore[assignment]


SUPPORTED_EXTENSIONS = {".mov", ".mp4", ".exr", ".dpx", ".jpg", ".png"}
# preview — наша служебная папка с кадрами превью, её нужно игнорировать,
# чтобы JPG-кадры не попадали в библиотеку как отдельные футажи.
IGNORE_FOLDERS = ["preview", "_trash", "backup", "temp", ".git", "__pycache__"]

SEQUENCE_REGEX = re.compile(r"^(?P<prefix>.*?)(?P<frame>\d{4})\.(?P<ext>exr|dpx)$", re.IGNORECASE)

AUTO_CATEGORY_RULES: Dict[str, str] = {
    "muzzle": "Muzzle",
    "smoke": "Smoke",
    "fire": "Fire",
    "blood": "Blood",
    "sparks": "Sparks",
    "dust": "Dust",
    "hit": "Hits",
    "hits": "Hits",
    "explosion": "Explosion",
    "explosions": "Explosion",
    "lightning": "Lightning",
    # Any asset whose name or folder path contains "textures"
    # will automatically get category "textures".
    "textures": "textures",
}


def _compute_asset_type_for_single(ext: str) -> str:
    if ext in (".mov", ".mp4"):
        return "video"
    if ext in (".jpg", ".png", ".exr", ".dpx"):
        return "image"
    return "unknown"


def scan_directory(root: Path) -> Iterable[FootageRecord]:
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        # Exclude ignored folders from recursion
        dirnames[:] = [d for d in dirnames if d not in IGNORE_FOLDERS]

        folder_path = Path(dirpath)
        folder_lower = str(folder_path).lower()

        # key: (prefix, ext) -> list of (frame_number, full_path, size)
        sequence_groups: Dict[Tuple[str, str], List[Tuple[int, Path, int]]] = {}
        single_records: List[FootageRecord] = []

        for filename in filenames:
            ext = Path(filename).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue

            full_path = folder_path / filename
            try:
                size = full_path.stat().st_size
            except OSError:
                continue

            if ext in (".exr", ".dpx"):
                match = SEQUENCE_REGEX.match(filename)
                if match:
                    prefix = match.group("prefix")
                    frame_str = match.group("frame")
                    try:
                        frame_num = int(frame_str)
                    except ValueError:
                        frame_num = 0
                    key = (prefix, ext)
                    sequence_groups.setdefault(key, []).append((frame_num, full_path, size))
                    continue

            # Non-sequence or non-matching files are treated as single assets
            asset_type = _compute_asset_type_for_single(ext)

            category = None
            name_lower = filename.lower()
            for key, value in AUTO_CATEGORY_RULES.items():
                if key in folder_lower or key in name_lower:
                    category = value
                    break

            record = FootageRecord(
                id=None,
                path=str(full_path),
                name=full_path.name,
                folder=str(folder_path),
                extension=ext,
                size=size,
                asset_type=asset_type,
                is_sequence=0,
                sequence_pattern=None,
                frame_start=None,
                frame_end=None,
                category=category,
            )

            # Build thumbnails at index time (thumb_cache/<hash>/thumb.jpg).
            if generate_preview is not None and get_thumb_path is not None:
                path_str = str(full_path)
                try:
                    if not get_thumb_path(path_str).exists():
                        generate_preview(path_str)
                except Exception:
                    pass

            single_records.append(record)

        # Yield single (non-sequence) assets
        for record in single_records:
            yield record

        # Yield grouped sequences
        for (prefix, ext), frames in sequence_groups.items():
            if not frames:
                continue

            frames.sort(key=lambda x: x[0])
            frame_numbers = [f[0] for f in frames]
            frame_start = min(frame_numbers)
            frame_end = max(frame_numbers)

            pattern = f"{prefix}%04d{ext}"
            sequence_path = folder_path / pattern

            # Auto category for sequences based on folder path
            category = None
            for key, value in AUTO_CATEGORY_RULES.items():
                if key in folder_lower:
                    category = value
                    break

            # Use first frame as representative size
            first_frame_num, first_path, first_size = frames[0]

            yield FootageRecord(
                id=None,
                path=str(sequence_path),
                name=pattern,
                folder=str(folder_path),
                extension=ext,
                size=first_size,
                asset_type="sequence",
                is_sequence=1,
                sequence_pattern=pattern,
                frame_start=frame_start,
                frame_end=frame_end,
                category=category,
            )


def run_indexer(folders: List[Path], db: FootageDatabase | None = None) -> None:
    if db is None:
        db = open_default_db()
        should_close = True
    else:
        should_close = False

    try:
        batch: List[FootageRecord] = []
        BATCH_SIZE = 1000
        total_count = 0
        existing_count = 0
        new_count = 0

        def flush_batch(records: List[FootageRecord]) -> None:
            nonlocal total_count, existing_count, new_count
            if not records:
                return
            paths = [r.path for r in records]
            existing_paths = db.get_existing_paths(paths)
            existing_batch = sum(1 for r in records if r.path in existing_paths)
            existing_count += existing_batch
            new_count += len(records) - existing_batch
            total_count += len(records)
            db.insert_or_replace_many(records)
            # Lightweight progress output so it's clear that indexing is running.
            print(
                f"[Indexer] scanned: {total_count} files "
                f"(new: {new_count}, updated: {existing_count})"
            )

        for folder in folders:
            for record in scan_directory(folder):
                batch.append(record)
                if len(batch) >= BATCH_SIZE:
                    flush_batch(batch)
                    batch.clear()

        if batch:
            flush_batch(batch)
            batch.clear()

        print(f"Всего найдено файлов: {total_count}")
        print(f"Добавлено новых: {new_count}")
        print(f"Уже существовало (обновлено): {existing_count}")
    finally:
        if should_close:
            db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rekursivnyy skaner fytazhey i indeksator v SQLite."
    )
    parser.add_argument(
        "folders",
        type=str,
        nargs="+",
        help="Kornevye papki dlya skanirovaniya (1 ili bolshe).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    folders: List[Path] = []
    for folder_str in args.folders:
        folder = Path(folder_str)
        if not folder.exists() or not folder.is_dir():
            raise SystemExit(
                f"Folder ne naydena ili ne yavlyaetsya direktoriyey: {folder}"
            )
        folders.append(folder)

    run_indexer(folders)


if __name__ == "__main__":
    main()

