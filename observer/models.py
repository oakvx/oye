"""Pure immutable data types. No platform or game dependencies."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

Scene = Literal['match', 'spectator', 'lobby', 'results', 'loading', 'unknown', 'unfocused']

@dataclass(frozen=True)
class Match:
    name: str
    score: float
    center: tuple[int, int]

@dataclass(frozen=True)
class Motion:
    pixels: float
    tracked: int
    reliable: bool

@dataclass(frozen=True)
class Observation:
    focused: bool
    scene: Scene
    menu: Match | None = None
    motion: Motion | None = None
    chest_audio: bool = False
    chest_visual: bool = False
    interact_prompt: bool = False

@dataclass(frozen=True)
class Action:
    name: str
    seconds: float = 0.0
    point: tuple[int, int] | None = None
    reason: str = ''

    @property
    def is_motion(self) -> bool:
        return self.name in ('forward', 'forward_jump', 'forward_shoot', 'strafe_left',
                             'strafe_right', 'strafe_left_shoot', 'strafe_right_shoot', 'reverse',
                             'patrol_forward', 'patrol_look_left', 'patrol_look_right',
                             'patrol_harvest', 'patrol_weave_left', 'patrol_weave_right', 'patrol_hop')
