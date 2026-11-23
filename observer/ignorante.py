"""Visual lifecycle for Ignorante. Decisions have no capture or input side effects."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Action, Match


FINAL_SIGNALS = frozenset({'posizione_finale', 'gioca_di_nuovo', 'torna_lobby'})
MAX_FRAME_AGE = 3.0
SIGNAL_LABELS = {
    'gioca': 'Gioca', 'annulla': 'Annulla', 'gioca_di_nuovo': 'Gioca di nuovo',
    'posizione_finale': 'Posizione finale', 'torna_lobby': 'Ritorna alla lobby',
}


@dataclass(frozen=True)
class MatchFrame:
    at: float
    valid: bool
    signals: frozenset[str] = frozenset()
    buttons: dict[str, Match] = field(default_factory=dict)
    playable: bool = False
    blocked: bool = False
    playable_at: float | None = None


@dataclass(frozen=True)
class CycleDecision:
    action: Action
    name: str | None = None
    move: bool = False


class IgnoranteController:
    LABELS = {
        'await_play': 'Attesa avvio',
        'joining': 'Attesa conferma',
        'searching': 'Ricerca partita',
        'playing': 'In partita',
        'ending': 'Fine partita',
        'requeue': 'Riavvio',
        'returning': 'Ritorno alla lobby',
        'lobby': 'Controllo ricompense',
        'error': 'Sospeso',
    }

    def __init__(self, now: float, *, matches_completed: int = 0):
        self.stage = 'await_play'
        self.matches_completed = matches_completed
        self.reason = 'Attendo il pulsante Gioca'
        self.last_signal = 'Nessun segnale'
        self.last_frame_at = float('-inf')
        self.last_playable_at = float('-inf')
        self.dispatch_at = float('-inf')
        self.deadline = now + 240
        self.attempts: dict[str, tuple[int, float]] = {}
        self.streaks: dict[str, tuple[int, float]] = {}
        self.previous_buttons: dict[str, Match] = {}
        self.match_open = False
        self.resume_required = False
        self.cancel_seen = False
        self.requeue_missing_since: float | None = None

    @property
    def label(self) -> str:
        return self.LABELS[self.stage]

    def _wait(self, reason: str) -> CycleDecision:
        self.reason = reason
        return CycleDecision(Action('wait', reason=reason))

    def _expired(self, now: float) -> bool:
        if self.stage not in ('playing', 'error') and now >= self.deadline:
            self.stage = 'error'
            self.reason = 'Ingresso in partita non confermato entro quattro minuti'
        return self.stage == 'error'

    def _clear_evidence(self) -> None:
        self.streaks.clear()
        self.previous_buttons.clear()
        self.requeue_missing_since = None

    def interrupt(self, now: float, reason: str) -> CycleDecision:
        """Discard observations while retaining the current transaction and count."""
        self.last_frame_at = max(now, self.last_frame_at)
        self._clear_evidence()
        self.resume_required = True
        return self._wait(self.reason if self._expired(now) else reason)

    def waiting(self, now: float) -> CycleDecision:
        self._expired(now)
        return self._wait(self.reason)

    def returned_to_lobby(self, at: float) -> None:
        self.stage = 'lobby'
        self.last_frame_at = max(at, self.last_frame_at)
        self._clear_evidence()

    def lobby_ready(self, now: float) -> None:
        self.stage = 'await_play'
        self.last_frame_at = max(now, self.last_frame_at)
        self.attempts.clear()
        self._clear_evidence()

    def on_clicked(self, name: str, now: float) -> None:
        self.last_frame_at = max(now, self.last_frame_at)
        self.dispatch_at = now
        count, _ = self.attempts.get(name, (0, now))
        self.attempts[name] = (count + 1, now)
        self._clear_evidence()
        if name == 'gioca':
            self.stage = 'joining'
        elif name == 'gioca_di_nuovo':
            self.cancel_seen = False
            self.stage = 'requeue'
        elif name == 'torna_lobby':
            self.stage = 'returning'

    def _offer(self, name: str, frame: MatchFrame, now: float) -> CycleDecision:
        count, sent_at = self.attempts.get(name, (0, float('-inf')))
        label = SIGNAL_LABELS[name]
        if count >= 3:
            return self._wait(f'{label}: tre clic senza conferma')
        if now - sent_at < 12:
            return self._wait(f'{label}: attendo conferma dopo il clic')
        if self.streaks.get(name, (0, 0))[0] < 2:
            return self._wait(f'{label}: attendo due letture del pulsante')
        self.reason = f'{label}: clic verificato {count + 1}/3'
        return CycleDecision(Action('menu_click', point=frame.buttons[name].center,
                                    reason=self.reason), name=name)

    def _enter(self) -> CycleDecision:
        self.stage = 'playing'
        self.match_open = True
        self.resume_required = False
        self.attempts.clear()
        self.reason = 'Ingresso confermato'
        return CycleDecision(Action('wait', reason=self.reason), move=True)

    def observe(self, frame: MatchFrame, *, now: float) -> CycleDecision:
        if self._expired(now):
            return self._wait(self.reason)
        if not frame.valid or not 0 <= now - frame.at <= MAX_FRAME_AGE:
            return self.interrupt(frame.at, 'Acquisizione o OCR non valido')
        if frame.at <= self.last_frame_at:
            return CycleDecision(Action('wait', reason=self.reason))
        if frame.at - self.last_frame_at > 4:
            self._clear_evidence()
            self.resume_required = True
        self.last_frame_at = frame.at
        end = frame.signals & FINAL_SIGNALS
        cancel = 'annulla' in frame.signals
        clear = not (end or cancel or frame.blocked or 'gioca' in frame.signals)
        if frame.signals:
            self.last_signal = ', '.join(SIGNAL_LABELS.get(name, name) for name in sorted(frame.signals))
        elif clear:
            self.last_signal = 'Scena giocabile' if frame.playable else 'Annulla assente'
        elif frame.blocked:
            self.last_signal = 'Schermata di attesa'
        keys = set()
        for name, button in frame.buttons.items():
            previous = self.previous_buttons.get(name)
            if previous and max(abs(a-b) for a, b in zip(previous.center, button.center)) > 48:
                self.streaks.pop(name, None)
            keys.add(name)
        self.previous_buttons = frame.buttons
        if end:
            keys.add('results')
        if cancel:
            keys.add('queue')
        if clear:
            keys.add('clear')
        confirmed_playable = self.streaks.get('playable', (0, frame.at))
        self.streaks = {key: (self.streaks[key][0]+1, self.streaks[key][1])
                        if key in self.streaks else (1, frame.at) for key in keys}
        if clear and frame.playable:
            at = frame.at if frame.playable_at is None else frame.playable_at
            if self.dispatch_at < at <= frame.at and now - at <= MAX_FRAME_AGE:
                if at > self.last_playable_at:
                    confirmed_playable = (confirmed_playable[0]+1, confirmed_playable[1])
                    self.last_playable_at = at
                self.streaks['playable'] = confirmed_playable

        if end:
            if self.match_open:
                self.resume_required = True
                if self.streaks['results'][0] < 2:
                    return self._wait('Risultati rilevati, attendo conferma')
                self.matches_completed += 1
                self.match_open = False
                self.stage = 'ending'
                self.deadline = now + 240
            if self.stage in ('ending', 'requeue', 'returning'):
                if self.stage == 'returning':
                    return self._offer('torna_lobby', frame, now)
                if 'gioca_di_nuovo' in end:
                    self.requeue_missing_since = None
                    return self._offer('gioca_di_nuovo', frame, now)
                if self.requeue_missing_since is None:
                    self.requeue_missing_since = frame.at
                if frame.at - self.requeue_missing_since >= 15:
                    return self._offer('torna_lobby', frame, now)
                return self._wait('Attendo Gioca di nuovo per 15 secondi')
            return self._wait('Risultati visibili, attendo la lobby')
        self.requeue_missing_since = None
        if self.stage == 'returning':
            if self.streaks.get('gioca', (0, 0))[0] >= 2:
                self.returned_to_lobby(frame.at)
            return self._wait('Verifico il ritorno alla lobby e le ricompense')
        if self.stage in ('joining', 'requeue', 'searching') and cancel:
            self.cancel_seen = True
            if self.streaks['queue'][0] >= 2:
                self.stage = 'searching'
            return self._wait('Annulla visibile: ricerca partita in corso')
        if (self.stage == 'requeue' and not self.cancel_seen and
                self.streaks.get('playable', (0, 0))[0] >= 2):
            return self._enter()
        count, since = self.streaks.get('clear', (0, frame.at))
        clear_confirmed = count >= 3 and frame.at - since >= 1
        if self.stage == 'searching' and clear_confirmed:
            return self._enter()
        if self.stage == 'playing':
            if not clear:
                self.resume_required = True
                return self._wait('Schermata di attesa, input sospesi')
            if self.resume_required and not clear_confirmed:
                return self._wait('Verifico nuovamente lo stato prima di muovermi')
            self.resume_required = False
            self.reason = 'Movimento in partita'
            return CycleDecision(Action('wait', reason=self.reason), move=True)
        if self.stage in ('await_play', 'joining') and not frame.blocked and not cancel:
            if 'gioca' in frame.buttons:
                return self._offer('gioca', frame, now)
        return self._wait({
            'await_play': 'Attendo il pulsante Gioca in basso a sinistra',
            'joining': 'Attendo Annulla dopo Gioca',
            'searching': 'Verifico la scomparsa di Annulla',
            'requeue': 'Attendo Annulla o una scena giocabile confermata',
            'ending': 'Attendo i pulsanti dei risultati',
            'lobby': 'Verifico le ricompense in lobby',
        }[self.stage])
