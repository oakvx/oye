"""Safe, in-place Oye source updates: never replace locally learned data.

This is deliberately independent of CV2/Tesseract so the preflight works even
before optional desktop dependencies are installed. SQLite is archived via its
backup API to include committed WAL transactions.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
import tempfile
import zipfile

UPDATE_ID = 'v38'
DATA_DIRS = ('templates', 'learning', 'state', 'logs', 'reports', 'training', 'screenshots', 'captures', 'recordings')
DB_NAME = 'learning/observer.sqlite3'
MARKER_NAME = '.oye-prepared-v38.json'


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _candidate_files(root: Path):
    """Only data files, never executables, venvs, scripts or symlinks."""
    root = Path(root)
    if (root / 'config.json').is_file() and not (root / 'config.json').is_symlink():
        yield root / 'config.json'
    for name in DATA_DIRS:
        folder = root / name
        if folder.is_symlink() or not folder.is_dir():
            continue
        for path in sorted(folder.rglob('*')):
            if not path.is_file() or path.is_symlink() or not _inside(path, root):
                continue
            if (path.name.endswith(('-wal', '-shm')) and path.name.startswith('observer.sqlite3')):
                continue
            yield path


def _backup_db(source: Path, filename: Path) -> None:
    """Consistent SQLite snapshot, without changing or vacuuming the source."""
    # Source opened read-only; sqlite backup includes committed WAL transactions.
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=8)) as src:
        with closing(sqlite3.connect(str(filename), timeout=8)) as dst:
            src.backup(dst, pages=128, sleep=.025)
            integrity = dst.execute('PRAGMA quick_check').fetchone()
            if not integrity or integrity[0] != 'ok':
                raise RuntimeError('Il database degli screenshot non supera il controllo di integrità')


def make_backup(root: Path, *, backup_dir: Path | None = None) -> Path | None:
    """Archive current training/config/frames; return None if no user data exists.

    Paths remain relative to the project so the zip can be extracted if needed.
    Does not follow links and never changes the original files.
    """
    root = Path(root).resolve()
    existing = list(_candidate_files(root))
    if not existing:
        return None
    folder = Path(backup_dir) if backup_dir else root / '_OYE_BACKUP'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    target = folder / f'Oye_dati_prima_{UPDATE_ID}_{stamp}.zip'
    temp = target.with_name(target.name + '.tmp')
    try:
        with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for src in existing:
                rel = src.relative_to(root)
                if rel.as_posix() == DB_NAME:
                    with tempfile.TemporaryDirectory() as td:
                        snapshot = Path(td) / 'observer.sqlite3'
                        _backup_db(src, snapshot)
                        archive.write(snapshot, rel.as_posix())
                else:
                    archive.write(src, rel.as_posix())
        with zipfile.ZipFile(temp) as check:
            if check.testzip() is not None:
                raise RuntimeError('Il backup non supera il controllo CRC')
        os.replace(temp, target)
        return target
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def copy_from_existing_install(root: Path, previous: Path) -> list[str]:
    """Use prior installed data ONLY for missing files; do not merge two datasets."""
    root, previous = Path(root).resolve(), Path(previous).resolve()
    if root == previous or not previous.is_dir():
        return []
    imported = []
    source = previous / 'config.json'
    if source.is_file() and not source.is_symlink() and not (root / 'config.json').exists():
        shutil.copy2(source, root / 'config.json')
        imported.append('config.json')
    for name in DATA_DIRS:
        src, dest = previous / name, root / name
        if src.is_symlink() or not src.is_dir():
            continue
        # Existing dataset or folder always wins: avoid mixing incompatible
        # screenshot IDs, WAL files or calibration/meta files.
        if not dest.exists() or (dest.is_dir() and not any(dest.iterdir())):
            shutil.copytree(src, dest, symlinks=False, dirs_exist_ok=True)
            imported.append(name + '/')
    return imported


def training_summary(root: Path) -> dict:
    """Read-only summary: no schema changes and no automatically invented data."""
    db = Path(root) / DB_NAME
    result = {'database': db.is_file(), 'frames': 0, 'labeled_frames': 0, 'actions': 0,
              'screenshots_on_disk': 0, 'trainable_actions': 0, 'status': 'not_found'}
    if not db.is_file():
        return result
    try:
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=3)) as conn:
            result['frames'] = int(conn.execute('SELECT count(*) FROM frames').fetchone()[0])
            result['labeled_frames'] = int(conn.execute('SELECT count(*) FROM frames WHERE user_label IS NOT NULL').fetchone()[0])
            try:
                result['actions'] = int(conn.execute('SELECT count(*) FROM agent_decisions').fetchone()[0])
            except sqlite3.OperationalError:
                pass
            try:
                cols = {row[1] for row in conn.execute('PRAGMA table_info(frames)')}
                # Schema-aware read-only query: older training databases can
                # legitimately lack the v44 review columns until first use.
                exclude = 'AND COALESCE(training_excluded,0)=0' if 'training_excluded' in cols else ''
                verified = 'OR demo_verified=1' if 'demo_verified' in cols else ''
                match_labels = "'partita in corso','in partita','gameplay','movimento','combattimento','scontro','sparando','atterraggio','raccolta risorse','bus della battaglia','discesa'"
                veto = "'lobby','nella lobby','menu lobby','caricamento','matchmaking','spettatore','modalità spettatore','eliminazione','risultati partita','risultati','schermata risultati','non visibile'"
                result['trainable_actions'] = int(conn.execute(f"""SELECT count(*) FROM frames
                    WHERE action_label IS NOT NULL AND action_source IN ('manual','demo')
                    {exclude}
                    AND (user_label IS NULL OR lower(trim(user_label)) NOT IN ({veto}))
                    AND (hud_scene='match' OR lower(trim(user_label)) IN ({match_labels}) {verified})
                    AND (hud_scene NOT IN ('lobby','loading','spectator','results','unfocused')
                         OR lower(trim(user_label)) IN ({match_labels}))""").fetchone()[0])
            except sqlite3.OperationalError:
                pass
        frames = Path(root) / 'learning' / 'frames'
        if frames.is_dir():
            result['screenshots_on_disk'] = sum(1 for p in frames.glob('*.jpg') if p.is_file())
        result['status'] = 'ok'
    except sqlite3.Error as exc:
        result['status'] = 'db_error:' + type(exc).__name__
    return result


def prepare_update(root: Path, *, previous: Path | None = None,
                   defaults: Path | None = None,
                   backup_dir: Path | None = None) -> dict:
    """Run once before starting Oye. Crash-safe and safe to rerun.

    An update archive contains NO config.json, template images, learning data,
    checkpoints or log files. Therefore even if the user extracts it with
    'replace all', it cannot overwrite these files.
    """
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER_NAME
    if marker.exists():
        summary = training_summary(root)
        return {'prepared': True, 'already_prepared': True, 'training': summary}

    # Backup local content BEFORE importing / generating defaults.
    archive = make_backup(root, backup_dir=backup_dir)
    imported = copy_from_existing_install(root, previous) if previous else []
    if not (root / 'config.json').exists():
        default_config = (Path(defaults) / 'config.json') if defaults else root / 'defaults' / 'config.json'
        if not default_config.is_file():
            raise FileNotFoundError('File defaults/config.json mancante nel pacchetto Oye')
        shutil.copy2(default_config, root / 'config.json')
    (root / 'templates').mkdir(exist_ok=True)
    summary = training_summary(root)
    report = {'prepared': True, 'already_prepared': False,
              'backup': str(archive) if archive else None,
              'imported': imported, 'training': summary,
              'completed_at': datetime.now().isoformat(timespec='seconds')}
    # Only a successful run writes the marker, for safe retry after interruption.
    temporary = marker.with_suffix(marker.suffix + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, marker)
    return report
