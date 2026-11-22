"""Consistent SQLite backups: python -m jobtracker.backup

Copies data/app.db and every data/<user>/jobs.db with SQLite's backup API, which is safe while the
app is running, into data/backups/<timestamp>/. Keeps the newest 14 backups.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

KEEP = 14


def backup_databases(data_dir: str | Path, keep: int = KEEP, stamp: str | None = None) -> Path | None:
    data_dir = Path(data_dir)
    databases = [p for p in sorted(data_dir.rglob("*.db")) if "backups" not in p.relative_to(data_dir).parts]
    if not databases:
        return None
    target = data_dir / "backups" / (stamp or datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    for source in databases:
        destination = target / source.relative_to(data_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
        dst.close()
        src.close()
    old = sorted(p for p in (data_dir / "backups").iterdir() if p.is_dir())
    for folder in old[:-keep] if keep > 0 else []:
        shutil.rmtree(folder, ignore_errors=True)
    return target


def main(argv=None) -> int:
    from .config import project_home

    args = argv if argv is not None else sys.argv[1:]
    data_dir = Path(args[0]) if args else project_home() / "data"
    target = backup_databases(data_dir)
    print(f"Backup written to {target}" if target else f"No databases found in {data_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
