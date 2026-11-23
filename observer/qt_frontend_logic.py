"""Frontend-independent helpers for Oye's Qt interface.

Never interpret a UI mode as consent: the window must separately confirm LIVE.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from .config import TEMPLATE_NAMES

MODES = ('observe', 'train', 'single_match', 'guided', 'autonomous')


def mode_flags(mode: str) -> dict:
    if mode not in MODES:
        raise ValueError('Modalità sconosciuta')
    return {
        'autonomous_enabled': mode in ('guided', 'autonomous', 'single_match'),
        'guided_explore_enabled': mode in ('guided', 'single_match'),
        'demonstration_enabled': mode == 'train',
        'live': mode in ('guided', 'autonomous', 'single_match'),
        'exploration': mode == 'train',
    }


def save_calibration(frame: np.ndarray, rectangle: tuple[int, int, int, int],
                     name: str, folder: Path) -> Path:
    """Match legacy Studio format exactly, with validated and atomic metadata writes."""
    if name not in TEMPLATE_NAMES:
        raise ValueError('Riferimento non riconosciuto')
    if frame is None or frame.ndim != 3 or frame.shape[2] not in (3, 4):
        raise ValueError('Screenshot non valido')
    if rectangle is None:
        raise ValueError('Seleziona prima il riferimento trascinando sull’immagine')
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = map(int, rectangle)
    if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
        raise ValueError('Selezione fuori dall’immagine')
    crop = frame[y1:y2, x1:x2]
    if x2-x1 < 24 or y2-y1 < 18 or crop.size == 0 or float(np.std(crop)) < 7:
        raise ValueError('Seleziona un’area più grande con bordi o testo riconoscibili')
    folder.mkdir(exist_ok=True, parents=True)
    margin_x, margin_y = max(60, w * .09), max(45, h * .085)
    roi = [round(max(0, (x1-margin_x)/w), 6), round(max(0, (y1-margin_y)/h), 6),
           round(min(1, (x2+margin_x)/w), 6), round(min(1, (y2+margin_y)/h), 6)]
    updates = [('_rois.json', name, roi), ('_resolution.json', name, [w, h])]
    originals = {}
    for filename, key, value in updates:
        path = folder / filename
        originals[filename] = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(originals[filename], dict):
            raise ValueError(f'Metadati di calibrazione errati: {filename}')
    output = folder / (name + '.png')
    tmp = folder / (name + '._tmp.png')
    if not cv2.imwrite(str(tmp), crop):
        raise OSError('Impossibile salvare il riferimento')
    try:
        for filename, key, value in updates:
            obj = dict(originals[filename]); obj[key] = value
            staging = folder / (filename + '.tmp')
            staging.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        tmp.replace(output)
        for filename, _, _ in updates:
            (folder / (filename + '.tmp')).replace(folder / filename)
    finally:
        tmp.unlink(missing_ok=True)
        for filename, _, _ in updates:
            (folder / (filename + '.tmp')).unlink(missing_ok=True)
    return output


def phase_display(value: str) -> str:
    return {
        'spectating': 'Spettatore', 'spectator': 'Spettatore',
        'reboot_wait': 'Attesa riavvio', 'waiting_reboot': 'Attesa riavvio',
        'normal': 'Nessun avviso', 'unknown': 'Non determinata',
    }.get(str(value).lower(), str(value).replace('_', ' ').capitalize() if value else '—')
