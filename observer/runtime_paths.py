"""Portable vs installed Oye writable paths.

Frozen builds keep mutable configuration and gameplay observations outside the
program directory.  That lets an Inno Setup uninstaller remove the binary
without corrupting settings or silently deleting user data.
"""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import sys


def data_directory(*, frozen: bool | None = None, base: Path | None = None) -> Path:
    if frozen is None:
        frozen = bool(getattr(sys, 'frozen', False))
    if not frozen:
        return Path(__file__).resolve().parents[1]
    if base is None:
        base = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local'))
    return Path(base) / 'Oye'


def initialize_data(data_root: Path, bundle_root: Path) -> Path:
    """Create user files once. Never overwrite a calibration or configuration."""
    data_root, bundle_root = Path(data_root), Path(bundle_root)
    data_root.mkdir(parents=True, exist_ok=True)
    target_config = data_root / 'config.json'
    source_config = bundle_root / 'config.json'
    if not source_config.is_file():
        source_config = bundle_root / 'defaults' / 'config.json'
    if not target_config.exists():
        if not source_config.is_file():
            raise FileNotFoundError('Configurazione iniziale mancante nel pacchetto Oye')
        shutil.copy2(source_config, target_config)
    template_root = data_root / 'templates'
    template_root.mkdir(exist_ok=True)
    for src in (bundle_root / 'templates').glob('*.png'):
        dst = template_root / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
    return data_root
