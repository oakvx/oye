"""Asynchronous in-RAM OCR of Fortnite spectator/reboot overlays.

Only extracts status, never issues inputs. A screenshot remains in RAM until the
OCR worker completes and is not persisted. A missing OCR installation is reported,
not interpreted as a clean gameplay screen.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import queue
import re
import threading
import unicodedata

import cv2
import numpy as np


def normalize(text: str) -> str:
    text = unicodedata.normalize('NFKD', str(text or '')).casefold()
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def classify_phase(text: str) -> str:
    """Return a *visual text cue*, not a claim of character control."""
    s = normalize(text)
    if not s:
        return 'none'
    # Italian and English Fortnite UI. Accept both 'attesa del' and 'attesa di'.
    reboot = re.search(r'\b(riavvi\w*|reboot\w*|respawn\w*)\b', s)
    waiting = re.search(r'\b(attes\w*|attend\w*|waiting|awaiting|pending)\b', s)
    # OCR often merges VV into W (RIAVVIO -> RIAWIO). Only apply fuzzy
    # matching inside a wait phrase, to avoid blocking on arbitrary UI text.
    if waiting and not reboot:
        reboot = any(len(word) >= 6 and SequenceMatcher(None, word, 'riavvio').ratio() >= .72
                     for word in s.split())
    if reboot and waiting:
        return 'reboot_wait'
    if reboot and re.search(r'\b(riavvio in corso|riavviando|rebooting|reboot in progress|reboot underway)\b', s):
        return 'rebooting'
    if re.search(r'\b(spettator\w*|spectat\w*)\b', s):
        return 'spectator'
    return 'none'


@dataclass(frozen=True)
class PhaseOCRResult:
    at: float
    status: str  # ok | not_installed | tesseract_missing | error:...
    cue: str = 'none'
    text: str = ''


class PhaseOCRWorker:
    """One-slot latest-frame worker with bounded Tesseract execution time."""

    def __init__(self, languages: str = 'eng'):
        self.languages = languages
        self._pending: queue.Queue = queue.Queue(maxsize=1)
        self._result: queue.Queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name='OyePhaseOCR')
        self._thread.start()

    def stop(self, timeout: float = 1.5):
        self._stop.set()
        try:
            self._pending.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def submit(self, frame: np.ndarray, at: float) -> bool:
        if self._stop.is_set() or self._pending.full():
            return False
        # Pass a copy: the producer can release the screenshot immediately.
        try:
            self._pending.put_nowait((frame.copy(), at))
            return True
        except queue.Full:
            return False

    def take(self) -> PhaseOCRResult | None:
        try:
            return self._result.get_nowait()
        except queue.Empty:
            return None

    @staticmethod
    def _read_text(frame: np.ndarray, languages: str) -> str:
        import pytesseract
        # Unlike the general-purpose OCR, focus on the screen regions where the
        # death/spectator/awaiting reboot banner actually appears.
        h, w = frame.shape[:2]
        # Top-center covers SPECTATOR; middle-center covers the reboot banner.
        crops = (frame[int(.01*h):int(.34*h), int(.14*w):int(.86*w)],
                 frame[int(.23*h):int(.66*h), int(.10*w):int(.90*w)])
        fragments: list[str] = []
        for crop in crops:
            if crop.size == 0:
                continue
            bgr = cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR) if crop.ndim == 3 and crop.shape[-1] == 4 else crop
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
            width = min(1800, max(720, gray.shape[1]))
            if width != gray.shape[1]:
                gray = cv2.resize(gray, (width, max(1, round(width * gray.shape[0] / gray.shape[1]))),
                                  interpolation=cv2.INTER_CUBIC if width > gray.shape[1] else cv2.INTER_AREA)
            gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
            # Run only through the public pytesseract API with a fixed timeout.
            text = pytesseract.image_to_string(gray, lang=languages, config='--psm 11', timeout=3)
            fragments.append(text)
        return '\n'.join(fragments)[:2500]

    def _loop(self):
        while not self._stop.is_set():
            try:
                item = self._pending.get(timeout=.2)
            except queue.Empty:
                continue
            if item is None:
                break
            frame, at = item
            try:
                # The initial call to the general OCR engine locates the Windows
                # Tesseract binary when it is absent from PATH.
                import pytesseract
                import shutil
                import sys
                from pathlib import Path
                if sys.platform == 'win32' and not shutil.which('tesseract'):
                    for p in (Path('C:/Program Files/Tesseract-OCR/tesseract.exe'),
                              Path('C:/Program Files (x86)/Tesseract-OCR/tesseract.exe')):
                        if p.is_file():
                            pytesseract.pytesseract.tesseract_cmd = str(p)
                            break
                text = self._read_text(frame, self.languages)
                result = PhaseOCRResult(at, 'ok', classify_phase(text), normalize(text)[:300])
            except ImportError:
                result = PhaseOCRResult(at, 'not_installed')
            except Exception as exc:
                result = PhaseOCRResult(at, 'error:' + type(exc).__name__ + ':' + str(exc)[:120])
            # Latest result only; the consumer must never mistake it for live OCR
            # after a screenshot has already gone stale.
            try:
                self._result.put_nowait(result)
            except queue.Full:
                try:
                    self._result.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._result.put_nowait(result)
                except queue.Full:
                    pass
