"""Small, active, *novice-style* time-only movement routine.

Used only by the user's explicitly armed Ignorante mode.  This is a local
movement toy, NOT an anti-cheat bypass, opponent model, or proof the game
received any input.  The runner retains focus, pause, stop and known-menu
vetoes; the policy itself has no menu clicks and never waits for OCR.

A short predictable warmup makes first-run diagnosis reproducible.  Later
micro-activities vary: wandering, poking at scenery, turning around, jumping,
brief backtracking.  Bounded action counters guarantee activity even if all
screenshots/OCR are broken or the timer's initial jump window has elapsed.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import random

from .models import Action

_WARMUP = (
    ('patrol_forward', .65),
    ('patrol_harvest', .45),
    ('strafe_left', .38),
    ('patrol_look_right', .45),
    ('patrol_harvest', .50),
    ('strafe_right', .40),
    ('patrol_hop', .55),
    ('patrol_look_left', .45),
    ('patrol_forward', .65),
    ('patrol_harvest', .45),
)
_ALLOWED_HINTS = frozenset({
    'forward', 'strafe_left', 'strafe_right', 'forward_jump',
    'patrol_forward', 'patrol_hop', 'patrol_look_left', 'patrol_look_right',
})
_MOVING = frozenset({
    'forward', 'reverse', 'strafe_left', 'strafe_right', 'forward_jump',
    'patrol_forward', 'patrol_look_left', 'patrol_look_right',
    'patrol_harvest', 'patrol_weave_left', 'patrol_weave_right', 'patrol_hop',
})
_HARVESTING = frozenset({'patrol_harvest', 'forward_harvest'})
_JUMPING = frozenset({'patrol_hop', 'jump', 'forward_jump'})

# Each motif models a simple exploratory impulse, NOT a tactical plan.  They
# are short so the user can always pause or stop before the next decision.
_MOTIFS: dict[str, tuple[tuple[str, float], ...]] = {
    'curioso': (
        ('patrol_forward', .90), ('patrol_look_left', .60),
        ('patrol_harvest', .50), ('patrol_hop', .45), ('strafe_right', .55),
    ),
    'cambio strada': (
        ('patrol_look_right', .70), ('patrol_forward', .95),
        ('reverse', .30), ('strafe_left', .55), ('patrol_forward', .75),
    ),
    'provo i comandi': (
        ('patrol_hop', .50), ('patrol_harvest', .55),
        ('crouch', .25), ('patrol_forward', .95), ('patrol_look_left', .65),
    ),
    'esploro': (
        ('patrol_weave_left', .80), ('patrol_forward', 1.15),
        ('patrol_look_right', .70), ('patrol_harvest', .42), ('patrol_hop', .48),
    ),
    'mi guardo intorno': (
        ('turn_left', .16), ('patrol_forward', .85),
        ('patrol_harvest', .50), ('strafe_right', .58),
        ('patrol_look_right', .58), ('patrol_forward', .70),
    ),
    'curva e riparto': (
        ('turn_right', .15), ('patrol_forward', .80),
        ('secondary_click', .13), ('patrol_harvest', .45),
        ('patrol_weave_right', .70), ('patrol_hop', .45),
    ),
}


@dataclass
class QuickRoutine:
    # Seed can be provided by tests for deterministic behaviour. No seed is
    # required in the app; this varies *activity*, never tries to evade detection.
    seed: int | None = None
    index: int = 0
    pickaxe_equipped: bool = False
    activity: str = 'primi passi'
    since_movement: int = 0
    since_harvest: int = 0
    since_jump: int = 0
    _plan: deque[tuple[str, float]] = field(default_factory=deque, repr=False)
    _rng: random.Random = field(init=False, repr=False)
    _last_motif: str = ''

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def reset(self) -> None:
        self.index = 0
        self.pickaxe_equipped = False
        self.activity = 'primi passi'
        self.since_movement = self.since_harvest = self.since_jump = 0
        self._plan.clear()
        self._last_motif = ''

    def _choose_motif(self) -> None:
        choices = [name for name in _MOTIFS if name != self._last_motif]
        self.activity = self._rng.choice(choices)
        self._last_motif = self.activity
        direction_flip = self._rng.choice((True, False))
        for name, duration in _MOTIFS[self.activity]:
            # Flip left/right consistently for this short activity.
            if direction_flip:
                if name.endswith('_left'):
                    name = name[:-5] + '_right'
                elif name.endswith('_right'):
                    name = name[:-6] + '_left'
            duration = min(1.35, max(.12, round(duration * self._rng.uniform(.83, 1.17), 2)))
            self._plan.append((name, duration))

    def next_action(self, *, harvest: bool, learned_hint: Action | None = None) -> Action:
        if harvest and not self.pickaxe_equipped:
            self.pickaxe_equipped = True
            return Action('equip_pickaxe', .16, reason='IGNORANTE: seleziono il piccone')

        if self.index < len(_WARMUP):
            name, seconds = _WARMUP[self.index]
            self.activity = 'primi passi'
        else:
            if not self._plan:
                self._choose_motif()
            name, seconds = self._plan.popleft()

        # The early jump timer is handled by SingleMatchController.  These
        # counters also ensure jumps/pickaxe continue after its 120s window.
        if self.since_movement >= 2:
            name, seconds = 'patrol_forward', .72
        elif self.since_jump >= 10:
            name, seconds = 'patrol_hop', .45
        elif harvest and self.since_harvest >= 7:
            name, seconds = 'patrol_harvest', .50
        elif (learned_hint is not None and learned_hint.name in _ALLOWED_HINTS
              and self.index > 10 and self.index % 17 == 0):
            name = learned_hint.name
            seconds = min(.90, max(.25, learned_hint.seconds))
            self.activity = 'provo una mossa imparata'

        if not harvest and name == 'patrol_harvest':
            name = 'patrol_forward'

        self.index += 1
        self.since_movement = 0 if name in _MOVING else self.since_movement + 1
        self.since_harvest = 0 if name in _HARVESTING else self.since_harvest + 1
        self.since_jump = 0 if name in _JUMPING else self.since_jump + 1
        return Action(name, seconds,
                      reason=f'IGNORANTE · {self.activity}: mi muovo, guardo, salto e piccono')
