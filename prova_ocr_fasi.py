"""Diagnostica locale OCR su uno screenshot fornito dall'utente (nessun input)."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from observer.phase_ocr import PhaseOCRWorker, classify_phase


def main() -> int:
    parser = argparse.ArgumentParser(description='Verifica Tesseract su screenshot di Fortnite.')
    parser.add_argument('screenshot', type=Path, help='Percorso PNG/JPEG dello screenshot')
    parser.add_argument('--lang', default='eng', help='Lingue Tesseract installate, es. eng o ita+eng')
    args = parser.parse_args()
    if not args.screenshot.is_file():
        print('ERRORE: screenshot non trovato:', args.screenshot)
        return 2
    try:
        import cv2
        import pytesseract
        frame = cv2.imread(str(args.screenshot), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError('file immagine non valido')
        import shutil
        if sys.platform == 'win32' and not shutil.which('tesseract'):
            tesseract = Path('C:/Program Files/Tesseract-OCR/tesseract.exe')
            if not tesseract.is_file():
                tesseract = Path('C:/Program Files (x86)/Tesseract-OCR/tesseract.exe')
            if tesseract.is_file():
                pytesseract.pytesseract.tesseract_cmd = str(tesseract)
        print('Tesseract:', pytesseract.get_tesseract_version())
        text = PhaseOCRWorker._read_text(frame, args.lang)
        print('OCR testo:', repr(text[:1000]))
        print('Classificazione:', classify_phase(text))
        print('NOTA: il testo non dimostra che il personaggio sia controllabile.')
        return 0
    except Exception as exc:
        print('ERRORE OCR:', type(exc).__name__, str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
