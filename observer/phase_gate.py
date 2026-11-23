"""Pure status gate for positive OCR death/reboot evidence and verified recovery.

The text 'IN ATTESA DEL RIAVVIO' is *not* a respawn signal. While it remains
visible the avatar cannot be assumed controllable. Resuming requires independent
visual gameplay evidence AND two later clean OCR samples.
"""
from __future__ import annotations
from dataclasses import dataclass
from .phase_ocr import PhaseOCRResult


@dataclass(frozen=True)
class GateOutcome:
    hold: bool
    recovered: bool
    state: str
    detail: str


class PhaseGate:
    def __init__(self):
        self.locked = False
        self.state = 'normal'
        self.last_positive_at = float('-inf')
        self.last_sample_at = float('-inf')
        self.clean_samples = 0
        self.scene_samples = 0
        self._last_scene_frame = float('-inf')
        self.last_error = ''

    def observe(self, result: PhaseOCRResult | None, *, scene: str, menu: str | None,
                at: float, allow_solo_return: bool = False) -> GateOutcome:
        recovered = False
        # Distinct fresh frames only; an old OCR result cannot authorize resume.
        if result is not None and result.at > self.last_sample_at and at - result.at <= 3.5:
            self.last_sample_at = result.at
            if result.status == 'ok':
                if result.cue in ('spectator', 'reboot_wait', 'rebooting'):
                    self.locked = True
                    self.state = result.cue
                    self.last_positive_at = result.at
                    self.clean_samples = 0
                    self.scene_samples = 0
                elif self.locked and result.at > self.last_positive_at:
                    self.clean_samples += 1
            else:
                self.last_error = result.status
                # An OCR error never counts as negative evidence.
        if at > self._last_scene_frame:
            self._last_scene_frame = at
            strong = (scene == 'match' or
                      (scene == 'lobby' and menu == 'gioca') or
                      (scene == 'results' and (menu in ('continua', 'riscatta') or
                                                (allow_solo_return and menu == 'torna_lobby'))))
            if strong:
                self.scene_samples += 1
            else:
                self.scene_samples = 0
        if self.locked and self.clean_samples >= 2 and self.scene_samples >= 6:
            self.locked = False
            self.state = 'normal'
            self.clean_samples = 0
            recovered = True
        reason = {
            'spectator': 'SPETTATORE OCR: resta in osservazione, nessun tasto',
            'reboot_wait': 'IN ATTESA DEL RIAVVIO OCR: attendi che torni controllabile',
            'rebooting': 'RIAVVIO OCR: attesa schermata di gioco confermata',
        }.get(self.state, 'Partita osservabile')
        return GateOutcome(self.locked, recovered, self.state, reason)
