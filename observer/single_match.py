"""Single, observable Fortnite match workflow.

Verified PLAY, one match at a time, bounded jump/movement phase, results,
reward claim, and confirmed lobby return. Optional repetition only after the lobby.  No menu coordinates are invented: a separate vision
system and Brain must confirm button targets.  OCR overlays veto inputs.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import random
from .guided import GuidedExplorer
from .models import Action
from .cycle_db import CyclePolicy
from .quick_routine import QuickRoutine


@dataclass
class SingleMatchController:
    jump_window_seconds: float = 120.0
    jump_period_seconds: float = 1.8
    harvest: bool = True
    smooth: bool = True
    profile: str = 'equilibrato'
    repeat: bool = False
    quick_start: bool = False
    quick_delay_seconds: float = 20.0
    quick_wait_play_exit: bool = False  # v38 dashboard mode: never leave visible PLAY
    quick_loading_seconds: float = 120.0
    quick_jump_min_seconds: float = 120.0
    quick_jump_max_seconds: float = 150.0
    quick_play_seen: bool = False
    quick_absent_frames: int = 0
    quick_last_absence_at: float = float('-inf')
    quick_play_streak: int = 0
    quick_play_click_attempts: int = 0
    quick_play_last_click_at: float | None = None
    quick_play_click_cooldown: float = 12.0
    quick_play_max_clicks: int = 3
    quick_departed: bool = False
    quick_jump_target_seconds: float = 0.0
    quick_manual_arm: bool = False
    quick_wait_elapsed: float = 0.0
    lobby_cooldown_seconds: float = 6.0
    matches_completed: int = 0
    next_play_at: float = 0.0
    recovery_limit: int = 3
    recovery_threshold: float = .55
    stage: str = 'await_play'
    policy: CyclePolicy = field(default_factory=CyclePolicy, repr=False)
    post_lobby_at: float | None = None
    claim_clicked_at: float | None = None
    claim_attempts: int = 0
    reward_streak: int = 0
    elapsed: float = 0.0
    last_tick: float | None = None
    last_jump_elapsed: float = -1e9
    last_secondary_elapsed: float = 0.0
    play_clicked_at: float | None = None
    results_at: float | None = None
    stable_match: int = 0
    stable_bus: int = 0
    stable_lobby: int = 0
    stable_results: int = 0
    last_frame_at: float = -1e9
    play_disappeared: bool = False
    quick_visual_match_seen: bool = False
    has_entered: bool = False
    last_error: str = ''
    recovery_count: int = 0
    explorer: GuidedExplorer = field(init=False, repr=False)
    quick_routine: QuickRoutine = field(init=False, repr=False)

    def __post_init__(self):
        if not 30 <= self.jump_window_seconds <= 180:
            raise ValueError('Finestra iniziale salti non valida')
        if not 10 <= self.quick_delay_seconds <= 45:
            raise ValueError('Attesa avvio rapido non valida')
        if (self.quick_wait_play_exit and not (5 <= self.quick_loading_seconds <= 360 and
                30 <= self.quick_jump_min_seconds <= self.quick_jump_max_seconds <= 300)):
            raise ValueError('Tempi Ignorante v38 non validi')
        if not .5 <= self.jump_period_seconds <= 5:
            raise ValueError('Intervallo salti non valido')
        self._reset_explorer()
        self.quick_routine = QuickRoutine()

    def arm_quick(self, now: float) -> None:
        """Explicit dashboard authorization; starts a timer even without PLAY/HUD OCR.

        Never sends a key on its own. Known lobby/results/spectator screens
        still veto movement at decision time. This is the user-selected blind
        timing mode, not a claim that the match has started.
        """
        if not self.quick_start or self.stage != 'await_play':
            return
        self.stage = 'joining'
        self.quick_manual_arm = True
        self.play_clicked_at = now
        self.quick_wait_elapsed = 0.0
        self.last_tick = now
        self.play_disappeared = False
        self.quick_play_seen = False
        self.quick_departed = False
        self.quick_absent_frames = 0
        self.quick_last_absence_at = float('-inf')
        self.quick_play_streak = 0
        self.quick_play_click_attempts = 0
        self.quick_play_last_click_at = None
        if self.quick_wait_play_exit:
            self.play_clicked_at = None

    def _advance_quick_clock(self, now: float) -> None:
        """Foreground/unpaused elapsed time, regardless of capture and OCR cadence."""
        if self.last_tick is not None:
            self.quick_wait_elapsed += max(0., now - self.last_tick)
        self.last_tick = now

    def _begin_quick_activity(self, now: float) -> Action:
        """The user's timed command is enough; it is NOT proof of a real match."""
        self.stage = 'jumping'
        self.elapsed = 0.0
        self.last_tick = now
        self.has_entered = True  # estimated for this manual mode, never an OCR assertion
        self.last_jump_elapsed = 0.0
        self.last_secondary_elapsed = 0.0
        self.quick_routine.reset()
        if self.quick_wait_play_exit:
            self.quick_jump_target_seconds = random.SystemRandom().uniform(
                self.quick_jump_min_seconds, self.quick_jump_max_seconds)
            return Action('jump', .16, reason=f'IGNORANTE: caricamento concluso; solo Space per {self.quick_jump_target_seconds:.0f} s')
        return Action('jump', .16, reason='IGNORANTE: timer scaduto, Space senza HUD, OCR o screenshot')

    def _quick_join_gate(self, *, now: float, visible: bool | None,
                         candidate: Action, menu: str | None = None,
                         play_point: tuple[int, int] | None = None,
                         scene: str = 'unknown',
                         click_authorized: bool = True,
                         evidence_at: float | None = None) -> Action:
        """Fortnite lobby: detect PLAY, click it, verify it disappears, then wait.

        The controller does not rely on Brain's menu-action proposal to ever
        click a confirmed PLAY target. Coordinates MUST come from the current
        vision reading, never an invented/fixed pixel. A click is counted only
        by on_executed() after the driver accepts it. No blind click retries.
        """
        if visible is True:
            self.quick_play_seen = True
            self.quick_play_streak += 1
            self.quick_absent_frames = 0
            if self.quick_departed:
                self.quick_departed = False
                self.play_disappeared = False
                self.quick_wait_elapsed = 0.0
            if self.quick_play_streak < 3:
                return self._wait(f'GIOCA visibile: confermo posizione {self.quick_play_streak}/3')
            if menu != 'gioca' or scene != 'lobby':
                return self._wait('GIOCA visibile, ma pulsante/contesto non confermato: attendo')
            target = play_point
            if target is None and candidate.name == 'menu_click':
                target = candidate.point
            if target is None or min(target) < 0:
                return self._wait('GIOCA riconosciuto, ma senza coordinate cliccabili: insegnami il pulsante GIOCA')
            if self.quick_play_click_attempts >= self.quick_play_max_clicks:
                return self._wait('GIOCA ancora visibile dopo 3 clic inviati: verifica il focus di Fortnite e la posizione insegnata')
            if not click_authorized:
                return self._wait('GIOCA visibile: attendo conferma clic dal supervisore dei menu')
            if (self.quick_play_last_click_at is not None and
                    now - self.quick_play_last_click_at < self.quick_play_click_cooldown):
                remaining = self.quick_play_click_cooldown - (now-self.quick_play_last_click_at)
                return self._wait(f'GIOCA cliccato: attendo ANNULLA o uscita lobby ({remaining:.0f} s prima di un eventuale retry)')
            return Action('menu_click', point=target,
                reason=f'GIOCA confermato: clic lobby {self.quick_play_click_attempts + 1}/{self.quick_play_max_clicks}, poi verifico il cambio schermata')
        # A missing/failed/stale OCR reading is NEVER verified absence.
        if visible is None:
            self.quick_play_streak = 0
            self.quick_absent_frames = 0
            if not self.quick_play_seen:
                return self._wait('Non vedo GIOCA: attendo prima rilevazione valida della lobby')
            if not self.quick_departed:
                return self._wait('GIOCA non verificabile: attendo scansione valida senza inviare movimenti')
        else:
            self.quick_play_streak = 0
        if not self.quick_play_seen:
            return self._wait('Non ho ancora visto GIOCA: fammi vedere il pulsante in lobby')
        if not self.quick_departed:
            if evidence_at is not None:
                if evidence_at <= self.quick_last_absence_at:
                    return self._wait(f'GIOCA scomparso? Attendo una nuova lettura ({self.quick_absent_frames}/3)')
                self.quick_last_absence_at = evidence_at
            self.quick_absent_frames += 1
            if self.quick_absent_frames < 3:
                return self._wait(f'GIOCA scomparso? Verifica {self.quick_absent_frames}/3, nessun movimento')
            self.quick_departed = self.play_disappeared = True
            self.quick_wait_elapsed = 0.0
            self.last_tick = now
            return self._wait(f'GIOCA scomparso in 3 fotogrammi: attendo {self.quick_loading_seconds:.0f} s')
        self._advance_quick_clock(now)
        if self.quick_wait_elapsed >= self.quick_loading_seconds:
            if scene in ('lobby', 'loading'):
                return self._wait('Lobby o caricamento ancora visibili: nessun tasto')
            return self._begin_quick_activity(now)
        return self._wait(f'Caricamento stimato: {max(0.0,self.quick_loading_seconds-self.quick_wait_elapsed):.0f} s, niente input')

    def _quick_jump_only(self, now: float) -> Action:
        """During the first 2–2.5 minutes only Space, then WASD routine."""
        if self.last_tick is not None:
            self.elapsed += max(0., now-self.last_tick)
        self.last_tick = now
        if self.elapsed >= self.quick_jump_target_seconds:
            self.stage = 'exploring'
            self.last_secondary_elapsed = self.elapsed
            self.quick_routine.reset()
            return self.quick_routine.next_action(harvest=self.harvest)
        if self.elapsed - self.last_jump_elapsed >= self.jump_period_seconds:
            self.last_jump_elapsed = self.elapsed
            return Action('jump', .16, reason=f'Spazio periodico: {self.elapsed:.0f}/{self.quick_jump_target_seconds:.0f} s')
        return self._wait(f'Solo salti: {max(0.,self.quick_jump_target_seconds-self.elapsed):.0f} s restanti')

    def quick_without_frame(self, now: float) -> Action:
        """Independent fallback when capture/vision fails after manual authorization.

        It never clicks menu coordinates or fabricates OCR. Focus, Stop and Pause
        are still enforced by Runner/Driver before this method is called.
        """
        if not self.quick_start:
            return self._wait('Fallback riservato alla modalità Ignorante')
        if self.quick_wait_play_exit and self.stage == 'joining':
            if not self.quick_departed:
                return self._wait('Gioca non ancora scomparso: cattura necessaria, nessun tasto')
            self._advance_quick_clock(now)
            if self.quick_wait_elapsed >= self.quick_loading_seconds:
                return self._begin_quick_activity(now)
            return self._wait(f'Attesa caricamento: {self.quick_loading_seconds-self.quick_wait_elapsed:.0f} s')
        if self.stage == 'joining' and self.play_clicked_at is not None:
            self._advance_quick_clock(now)
            if self.quick_wait_elapsed >= self.quick_delay_seconds:
                return self._begin_quick_activity(now)
            return self._wait(f'IGNORANTE: countdown {max(0., self.quick_delay_seconds-self.quick_wait_elapsed):.0f} s, immagini non necessarie')
        if self.stage not in ('jumping', 'exploring'):
            return self._wait('IGNORANTE: attesa schermata finale; non invento clic')
        if self.last_tick is not None:
            self.elapsed += max(0., min(3.5, now - self.last_tick))
        self.last_tick = now
        if self.quick_wait_play_exit and self.stage == 'jumping':
            return self._quick_jump_only(now)
        if self.stage == 'jumping' and self.elapsed >= self.jump_window_seconds:
            self.stage = 'exploring'
        if self.stage == 'jumping' and self.elapsed - self.last_jump_elapsed >= self.jump_period_seconds:
            self.last_jump_elapsed = self.elapsed
            return Action('jump', .16, reason='IGNORANTE: Space temporizzato senza immagini')
        if self.elapsed >= 14 and self.elapsed - self.last_secondary_elapsed >= 26:
            self.last_secondary_elapsed = self.elapsed
            return Action('secondary_click', .12, reason='IGNORANTE: clic destro breve')
        return self.quick_routine.next_action(harvest=self.harvest)

    def _reset_explorer(self):
        self.explorer = GuidedExplorer(harvest=self.harvest, smooth=self.smooth,
            profile=self.profile, max_recoveries=self.recovery_limit,
            motion_threshold=self.recovery_threshold)

    def _complete_match(self, now: float, message: str) -> Action:
        """A lobby is NOT the end of the workflow: rewards may appear AFTER it."""
        self.matches_completed += 1
        source = 'ending' if self.stage == 'ending' else 'in_match'
        event = 'lobby_confirmed' if source == 'ending' else 'direct_lobby'
        self.stage = self.policy.for_event(source, event).next_stage
        self.post_lobby_at = now
        self.claim_clicked_at = None
        self.claim_attempts = 0
        self.reward_streak = 0
        self.suspend_clock()
        self.stable_lobby = 0
        return self._wait(message + '; controllo ricompense prima di Gioca')

    def _ready_after_rewards(self, now: float, message: str) -> Action:
        if not self.repeat:
            self.stage = 'done'
            return self._wait(message + '; sessione conclusa')
        source = self.stage
        event = 'lobby_after_claim' if source == 'claim_rewards' else 'lobby_without_rewards'
        self.stage = self.policy.for_event(source,event).next_stage
        self.next_play_at = now + self.lobby_cooldown_seconds
        self.play_clicked_at = None
        self.results_at = None
        self.post_lobby_at = None
        self.claim_clicked_at = None
        self.claim_attempts = 0
        self.reward_streak = 0
        self.elapsed = 0.0
        self.last_tick = None
        self.last_jump_elapsed = -1e9
        self.last_secondary_elapsed = 0.0
        self.stable_match = self.stable_bus = self.stable_lobby = self.stable_results = 0
        self.play_disappeared = False
        self.has_entered = False
        self._reset_explorer()
        self.quick_routine.reset()
        self.quick_visual_match_seen = False
        self.quick_manual_arm = False
        self.quick_wait_elapsed = 0.0
        self.quick_play_seen = False
        self.quick_departed = False
        self.quick_absent_frames = 0
        self.quick_last_absence_at = float('-inf')
        self.quick_play_streak = 0
        self.quick_play_click_attempts = 0
        self.quick_play_last_click_at = None
        return self._wait(message + '; prossima partita solo dopo nuova verifica della lobby')

    def _handle_rewards(self, *, now: float, scene: str, menu: str | None,
                        candidate: Action, locked: bool, spectator: bool,
                        reward_overlay: bool, reward_scan_confirmed: bool) -> Action | None:
        """Higher-priority reward workflow, including rewards that appear after lobby."""
        if self.stage not in ('await_play', 'post_lobby', 'claim_rewards', 'jumping', 'exploring', 'ending'):
            return None
        safe = not locked and not spectator
        # Results/rewards may be drawn over the current match without a normal
        # RESULTS transition. The title has already been verified by two distinct
        # OCR captures by Runner. Never interpret a shop CTA without that title.
        if (self.stage in ('jumping', 'exploring', 'ending') and safe and
                reward_overlay and self.has_entered and scene in ('results', 'lobby', 'unknown')):
            if self.stage == 'ending':
                # Preserve the existing results -> post-lobby -> reward flow;
                # the title can overlay the lobby without PLAY ever appearing.
                return self._complete_match(now, 'Risultati seguiti direttamente dalle ricompense')
            # A rewarded end screen can replace the match before the generic
            # RESULTS classifier sees it. Count once and enter the claim phase.
            self.matches_completed += 1
            self.stage = 'claim_rewards'
            self.post_lobby_at = now
            self.claim_clicked_at = None
            self.claim_attempts = 0
            self.reward_streak = 2
            self.stable_lobby = 0
            self.suspend_clock()
            return self._wait('Ricompense rilevate durante la partita: movimento sospeso; cerco RISCATTA')
        if self.stage in ('jumping', 'exploring', 'ending'):
            return None
        # Never interpret an EQUIPAGGIA button as RISCATTA. We only allow
        # reward actions when an actual reward title was confirmed via OCR.
        reward_visible = bool(safe and reward_overlay and scene in ('results','lobby','unknown'))
        if reward_visible:
            self.reward_streak += 1
        else:
            self.reward_streak = 0
        if self.stage != 'claim_rewards':
            key = 'post_lobby' if self.stage == 'post_lobby' else 'await_play'
            rule = self.policy.for_event(key,'reward_visible')
            if self.reward_streak >= rule.confirmations:
                self.stage = rule.next_stage
                self.post_lobby_at = now
                self.stable_lobby = 0
                self.claim_clicked_at = None
                return self._wait('Ricompensa ottenuta: cerco RISCATTA, non EQUIPAGGIA')
        if self.stage == 'claim_rewards':
            if reward_visible:
                self.stable_lobby = 0
                since_click = (now - self.claim_clicked_at if self.claim_clicked_at is not None else None)
                # At most two confirmed clicks, separated by 12 seconds. An
                # ineffective click never turns into blind repeated clicking.
                can_click = (self.claim_attempts < 2 and
                             (since_click is None or since_click >= 12))
                if menu == 'riscatta' and candidate.name == 'menu_click' and can_click:
                    needed = self.policy.for_event('claim_rewards','riscatta_visible').confirmations
                    if self.reward_streak >= needed:
                        return candidate
                if self.claim_attempts >= 2 and since_click is not None and since_click >= 12:
                    return self._wait('RISCATTA non ha chiuso la schermata: mostrami il pulsante corretto')
                if self.claim_clicked_at is not None:
                    return self._wait('RISCATTA premuto: verifico la chiusura; massimo due tentativi')
                return self._wait('Ricompensa riconosciuta: mostrami il pulsante RISCATTA')
            if safe and scene == 'lobby' and menu == 'gioca':
                self.stable_lobby += 1
                rule = self.policy.for_event('claim_rewards', 'lobby_after_claim')
                if self.stable_lobby >= rule.confirmations and (
                        (self.claim_clicked_at is not None and
                         now - self.claim_clicked_at >= rule.wait_seconds)
                        or (self.claim_clicked_at is None and
                            self.post_lobby_at is not None and
                            now - self.post_lobby_at >= 18)):
                    return self._ready_after_rewards(now,
                        'Ricompense chiuse e lobby riconosciuta' if self.claim_clicked_at is not None
                        else 'Ricompense scomparse senza clic: lobby riconosciuta dopo attesa prudenziale')
            else:
                self.stable_lobby = 0
            return self._wait('Attesa chiusura ricompense e nuova conferma lobby')
        if self.stage == 'post_lobby':
            if safe and scene == 'lobby' and menu == 'gioca':
                self.stable_lobby += 1
            else:
                self.stable_lobby = 0
            rule = self.policy.for_event('post_lobby','lobby_without_rewards')
            if (self.post_lobby_at is not None and now - self.post_lobby_at >= rule.wait_seconds
                    and self.stable_lobby >= rule.confirmations and reward_scan_confirmed):
                return self._ready_after_rewards(now, 'Nessuna ricompensa rilevata dopo la finestra OCR')
            return self._wait('Lobby raggiunta: verifico eventuale RICOMPENSA OTTENUTA prima di Gioca')
        return None

    @property
    def label(self) -> str:
        return {
            'await_play': 'Lobby · pronto a Gioca',
            'post_lobby': 'Lobby · controllo ricompense',
            'claim_rewards': 'Ricompense · Riscatta',
            'joining': ('Attesa GIOCA / caricamento' if self.quick_wait_play_exit else 'Ingresso in partita'),
            'jumping': ('Solo salti · 2–2,5 min' if self.quick_wait_play_exit else 'Salti e movimento'),
            'exploring': 'Esplorazione',
            'ending': 'Ritorno alla lobby',
            'done': 'Partita completata',
            'error': 'Verifica necessaria',
        }[self.stage]

    def on_requeue_confirmed(self, now: float) -> None:
        """A verified PLAY AGAIN transition starts a new matchmaking cycle.

        Do not assume it succeeded merely because an input was sent. Preserve
        the completed-match count; the next game is not yet confirmed.
        """
        if self.stage != 'ending' or not self.repeat:
            return
        self.matches_completed += 1
        self.stage = 'joining'
        self.results_at = None
        self.play_clicked_at = now
        self.play_disappeared = True
        self.has_entered = False
        self.quick_routine.reset()
        self.elapsed = 0.0
        self.stable_match = self.stable_bus = self.stable_lobby = self.stable_results = 0
        self.quick_play_seen = True
        self.quick_departed = True
        self.quick_absent_frames = 3
        self.quick_wait_elapsed = 0.0
        self.last_tick = now

    def suspend_clock(self) -> None:
        """Pause/focus loss must not consume the two-minute routine."""
        self.last_tick = None

    def on_executed(self, action: Action, menu_name: str | None, now: float) -> None:
        if action.name == 'menu_click' and menu_name == 'riscatta' and self.stage == 'claim_rewards':
            self.claim_clicked_at = now
            self.claim_attempts += 1
        if action.name == 'menu_click' and menu_name == 'gioca' and self.stage in ('await_play', 'joining'):
            if self.quick_wait_play_exit:
                self.quick_play_click_attempts += 1
                self.quick_play_last_click_at = now
            self.stage = 'joining'
            self.play_clicked_at = now
            self.quick_manual_arm = False
            self.quick_wait_elapsed = 0.0
            self.last_tick = now
            self.stable_match = 0
            if self.quick_wait_play_exit:
                self.quick_play_seen = True
                self.quick_absent_frames = 0
                self.quick_departed = False

    @staticmethod
    def _wait(reason: str) -> Action:
        return Action('wait', reason=reason)

    def decide(self, *, now: float, frame_at: float, scene: str, menu: str | None,
               candidate: Action, authorized: bool, locked: bool, spectator: bool, bus_confirmed: bool = False,
               motion_pixels: float | None = None, motion_reliable: bool = False,
               feedback_action: str = '', reward_overlay: bool = False,
               reward_scan_confirmed: bool = True,
               learned_hint: Action | None = None, reward_pending: bool = False,
               play_visible: bool | None = None,
               play_point: tuple[int, int] | None = None,
               menu_candidate_authorized: bool = True,
               play_evidence_at: float | None = None) -> Action:
        # A cached frame does not count twice toward a screen transition.
        if frame_at <= self.last_frame_at:
            return self._wait('Fotogramma già analizzato')
        self.last_frame_at = frame_at
        if self.stage in ('done', 'error'):
            return self._wait('Ciclo terminato' if self.stage == 'done' else self.last_error)
        reward_action = self._handle_rewards(now=now, scene=scene, menu=menu,
            candidate=candidate, locked=locked, spectator=spectator,
            reward_overlay=reward_overlay,
            reward_scan_confirmed=reward_scan_confirmed)
        if reward_action is not None:
            return reward_action
        # A first credible OCR sighting of a reward banner immediately suspends
        # movement while the independent second OCR observation is pending.
        if reward_pending and self.stage in ('joining', 'jumping', 'exploring', 'ending'):
            self.suspend_clock()
            return self._wait('Possibile pannello ricompense: attendo seconda lettura OCR, input sospesi')
        if self.stage == 'await_play':
            if not reward_scan_confirmed:
                return self._wait('Attendo due letture OCR recenti prima di verificare Gioca')
            if reward_overlay:
                return self._wait('Ricompensa rilevata: Gioca sospeso')
            if now < self.next_play_at:
                return self._wait('Lobby confermata: breve pausa prima della prossima partita')
            if scene == 'lobby' and menu == 'gioca' and candidate.name == 'menu_click':
                return candidate
            return self._wait('Attesa pulsante GIOCA nella lobby: calibra il pulsante se non compare')

        if self.stage == 'joining':
            if self.quick_wait_play_exit:
                if locked or spectator or scene in ('spectator', 'results'):
                    self.suspend_clock()
                    return self._wait('Schermata spettatore/risultati: input sospesi')
                # None means UNOBSERVED, never convert it to False.
                visible = play_visible if play_visible is not None else (True if menu == 'gioca' else None)
                return self._quick_join_gate(now=now, visible=visible, candidate=candidate,
                                             menu=menu, play_point=play_point, scene=scene,
                                             click_authorized=menu_candidate_authorized,
                                             evidence_at=frame_at if play_evidence_at is None else play_evidence_at)
            if self.play_clicked_at is not None and now - self.play_clicked_at >= 240:
                self.last_error = 'Ingresso non confermato entro quattro minuti: controllo schermata e matchmaking'
                if not self.repeat:
                    self.stage = 'error'
                    return self._wait(self.last_error)
                self.recovery_count += 1
                self.play_clicked_at = now  # throttle notices; do not reset elapsed gameplay
                if scene == 'lobby' and menu == 'gioca' and not locked and not spectator:
                    # The same verified lobby is back: re-arm only from here.
                    self.stage = 'await_play'
                    self.next_play_at = now + self.lobby_cooldown_seconds
                    self.play_disappeared = False
                    self.stable_match = self.stable_bus = 0
                return self._wait('Recupero ingresso: ' + self.last_error + '; input sospesi')
            if scene != 'lobby' or menu != 'gioca':
                self.play_disappeared = True
            if locked or spectator or scene in ('results', 'spectator'):
                self.stable_match = 0
                return self._wait('Overlay/modalità spettatore: attesa')
            if self.quick_start:
                # Clock progresses on foreground time rather than OCR/frame count.
                self._advance_quick_clock(now)
            if authorized and self.play_disappeared:
                self.stable_match += 1
            else:
                self.stable_match = 0
            if bus_confirmed and self.play_disappeared:
                self.stable_bus += 1
            else:
                self.stable_bus = 0
            if (self.quick_start and self.play_clicked_at is not None
                    and self.quick_wait_elapsed >= self.quick_delay_seconds
                    and not locked and not spectator
                    and scene not in ('results', 'spectator')):
                # IGNORANTE deliberately does NOT wait for a reliable PLAY/lobby
                # classifier: even a stale/false lobby label cannot stall it.
                # Results and explicit spectator/reboot evidence still veto input.
                return self._begin_quick_activity(now)
            if self.stable_match >= 3 or self.stable_bus >= 3:
                self.stage = 'jumping'
                self.elapsed = 0.0
                self.last_tick = now
                self.has_entered = True
                self.last_jump_elapsed = self.elapsed
                self.last_secondary_elapsed = self.elapsed
                self.quick_routine.reset()
                return Action('jump', .16, reason='Ingresso verificato tramite HUD/bus: primo salto')
            # Permit Brain to retry confirmed PLAY if previous click did not work.
            if (scene == 'lobby' and menu == 'gioca' and candidate.name == 'menu_click'
                    and (self.quick_manual_arm or now - (self.play_clicked_at or 0) >= 12)):
                return candidate
            if self.quick_start:
                remaining = max(0., self.quick_delay_seconds - self.quick_wait_elapsed)
                return self._wait(f'IGNORANTE: {remaining:.0f} s; screenshot, HUD e bus NON richiesti')
            return self._wait('GIOCA inviato: attendo uscita dalla lobby e HUD partita stabile')

        # An observed results screen is authoritative only after three distinct frames.
        if scene == 'results' and not locked and not spectator:
            self.stable_results += 1
        else:
            self.stable_results = 0
        if self.stable_results >= 3 and self.stage != 'ending':
            self.stage = 'ending'
            self.results_at = now
            self.suspend_clock()

        # If the reward panel covers the lobby immediately after confirmed
        # results, the lobby PLAY control may never become visible. The panel
        # title itself then acts as a terminal cue, NEVER as authority to click.
        if (self.stage == 'ending' and reward_overlay and self.has_entered
                and self.results_at is not None and
                scene in ('results','unknown','lobby') and not locked and not spectator):
            self.reward_streak += 1
            if self.reward_streak >= 2:
                return self._complete_match(now,
                    'Risultati conclusi: pannello ricompense presente sopra la lobby')
        elif self.stage == 'ending':
            self.reward_streak = 0
        # Lobby may only finish the run AFTER a real match and a terminal phase.
        if self.stage == 'ending':
            if scene == 'lobby' and menu == 'gioca' and not locked:
                self.stable_lobby += 1
                if self.stable_lobby >= self.policy.for_event('ending','lobby_confirmed').confirmations:
                    return self._complete_match(now, 'Lobby confermata dopo i risultati')
            else:
                self.stable_lobby = 0
            if self.results_at is not None and now - self.results_at >= 240:
                self.last_error = 'Fine partita senza lobby confermata: mostrami RISCATTA, CONTINUA o TORNA ALLA LOBBY'
                if not self.repeat:
                    self.stage = 'error'
                    return self._wait(self.last_error)
                self.recovery_count += 1
                self.results_at = now  # bounded diagnostics, never blind menu clicks
                return self._wait('Recupero risultati: ' + self.last_error + '; input sospesi')
            if (scene == 'results' and not locked and not spectator and menu in
                    ('continua', 'torna_lobby', 'gioca_di_nuovo') and candidate.name == 'menu_click'
                    and (menu != 'gioca_di_nuovo' or self.repeat)):
                return candidate
            return self._wait('Fine partita: attendo GIOCA DI NUOVO / CONTINUA / TORNA ALLA LOBBY')

        # Manual time-only start is NOT a verified match. A persistent false
        # PLAY/lobby match must not re-arm the workflow and silently stop W.
        if self.quick_start and authorized and scene == 'match':
            self.quick_visual_match_seen = True
        if (self.quick_start and not self.quick_wait_play_exit and scene == 'lobby' and
                self.stage in ('jumping', 'exploring') and not self.quick_visual_match_seen):
            scene = 'unknown'  # avoid using an unverified lobby to end a blind run

        # Some playlists return straight to the lobby after the match without
        # showing a separate RESULTS CTA. Never click Play again on this path.
        if self.has_entered and scene == 'lobby' and menu == 'gioca' and not locked:
            self.stable_lobby += 1
            if self.stable_lobby >= self.policy.for_event('in_match','direct_lobby').confirmations:
                return self._complete_match(now, 'Ritorno diretto alla lobby confermato')
            return self._wait('Conferma lobby dopo la partita; non ripremere GIOCA')
        self.stable_lobby = 0
        # A calibrated battle-bus CTA is sufficient for Space, NOT for walking.
        if self.stage == 'jumping' and bus_confirmed and not self.quick_wait_play_exit and not locked and not spectator:
            if self.last_tick is not None:
                self.elapsed += max(0, min(3.5, now - self.last_tick))
            self.last_tick = now
            if self.elapsed - self.last_jump_elapsed >= self.jump_period_seconds:
                self.last_jump_elapsed = self.elapsed
                return Action('jump', .09, reason='Bus verificato: Space periodico')
            return self._wait('Bus verificato: attesa prossimo salto; W sospeso')
        if self.quick_wait_play_exit and scene in ('lobby', 'loading'):
            self.suspend_clock()
            return self._wait('Lobby o caricamento visibili: movimento sospeso')
        if locked or spectator or scene in (('spectator', 'results') if self.quick_wait_play_exit else
                                             ('spectator', 'results', 'lobby') if self.quick_start else
                                             ('spectator', 'results', 'lobby', 'loading')):
            self.suspend_clock()
            return self._wait('Nessun input: spettatore, attesa riavvio o schermata non giocabile')
        # v38: after the verified PLAY exit + 120s loading gate, Ignorante
        # does not depend on an HUD scene label to send Space/WASD.
        if not authorized and not ((self.quick_wait_play_exit and self.quick_departed) or
                                   (self.quick_start and scene == 'unknown')):
            self.suspend_clock()
            return self._wait('HUD non confermato: niente movimento alla cieca')
        if self.quick_wait_play_exit and self.stage == 'jumping':
            return self._quick_jump_only(now)
        if self.last_tick is not None:
            self.elapsed += max(0, min(3.5, now - self.last_tick))
        self.last_tick = now
        if self.stage == 'jumping' and self.elapsed >= self.jump_window_seconds:
            self.stage = 'exploring'
        if feedback_action in ('forward', 'patrol_forward'):
            self.explorer.observe_motion(motion_pixels, motion_reliable, feedback_action)
        if self.stage == 'jumping' and self.elapsed - self.last_jump_elapsed >= self.jump_period_seconds:
            self.last_jump_elapsed = self.elapsed
            return Action('jump', .16, reason=f'Salto + esplorazione ({self.elapsed:.0f}/{self.jump_window_seconds:.0f} s)')
        # A brief right-click can be part of verified in-match activity; never
        # send it to menus, during reboot, while spectating, or on the bus.
        if self.elapsed >= 14 and self.elapsed - self.last_secondary_elapsed >= 26:
            self.last_secondary_elapsed = self.elapsed
            return Action('secondary_click', .12, reason='Azione secondaria breve (clic destro)')
        if self.quick_start:
            # Predictable fallback: W/A/D, left mouse and pickaxe happen even
            # without navigation training or image classification.
            self.explorer.planner.activity = 'Routine rapida a tempo'
            if self.explorer.recovery_plan:
                return self.explorer.next_action(now=now)
            return self.quick_routine.next_action(harvest=self.harvest, learned_hint=learned_hint)
        action = self.explorer.next_action(learned_hint=learned_hint, now=now)
        if self.stage == 'jumping' and action.name.startswith('patrol_'):
            # Keep early movement brief: frequent Space presses between segments.
            return Action(action.name, min(.65, action.seconds), reason='Movimento + salto periodico · ' + action.reason)
        return action
