"""User-taught OCR anchors for menu controls.

A screenshot demonstration defines the approximate screen region and the
expected text. Runtime uses short, partial OCR fragments only inside that
region. The caller must still check multi-frame stability and game phase.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import json
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .teach_ocr import PHRASES, normalize, _configure_tesseract

FILE = '_menu_text.json'
TARGETS = frozenset(PHRASES)
STOPWORDS = frozenset({'alla', 'alle', 'the', 'back', 'to', 'per', 'di', 'nel', 'and', 'con'})


def _tokens(text: str) -> list[str]:
    return [x for x in normalize(text).split() if len(x) >= 4 and x not in STOPWORDS]


def fragment_score(seen: str, phrases: tuple[str, ...] | list[str]) -> float:
    """One distinctive partial word can match; 3-character noise cannot.

    Intended to be used only within a user-demonstrated region and after
    several fresh OCR captures, not to authorize clicks across the whole screen.
    """
    detected = _tokens(seen)
    if not detected:
        return 0.0
    best = 0.0
    for phrase in phrases:
        for expected in _tokens(phrase):
            for word in detected:
                if word == expected:
                    best = max(best, 1.)
                elif min(len(word), len(expected)) >= 4 and (
                        word.startswith(expected[:4]) or expected.startswith(word[:4])):
                    similarity = SequenceMatcher(None, word, expected).ratio()
                    if similarity >= .60 and len(word) >= 4:
                        best = max(best, .78 + .2 * similarity)
                else:
                    similarity = SequenceMatcher(None, word, expected).ratio()
                    if similarity >= .82 and min(len(word), len(expected)) >= 5:
                        best = max(best, .7 + .25 * similarity)
    return best


def _load_json(folder: Path) -> dict:
    path = Path(folder) / FILE
    if not path.exists():
        return {}
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(result, dict):
        return {}
    safe = {}
    for name, entry in result.items():
        if name not in TARGETS or not isinstance(entry, dict):
            continue
        rect, label = entry.get('rect'), entry.get('label', '')
        if (not isinstance(label, str) or len(label) > 120 or
                not isinstance(rect, list) or len(rect) != 4 or
                not all(type(x) in (int, float) and 0 <= x <= 1 for x in rect) or
                not rect[0] < rect[2] or not rect[1] < rect[3]):
            continue
        safe[name] = {'rect': rect, 'label': label}
    return safe


def load_references(folder: Path) -> dict:
    return _load_json(Path(folder))


def learn_reference(folder: Path, name: str, frame_shape: tuple[int, ...],
                    rectangle: tuple[int, int, int, int], label: str = '') -> dict:
    if name not in TARGETS:
        raise ValueError('Testo insegnabile soltanto per i pulsanti del menu')
    if not isinstance(label, str) or len(label) > 120:
        raise ValueError('Testo del pulsante non valido')
    h, w = frame_shape[:2]
    x1, y1, x2, y2 = map(int, rectangle)
    if not 0 <= x1 < x2 <= w or not 0 <= y1 < y2 <= h:
        raise ValueError('Area OCR non valida')
    # Keep the taught position tight to prevent an unrelated HUD label from
    # being confused with a button elsewhere on screen.
    pad_x, pad_y = max(12, round((x2-x1)*.26)), max(8, round((y2-y1)*.38))
    rect = [round(max(0,x1-pad_x)/w, 6), round(max(0,y1-pad_y)/h, 6),
            round(min(w,x2+pad_x)/w, 6), round(min(h,y2+pad_y)/h, 6)]
    entry = {'rect': rect, 'label': normalize(label)[:120]}
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    saved = _load_json(folder)
    saved[name] = entry
    temp = folder / (FILE + '.tmp')
    try:
        temp.write_text(json.dumps(saved, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        temp.replace(folder / FILE)
    finally:
        temp.unlink(missing_ok=True)
    return entry


def read_label(frame: np.ndarray, rectangle: tuple[int, int, int, int], language: str = 'eng') -> str:
    """Read once during teaching. Failure returns empty; never blocks saving."""
    try:
        pytesseract = _configure_tesseract()
        x1, y1, x2, y2 = rectangle
        img = frame[y1:y2, x1:x2]
        if img.size == 0:
            return ''
        gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY if img.shape[-1] == 4 else cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (gray.shape[1]*2, gray.shape[0]*2), interpolation=cv2.INTER_CUBIC)
        raw = pytesseract.image_to_string(gray, lang=language, config='--psm 7', timeout=3)
        return normalize(raw)[:120]
    except Exception:
        return ''


def match_reference(frame: np.ndarray, reference: dict, name: str,
                    ocr: Callable[[np.ndarray], str]) -> tuple[tuple[int, int], float] | None:
    """Pure lookup with injected OCR: useful for deterministic regression tests."""
    h, w = frame.shape[:2]
    rect = reference['rect']
    x1, y1, x2, y2 = (max(0,min(w,round(rect[0]*w))), max(0,min(h,round(rect[1]*h))),
                      max(0,min(w,round(rect[2]*w))), max(0,min(h,round(rect[3]*h))))
    if x2-x1 < 20 or y2-y1 < 14:
        return None
    crop = frame[y1:y2,x1:x2]
    seen = ocr(crop)
    accepted = list(PHRASES[name])
    if reference.get('label'):
        accepted.insert(0, reference['label'])
    score = fragment_score(seen, accepted)
    # GI0CA in the persistent top navigation must NEVER satisfy PLAY AGAIN.
    if name == 'gioca_di_nuovo':
        tokens = set(normalize(seen).split())
        if not ({'gioca', 'nuovo'} <= tokens or {'play', 'again'} <= tokens):
            return None
    if score < .81:
        return None
    return ((x1+x2)//2, (y1+y2)//2), score


def update_label(folder: Path, name: str, label: str) -> dict:
    if name not in TARGETS or not isinstance(label, str) or not 4 <= len(label.strip()) <= 120:
        raise ValueError('Etichetta OCR non valida')
    folder = Path(folder)
    entries = _load_json(folder)
    if name not in entries:
        raise ValueError('Prima mostra dove si trova il pulsante')
    entries[name]['label'] = normalize(label)
    path = folder / FILE
    tmp = folder / (FILE + '.tmp')
    try:
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return entries[name]
