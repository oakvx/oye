"""Interruptible desktop input, with unconditional key/mouse release.

No bypass techniques. pyautogui's public Windows APIs may be ignored by
some games; only independent tests on the user's machine can determine this.
"""
from __future__ import annotations
import random
import threading
import time
from .models import Action


class Driver:
    def __init__(self, *, live: bool, allowed, config: dict, stop: threading.Event,
                 paused: threading.Event):
        self.live, self.allowed, self.config = live, allowed, config
        self.stop, self.paused = stop, paused
        self.down: set[str] = set()
        self.mouse_is_down = False
        self.mouse_button = 'left'
        self.rng = random.Random()
        self.gui = None
        if live:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = .010
            self.gui = pyautogui

    def permitted(self) -> bool:
        return not self.stop.is_set() and not self.paused.is_set() and self.allowed()

    def release(self) -> None:
        if self.gui is None:
            return
        # Mouse and all keys must be released even if the pointer triggers FAILSAFE.
        failsafe_before = self.gui.FAILSAFE
        try:
            self.gui.FAILSAFE = False
            if self.mouse_is_down:
                try:
                    self.gui.mouseUp(button=self.mouse_button)
                finally:
                    self.mouse_is_down = False
            for key in tuple(self.down):
                try:
                    self.gui.keyUp(key)
                finally:
                    self.down.discard(key)
        finally:
            self.gui.FAILSAFE = failsafe_before

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0, seconds)
        while self.permitted() and time.monotonic() < deadline:
            time.sleep(max(0, min(.035, deadline - time.monotonic())))

    def _hold(self, keys: list[str], seconds: float) -> None:
        try:
            for key in keys:
                if not self.permitted():
                    return
                self.gui.keyDown(key)
                self._sent = True
                self.down.add(key)
            self._sleep(seconds)
        finally:
            self.release()

    def _hold_and_fire(self, keys: list[str], seconds: float) -> None:
        try:
            for key in keys:
                if not self.permitted():
                    return
                self.gui.keyDown(key)
                self._sent = True
                self.down.add(key)
            if not self.permitted():
                return
            self.gui.mouseDown(button='left')
            self.mouse_button = 'left'
            self._sent = True
            self.mouse_is_down = True
            self._sleep(seconds)
        finally:
            self.release()

    def _patrol(self, action: Action) -> None:
        """Bounded, interruptible W hold with small view updates and optional pickaxe.

        Does not imply games using raw mouse input will accept moveRel. No low-level
        hooks or anti-cheat workarounds are attempted.
        """
        duration = min(2.5, max(.15, float(action.seconds)))
        name = action.name
        turn = (-1 if name in ('patrol_look_left', 'patrol_weave_left')
                else 1 if name in ('patrol_look_right', 'patrol_weave_right') else 0)
        total_px = min(320, max(20, int(self.config.get('guided_camera_pixels', 150)))) * turn
        if name.startswith('patrol_weave'):
            total_px = round(total_px * .32)  # Strafe is the primary steering signal.
        # Small updates at 50 Hz instead of visible 200 ms camera jumps.
        steps = max(1, round(duration / .02))
        try:
            if not self.permitted():
                return
            key = self.config['keys']['forward']
            self.gui.keyDown(key)
            self.down.add(key)
            self._sent = True
            if name in ('patrol_weave_left', 'patrol_weave_right'):
                lateral = self.config['keys']['left' if name.endswith('left') else 'right']
                self.gui.keyDown(lateral)
                self.down.add(lateral)
                self._sent = True
            if name == 'patrol_harvest':
                if not self.permitted():
                    return
                self.gui.mouseDown(button='left')
                self.mouse_button = 'left'
                self.mouse_is_down = True
                self._sent = True
            progress = 0
            started = time.monotonic()
            jump_start = max(1, steps // 3)
            jump_end = min(steps - 1, jump_start + max(1, round(.12 * steps / duration)))
            for i in range(steps):
                if not self.permitted():
                    break
                if name == 'patrol_hop' and i == jump_start:
                    # Brief jump while W is still held. Release before the end
                    # of the segment; unconditional final release is a backstop.
                    jump = self.config['keys']['jump']
                    self.gui.keyDown(jump)
                    self.down.add(jump)
                    self._sent = True
                if name == 'patrol_hop' and i == jump_end:
                    jump = self.config['keys']['jump']
                    if jump in self.down:
                        self.gui.keyUp(jump)
                        self.down.discard(jump)
                if turn:
                    # Distribute the rotation across the full W hold: no isolated
                    # camera action with W released. Caller verifies camera result.
                    fraction = (i + 1) / steps
                    eased = fraction * fraction * (3 - 2 * fraction)
                    target = round(total_px * eased)
                    dx = target - progress
                    if dx:
                        self.gui.moveRel(dx, 0, duration=0)
                        progress = target
                # Include API overhead in the segment budget instead of adding
                # it to every step and progressively slowing the movement.
                self._sleep(max(0., started + duration * (i + 1) / steps - time.monotonic()))
        finally:
            self.release()

    def _tap(self, key: str) -> None:
        # 90 ms often spans only a few frames; a still-bounded 160 ms press
        # is easier to observe in a manual input diagnostic.
        self._hold([key], .16 if key == self.config['keys']['jump'] else .12)

    def run(self, action: Action) -> bool:
        if self.gui is None:
            return False
        if not self.permitted() or action.name == 'wait':
            return False
        keys = self.config['keys']
        self._sent = False
        try:
            if action.name == 'menu_click':
                if action.point is None:
                    return False
                self.gui.click(*action.point)
                self._sent = True
            elif action.name.startswith('patrol_') and action.name in (
                    'patrol_forward', 'patrol_look_left', 'patrol_look_right', 'patrol_harvest',
                    'patrol_weave_left', 'patrol_weave_right', 'patrol_hop'):
                self._patrol(action)
            elif action.name == 'forward':
                self._hold([keys['forward']], action.seconds)
            elif action.name == 'forward_jump':
                self._hold([keys['forward'], keys['jump']], action.seconds)
            elif action.name == 'forward_shoot':
                self._hold_and_fire([keys['forward']], action.seconds)
            elif action.name == 'strafe_left_shoot':
                self._hold_and_fire([keys['forward'], keys['left']], action.seconds)
            elif action.name == 'strafe_right_shoot':
                self._hold_and_fire([keys['forward'], keys['right']], action.seconds)
            elif action.name == 'reverse':
                self._hold([keys['back']], action.seconds)
            elif action.name == 'strafe_left':
                self._hold([keys['forward'], keys['left']], action.seconds)
            elif action.name == 'strafe_right':
                self._hold([keys['forward'], keys['right']], action.seconds)
            elif action.name in ('turn_left', 'turn_right'):
                n = self.rng.randint(110, 235)
                self.gui.moveRel(-n if action.name == 'turn_left' else n, 0, duration=action.seconds)
                self._sent = True
            elif action.name in ('forward_look_left', 'forward_look_right'):
                # A bounded camera adjustment while W is held. Mouse steering
                # is intentionally brief: focus cannot be checked mid-moveRel.
                with_key = keys['forward']
                self.gui.keyDown(with_key)
                self._sent = True
                self.down.add(with_key)
                if not self.permitted():
                    return self._sent
                self.gui.moveRel(-85 if action.name.endswith('left') else 85, 0,
                                 duration=min(.23, max(.08, action.seconds)))
            elif action.name == 'equip_pickaxe':
                self._tap(keys['pickaxe'])
            elif action.name == 'forward_harvest':
                # No weapon-selection heuristics: the routine equips pickaxe
                # explicitly before requesting this primitive.
                self._hold_and_fire([keys['forward']], min(.75, max(.1, action.seconds)))
            elif action.name == 'aim':
                if action.point is None:
                    return False
                dx, dy = action.point
                if not (-180 <= dx <= 180 and -110 <= dy <= 110):
                    return False
                self.gui.moveRel(dx, dy, duration=action.seconds)
                self._sent = True
            elif action.name in ('jump', 'reload'):
                self._tap(keys[action.name])
            elif action.name == 'crouch':
                self._hold([keys['crouch']], action.seconds)
            elif action.name in ('slot_1', 'slot_2'):
                self._tap(keys[action.name])
            elif action.name == 'interact':
                self._tap(keys['interact'])
            elif action.name == 'equip':
                self._tap(keys[self.rng.choice(('slot_1', 'slot_2'))])
            elif action.name == 'harvest':
                self._tap(keys['pickaxe'])
                self.gui.mouseDown(button='left')
                self.mouse_button = 'left'
                self._sent = True
                self.mouse_is_down = True
                self._sleep(action.seconds)
            elif action.name == 'shoot':
                self.gui.mouseDown(button='left')
                self.mouse_button = 'left'
                self._sent = True
                self.mouse_is_down = True
                self._sleep(min(.75,max(.08,action.seconds)))
            elif action.name in ('secondary_click','aim_hold'):
                # Explicit secondary action only; never random right-click spam.
                self.gui.mouseDown(button='right')
                self.mouse_button = 'right'
                self._sent = True
                self.mouse_is_down = True
                self._sleep(min(.75,max(.08,action.seconds)))
            else:
                raise ValueError(f'Azione sconosciuta: {action.name}')
            return self._sent
        finally:
            self.release()
