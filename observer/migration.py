"""Import a previous local Oye learning archive into an empty installation.

The previous SQLite database is backed up consistently, even when it uses WAL.
Never merges or overwrites an existing nonempty destination dataset.
"""
from __future__ import annotations
from pathlib import Path
import os
import re
import shutil
import sqlite3
from contextlib import closing
import uuid

from .learning import LearningStore


def import_learning_archive(destination: Path, previous_install: Path) -> dict[str, int]:
    """Import an old installation's learning/ directory; reject unsafe overwrites."""
    dest = Path(destination).resolve()
    src = Path(previous_install).resolve()
    if (src / 'learning' / 'observer.sqlite3').is_file():
        src = src / 'learning'
    if src == dest:
        raise ValueError('Hai selezionato la cartella di apprendimento già in uso')
    source_db = src / 'observer.sqlite3'
    if not source_db.is_file():
        raise ValueError('Nella cartella scelta non trovo learning/observer.sqlite3')
    old_store = LearningStore(src)
    old_stats = old_store.stats()
    if old_stats['frames'] < 1:
        raise ValueError('L’archivio precedente non contiene screenshot registrati')
    current = LearningStore(dest)
    if current.stats()['frames'] > 0:
        raise ValueError('L’installazione corrente ha già screenshot: importazione annullata per non cancellarli')
    # Verify source references are well-formed and copy only generated screenshots.
    with closing(sqlite3.connect(str(source_db))) as database:
        referenced = {name for (name,) in database.execute(
            'SELECT image_file FROM frames WHERE image_file IS NOT NULL')
            if isinstance(name, str) and re.fullmatch(r'[a-f0-9]{32}\.jpg', name)}
    target_images = dest / 'frames'
    target_images.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in sorted(referenced):
        original = src / 'frames' / name
        if original.is_file():
            shutil.copy2(original, target_images / name)
            copied += 1
    tmp = dest / f'.oye_import_{uuid.uuid4().hex}.sqlite3'
    try:
        # sqlite.backup copies committed WAL transactions safely; raw copy2 of
        # an active SQLite database could silently omit the last observations.
        uri = source_db.as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as source, closing(sqlite3.connect(str(tmp))) as target:
            source.backup(target)
        os.replace(tmp, dest / 'observer.sqlite3')
        # Import the prior schema by applying the current additive migrations.
        LearningStore(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return {'frames': old_stats['frames'], 'screenshots': copied,
            'actions': old_stats['actions']}
