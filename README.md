# Footage Library Standalone

Standalone Python application for indexing and viewing footage files.

## Requirements

- Python 3.10+
- SQLite (built into Python)
- PySide6

Install dependencies:

```bash
pip install -r requirements.txt
```

## Indexer

Run indexer on a folder (recursive scan):

```bash
python -m indexer.scan "D:\\path\\to\\footage"
```

Supported extensions:

- `.mov`
- `.mp4`
- `.exr`
- `.jpg`
- `.png`

Data is stored in `database/footage.db` with fields:

- `id`
- `path` (unique)
- `name`
- `folder`
- `extension`
- `size`

Indexes are created for `name` and `folder`.

## Viewer

Run viewer:

```bash
python -m viewer.app
```

Viewer features:

- Simple PySide6 window with `QListWidget`
- Loads first 1000 records from the database
- Double-click on an item prints the full path to stdout and shows it in a message box

The application runs completely standalone and does not require Nuke.

