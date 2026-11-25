"""Read complete lifecycle labels in the corresponding Fortnite UI regions."""
from dataclasses import dataclass, field
from functools import partial
import re

import cv2
import numpy as np

from .ignorante import FINAL_SIGNALS, MatchFrame
from .models import Match
from .phase_ocr import PhaseOCRResult, PhaseOCRWorker
from .teach_ocr import PHRASES, _configure_tesseract, normalize


ZONES = (
    ((.035, .79, .235, .915), ('gioca', 'annulla')),
    ((.66, .22, .97, .66), ('gioca_di_nuovo', 'torna_lobby')),
    ((.38, .041, .55, .093), ('posizione_finale',)),
    ((.337, .155, .548, .215), ('posizione_finale',)),
    ((.55, .48, .94, .65), ('aerial_prompt',)),
)
CLICKABLE = frozenset({'gioca', 'gioca_di_nuovo', 'torna_lobby'})


def valid_capture(frame):
    if frame is None or frame.ndim != 3 or min(frame.shape[:2]) < 100:
        return False
    sample = frame[::12, ::12, :3]
    return sample.max() > 16 and sample.mean(axis=2).std() > 3


@dataclass(frozen=True)
class CycleOCRResult(PhaseOCRResult):
    matches: dict[str, Match] = field(default_factory=dict)
    phase_hint: str | None = None


def aerial_phase(text):
    text = normalize(text)
    if re.search(r'\b(spazio per saltare|space to jump|space per saltare)\b', text):
        return 'battle_bus'
    if re.search(r'\b(spazio per schierare|spazio per aprire|space to deploy|space to skydive)\b', text):
        return 'skydiving'
    return None


def _button_crops(gray, rect):
    h, w = gray.shape
    x1, y1, x2, y2 = rect
    dx, dy = round(x1*w), round(y1*h)
    zone = gray[dy:round(y2*h), dx:round(x2*w)]
    contours, _ = cv2.findContours(cv2.inRange(zone, 160, 255), cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        if (.10*w <= bw <= .31*w and .025*h <= bh <= .11*h and
                cv2.contourArea(contour) >= .91*bw*bh and
                0 < x < x+bw < zone.shape[1] and 0 < y < y+bh < zone.shape[0]):
            px, py = round(.05*bw), round(.12*bh)
            crop = zone[y+py:y+bh-py, x+px:x+bw-px]
            yield dx+x+px, dy+y+py, cv2.threshold(crop, 105, 255, cv2.THRESH_BINARY)[1]


def _lines(data, width, height):
    lines = {}
    for index, token in enumerate(data['text']):
        token = normalize(token)
        if not token:
            continue
        line = tuple(int(data[key][index]) for key in ('block_num', 'par_num', 'line_num'))
        x, y, w, h = (int(data[key][index]) for key in ('left', 'top', 'width', 'height'))
        if 0 <= x < width and 0 <= y < height:
            lines.setdefault(line, []).append((x, y, w, h, token, float(data['conf'][index])))
    return (sorted(words) for words in lines.values())


def read_cycle_labels(frame, at: float, *, language='eng') -> CycleOCRResult:
    if not valid_capture(frame):
        return CycleOCRResult(at, 'invalid_capture')
    tess = _configure_tesseract()
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    w = min(1920, max(1280, width))
    h = round(height*w/width)
    if w != width:
        gray = cv2.resize(gray, (w, h), interpolation=cv2.INTER_CUBIC if w > width else cv2.INTER_AREA)
    crops = []
    offset = 12
    for rect, names in ZONES:
        if 'posizione_finale' in names or 'aerial_prompt' in names:
            x1, y1, x2, y2 = rect
            x, y = round(x1*w), round(y1*h)
            title = gray[y:round(y2*h), x:round(x2*w)]
            title = cv2.threshold(title, 200, 255, cv2.THRESH_BINARY_INV)[1]
            candidates = [(x, y, title)]
        else:
            candidates = list(_button_crops(gray, rect))
            if not candidates:
                # Highlighted/dark controls have no solid white rectangle.
                # Read only their known region, still requiring the full label.
                x1, y1, x2, y2 = rect
                x, y = round(x1*w), round(y1*h)
                zone = gray[y:round(y2*h), x:round(x2*w)]
                zone = cv2.threshold(zone, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
                candidates = [(x, y, zone)]
        for x, y, crop in candidates:
            crops.append((x, y, offset, crop, names))
            offset += crop.shape[0] + 24
    montage = np.full((offset, max(crop.shape[1] for _, _, _, crop, _ in crops) + 24),
                      255, dtype=np.uint8)
    for _, _, row, crop, _ in crops:
        crop_height, crop_width = crop.shape
        montage[row:row+crop_height, 12:12+crop_width] = crop
    data = tess.image_to_data(montage, lang=language, config='--psm 6',
                              output_type=tess.Output.DICT, timeout=2)
    matches = {}
    phase_hint = None
    for words in _lines(data, montage.shape[1], montage.shape[0]):
        label = ' '.join(word[4] for word in words)
        score = min(word[5] for word in words) / 100
        left = words[0][0]
        top = min(word[1] for word in words)
        right = max(word[0] + word[2] for word in words)
        bottom = max(word[1] + word[3] for word in words)
        for x, y, row, crop, names in crops:
            if not (12 <= left < right <= 12+crop.shape[1] and
                    row <= top < bottom <= row+crop.shape[0]):
                continue
            for name in names:
                if name == 'aerial_prompt':
                    if score >= .60:
                        phase_hint = aerial_phase(label) or phase_hint
                    continue
                exact = label in PHRASES[name]
                title = name == 'posizione_finale' and any(
                    label == phrase or label.startswith(phrase + ' ') for phrase in PHRASES[name])
                title_score = min(word[5] for word in words[:2]) / 100
                if (exact and score >= .60) or (title and title_score >= .60):
                    center = (round((x + (left+right)/2 - 12)*width/w),
                              round((y + (top+bottom)/2 - row)*height/h))
                    matches[name] = Match(name, title_score if title else score, center)
    return CycleOCRResult(at, 'ok', matches=matches, phase_hint=phase_hint)


def cycle_ocr_worker(language: str) -> PhaseOCRWorker:
    return PhaseOCRWorker(analyzer=partial(read_cycle_labels, language=language))


def playable_scene(reading) -> bool:
    return bool(reading.scene == 'match' or (reading.hud is not None and reading.hud.present)
                or 'hud_in_partita' in reading.best
                or reading.phase_hint in ('battle_bus', 'skydiving'))


def match_frame(result: PhaseOCRResult, reading, *, left=0, top=0, locked=False,
                modular_at: float | None = None) -> MatchFrame:
    if result.status != 'ok' or not isinstance(result, CycleOCRResult):
        return MatchFrame(result.at, False)
    # Templates can stop movement immediately. Click targets come from a
    # complete OCR label in the correct zone, with two acquisition timestamps.
    signals = frozenset(result.matches) | (reading.best.keys() & (FINAL_SIGNALS | {'annulla'}))
    buttons = {name: Match(name, item.score, (left+item.center[0], top+item.center[1]))
               for name, item in result.matches.items() if name in CLICKABLE}
    playable = playable_scene(reading) or result.phase_hint is not None
    playable_at = result.at if playable else modular_at
    blocked = locked or reading.scene == 'loading' or ('lobby_indizio' in reading.best and not playable)
    return MatchFrame(result.at, True, signals, buttons, playable_at is not None,
                      blocked, playable_at)
