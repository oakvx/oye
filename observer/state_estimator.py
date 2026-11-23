"""Contextual phase display with hysteresis; never authorizes keyboard input.

The live input guard in Runner and the calibrated templates remain authoritative.
OCR hints may be displayed as hypotheses but never promote a state to confirmed.
"""
from __future__ import annotations
from dataclasses import dataclass

ALLOWED_NEXT = {
    'unknown': {'lobby', 'loading', 'battle_bus', 'skydiving', 'ground', 'spectator', 'results'},
    'lobby': {'loading', 'battle_bus', 'lobby'},
    'loading': {'lobby', 'battle_bus', 'skydiving', 'ground', 'loading'},
    'battle_bus': {'skydiving', 'ground', 'loading', 'lobby', 'battle_bus'},
    'skydiving': {'ground', 'spectator', 'results', 'skydiving'},
    'ground': {'spectator', 'results', 'loading', 'lobby', 'ground'},
    'spectator': {'results', 'lobby', 'ground', 'spectator'},
    'results': {'lobby', 'loading', 'results'},
}


@dataclass(frozen=True)
class PhaseEstimate:
    confirmed: str
    candidate: str
    candidate_frames: int
    detail: str
    anomaly: bool = False


class PhaseEstimator:
    """Stabilize labels for the monitor; unknown frames do not unlock controls."""
    def __init__(self, stable_frames: int = 2):
        if not 2 <= stable_frames <= 8:
            raise ValueError('Numero di conferme fase non valido')
        self.stable_frames = stable_frames
        self.confirmed = 'unknown'
        self.candidate = 'unknown'
        self.frames = 0

    def observe(self, scene: str, phase: str | None = None,
                source: str = '') -> PhaseEstimate:
        if scene == 'spectator':
            value = 'spectator'
        elif scene == 'results':
            value = 'results'
        elif scene == 'lobby':
            value = 'lobby'
        elif scene == 'loading':
            value = 'loading'
        elif phase in ('battle_bus', 'skydiving'):
            value = phase
        elif scene == 'match':
            value = 'ground'  # UI label only; NOT physical landing proof
        else:
            value = 'unknown'
        if value != self.candidate:
            self.candidate, self.frames = value, 1
        else:
            self.frames += 1
        if value == 'unknown':
            return PhaseEstimate(self.confirmed, value, self.frames,
                                 'Schermata incerta: nessuna autorizzazione aggiuntiva')
        if phase in ('battle_bus', 'skydiving') and source != 'template calibrato' and scene not in ('lobby', 'loading', 'spectator', 'results'):
            return PhaseEstimate(self.confirmed, value, self.frames,
                                 'Fase suggerita dall’OCR, non confermata')
        if self.frames < self.stable_frames:
            return PhaseEstimate(self.confirmed, value, self.frames,
                                 f'Conferma in corso {self.frames}/{self.stable_frames}')
        anomaly = value not in ALLOWED_NEXT.get(self.confirmed, set())
        if anomaly and self.confirmed != value:
            # Do not silently accept an improbable transition based on two frames.
            # Require extra evidence instead of imposing a hard-coded sequence.
            if self.frames < self.stable_frames + 2:
                return PhaseEstimate(self.confirmed, value, self.frames,
                                     'Passaggio insolito: attendo ulteriori fotogrammi', True)
        self.confirmed = value
        return PhaseEstimate(self.confirmed, value, self.frames,
                             'Fase confermata dal contesto visivo', anomaly)
