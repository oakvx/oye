"""Visual feedback for short locomotion actions.

Movement is *suggested* by screen-space optical flow; this cannot establish
that a particular key was accepted by a game (camera motion/animations exist).
This component never issues input and never weakens scene/focus safeguards.
"""
from __future__ import annotations
from dataclasses import dataclass
from .models import Motion

# Looking around and pickaxe swings are deliberately excluded: these create
# optical flow without demonstrating a translation of the player.
TRANSLATION_PROBES = frozenset({'forward', 'reverse', 'strafe_left', 'strafe_right',
                                'forward_jump', 'patrol_forward'})


@dataclass(frozen=True)
class Feedback:
    action: str
    status: str      # moved_visual | low_visual | inconclusive
    detail: str
    pixels: float | None = None
    reliable: bool = False


def evaluate(action: str, motion: Motion | None, *, threshold: float = .55) -> Feedback:
    if action not in TRANSLATION_PROBES:
        return Feedback(action, 'inconclusive', 'Azione non adatta a misurare lo spostamento')
    if motion is None or not motion.reliable:
        return Feedback(action, 'inconclusive', 'Punti visivi insufficienti; nessuna conclusione')
    px = float(motion.pixels)
    if px < threshold:
        return Feedback(action, 'low_visual',
                        f'Variazione visiva bassa ({px:.2f} px); possibile ostacolo o input non recepito',
                        px, True)
    return Feedback(action, 'moved_visual',
                    f'Variazione visiva rilevata ({px:.2f} px); non prova la ricezione di W',
                    px, True)
