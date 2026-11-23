"""Ignorante: simple random WASD, periodic jumps and harvesting.

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
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self):
        self._rng = random.Random(self.seed)

    def reset(self):
        self.index = 0
        self.pickaxe_equipped = False

    def next_action(self, *, harvest: bool, learned_hint: Action | None = None) -> Action:
        # learned_hint is intentionally ignored: this mode has no tactics or
        # learned navigation. Keep the argument for existing callers.
        if harvest and not self.pickaxe_equipped:
            self.pickaxe_equipped = True
            return Action('equip_pickaxe', .16, reason='IGNORANTE: seleziono il piccone')
        self.index += 1
        if self.index == 1:
            name, duration = 'patrol_forward', .65
        elif harvest and self.index % 5 == 0:
            name, duration = 'patrol_harvest', .5
        elif self.index % 8 == 0:
            name, duration = 'patrol_hop', .45
        else:
            name, duration = self._rng.choice((('patrol_forward', .65), ('strafe_left', .4),
                                               ('strafe_right', .4), ('reverse', .35)))
        return Action(name, duration, reason='IGNORANTE: movimento casuale, salti e piccone')
