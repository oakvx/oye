"""Staged screen-driven controller. No hard-coded coordinates or speculative clicks.

This is not a navigational AI: all actions require confirmed scene evidence.
"""
from __future__ import annotations
from collections import deque
import random
from .models import Observation, Action, Match


class Brain:
    def __init__(self, *, seed: int | None = None, motion_threshold: float = 1.2,
                 menu_cooldown: float = 16, menu_retries: int = 2,
                 auto_requeue: bool = False, enable_shoot: bool = False,
                 respawn_confirm_frames: int = 6, menu_only: bool = False,
                 solo_mode: bool = False):
        self.rng = random.Random(seed)
        self.motion_threshold = motion_threshold
        self.menu_cooldown = menu_cooldown
        self.menu_retries = menu_retries
        self.auto_requeue = auto_requeue
        self.enable_shoot = enable_shoot
        self.menu_only = menu_only
        self.solo_mode = bool(solo_mode)
        self.respawn_confirm_frames = max(3, int(respawn_confirm_frames))
        self.spectator_latched = False
        self.respawn_frames = 0
        self.final_continue_frames = 0
        self.back_lobby_frames = 0
        self.scene = 'unknown'
        self.scene_streak = 0
        self.menu_streak = 0
        self.last_menu: Match | None = None
        self.menu_attempts: dict[str, int] = {}
        self.menu_cooldown_until: dict[str, float] = {}
        self.pending_menu: str | None = None
        self.pending_since = float('-inf')
        self.pending_absent = 0
        self.last_action: Action | None = None
        self.stuck_count = 0
        self.failed_recoveries = 0
        self.plan: deque[str] = deque()
        self.chest_last = float('-inf')
        self.prompt_last = float('-inf')
        self.alert = ''

    def _reset(self, *, scene_changed: bool = False) -> None:
        self.plan.clear()
        self.stuck_count = 0
        self.failed_recoveries = 0
        self.last_action = None
        self.menu_streak = 0
        self.last_menu = None
        if scene_changed:
            self.menu_attempts.clear()
            self.menu_cooldown_until.clear()
            self.pending_menu = None
            self.pending_absent = 0
            self.alert = ''

    def _act(self, name: str, why: str) -> Action:
        periods = {
            'forward': (.7, 1.7), 'forward_jump': (.45, .9),
            'reverse': (.35, .65), 'strafe_left': (.4, 1.0),
            'strafe_right': (.4, 1.0), 'turn_left': (.15, .33),
            'turn_right': (.15, .33), 'harvest': (.3, .8),
            'interact': (.08, .13), 'equip': (.08, .13),
            'shoot': (.08, .14), 'wait': (.25, .4),
        }
        lo, hi = periods.get(name, (0, 0))
        a = Action(name, self.rng.uniform(lo, hi), reason=why)
        self.last_action = a
        return a

    @staticmethod
    def _same_menu(a: Match | None, b: Match | None) -> bool:
        return (a is not None and b is not None and a.name == b.name
                and abs(a.center[0] - b.center[0]) <= 20
                and abs(a.center[1] - b.center[1]) <= 20)

    def _menu_action(self, obs: Observation, now: float) -> Action:
        menu = obs.menu
        allowed = {'lobby': ('gioca',), 'results': ('riscatta', 'continua', 'torna_lobby', 'gioca_di_nuovo')}
        if menu and menu.name not in allowed.get(obs.scene, ()):
            menu = None

        # A click can only be considered successful when its visual target disappears
        # for >=3 frames or a new, distinct button is confirmed. On failure, bounded
        # retry is permitted; no blind clicking or clicking a play-again alternative.
        if self.pending_menu:
            if menu is None:
                self.pending_absent += 1
                if self.pending_absent < 3:
                    return self._act('wait', 'attesa variazione dopo click')
                self.pending_menu = None
                self.pending_absent = 0
            elif menu.name != self.pending_menu:
                self.pending_menu = None
                self.pending_absent = 0
            else:
                self.pending_absent = 0
                if now - self.pending_since < self.menu_cooldown:
                    return self._act('wait', 'attesa risposta del menu')
                if self.menu_attempts.get(menu.name, 0) >= self.menu_retries:
                    self.alert = f'{menu.name}: nessun cambio schermata dopo {self.menu_retries} tentativi'
                    self.menu_cooldown_until[menu.name] = float('inf')
                    return self._act('wait', self.alert)
                self.pending_menu = None  # allow retry only after confirmation below

        if menu is None:
            self.menu_streak = 0
            self.last_menu = None
            return self._act('wait', 'attesa pulsante Continua / Torna alla lobby / Gioca')
        if self._same_menu(menu, self.last_menu):
            self.menu_streak += 1
        else:
            self.menu_streak = 1
        self.last_menu = menu
        if self.menu_streak < 3:
            return self._act('wait', 'conferma pulsante su 3 screenshot')
        if not self.auto_requeue:
            return self._act('wait', f'{menu.name} riconosciuto (osservazione)')
        if now < self.menu_cooldown_until.get(menu.name, float('-inf')):
            return self._act('wait', 'pulsante sospeso dopo tentativi falliti')
        self.menu_attempts[menu.name] = self.menu_attempts.get(menu.name, 0) + 1
        self.pending_menu = menu.name
        self.pending_since = now
        self.pending_absent = 0
        self.menu_streak = 0
        self.last_menu = None
        action = Action('menu_click', point=menu.center,
                        reason=f'{menu.name} ({menu.score:.3f}, tentativo {self.menu_attempts[menu.name]})')
        self.last_action = action
        return action

    def discard_unexecuted_menu_click(self, proposal: Action, menu: Match | None) -> None:
        """Forget a proposed click vetoed by a higher-level lifecycle controller.

        Otherwise an unexecuted PLAY may count toward the menu retry cap and
        impose the entire 12-second cooldown before the next match starts.
        """
        if (proposal.name != 'menu_click' or menu is None or
                self.pending_menu != menu.name):
            return
        self.pending_menu = None
        self.pending_since = float('-inf')
        self.pending_absent = 0
        self.menu_attempts[menu.name] = max(0, self.menu_attempts.get(menu.name, 0)-1)
        self.menu_streak = 0
        self.last_menu = None

    def decide(self, obs: Observation, now: float) -> Action:
        if not obs.focused:
            self.scene = 'unfocused'
            self.scene_streak = 0
            self._reset(scene_changed=True)
            return self._act('wait', 'finestra non in primo piano: nessun input')
        if obs.scene != self.scene:
            self.scene, self.scene_streak = obs.scene, 1
            self._reset(scene_changed=True)
        else:
            self.scene_streak += 1
        # Once we see spectator controls, never use the gameplay plan or click a
        # 'return to lobby' option until a real respawn or final results are proven.
        # The initial player HUD can remain visible while spectating.
        if obs.scene == 'spectator':
            self.spectator_latched = True
            self.respawn_frames = 0
            self.final_continue_frames = 0
            self.back_lobby_frames = 0
            return self._act('wait', 'SPETTATORE: compagni ancora in partita, nessun input')

        if self.spectator_latched:
            if obs.scene == 'match':
                self.respawn_frames += 1
                if self.respawn_frames < self.respawn_confirm_frames:
                    return self._act('wait',
                        f'possibile riavvio: conferma HUD {self.respawn_frames}/{self.respawn_confirm_frames}')
                self.spectator_latched = False
                self.respawn_frames = 0
                self._reset(scene_changed=True)  # discard pre-elimination movement plan
            elif obs.scene == 'lobby' and obs.menu and obs.menu.name == 'gioca':
                # Do not unlock on a transient matching error.
                self.back_lobby_frames += 1
                self.final_continue_frames = 0
                if self.back_lobby_frames < 3:
                    return self._act('wait', 'conferma nuova lobby dopo spettatore')
                self.spectator_latched = False
                self.respawn_frames = 0
            elif obs.scene == 'results' and obs.menu and (
                    obs.menu.name in ('continua', 'riscatta') or
                    (self.solo_mode and obs.menu.name in ('torna_lobby', 'gioca_di_nuovo'))):
                # In solo mode, a persistent calibrated 'torna_lobby' is an
                # acceptable final CTA after scene RESULTS has been confirmed.
                # In squads, that CTA alone may appear during spectating.
                self.final_continue_frames += 1
                self.back_lobby_frames = 0
                if self.final_continue_frames < 3:
                    return self._act('wait', 'conferma pulsante conclusivo (CONTINUA/RISCATTA)')
                self.spectator_latched = False
                self.respawn_frames = 0
            else:
                self.respawn_frames = 0
                self.final_continue_frames = 0
                self.back_lobby_frames = 0
                return self._act('wait', 'spettatore: attesa riavvio o schermata conclusiva')

        if self.scene_streak < 3:
            return self._act('wait', 'conferma schermata su 3 screenshot')
        if obs.scene in ('lobby', 'results'):
            return self._menu_action(obs, now)
        if obs.scene != 'match':
            return self._act('wait', 'caricamento o schermata non riconosciuta')
        if self.menu_only:
            # The autonomous planner/guided policy owns all gameplay inputs.
            # Avoid filling the legacy random movement queue or generating
            # misleading stuck alerts for actions Brain did not actually send.
            return self._act('wait', 'partita confermata: azioni delegate al pilota')

        # Optical-flow checks are heuristic, not a guarantee of wall detection.
        if self.last_action and self.last_action.name in ('forward', 'forward_jump'):
            if obs.motion is not None and obs.motion.reliable:
                if obs.motion.pixels < self.motion_threshold:
                    self.stuck_count += 1
                else:
                    self.stuck_count = 0
                    self.failed_recoveries = 0
        if self.failed_recoveries > 4:
            return self._act('wait', 'movimento inefficace: azioni sospese fino al prossimo cambio schermata')
        if self.stuck_count >= 3 and not self.plan:
            self.stuck_count = 0
            self.failed_recoveries += 1
            if self.failed_recoveries > 4:
                self.alert = 'Quattro recuperi inefficaci; serve supervisione'
                return self._act('wait', self.alert)
            turn = self.rng.choice(('turn_left', 'turn_right'))
            side = self.rng.choice(('strafe_left', 'strafe_right'))
            self.plan.extend(('reverse', turn, turn, side, 'forward'))
        if obs.interact_prompt and now - self.prompt_last >= 4:
            self.prompt_last = now
            return self._act('interact', 'prompt interazione visibile')
        if (obs.chest_audio or obs.chest_visual) and now - self.chest_last >= 24:
            self.chest_last = now
            self.plan.clear()
            self.plan.extend(('turn_left', 'interact', 'harvest', 'turn_right',
                              'turn_right', 'interact', 'harvest'))
        if not self.plan:
            motifs = [
                (46, ('forward', 'forward', 'turn_left', 'forward_jump')),
                (24, ('turn_right', 'forward', 'strafe_left', 'forward')),
                (14, ('turn_left', 'turn_right', 'forward', 'interact')),
                (10, ('harvest', 'forward', 'interact')),
                (6, ('equip', 'turn_right', 'forward')),
            ]
            selected = self.rng.choices(motifs, weights=[m[0] for m in motifs])[0]
            self.plan.extend(selected[1])
            if self.enable_shoot and self.rng.random() < .04:
                self.plan.append('shoot')
        return self._act(self.plan.popleft(), 'sequenza contestuale')
