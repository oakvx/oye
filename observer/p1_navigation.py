"""Conservative visual-navigation hints and verifiable camera feedback.

These are *heuristics*, not depth perception, collision detection or proof
of accepted game input. Never authorize input independently of the HUD gate.
"""
from __future__ import annotations
from dataclasses import dataclass

import cv2
import numpy as np

from .models import Motion


@dataclass(frozen=True)
class RouteHint:
    side: str | None
    confidence: float
    reason: str


def route_hint(frame: np.ndarray) -> RouteHint:
    """Suggest the less visually cluttered side of the lower central view.

    Ignore the HUD-dominated screen edges and reject low-texture/ambiguous
    frames. Edge density is NOT reliable obstacle recognition.
    """
    if frame is None or frame.ndim != 3 or frame.shape[0] < 120 or frame.shape[1] < 160:
        return RouteHint(None, 0., 'Immagine non valida')
    height, width = frame.shape[:2]
    # Nearby world content: bottom-middle, excluding inventory and screen edges.
    y0, y1 = int(height * .48), int(height * .73)
    x0, x1 = int(width * .22), int(width * .78)
    crop = frame[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGRA2GRAY if crop.shape[-1] == 4 else cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (240, 105), interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(gray, 65, 140)
    left = float(np.mean(edges[:, :105] > 0))
    right = float(np.mean(edges[:, 135:] > 0))
    if max(left, right) < .025:
        return RouteHint(None, 0., 'Scena poco dettagliata: direzione non deducibile')
    difference = abs(left - right) / max(.01, left + right)
    if difference < .23:
        return RouteHint(None, difference, 'Densità visiva simile sui due lati')
    side = 'left' if left < right else 'right'
    return RouteHint(side, min(1., difference), f'Possibile lato meno ingombro: {side} (solo indizio visivo)')


@dataclass
class CameraFeedback:
    good: int = 0
    failed: int = 0
    inconclusive: int = 0
    status: str = 'untested'
    detail: str = 'Telecamera non verificata su fotogrammi distinti'

    def observe(self, motion: Motion | None, *, isolated: bool) -> str:
        if not isolated:
            # Forward movement can create apparent screen drift; never mark it verified.
            self.detail = 'Telecamera in movimento combinato con W: verifica non conclusiva'
            return self.status
        if motion is None or not motion.reliable or motion.tracked < 18:
            self.inconclusive += 1
            self.detail = 'Rotazione isolata: fotogrammi insufficienti o scena senza riferimenti'
            return self.status
        if motion.pixels >= .9:
            self.good += 1
            self.failed = 0
            self.status = 'observed'
            self.detail = f'Rotazione visiva osservata ({motion.pixels:.2f} px): input non garantito'
        else:
            self.failed += 1
            self.status = 'not_observed' if self.failed < 2 else 'attention'
            self.detail = ('Nessuna rotazione visibile su 2 prove: verifica focus, input del gioco e sensibilità'
                           if self.failed >= 2 else 'Rotazione non rilevata: riprovo con un fotogramma nuovo')
        return self.status
