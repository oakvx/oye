"""Offline smoke-test of Oye's phase and simple-action logic.

No screenshots, input driver, Fortnite process or network needed. A scenario is
JSON containing an ordered ``frames`` array of:
 ``{at, scene, phase, phase_source, ground, spectator, motion_pixels, reliable}``.
The ground flag is explicit test evidence: replay NEVER infers it from OCR.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from .guided import SimpleMatchRoutine
from .state_estimator import PhaseEstimator


@dataclass(frozen=True)
class ReplayStep:
    at: float
    scene: str
    phase: str
    stage: str
    prediction: str
    action: str
    reason: str


def simulate(frames: list[dict], *, harvest: bool = True,
             min_fall: float = 20.) -> list[ReplayStep]:
    runner = SimpleMatchRoutine(harvest=harvest, min_fall=min_fall)
    tracker = PhaseEstimator()
    out: list[ReplayStep] = []
    previous = -float('inf')
    for row in frames:
        if not isinstance(row, dict):
            raise ValueError('Ogni frame deve essere un oggetto')
        now = float(row['at'])
        if now <= previous:
            raise ValueError('I tempi devono essere crescenti')
        previous = now
        scene = str(row.get('scene', 'unknown'))
        phase = row.get('phase')
        source = str(row.get('phase_source', ''))
        spectator = bool(row.get('spectator', False))
        estimate = tracker.observe(scene, phase, source)
        action = runner.choose(scene=scene, phase=phase, phase_source=source,
                               spectator=spectator, now=now,
                               authorized_ground=bool(row.get('ground', False)),
                               motion_pixels=row.get('motion_pixels'),
                               motion_reliable=bool(row.get('reliable', False)))
        out.append(ReplayStep(now, scene, str(phase or ''), runner.stage,
                              estimate.confirmed, action.name, action.reason))
    return out


def run_file(source: Path, destination: Path | None = None) -> list[ReplayStep]:
    data = json.loads(source.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('frames'), list):
        raise ValueError('Scenario: previsto oggetto con array frames')
    result = simulate(data['frames'], harvest=data.get('harvest', True),
                      min_fall=float(data.get('min_fall', 20.)))
    if destination:
        destination.write_text(json.dumps([asdict(x) for x in result],
                                          ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result
