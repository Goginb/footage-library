from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple


DB_FILE_NAME = "footage.db"


@dataclass
class FootageRecord:
    id: Optional[int]
    path: str
    name: str
    folder: str
    extension: str
    size: int
    asset_type: Optional[str]
    is_sequence: int = 0
    sequence_pattern: Optional[str] = None
    frame_start: Optional[int] = None
    frame_end: Optional[int] = None
    category: Optional[str] = None


class FootageDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS footage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                folder TEXT NOT NULL,
                extension TEXT NOT NULL,
                size INTEGER NOT NULL,
                asset_type TEXT,
                is_sequence INTEGER NOT NULL DEFAULT 0,
                sequence_pattern TEXT,
                frame_start INTEGER,
                frame_end INTEGER,
                category TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_footage_name ON footage(name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_footage_folder ON footage(folder)")
        self._conn.commit()

        # Ensure all expected columns exist on already created tables
        cur.execute("PRAGMA table_info(footage)")
        columns = {row["name"] for row in cur.fetchall()}
        migrations: List[Tuple[str, str]] = []
        if "asset_type" not in columns:
            migrations.append(("asset_type", "TEXT"))
        if "is_sequence" not in columns:
            migrations.append(("is_sequence", "INTEGER NOT NULL DEFAULT 0"))
        if "sequence_pattern" not in columns:
            migrations.append(("sequence_pattern", "TEXT"))
        if "frame_start" not in columns:
            migrations.append(("frame_start", "INTEGER"))
        if "frame_end" not in columns:
            migrations.append(("frame_end", "INTEGER"))
        if "category" not in columns:
            migrations.append(("category", "TEXT"))

        for name, type_sql in migrations:
            cur.execute(f"ALTER TABLE footage ADD COLUMN {name} {type_sql}")
        if migrations:
            self._conn.commit()

    def insert_or_replace_many(self, records: Iterable[FootageRecord]) -> None:
        rows = [
            (
                r.path,
                r.name,
                r.folder,
                r.extension,
                r.size,
                r.asset_type,
                r.is_sequence,
                r.sequence_pattern,
                r.frame_start,
                r.frame_end,
                r.category,
            )
            for r in records
        ]
        if not rows:
            return

        cur = self._conn.cursor()
        cur.executemany(
            """
            INSERT INTO footage (
                path,
                name,
                folder,
                extension,
                size,
                asset_type,
                is_sequence,
                sequence_pattern,
                frame_start,
                frame_end,
                category
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                name=excluded.name,
                folder=excluded.folder,
                extension=excluded.extension,
                size=excluded.size,
                asset_type=excluded.asset_type,
                is_sequence=excluded.is_sequence,
                sequence_pattern=excluded.sequence_pattern,
                frame_start=excluded.frame_start,
                frame_end=excluded.frame_end,
                category=excluded.category
            """,
            rows,
        )
        self._conn.commit()

    def get_existing_paths(self, paths: Iterable[str]) -> Set[str]:
        """
        Return set of paths that already exist in the database.
        """
        path_list = list(paths)
        if not path_list:
            return set()

        placeholders = ",".join("?" for _ in path_list)
        query = f"SELECT path FROM footage WHERE path IN ({placeholders})"

        cur = self._conn.cursor()
        cur.execute(query, path_list)
        rows = cur.fetchall()
        return {row["path"] for row in rows}

    def fetch_first_n(self, limit: int = 1000) -> List[FootageRecord]:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT
                id,
                path,
                name,
                folder,
                extension,
                size,
                asset_type,
                is_sequence,
                sequence_pattern,
                frame_start,
                frame_end,
                category
            FROM footage
            ORDER BY id ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [
            FootageRecord(
                id=row["id"],
                path=row["path"],
                name=row["name"],
                folder=row["folder"],
                extension=row["extension"],
                size=row["size"],
                asset_type=row["asset_type"],
                is_sequence=row["is_sequence"],
                sequence_pattern=row["sequence_pattern"],
                frame_start=row["frame_start"],
                frame_end=row["frame_end"],
                category=row["category"],
            )
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()


def get_default_db_path() -> Path:
    """
    Returns the default database path: <project_root>/database/footage.db
    """
    current_dir = Path(__file__).resolve().parent.parent
    db_dir = current_dir / "database"
    return db_dir / DB_FILE_NAME


def open_default_db() -> FootageDatabase:
    return FootageDatabase(get_default_db_path())

