"""Conservative OCR suggestions for the local 'Fammelo vedere' onboarding.

An OCR hit is not proof that a button is interactable. This module only
suggests a static template crop. The existing Runner still checks scene,
foreground and phase before issuing input.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import sys
import unicodedata

import cv2
import numpy as np


def normalize(value: str) -> str:
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    return ' '.join(re.findall(r'[a-z0-9]+', value))


# Avoid guesses for spectator controls and HUD: their text is not a stable
# representation of the button/panel itself. They require a user-selected spot.
PHRASES = {
    'gioca': ('gioca', 'play'),
    'riscatta': ('riscatta', 'riscattare', 'claim', 'collect'),
    'reward_title': ('ricompensa ottenuta', 'ricompense ottenute', 'reward obtained', 'rewards unlocked'),
    'continua': ('continua', 'continue'),
    'gioca_di_nuovo': ('gioca di nuovo', 'gioca ancora', 'play again'),
    'annulla': ('annulla', 'cancel'),
    'posizione_finale': ('posizione finale', 'final placement'),
    'torna_lobby': ('torna alla lobby', 'ritorna alla lobby', 'return to lobby', 'back to lobby'),
}


@dataclass(frozen=True)
class OCRSuggestion:
    rect: tuple[float, float, float, float]  # normalized full-frame crop
    text: str
    confidence: float


def _tokens_to_suggestions(data: dict, target: str, shape: tuple[int, int]) -> list[OCRSuggestion]:
    """Collect exact OCR phrase tokens on the SAME line, never fuzzy text.

    No automatic learning on merely similar words. Multiple hits remain
    ambiguous and must be selected by the user.
    """
    phrases = PHRASES.get(target)
    if not phrases:
        return []
    h, w = shape
    by_line = {}
    count = len(data.get('text', []))
    for i in range(count):
        word = normalize(data['text'][i])
        if not word:
            continue
        try:
            conf = float(data['conf'][i])
            x, y, cw, ch = (int(data[k][i]) for k in ('left','top','width','height'))
            line = tuple(int(data[k][i]) for k in ('block_num','par_num','line_num'))
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        if conf < 60 or cw < 4 or ch < 8 or not (0 <= x < w and 0 <= y < h):
            continue
        by_line.setdefault(line, []).append((x, y, cw, ch, word, conf))
    suggestions = []
    for items in by_line.values():
        items.sort(key=lambda x:x[0])
        for phrase in phrases:
            desired = phrase.split()
            if not desired:
                continue
            for i in range(len(items)-len(desired)+1):
                group = items[i:i+len(desired)]
                if [x[4] for x in group] != desired:
                    continue
                # Cross-element strings far apart on same OCR line do not count.
                if any(group[j+1][0] - (group[j][0]+group[j][2]) > max(38, .018*w)
                       for j in range(len(group)-1)):
                    continue
                # The OCR phrase must be fairly reliable to save automatically.
                confidence = sum(x[5] for x in group) / len(group)
                left = min(x[0] for x in group)
                top = min(x[1] for x in group)
                right = max(x[0]+x[2] for x in group)
                bottom = max(x[1]+x[3] for x in group)
                # Keep a little distinctive UI around the text: not an entire menu.
                dx = max(14, round((right-left)*.18))
                dy = max(10, round((bottom-top)*.55))
                left, top = max(0, left-dx), max(0, top-dy)
                right, bottom = min(w, right+dx), min(h, bottom+dy)
                if right-left < 24 or bottom-top < 18:
                    continue
                suggestions.append(OCRSuggestion((left/w,top/h,right/w,bottom/h),
                                                  phrase, round(confidence,1)))
    # A text region may occur in several OCR blocks, remove exact repeats.
    unique = {}
    for s in suggestions:
        unique[s.rect] = s
    return list(unique.values())


def _configure_tesseract():
    import pytesseract
    if sys.platform == 'win32' and not shutil.which('tesseract'):
        for path in (Path('C:/Program Files/Tesseract-OCR/tesseract.exe'),
                     Path('C:/Program Files (x86)/Tesseract-OCR/tesseract.exe')):
            if path.is_file():
                pytesseract.pytesseract.tesseract_cmd = str(path)
                break
    return pytesseract


def suggest(frame: np.ndarray, target: str, languages: str='eng') -> tuple[OCRSuggestion | None, str]:
    """Return (high-confidence unique suggestion, diagnostic).

    OCR is an optional convenience. A failed/missing OCR always falls back to
    the click-on-screenshot path and never treats the frame as confirmed gameplay.
    """
    if target not in PHRASES:
        return None, 'manuale'
    try:
        pytesseract = _configure_tesseract()
        h, w = frame.shape[:2]
        # Use a bounded OCR image and remap its coordinates to original frame.
        scale = min(1., 1600/max(1,w))
        resized = cv2.resize(frame, (max(1,int(w*scale)), max(1,int(h*scale)))) if scale<1 else frame
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY) if resized.ndim==3 else resized
        data = pytesseract.image_to_data(gray, lang=languages, config='--psm 11',
                                         output_type=pytesseract.Output.DICT, timeout=6)
        picks = _tokens_to_suggestions(data,target,gray.shape[:2])
        strong = [x for x in picks if x.confidence>=83]
        if len(strong)==1 and len(picks)==1:
            return strong[0], 'ocr'
        if picks:
            return None,'ocr_ambiguo'
        return None,'ocr_non_trovato'
    except (ImportError, FileNotFoundError):
        return None,'ocr_assente'
    except Exception as exc:
        # No UI or OCR exception should be able to inject false templates.
        return None,'ocr_non_disponibile:'+type(exc).__name__


def point_crop(frame: np.ndarray, name: str, point: tuple[float,float]) -> tuple[int,int,int,int]:
    """Turn one click into a bounded crop centered on a visible target.

    This is a *suggested* crop for ordinary desktop resolutions. The manual
    drag interface remains available for oddly shaped HUD or scaled UIs.
    """
    if len(point)!=2 or any(type(p) not in (int,float) or not np.isfinite(p) or p<0 or p>1 for p in point):
        raise ValueError('Indica un punto valido nello screenshot')
    h,w=frame.shape[:2]
    dims={
        'hud_salute':(.13,.065), 'hud_inventario':(.17,.08),
        'hud_minimappa':(.12,.20), 'hud_in_partita':(.13,.07),
        'spettatore_prossimo':(.13,.065), 'spettatore_precedente':(.13,.065),
        'continua':(.14,.07), 'torna_lobby':(.18,.09), 'gioca':(.14,.075),
        'reward_title':(.30,.09),
    }
    wf,hf=dims.get(name,(.13,.08))
    cw=min(w,max(36,round(w*wf)))
    ch=min(h,max(25,round(h*hf)))
    cx,cy=round(point[0]*w),round(point[1]*h)
    x1=max(0,min(w-cw,cx-cw//2))
    y1=max(0,min(h-ch,cy-ch//2))
    return x1,y1,x1+cw,y1+ch
