"""Bounded activity-level exploration planner (no AI inference or input APIs).

Plans short, varied sequences of permitted game actions. Variety is intended to
avoid a rigid patrol script, NOT to disguise automation or evade game detection.
The driver and runner remain responsible for focus, pause, stop and input safety.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
import random
from .models import Action

PROFILES = ('equilibrato', 'esploratore', 'raccoglitore')
ACTIVITY_LABELS = {
    'survey': 'Osserva i dintorni',
    'travel': 'Esplora una direzione',
    'weave': 'Corregge il percorso',
    'hop': 'Supera piccoli dislivelli',
    'harvest': 'Usa il piccone',
}


@dataclass
class ActivityPlanner:
    profile: str = 'equilibrato'
    harvest: bool = True
    seed: int | None = None
    plan: deque = field(default_factory=deque, init=False)
    last_activities: deque = field(default_factory=lambda: deque(maxlen=3), init=False)
    activity: str = 'Pronto a esplorare'
    decisions: int = 0
    finished_motifs: int = 0
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self):
        if self.profile not in PROFILES:
            raise ValueError('Profilo esplorazione non valido')
        self._rng = random.Random(self.seed)

    def reset(self):
        self.plan.clear()
        self.last_activities.clear()
        self.activity = 'Pronto a esplorare'
        self.decisions = 0
        self.finished_motifs = 0

    def _choose_activity(self) -> str:
        # Ensure the first moments demonstrate observation and harvesting rather
        # than an unexplained long run along the same heading.
        if self.finished_motifs == 0:
            return 'survey'
        if self.finished_motifs == 1 and self.harvest:
            return 'harvest'
        weights = {
            'equilibrato': {'travel': 35, 'survey': 22, 'weave': 20, 'hop': 10, 'harvest': 13},
            'esploratore': {'travel': 31, 'survey': 29, 'weave': 27, 'hop': 10, 'harvest': 3},
            'raccoglitore': {'travel': 25, 'survey': 14, 'weave': 14, 'hop': 7, 'harvest': 40},
        }[self.profile]
        candidates = [name for name in weights if (name != 'harvest' or self.harvest)]
        # Preserve activity variety while keeping all input inside a fixed whitelist.
        adjusted = [weights[name] * (.12 if name == self.last_activities[-1] else 1.0)
                    for name in candidates] if self.last_activities else [weights[name] for name in candidates]
        return self._rng.choices(candidates, weights=adjusted, k=1)[0]

    def _fill(self):
        activity = self._choose_activity()
        self.activity = ACTIVITY_LABELS[activity]
        self.last_activities.append(activity)
        self.finished_motifs += 1
        side = self._rng.choice(('left', 'right'))
        other = 'right' if side == 'left' else 'left'
        # Segments take 1–2 seconds and keep W down during camera/strafe actions.
        patterns = {
            'survey': [('patrol_look_' + side, 1.55), ('patrol_forward', 1.55),
                       ('patrol_look_' + other, 1.60)],
            'travel': [('patrol_forward', 1.90), ('patrol_forward', 1.75),
                       ('patrol_look_' + side, 1.45)],
            'weave': [('patrol_weave_' + side, 1.60), ('patrol_forward', 1.55),
                      ('patrol_weave_' + other, 1.60)],
            'hop': [('patrol_forward', 1.55), ('patrol_hop', 1.65),
                    ('patrol_forward', 1.50)],
            'harvest': [('patrol_harvest', 1.45), ('patrol_look_' + side, 1.55),
                        ('patrol_harvest', 1.20)],
        }
        self.plan.extend(patterns[activity])

    def next_action(self) -> Action:
        if not self.plan:
            self._fill()
        name, seconds = self.plan.popleft()
        self.decisions += 1
        return Action(name, seconds, reason=f'{self.activity} · {self.profile} · azione {self.decisions}')


ACTION_DESCRIPTIONS = {
    'patrol_forward': 'Avanza',
    'patrol_look_left': 'Avanza e guarda a sinistra',
    'patrol_look_right': 'Avanza e guarda a destra',
    'patrol_weave_left': 'Avanza e corregge verso sinistra',
    'patrol_weave_right': 'Avanza e corregge verso destra',
    'patrol_hop': 'Avanza e salta un piccolo dislivello',
    'patrol_harvest': 'Avanza e usa il piccone',
}


def activity_preview(profile: str = 'equilibrato', *, harvest: bool = True,
                     seed: int = 21, count: int = 20) -> str:
    """Deterministic OFFLINE preview. Never reads the screen or sends inputs."""
    if not 1 <= count <= 100:
        raise ValueError('Numero di azioni di anteprima non valido')
    planner = ActivityPlanner(profile=profile, harvest=harvest, seed=seed)
    lines = [f'ANTEPRIMA OFFLINE · {profile.upper()}',
             'Nessun tasto viene premuto. Non è una previsione del gioco.', '']
    previous = None
    for index in range(1, count + 1):
        action = planner.next_action()
        if planner.activity != previous:
            lines.extend([f'[{planner.activity}]'])
            previous = planner.activity
        label = ACTION_DESCRIPTIONS.get(action.name, action.name)
        lines.append(f'{index:02d}   {label:<43} {action.seconds:.2f} s')
    return '\n'.join(lines)
