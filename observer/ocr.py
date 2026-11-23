"""Optional local OCR; explicit status instead of silently claiming text was read."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
import cv2
import numpy as np


@dataclass(frozen=True)
class OCRResult:
    text: str = ''
    words: tuple[str, ...] = ()
    status: str = 'disabled'
    confidence: float = 0.0


class LocalOCR:
    def __init__(self, enabled: bool = True, languages: str = 'eng'):
        self.enabled = enabled
        self.languages = languages
        self._missing = None

    def read(self, frame: np.ndarray) -> OCRResult:
        if not self.enabled:
            return OCRResult(status='disabled')
        if self._missing is not None:
            return OCRResult(status=self._missing)
        try:
            import pytesseract
        except ImportError:
            self._missing = 'pytesseract_not_installed'
            return OCRResult(status=self._missing)
        if sys.platform == 'win32' and not shutil.which('tesseract'):
            for candidate in (Path('C:/Program Files/Tesseract-OCR/tesseract.exe'),
                              Path('C:/Program Files (x86)/Tesseract-OCR/tesseract.exe')):
                if candidate.is_file():
                    pytesseract.pytesseract.tesseract_cmd = str(candidate)
                    break
        try:
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR) if frame.shape[2] == 4 else frame
            height, width = bgr.shape[:2]
            scale = min(1.0, 1600 / max(width, 1))
            if scale < 1:
                bgr = cv2.resize(bgr, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
            data = pytesseract.image_to_data(gray, lang=self.languages, config='--psm 11',
                                             output_type=pytesseract.Output.DICT,
                                             timeout=6)
            words, confidences = [], []
            for text, value in zip(data['text'], data['conf']):
                word = text.strip()
                try:
                    score = float(value)
                except (ValueError, TypeError):
                    score = -1
                if word and score >= 35:
                    words.append(word)
                    confidences.append(score)
            return OCRResult(' '.join(words)[:4000], tuple(words[:400]), 'ok',
                             round(sum(confidences) / len(confidences) / 100, 3) if confidences else 0.)
        except pytesseract.TesseractNotFoundError:
            self._missing = 'tesseract_executable_not_found'
            return OCRResult(status=self._missing)
        except pytesseract.TesseractError as exc:
            return OCRResult(status=f'tesseract_error:{str(exc)[:160]}')
        except RuntimeError as exc:
            return OCRResult(status=f'ocr_timeout_or_error:{str(exc)[:160]}')
