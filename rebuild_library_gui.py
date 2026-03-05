"""
Simple GUI helper to rebuild the footage library:

- lets you choose one or more root folders
- runs indexer.scan for each selected folder
- then runs build_previews_from_db.py to generate previews from DB

Usage:
    python rebuild_library_gui.py
or:
    double-click rebuild_library_gui.py / corresponding .bat
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox


def _run_indexer(roots: list[str]) -> bool:
    """Run python -m indexer.scan for each root. Returns True on overall success."""
    total = len(roots)
    print(f"[Indexer] {total} folder(s) selected")
    for i, root_path in enumerate(roots, start=1):
        print(f"[Indexer] {i}/{total}: {root_path}")
        cmd = [sys.executable, "-m", "indexer.scan", root_path]
        try:
            proc = subprocess.run(cmd)
        except Exception:
            messagebox.showerror(
                "Indexer error",
                f"Failed to run indexer for:\n{root_path}\n\n"
                "Check that Python and the project environment are configured.",
            )
            return False
        if proc.returncode != 0:
            messagebox.showerror(
                "Indexer error",
                f"Indexer failed for:\n{root_path}\n\n"
                "See console output for details.",
            )
            return False
    print("[Indexer] Done")
    return True


def _run_previews_from_db(script_dir: Path) -> bool:
    """Run build_previews_from_db.py in the same directory as this script."""
    script = script_dir / "build_previews_from_db.py"
    if not script.exists():
        messagebox.showerror(
            "Preview builder",
            f"Script not found:\n{script}\n\n"
            "Make sure build_previews_from_db.py is in the project root.",
        )
        return False
    cmd = [sys.executable, str(script)]
    print("[Previews] Running build_previews_from_db.py ...")
    try:
        proc = subprocess.run(cmd)
    except Exception:
        messagebox.showerror(
            "Preview builder error",
            "Failed to run build_previews_from_db.py.\n"
            "Check that Python and the project environment are configured.",
        )
        return False
    if proc.returncode != 0:
        messagebox.showerror(
            "Preview builder error",
            "build_previews_from_db.py finished with errors.\n"
            "See console output for details.",
        )
        return False
    return True


def main() -> None:
    root = tk.Tk()
    root.withdraw()

    folders: list[str] = []
    while True:
        path = filedialog.askdirectory(
            title="Выберите папку с материалом (Отмена — закончить выбор)"
        )
        if not path:
            break
        if path not in folders:
            folders.append(path)

    if not folders:
        messagebox.showinfo("Rebuild library", "Папки не выбраны. Операция отменена.")
        return

    if not messagebox.askyesno(
        "Rebuild library",
        "Будет выполнена переиндексация и сбор превью для выбранных папок.\n"
        "Продолжить?",
    ):
        return

    if not _run_indexer(folders):
        return

    script_dir = Path(__file__).resolve().parent
    if not _run_previews_from_db(script_dir):
        return

    messagebox.showinfo(
        "Rebuild library",
        "Готово.\n\n"
        "База данных обновлена, превью созданы для ассетов из базы.",
    )


if __name__ == "__main__":
    main()

