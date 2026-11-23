"""Offline deterministic demonstration; never touches Fortnite or the keyboard."""
from __future__ import annotations
from .brain import Brain
from .models import Observation, Match, Motion


def run_demo() -> list[tuple[str, str, str]]:
    brain = Brain(seed=37, auto_requeue=True)
    examples = (
        [Observation(False, 'unfocused')] * 2 +
        [Observation(True, 'lobby', Match('gioca', .98, (600,380)))] * 7 +
        [Observation(True, 'loading')] * 3 +
        [Observation(True, 'match', motion=Motion(7,64,True))] * 5 +
        [Observation(True, 'match', motion=Motion(.02,64,True))] * 8 +
        [Observation(True, 'match', chest_audio=True)] +
        [Observation(True, 'match', motion=Motion(8,64,True))] * 10 +
        [Observation(True, 'spectator')] * 6 +
        [Observation(True, 'unknown')] * 2 +
        [Observation(True, 'match', motion=Motion(7,64,True))] * 8 +
        [Observation(True, 'spectator')] * 6 +
        [Observation(True, 'results', Match('continua', .98, (700,500)))] * 7 +
        [Observation(True, 'results', Match('torna_lobby', .98, (680,550)))] * 7 +
        [Observation(True, 'loading')] * 3 +
        [Observation(True, 'lobby', Match('gioca', .99, (155,660)))] * 7
    )
    return [(o.scene, a.name, a.reason) for i,o in enumerate(examples)
            for a in (brain.decide(o, i * 9.0),)]
