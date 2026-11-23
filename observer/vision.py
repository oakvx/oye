"""Per-user calibrated template recognition and optical-flow movement feedback."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
import json
import cv2
import numpy as np
from .config import TEMPLATE_NAMES, load_rois
from .models import Match, Motion
from .hud import HUD_ANCHORS, HudConsensus, consensus, score_outline, prepare_outline

MENU_PRIORITY = ('riscatta', 'torna_lobby', 'continua', 'gioca_di_nuovo', 'gioca')


@dataclass(frozen=True)
class Reading:
    scene: str
    best: dict[str, Match]
    hud: HudConsensus | None = None
    hud_scores: dict[str, float] | None = None
    phase_hint: str | None = None
    phase_source: str = ''
    menu_checks: dict[str, str] | None = None  # present / absent / unknown; independently evaluated

    @property
    def menu(self) -> Match | None:
        return next((self.best[name] for name in MENU_PRIORITY if name in self.best), None)

    @property
    def prompt(self) -> bool:
        return 'interazione' in self.best

    @property
    def chest(self) -> bool:
        return 'cassa_visiva' in self.best


def recognize_aerial_text(text: str) -> str | None:
    """Tentative context only: NEVER authorizes gameplay actions.

    OCR is noisy and can describe a menu or a previous frame. It serves only
    to explain a currently unknown image, not to bypass HUD checks.
    """
    low = ' '.join(str(text or '').casefold().split())
    if re.search(r'\b(battle bus|bus della battaglia|salta dal bus|press (?:space|\w+) to jump|space to jump)\b', low):
        return 'battle_bus'
    if re.search(r'\b(deploy glider|open glider|apri il deltaplano|schiera il deltaplano|caduta libera)\b', low):
        return 'skydiving'
    return None


class Vision:
    def __init__(self, folder: Path, threshold: float = .89, profile_folder: Path | None = None):
        self.threshold = threshold
        self.rois = load_rois(folder / '_rois.json')
        meta_file = folder / '_resolution.json'
        self.base_resolution = json.loads(meta_file.read_text(encoding='utf-8')) if meta_file.exists() else {}
        if not isinstance(self.base_resolution, dict):
            raise ValueError('Metadati risoluzione non validi')
        self.templates: dict[str, np.ndarray] = {}
        self.invalid_templates: dict[str,str] = {}
        self._prepared_cache: dict[tuple[str, int, int], tuple[np.ndarray, object]] = {}
        for name in TEMPLATE_NAMES:
            file = folder / (name + '.png')
            if not file.is_file():
                continue
            image = cv2.imread(str(file), cv2.IMREAD_COLOR)
            if image is None or min(image.shape[:2]) < 14 or float(np.std(image)) < 7:
                override = (Path(profile_folder) / file.name if profile_folder is not None else None)
                if profile_folder is not None:
                    # Legacy corruption is isolated when a modern selected
                    # profile is present. Keep the diagnostic for the editor.
                    self.invalid_templates[name]='Template legacy illeggibile'
                    continue
                raise ValueError(f'Template troppo uniforme, piccolo o corrotto: {file.name}')
            self.templates[name] = image

        # A selected resolution can override templates WITHOUT touching the
        # previous calibration files. Other legacy anchors remain available.
        if profile_folder is not None and Path(profile_folder).is_dir():
            folder2=Path(profile_folder)
            self.rois.update(load_rois(folder2 / '_rois.json'))
            meta2=folder2 / '_resolution.json'
            if meta2.exists():
                new=json.loads(meta2.read_text(encoding='utf-8'))
                if not isinstance(new,dict):raise ValueError('Metadati profilo risoluzione non validi')
                self.base_resolution.update(new)
            for name in TEMPLATE_NAMES:
                file=folder2/(name+'.png')
                if not file.exists():continue
                img=cv2.imread(str(file),cv2.IMREAD_COLOR)
                if img is None or min(img.shape[:2])<14 or float(np.std(img))<7:
                    raise ValueError('Riferimento profilo corrotto: '+name)
                self.templates[name]=img

    @staticmethod
    def _crop(image: np.ndarray, roi):
        if roi is None:
            return image, 0, 0
        h, w = image.shape[:2]
        x1, y1, x2, y2 = roi
        px1, py1 = max(0, int(x1*w)), max(0, int(y1*h))
        px2, py2 = min(w, max(px1 + 1, int(x2*w))), min(h, max(py1 + 1, int(y2*h)))
        return image[py1:py2, px1:px2], px1, py1

    def _scaled_template(self, name: str, original: np.ndarray, frame: np.ndarray) -> np.ndarray | None:
        if name not in self.base_resolution:
            return original
        base = self.base_resolution[name]
        if not isinstance(base, list) or len(base) != 2 or min(base) < 100:
            raise ValueError(f'Metadati risoluzione non validi per {name}')
        sx, sy = frame.shape[1] / base[0], frame.shape[0] / base[1]
        if not (.65 < sx < 1.55 and .65 < sy < 1.55) or abs(sx - sy) > .04:
            return None
        if abs(sx - 1) <= .015 and abs(sy - 1) <= .015:
            return original
        w, h = max(14, int(original.shape[1] * sx)), max(14, int(original.shape[0] * sy))
        return cv2.resize(original, (w, h), interpolation=cv2.INTER_LINEAR)

    def recognize(self, bgra: np.ndarray, left: int = 0, top: int = 0) -> Reading:
        frame = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR) if bgra.shape[2] == 4 else bgra
        matches: dict[str, Match] = {}
        hud_scores: dict[str, float] = {}
        menu_checks: dict[str,str] = {}
        for name, original in self.templates.items():
            key = (name, frame.shape[1], frame.shape[0])
            cached = self._prepared_cache.get(key)
            if cached is None:
                template = self._scaled_template(name, original, frame)
                prepared = prepare_outline(template) if template is not None and name in HUD_ANCHORS else None
                # Typical games use one screen resolution for an entire session.
                # A dimension change invalidates old cached variants to bound RAM.
                if len(self._prepared_cache) > len(self.templates) * 2:
                    self._prepared_cache.clear()
                self._prepared_cache[key] = (template, prepared)
            else:
                template, prepared = cached
            if template is None:
                if name in HUD_ANCHORS:
                    hud_scores[name] = 0.0
                continue
            crop, dx, dy = self._crop(frame, self.rois.get(name))
            th, tw = template.shape[:2]
            if th > crop.shape[0] or tw > crop.shape[1]:
                if name in HUD_ANCHORS:
                    hud_scores[name] = 0.0
                continue
            if name in HUD_ANCHORS:
                # Compare only local interface outlines, not changing content.
                # Calibrated template locations are constrained by user ROIs.
                hud_scores[name] = score_outline(crop, template, prepared=prepared)
                continue
            correlations = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, point = cv2.minMaxLoc(correlations)
            if name == 'gioca':
                # A very marginal template score is uncertain, not proven absence.
                menu_checks[name] = ('present' if score >= self.threshold else
                                     'absent' if score < self.threshold - .10 else 'unknown')
            if score >= self.threshold:
                matches[name] = Match(name, float(score), (
                    left + dx + point[0] + tw // 2,
                    top + dy + point[1] + th // 2,
                ))
        hud = consensus(hud_scores) if hud_scores else None
        # Spectator controls often coexist with the match HUD AND 'return to lobby'.
        # They must win over any menu, otherwise we can leave while allies are alive.
        # A single specific, calibrated control is a stop signal (fail closed).
        if ('spettatore_prossimo' in matches or 'spettatore_precedente' in matches):
            scene = 'spectator'
        elif 'riscatta' in matches or 'continua' in matches or 'torna_lobby' in matches or 'gioca_di_nuovo' in matches:
            scene = 'results'
        elif 'eliminato' in matches:
            # Conservative fallback while the death overlay is on screen.
            scene = 'spectator'
        elif 'caricamento' in matches:
            scene = 'loading'
        elif hud is not None and hud.present:
            scene = 'match'
        elif hud is None and 'hud_in_partita' in matches:
            # Backward compatibility for existing v5 calibrations only.
            scene = 'match'
        elif 'gioca' in matches or 'lobby_indizio' in matches:
            scene = 'lobby'
        else:
            scene = 'unknown'
        # Stage hints are separate from scene authorization: a battle bus or
        # parachute prompt is gameplay context but NOT a verified ground HUD.
        # Never promote it to 'match' merely to permit movement.
        phase_hint = ('battle_bus' if 'bus_salto' in matches else
                      'skydiving' if 'discesa_apri' in matches else None)
        # The persistent health/minimap HUD can also appear in the air.
        # An explicitly calibrated aerial control must override 'ground'.
        # The phase hint stays available to the bounded bus routine.
        if scene == 'match' and phase_hint is not None:
            scene = 'unknown'
        return Reading(scene, matches, hud, hud_scores, phase_hint,
                       'template calibrato' if phase_hint else '', menu_checks)


def motion_between(before: np.ndarray | None, after: np.ndarray) -> Motion | None:
    """Median flow across *matched* world features; reliable only with enough tracks.

    The score can still be fooled by animated scenes, camera changes or large actors.
    Only feed screenshots captured immediately before and after a translation action.
    """
    if before is None or before.shape != after.shape:
        return None
    def world_gray(frame):
        h, w = frame.shape[:2]
        region = frame[int(h*.27):int(h*.77), int(w*.20):int(w*.80)]
        grey = cv2.cvtColor(region, cv2.COLOR_BGRA2GRAY) if region.shape[-1] == 4 else cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        return cv2.resize(grey, (320, 180), interpolation=cv2.INTER_AREA)
    old, new = world_gray(before), world_gray(after)
    corners = cv2.goodFeaturesToTrack(old, maxCorners=140, qualityLevel=.015, minDistance=8)
    if corners is None or len(corners) < 20:
        return Motion(0.0, 0, False)
    newpts, status, _ = cv2.calcOpticalFlowPyrLK(old, new, corners, None, winSize=(17,17), maxLevel=3)
    if newpts is None or status is None:
        return Motion(0.0, 0, False)
    ok = status.reshape(-1).astype(bool)
    if ok.sum() < 16:
        return Motion(0.0, int(ok.sum()), False)
    magnitude = np.linalg.norm((newpts - corners).reshape(-1,2)[ok], axis=1)
    finite = magnitude[np.isfinite(magnitude)]
    if len(finite) < 16:
        return Motion(0.0, len(finite), False)
    return Motion(float(np.median(finite)), len(finite), True)


def camera_turn_between(before: np.ndarray | None, after: np.ndarray) -> Motion | None:
    """Estimate camera yaw after an isolated turn, excluding the moving HUD.

    It is a world-image heuristic, not proof that mouse input was accepted.
    Mixed motion / animated UI / low texture yield an inconclusive reading.
    """
    if before is None or before.shape != after.shape:
        return None

    def prepare(frame: np.ndarray):
        h, w = frame.shape[:2]
        region = frame[int(h*.19):int(h*.78), int(w*.18):int(w*.82)]
        if region.size == 0:
            return None
        gray = cv2.cvtColor(region, cv2.COLOR_BGRA2GRAY) if region.shape[-1] == 4 else cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)

    old, new = prepare(before), prepare(after)
    if old is None or new is None:
        return None
    points = cv2.goodFeaturesToTrack(old, maxCorners=180, qualityLevel=.012, minDistance=7)
    if points is None or len(points) < 18:
        return Motion(0.0, 0, False)
    moved, status, _ = cv2.calcOpticalFlowPyrLK(old, new, points, None,
                                                  winSize=(19,19), maxLevel=3)
    if moved is None or status is None:
        return Motion(0.0, 0, False)
    dx = (moved - points).reshape(-1, 2)[status.reshape(-1).astype(bool), 0]
    dx = dx[np.isfinite(dx)]
    if len(dx) < 18:
        return Motion(0.0, len(dx), False)
    median = float(np.median(dx))
    # Coherent drift across the scene is much more likely to be a camera pan
    # than an actor moving through the image. Tiny drift is a valid low-motion
    # observation if there are enough tracked world features.
    coherence = max(float(np.mean(dx > .4)), float(np.mean(dx < -.4)))
    reliable = abs(median) <= .4 or coherence >= .65
    return Motion(abs(median), len(dx), reliable)
