"""Explicitly opted-in timing-only game routine. Times are estimates, never observations.

Does not attempt to evade game restrictions, humanize input, or infer grounded
state from elapsed time. All activity is bounded and interruptible by Runner.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from .guided import GuidedExplorer
from .models import Action

@dataclass
class TimedMatchRoutine:
    load_seconds: float = 90.0
    jump_period: float = 2.0
    jump_window_seconds: float = 45.0
    fall_seconds: float = 40.0
    ground_seconds: float = 540.0
    # Primo segnale visivo di salute, senza richiedere l'intero HUD.
    health_jump_enabled: bool = True
    health_threshold: float = 0.62
    health_confirm_frames: int = 3
    health_min_wait_seconds: float = 8.0
    health_jump_window_seconds: float = 90.0
    harvest: bool = True
    smooth: bool = False
    profile: str = "equilibrato"
    recovery_limit: int = 3
    recovery_threshold: float = .55
    stage: str = 'idle'
    elapsed: float = 0.0
    last_tick: float | None = None
    last_jump_elapsed: float = -1e9
    jumps: int = 0
    health_streak: int = 0
    health_triggered: bool = False
    health_start_at: float | None = None
    last_health_frame_at: float = -1e9
    descent_start_at: float | None = None
    descent_streak: int = 0
    last_descent_frame_at: float = -1e9
    explorer: GuidedExplorer = field(default_factory=GuidedExplorer)

    def __post_init__(self):
        if not (15 <= self.load_seconds <= 240 and .75 <= self.jump_period <= 5
                and 10 <= self.jump_window_seconds <= 120 and 15 <= self.fall_seconds <= 120
                and 60 <= self.ground_seconds <= 3600
                and .45 <= self.health_threshold <= .90
                and 2 <= self.health_confirm_frames <= 6
                and 0 <= self.health_min_wait_seconds <= 60
                and 10 <= self.health_jump_window_seconds <= 120):
            raise ValueError('Tempi della routine non validi')
        self._new_explorer()

    def _new_explorer(self):
        self.explorer = GuidedExplorer(harvest=self.harvest, smooth=self.smooth, profile=self.profile, max_recoveries=self.recovery_limit,
                                       motion_threshold=self.recovery_threshold)

    def arm(self, now: float):
        """Use after the Play click; may also be explicitly armed at startup."""
        self.stage, self.elapsed, self.last_tick = 'loading', 0.0, now
        self.last_jump_elapsed, self.jumps = -1e9, 0
        self.health_streak, self.health_triggered = 0, False
        self.health_start_at, self.last_health_frame_at = None, -1e9
        self.descent_start_at, self.descent_streak = None, 0
        self.last_descent_frame_at = -1e9
        self._new_explorer()
        self._reboot_probe = False

    def observe_health(self, score: float | None, *, scene: str, frame_at: float) -> bool:
        """Trigger one bounded jump window from repeated FRESH health-panel frames.

        The health bar is weak evidence of gameplay (also visible on pre-game
        island and on ground). Hence this can start only once after a timed
        routine was armed, after its minimum load wait, and never on a menu.
        It is NOT evidence of a bus, a jump or a landing.
        """
        if not self.health_jump_enabled or self.stage != 'loading' or self.health_triggered:
            return False
        if frame_at <= self.last_health_frame_at:
            return False  # Repeat read of the same cached screenshot is NOT new evidence.
        self.last_health_frame_at = frame_at
        if scene in ('lobby', 'results', 'spectator') or score is None or score < self.health_threshold:
            self.health_streak = 0
            return False
        self.health_streak += 1
        if self.elapsed < self.health_min_wait_seconds or self.health_streak < self.health_confirm_frames:
            return False
        self.health_triggered = True
        self.health_start_at = self.elapsed
        self.last_jump_elapsed = -1e9
        return True

    def observe_descent(self, *, phase: str | None, frame_at: float) -> bool:
        """Stop pressing Space early when a CALIBRATED descent cue is stable.

        A text-only OCR guess must not perform this transition. Timer fallback
        remains in place if no aerial template is configured.
        """
        if self.stage != 'jumping' or self.jumps == 0 or self.descent_start_at is not None:
            return False
        if frame_at <= self.last_descent_frame_at:
            return False
        self.last_descent_frame_at = frame_at
        if phase != 'skydiving':
            self.descent_streak = 0
            return False
        self.descent_streak += 1
        if self.descent_streak < 2:
            return False
        self.descent_start_at = self.elapsed
        return True

    def stop_for_menu(self):
        self.stage, self.last_tick = 'idle', None

    def resume_after_reboot(self, now: float):
        """Re-enter ground ONLY after caller independently verifies a respawn."""
        self._new_explorer()
        self.stage = 'ground'
        self.elapsed = self.load_seconds + self.jump_window_seconds + self.fall_seconds
        self.last_tick = now
        self.last_jump_elapsed = -1e9
        self.jumps = 0
        self.health_triggered = False
        self.health_start_at = None
        self.descent_start_at = None
        self._reboot_probe = True

    def choose(self, now: float, *, menu_blocked: bool = False,
               motion_pixels: float | None = None, motion_reliable: bool = False,
               feedback_action: str = '') -> Action:
        if menu_blocked:
            self.stop_for_menu()
            return Action('wait', reason='Menu conclusivo confermato: attesa pulsanti')
        if self.stage == 'idle':
            return Action('wait', reason='In attesa di GIOCA o di avvio manuale timer')
        # Pauses and focus-loss are handled by Runner; avoid counting an unseen gap.
        if self.last_tick is not None:
            self.elapsed += max(0.0, min(3.5, now - self.last_tick))
        self.last_tick = now
        t = self.elapsed
        load_end = self.health_start_at if self.health_triggered else self.load_seconds
        if t < load_end:
            self.stage = 'loading'
            return Action('wait', reason=f'Caricamento stimato: {max(0,int(load_end-t))} s rimanenti (non verificato)')
        jump_window = self.health_jump_window_seconds if self.health_triggered else self.jump_window_seconds
        jump_end = self.descent_start_at if self.descent_start_at is not None else load_end + jump_window
        if t < jump_end:
            self.stage = 'jumping'
            if t - self.last_jump_elapsed >= self.jump_period:
                self.last_jump_elapsed = t
                self.jumps += 1
                return Action('jump', .09, reason=f'Spazio #{self.jumps}; avvio ' + ('salute rilevata' if self.health_triggered else 'timer') + '; bus non verificato')
            return Action('wait', reason='Finestra salto: attesa intervallo')
        fall_end = jump_end + self.fall_seconds
        if t < fall_end:
            self.stage = 'falling'
            return Action('wait', reason=f'Attesa discesa stimata: {max(0,int(fall_end-t))} s')
        if t >= fall_end + self.ground_seconds:
            self.stage = 'finished'
            return Action('wait', reason='Tempo massimo esplorazione raggiunto: attesa nuovo Gioca')
        self.stage = 'ground'
        if getattr(self, '_reboot_probe', False):
            self._reboot_probe = False
            return Action('forward', .45, reason='Dopo riavvio: breve prova di movimento per verifica')
        if motion_pixels is not None and motion_reliable:
            self.explorer.observe_motion(motion_pixels, True, feedback_action)
        return self.explorer.next_action()
