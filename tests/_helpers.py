"""Fixtures containing decodable, contrast-rich visual templates."""
from pathlib import Path
import cv2
import numpy as np


def put_template(folder: Path, name: str):
    rng = np.random.default_rng(sum(name.encode('utf-8')))
    img = rng.integers(15, 235, (46, 124, 3), dtype=np.uint8)
    path = Path(folder) / f'{name}.png'
    if not cv2.imwrite(str(path), img):
        raise RuntimeError(f'Cannot write test template: {path}')
    return path
