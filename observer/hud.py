"""Robust, conservative HUD evidence from independent interface anchors.

Each user calibration is a local crop of a *frame* (map border, inventory
slots, life-panel outline, etc.), never a full screenshot. The center of the
crop is deliberately masked when comparing contours so its content may vary.

This is a computer-vision heuristic, NOT semantic recognition of Fortnite.
"""
from __future__ import annotations
from dataclasses import dataclass

import cv2
import numpy as np

HUD_ANCHORS = (
    'hud_minimappa', 'hud_salute', 'hud_inventario',
    'hud_bussola', 'hud_squadra', 'hud_mirino',
)
# Solo i pannelli persistenti sono validi per confermare la presenza dell'HUD.
# Bussola, mirino e squadra sono facoltativi: si muovono o possono sparire.
HUD_CORE = ('hud_salute', 'hud_minimappa', 'hud_inventario')
HUD_OPTIONAL = ('hud_bussola', 'hud_squadra', 'hud_mirino')
HUD_WEIGHTS = {
    'hud_minimappa': 1.15,
    'hud_salute': 1.15,
    'hud_inventario': 1.20,
    'hud_bussola': .85,
    'hud_squadra': .95,
    'hud_mirino': .55,
}


@dataclass(frozen=True)
class HudConsensus:
    present: bool
    confidence: float
    votes: int
    configured: int
    matched: tuple[str, ...]
    core_votes: int = 0
    core_configured: int = 0
    reason: str = ''


def outline_edges(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        gray = frame
    elif frame.shape[2] == 4:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    else:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(gray, 55, 140)


def peripheral_mask(height: int, width: int) -> np.ndarray:
    """Exclude changing text, map tiles, weapon icons and numeric values.

    The peripheral band is ~20% of the patch on each side. For HUD components
    with variable contents, users should select their *static outer frame*.
    """
    mask = np.full((height, width), 255, dtype=np.uint8)
    margin_x = min(width // 3, max(3, round(width * .19)))
    margin_y = min(height // 3, max(3, round(height * .19)))
    if width > 2 * margin_x and height > 2 * margin_y:
        mask[margin_y:height-margin_y, margin_x:width-margin_x] = 0
    return mask


def prepare_outline(template: np.ndarray) -> tuple[np.ndarray, np.ndarray, bool]:
    """Cache the stable template contours and peripheral mask per resolution."""
    h, w = template.shape[:2]
    edges = outline_edges(template)
    mask = peripheral_mask(h, w)
    useful = np.count_nonzero((edges > 0) & (mask > 0)) >= 16
    return edges, mask, bool(useful)


def score_outline(crop: np.ndarray, template: np.ndarray, *,
                  prepared: tuple[np.ndarray, np.ndarray, bool] | None = None) -> float:
    """Score structure inside the peripheral band over a cropped search ROI.

    N.B. masks work with TM_CCORR_NORMED; guard against extremely blank
    contours (e.g. uniform map corners) and finite floating point behavior.
    """
    th, tw = template.shape[:2]
    if th > crop.shape[0] or tw > crop.shape[1]:
        return 0.0
    edges, mask, useful = prepared if prepared is not None else prepare_outline(template)
    if not useful:
        return 0.0
    frame_edges = outline_edges(crop)
    response = cv2.matchTemplate(frame_edges, edges, cv2.TM_CCORR_NORMED, mask=mask)
    finite = response[np.isfinite(response)]
    return float(np.max(finite)) if finite.size else 0.0


def consensus(scores: dict[str, float], *, threshold: float = .68) -> HudConsensus:
    """Riconoscimento conservativo di >=2 pannelli persistenti indipendenti.

    Gli indizi facoltativi sono diagnostici, mai prerequisiti e non possono
    compensare la mancata conferma dei pannelli stabili. In particolare,
    lo schermo solo con barra della salute NON autorizza input LIVE.
    """
    valid = {k: float(v) for k, v in scores.items()
             if k in HUD_WEIGHTS and np.isfinite(v)}
    matched = tuple(k for k, v in valid.items() if v >= threshold)
    core = {k: v for k, v in valid.items() if k in HUD_CORE}
    core_matched = {k: v for k, v in core.items() if v >= threshold}
    total_weight = sum(HUD_WEIGHTS[k] for k in core)
    matched_weight = sum(HUD_WEIGHTS[k] for k in core_matched)
    coverage = matched_weight / total_weight if total_weight else 0.0
    quality = (sum(HUD_WEIGHTS[k] * v for k, v in core_matched.items()) /
               matched_weight if matched_weight else 0.0)
    confidence = coverage * quality
    sufficient = len(core) >= 2 and len(core_matched) >= 2
    if not sufficient:
        reason = (f'{len(core_matched)}/{len(core)} indizi strutturali confermati: '
                  'servono 2 tra salute, cornice minimappa e slot inventario')
    else:
        reason = 'HUD strutturale confermato; elementi dinamici ignorati'
    return HudConsensus(
        present=sufficient, confidence=round(confidence, 3),
        votes=len(matched), configured=len(valid), matched=matched,
        core_votes=len(core_matched), core_configured=len(core), reason=reason,
    )
