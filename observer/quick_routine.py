"""Ignorante: sustained forward travel, periodic jumps and harvesting.

Randomness selects directions for activity, not to evade detection. This
routine does not detect menus or send input: Runner retains all safety gates.
"""
from dataclasses import dataclass, field
import random

from .models import Action


@dataclass
class QuickRoutine:
    seed: int | None = None
    index: int = 0
    pickaxe_equipped: bool = False
    _direction: str = field(default='patrol_forward', init=False)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self):
        self._rng = random.Random(self.seed)

    def reset(self):
        self.index = 0
        self.pickaxe_equipped = False
        self._direction = 'patrol_forward'

    def next_action(self, *, harvest: bool, learned_hint: Action | None = None) -> Action:
        # learned_hint is intentionally ignored: this mode has no tactics or
        # learned navigation. Keep the argument for existing callers.
        if harvest and not self.pickaxe_equipped:
            self.pickaxe_equipped = True
            return Action('equip_pickaxe', .16, reason='IGNORANTE: seleziono il piccone')
        self.index += 1
        if self.index == 1:
            name, duration = 'patrol_forward', 1.4
        elif harvest and self.index % 5 == 0:
            name, duration = 'patrol_harvest', 1.0
        elif self.index % 8 == 0:
            name, duration = 'patrol_hop', 1.2
        else:
            # Keep forward momentum. A straight segment separates opposite
            # lateral directions; reverse remains available to recovery policies.
            choices = ['patrol_forward', 'patrol_forward', 'strafe_left', 'strafe_right']
            opposite = {'strafe_left': 'strafe_right', 'strafe_right': 'strafe_left'}
            choices = [choice for choice in choices if choice != opposite.get(self._direction)]
            name = self._rng.choice(choices)
            duration = 1.4 if name == 'patrol_forward' else 1.0
        self._direction = name
        return Action(name, duration, reason='IGNORANTE: movimento fluido, salti e piccone')
