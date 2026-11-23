"""Validate calibration files using the same image constraints as Vision.

A filename is not evidence that a usable template exists.  This runs before
any LIVE runner can be started and returns actionable per-item diagnostics.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from .config import TEMPLATE_NAMES, load_rois


def audit_calibrations(folder: Path) -> tuple[set[str], dict[str, str], list[str]]:
    folder = Path(folder)
    valid: set[str] = set()
    invalid: dict[str, str] = {}
    issues: list[str] = []
    for name in TEMPLATE_NAMES:
        path = folder / f'{name}.png'
        if not path.is_file():
            continue
        try:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError('PNG illeggibile o corrotto')
            if min(image.shape[:2]) < 14:
                raise ValueError('immagine troppo piccola (minimo 14 pixel per lato)')
            if float(np.std(image)) < 7:
                raise ValueError('immagine troppo uniforme per il riconoscimento')
            valid.add(name)
        except Exception as exc:
            invalid[name] = str(exc)

    # Vision loads these metadata too, so a corrupt file makes the entire
    # library unusable even when all of its images look fine.
    try:
        load_rois(folder / '_rois.json')
    except (OSError, ValueError, TypeError) as exc:
        issues.append(f'Metadati _rois.json non validi: {exc}')
    resolution = folder / '_resolution.json'
    if resolution.is_file():
        try:
            base = json.loads(resolution.read_text(encoding='utf-8'))
            if not isinstance(base, dict):
                raise ValueError('deve contenere un oggetto JSON')
            for name, dimensions in base.items():
                if name not in TEMPLATE_NAMES:
                    raise ValueError(f'riferimento sconosciuto: {name}')
                if (not isinstance(dimensions, list) or len(dimensions) != 2 or
                        any(type(d) not in (int, float) or not np.isfinite(d) or d < 100
                            for d in dimensions)):
                    raise ValueError(f'risoluzione non valida per {name}')
        except (OSError, ValueError, TypeError) as exc:
            issues.append(f'Metadati _resolution.json non validi: {exc}')
    return valid, invalid, issues
