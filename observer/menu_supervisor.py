"""One state-aware, bounded menu-click transaction gate for Oye's match lifecycle.

Pure decision/observation code: never captures images or sends input. It consumes
fresh user-calibrated detections and records a click *only after* the driver
reports that the command was sent. A missing OCR reading is not a click success.
"""
from __future__ import annotations
from dataclasses import dataclass
from .models import Action, Match


@dataclass(frozen=True)
class MenuProposal:
    name: str | None
    action: Action
    detail: str


@dataclass
class _Pending:
    name: str
    sent_at: float
    origin_scene: str
    attempts: int = 1
    transition_frames: int = 0
    last_transition_at: float = float('-inf')


class MenuSupervisor:
    """A single verified click/retry path for every match UI button.

    Priority is fixed per lifecycle stage, NOT global OCR word frequency.
    A click is never generated without a current-screen coordinate, three fresh
    target observations, the right stage, and no spectator/reward veto.
    """
    CONFIRM_FRAMES = 3
    COOLDOWN = 12.0
    MAX_ATTEMPTS = 3
    CLAIM_ATTEMPTS = 2
    MAX_FRAME_GAP = 6.0
    TARGETS = frozenset({'gioca', 'continua', 'torna_lobby', 'riscatta', 'gioca_di_nuovo'})

    def __init__(self):
        self.stable_name: str | None = None
        self.stable_count = 0
        self.last_frame_at = float('-inf')
        self.last_seen_at = float('-inf')
        self.last_evidence: dict[str, float] = {}
        self.pending: _Pending | None = None
        self.last_event = ''
        self.last_completed: str | None = None
        self.last_action_name: str | None = None

    @staticmethod
    def _targets(*, stage: str, scene: str, repeat: bool, reward_overlay: bool,
                 reward_scan_confirmed: bool, prefer_requeue: bool = False) -> tuple[str, ...]:
        if reward_overlay:
            return ('riscatta',) if stage in ('claim_rewards', 'post_lobby', 'await_play', 'ending') else ()
        if stage == 'claim_rewards' or stage == 'post_lobby':
            return ()
        if stage in ('await_play', 'joining') and scene in ('lobby', 'unknown'):
            return ('gioca',) if (stage == 'joining' or reward_scan_confirmed) else ()
        if stage == 'ending' and scene == 'results':
            if repeat and prefer_requeue:
                return ('gioca_di_nuovo', 'torna_lobby', 'continua')
            return ('torna_lobby', 'continua', 'gioca_di_nuovo') if repeat else ('torna_lobby', 'continua')
        return ()

    @staticmethod
    def _transition_verified(pending: _Pending, scene: str, matches: dict[str, Match],
                             *, reward_overlay: bool) -> bool:
        # A menu button merely going missing on an unknown frame is not proof.
        if pending.name == 'gioca':
            return scene == 'loading' or 'annulla' in matches
        if pending.name == 'riscatta':
            return scene == 'lobby' and 'gioca' in matches and not reward_overlay
        if pending.name == 'gioca_di_nuovo':
            return scene in ('loading', 'match') or 'annulla' in matches
        if pending.name in ('torna_lobby', 'continua'):
            return ((scene == 'lobby' and 'gioca' in matches) or
                    (scene == 'results' and
                     any(name in matches for name in ('riscatta','continua','torna_lobby')) and
                     pending.name not in matches))
        return False

    def offer(self, *, now: float, frame_at: float, stage: str, scene: str,
              matches: dict[str, Match], repeat: bool, reward_overlay: bool = False,
              reward_scan_confirmed: bool = True, locked: bool = False,
              spectator: bool = False,
              evidence_times: dict[str, float] | None = None,
              prefer_requeue: bool = False) -> MenuProposal:
        wait = lambda message: MenuProposal(None, Action('wait', reason=message), message)
        if locked or spectator or scene == 'spectator':
            self.stable_count = 0
            self.stable_name = None
            return wait('Menu: fase spettatore/riavvio; nessun clic')
        if frame_at <= self.last_frame_at:
            return wait('Menu: immagine precedente, non conta come nuova conferma')
        if now - frame_at > 4.0 or frame_at > now + 1.0:
            return wait('Menu: immagine scaduta, nessun clic')
        if frame_at - self.last_seen_at > self.MAX_FRAME_GAP:
            self.stable_name = None
            self.stable_count = 0
        self.last_frame_at = self.last_seen_at = frame_at
        self.last_completed = None
        # Once the lifecycle itself has confirmed a new phase, an old CTA
        # cannot block a later result/reward/lobby action indefinitely.
        if self.pending is not None:
            old = self.pending.name
            phase_proves_exit = (
                (old in ('gioca', 'gioca_di_nuovo') and stage in ('jumping','exploring')) or
                (old in ('torna_lobby','continua') and stage in ('post_lobby','claim_rewards')) or
                (old == 'riscatta' and stage == 'await_play' and not reward_overlay)
            )
            if phase_proves_exit:
                self.last_event = f'{old}: fase successiva confermata dal controller'
                self.pending = None
                self.stable_name = None
                self.stable_count = 0

        if self.pending is not None:
            previous = self.pending
            if self._transition_verified(previous, scene, matches, reward_overlay=reward_overlay):
                if frame_at > previous.last_transition_at:
                    previous.transition_frames += 1
                    previous.last_transition_at = frame_at
                if previous.transition_frames >= 2:
                    self.last_completed = previous.name
                    self.last_event = f'{previous.name}: cambio schermata confermato dopo il clic'
                    self.pending = None
                    self.stable_name = None
                    self.stable_count = 0
                    return wait(self.last_event)
                return wait(f'{previous.name}: verifica nuova schermata {previous.transition_frames}/2')
            previous.transition_frames = 0
            # Unknown/absent is not authorization for a retry or a success.
            if previous.name not in matches:
                self.stable_name = None
                self.stable_count = 0
                return wait(f'{previous.name}: attendo riscontro visivo, nessun clic alla cieca')
            limit = self.CLAIM_ATTEMPTS if previous.name=='riscatta' else self.MAX_ATTEMPTS
            if previous.attempts >= limit and now-previous.sent_at >= self.COOLDOWN:
                return wait(f'{previous.name}: {limit} clic senza conferma; mostrami il pulsante')
            # Retry may be proposed below only after fresh stable observations.
            allowed = (previous.name,)
        else:
            allowed = self._targets(stage=stage, scene=scene, repeat=repeat,
                reward_overlay=reward_overlay, reward_scan_confirmed=reward_scan_confirmed,
                prefer_requeue=prefer_requeue)
        name = next((n for n in allowed if n in matches), None)
        if name is None:
            self.stable_name = None
            self.stable_count = 0
            return wait('Menu: nessun pulsante valido per questa fase')
        match = matches[name]
        evidence_at = (evidence_times or {}).get(name, frame_at)
        # A 2-second-old OCR result copied into five newer captured frames is
        # still ONE reading, not five independent confirmations. Templates
        # matched on the actual frame use its capture timestamp by default.
        if not (0 <= now-evidence_at <= 4.0):
            return wait(f'{name}: OCR precedente non utilizzabile per il clic')
        if evidence_at <= self.last_evidence.get(name, float('-inf')):
            return wait(f'{name}: attendo nuova acquisizione OCR per il clic')
        self.last_evidence[name] = evidence_at
        if self.pending is not None:
            if self.stable_name == name:
                self.stable_count += 1
            else:
                self.stable_name,self.stable_count = name,1
            if now-self.pending.sent_at < self.COOLDOWN:
                return wait(f'{name}: clic inviato; attendo risposta ({self.COOLDOWN-(now-self.pending.sent_at):.0f}s)')
        if (match.center is None or len(match.center) != 2 or
            any(type(x) is not int or x < 0 for x in match.center)):
            return wait(f'{name}: coordinate non valide, insegnami il pulsante')
        if self.pending is None:
            if self.stable_name == name:
                self.stable_count += 1
            else:
                self.stable_name, self.stable_count = name, 1
        if self.stable_count < self.CONFIRM_FRAMES:
            return wait(f'{name}: confermo pulsante {self.stable_count}/{self.CONFIRM_FRAMES}')
        attempt = (self.pending.attempts + 1) if self.pending else 1
        limit = self.CLAIM_ATTEMPTS if name=='riscatta' else self.MAX_ATTEMPTS
        action = Action('menu_click', point=match.center,
            reason=f'{name}: clic verificato {attempt}/{limit}; controllo la schermata successiva')
        return MenuProposal(name, action, action.reason)

    def on_executed(self, name: str, action: Action, *, now: float, scene: str) -> None:
        if action.name != 'menu_click' or action.point is None or name not in self.TARGETS:
            return
        if self.pending and self.pending.name == name:
            self.pending.attempts += 1
            self.pending.sent_at = now
            self.pending.transition_frames = 0
        else:
            self.pending = _Pending(name=name, sent_at=now, origin_scene=scene)
        self.last_action_name = name
        limit = self.CLAIM_ATTEMPTS if name=='riscatta' else self.MAX_ATTEMPTS
        self.last_event = f'{name}: clic inviato ({self.pending.attempts}/{limit}); esito nel gioco non ancora confermato'
        self.stable_name = None
        self.stable_count = 0

    def reset(self) -> None:
        self.pending = None
        self.stable_name = None
        self.stable_count = 0
        self.last_completed = None
