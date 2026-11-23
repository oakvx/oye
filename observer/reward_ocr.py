"""Optional RAM-only OCR for a clearly visible Claim/Riscatta button.

Never clicks on OCR alone: Runner demands repeated, position-stable evidence
and a screen that is not recognized as active gameplay.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import queue
import re
import shutil
import sys
import threading
import time
import cv2
import numpy as np

@dataclass(frozen=True)
class RewardCandidate:
    at: float
    center: tuple[int, int] | None
    score: float = 0.0
    status: str = 'ok'
    name: str = 'riscatta'
    reward_screen: bool = False
    learned: bool = False


def ocr_available() -> bool:
    """Whether the installed OCR engine can read the rewards overlay."""
    try:
        import pytesseract
    except ImportError:
        return False
    if shutil.which('tesseract'):
        return True
    return bool(sys.platform == 'win32' and any(p.is_file() for p in (
        Path('C:/Program Files/Tesseract-OCR/tesseract.exe'),
        Path('C:/Program Files (x86)/Tesseract-OCR/tesseract.exe'))))


def is_reward_screen(text: str) -> bool:
    """A reward *screen*, not a shop's generic RISCATTA call to action.

    Accept singular/plural Italian headers, imperfect OCR word endings, or the
    pair EQUIPAGGIA + RISCATTA. Unrelated shop CTA text stays negative.
    """
    import unicodedata
    clean = unicodedata.normalize('NFKD', text or '').casefold()
    clean = ''.join(ch for ch in clean if not unicodedata.combining(ch))
    tokens = set(re.findall(r'[a-z]+', clean))
    has_title = bool(any(t.startswith('ricompens') for t in tokens) and
                     any(t.startswith('otten') for t in tokens))
    english_title = bool(('rewards' in tokens or 'reward' in tokens or 'items' in tokens) and
                         ('unlocked' in tokens or 'obtained' in tokens))
    # EQUIPAGGIA + RISCATTA can also occur outside a reward overlay.
    # A reward TITLE is therefore required, even in the lobby.
    return has_title or english_title


def extract_reward_button(data: dict, *, scale: float, left: int, top: int,
                          crop_y: int, original_height: int) -> tuple[tuple[int, int], float] | None:
    """Read a single prominent reward CTA, never generic CONTINUA/JOIN buttons."""
    best = None
    words = data.get('text', [])
    for i, raw in enumerate(words):
        word = re.sub(r'[^a-z]', '', str(raw).casefold())
        if word not in {'riscatta', 'riscattare', 'claim', 'collect'}:
            continue
        try:
            conf = float(data['conf'][i])
            x, y, w, h = (float(data[k][i]) for k in ('left', 'top', 'width', 'height'))
        except (ValueError, TypeError, IndexError, KeyError):
            continue
        if conf < 60 or not 25 <= w <= 500 or not 11 <= h <= 130:
            continue
        origin_y = crop_y + (y + h/2) / scale
        if not original_height * .30 <= origin_y <= original_height * .97:
            continue
        candidate = ((int(left + (x+w/2)/scale), int(top + origin_y)), conf / 100.)
        if best is None or candidate[1] > best[1]:
            best = candidate
    return best

def extract_results_button(data: dict, *, scale: float, left: int, top: int,
                           crop_y: int, original_height: int):
    """Detect only CTA labels that unambiguously name a results-menu action.

    The caller MUST have independently recognized RESULTS before this can be
    clicked. Recognition of text is never allowed to promote a game to RESULTS.
    """
    groups = {}
    for i, value in enumerate(data.get('text', [])):
        word = re.sub(r'[^a-z]', '', str(value).casefold())
        if not word:
            continue
        try:
            conf = float(data['conf'][i])
            box = tuple(float(data[key][i]) for key in ('left','top','width','height'))
        except (ValueError, TypeError, IndexError, KeyError):
            continue
        if conf < 60 or min(box[2:]) < 7:
            continue
        line = tuple(data.get(key, [0] * len(data['text']))[i]
                     for key in ('block_num', 'par_num', 'line_num'))
        groups.setdefault(line, []).append((word, conf, box))
    choice = None
    for words in groups.values():
        line = [word for word, _, _ in words]
        name = None
        if ((('torna' in line or 'ritorna' in line) and 'lobby' in line) or
                ('return' in line and 'lobby' in line)):
            name = 'torna_lobby'
        elif ('gioca' in line and 'nuovo' in line) or ('play' in line and 'again' in line):
            name = 'gioca_di_nuovo'
        elif 'continua' in line or 'continue' in line:
            name = 'continua'
        if name is None:
            continue
        boxes = [box for _, _, box in words]
        x0, y0 = min(box[0] for box in boxes), min(box[1] for box in boxes)
        x1 = max(box[0] + box[2] for box in boxes)
        y1 = max(box[1] + box[3] for box in boxes)
        cy = crop_y + (y0 + y1) / (2 * scale)
        if not .30 * original_height <= cy <= .97 * original_height:
            continue
        confidence = min(conf for _,conf,_ in words) / 100.
        position = (int(left + (x0 + x1) / (2 * scale)), int(top + cy))
        if choice is None or confidence > choice[1]:
            choice = (position, confidence, name)
    return choice


class RewardOCRWorker:
    def __init__(self, language='eng', templates_dir=None, profile_dir=None):
        self.language = language
        from .menu_text import load_references
        self.references = load_references(Path(templates_dir)) if templates_dir else {}
        if profile_dir is not None:
            self.references.update(load_references(Path(profile_dir)))
        self.frames = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=2)
        self.stop_event = threading.Event()
        self.thread = None
        self.error = ''

    def start(self):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name='RewardOCR', daemon=True)
        self.thread.start()

    def submit(self, frame: np.ndarray, left: int, top: int, at: float, stage: str = '') -> bool:
        if self.frames.full():
            return False
        try:
            # Never save frames to disk; OCR works on the private RAM copy.
            self.frames.put_nowait((frame.copy(), left, top, at, stage))
            return True
        except queue.Full:
            return False

    def take(self) -> RewardCandidate | None:
        item = None
        while True:
            try:
                item = self.results.get_nowait()
            except queue.Empty:
                return item

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.)

    def _loop(self):
        try:
            import pytesseract
            if sys.platform == 'win32' and not shutil.which('tesseract'):
                for candidate in (Path('C:/Program Files/Tesseract-OCR/tesseract.exe'),
                                  Path('C:/Program Files (x86)/Tesseract-OCR/tesseract.exe')):
                    if candidate.is_file():
                        pytesseract.pytesseract.tesseract_cmd = str(candidate)
                        break
        except ImportError:
            self.error = 'Installa pytesseract e Tesseract OCR o calibra RISCATTA'
            return
        while not self.stop_event.is_set():
            try:
                frame, left, top, at, stage = self.frames.get(timeout=.25)
            except queue.Empty:
                continue
            try:
                bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR) if frame.shape[2] == 4 else frame
                h, w = bgr.shape[:2]
                match = None
                learned = False
                # Highest-priority candidates are taught by the user. A partial
                # OCR label is accepted only within the demonstrated location.
                if self.references:
                    from .menu_text import match_reference
                    # Reward controls must win over PLAY even after returning to
                    # lobby. In 'post_lobby' we do not look for Gioca at all.
                    targets = (('riscatta','gioca') if stage in ('await_play','post_lobby','claim_rewards') else
                               ('gioca',) if stage == 'joining' else
                               ('riscatta','torna_lobby','continua','gioca_di_nuovo'))
                    for target in targets:
                        reference = self.references.get(target)
                        if not reference:
                            continue
                        def read_crop(crop):
                            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                            if gray.shape[1] < 350:
                                factor = max(1, min(3, round(350 / max(1, gray.shape[1]))))
                                gray = cv2.resize(gray, None, fx=factor, fy=factor,
                                                  interpolation=cv2.INTER_CUBIC)
                            return pytesseract.image_to_string(gray, lang=self.language,
                                                               config='--psm 7', timeout=1.8)
                        result = match_reference(bgr, reference, target, read_crop)
                        if result is not None:
                            match = ((result[0][0] + left, result[0][1] + top), result[1], target)
                            learned = True
                            break
                # Read the header independently: reward overlays may appear
                # AFTER the lobby and need priority before the next PLAY. A
                # detected title without a button is a WAIT/teach request.
                header = bgr[int(.08*h):int(.67*h), int(.05*w):int(.96*w)]
                header_scale = min(1.0, 1300.0/max(1, header.shape[1]))
                if header_scale < 1:
                    header = cv2.resize(header, None, fx=header_scale, fy=header_scale,
                                        interpolation=cv2.INTER_AREA)
                header_text = pytesseract.image_to_string(
                    cv2.cvtColor(header, cv2.COLOR_BGR2GRAY), lang=self.language,
                    config='--psm 11', timeout=4.)
                overlay = is_reward_screen(header_text)
                taught_title = self.references.get('reward_title')
                if not overlay and taught_title:
                    from .menu_text import match_reference
                    def title_ocr(crop):
                        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                        return pytesseract.image_to_string(gray, lang=self.language,
                            config='--psm 7', timeout=1.8)
                    overlay = match_reference(bgr, taught_title, 'reward_title', title_ocr) is not None
                if overlay and match is not None and match[2] == 'gioca':
                    match = None  # never let PLAY mask a reward panel
                # First look for prominent colored CTA rectangles. OCR of a
                # tight button crop is more reliable than a full game screenshot.
                hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
                sat_mask = cv2.inRange(hsv, (0, 55, 70), (179, 255, 255))
                contours, _ = cv2.findContours(sat_mask, cv2.RETR_EXTERNAL,
                                               cv2.CHAIN_APPROX_SIMPLE)
                candidates = []
                for contour in contours:
                    cx, cy, cw, ch = cv2.boundingRect(contour)
                    if (cy < h*.30 or cw < 75 or ch < 22 or cw > w*.90 or ch > h*.30
                            or not 1.5 <= cw/max(ch,1) <= 12):
                        continue
                    # Only a few large buttons, never arbitrary tiny text pieces.
                    candidates.append((cw*ch, (cx, cy, cx+cw, cy+ch)))
                candidates.sort(reverse=True)
                regions_px = [coords for _,coords in candidates[:4]]
                # Fallback to broad lower/central regions for non-colored CTAs.
                for x0,y0,x1,y1 in ((0,.25,1.,1.), (.22,.42,.84,.96), (.48,.42,1.,.96)):
                    regions_px.append((int(w*x0),int(h*y0),int(w*x1),int(h*y1)))
                for index, (px0, py0, px1, py1) in enumerate(regions_px if match is None else []):
                    crop = bgr[py0:py1,px0:px1]
                    if crop.size == 0:
                        continue
                    scale = min(1.,1280. / max(px1-px0,1))
                    if scale < 1:
                        crop = cv2.resize(crop,(round((px1-px0)*scale),round((py1-py0)*scale)),
                                          interpolation=cv2.INTER_AREA)
                    gray = cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
                    data = pytesseract.image_to_data(gray,lang=self.language,
                                                     config='--psm 7' if index < len(candidates[:4]) else '--psm 11',
                                                     output_type=pytesseract.Output.DICT,timeout=4.)
                    match = extract_reward_button(data,scale=scale,left=left+px0,
                                                  top=top,crop_y=py0,original_height=h)
                    if match is not None:
                        match = (match[0], match[1], 'riscatta')
                        break
                    match = extract_results_button(data,scale=scale,left=left+px0,
                                                   top=top,crop_y=py0,original_height=h)
                    if match is not None:
                        break
                # An isolated learned RISCATTA or shop Claim is not enough:
                # OCR must also confirm the surrounding reward context.
                if match is not None and match[2] == 'riscatta' and not overlay:
                    match = None
                candidate = (RewardCandidate(at, match[0], match[1], name=match[2],
                                             reward_screen=overlay, learned=learned)
                             if match else RewardCandidate(at, None, reward_screen=overlay))
            except Exception as exc:
                # Operational OCR errors are diagnostic, not fatal to play.
                self.error = f'OCR ricompense: {type(exc).__name__}: {str(exc)[:80]}'
                candidate = RewardCandidate(at, None, status=self.error)
            try:
                self.results.put_nowait(candidate)
            except queue.Full:
                try:
                    self.results.get_nowait()
                    self.results.put_nowait(candidate)
                except queue.Empty:
                    pass
