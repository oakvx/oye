"""Optional XInput gamepad backend. Hybrid mode: visual menu targets use mouse.

No game-specific hooks, anti-cheat workarounds, or driver auto-installation.
All held controls are neutralized after every action, on pause, and on errors.
"""
from __future__ import annotations
import random
import time
from .driver import Driver
from .models import Action


class GamepadDriver(Driver):
    def __init__(self, *, gamepad=None, gamepad_api=None, **kwargs):
        super().__init__(**kwargs)
        self.pad = gamepad
        self.vg = gamepad_api
        if self.live and self.pad is None:
            try:
                import vgamepad as vg
                self.pad = vg.VX360Gamepad()
                self.vg = vg
            except Exception as exc:
                raise RuntimeError(
                    'Controller virtuale non disponibile. Non installare driver alla cieca: '
                    'leggi la sezione ViGEmBus in LEGGIMI.md. ' + str(exc)
                ) from exc
        self.rng = random.Random()

    def release(self) -> None:
        try:
            if self.pad is not None:
                self.pad.reset()
                self.pad.update()
        finally:
            super().release()

    def _press(self, button):
        self.pad.press_button(button=button)
        self.pad.update()

    def _move(self, lx=0.0, ly=0.0, rx=0.0, ry=0.0):
        self.pad.left_joystick_float(x_value=lx, y_value=ly)
        self.pad.right_joystick_float(x_value=rx, y_value=ry)
        self.pad.update()

    def run(self, action: Action) -> bool:
        if not self.live or not self.permitted() or action.name == 'wait':
            return False
        if action.name == 'menu_click':
            # Controller D-pad navigation requires visual proof of focus; using
            # A blindly could activate the wrong item. Keep verified mouse target.
            self.release()
            return super().run(action)
        if self.pad is None or self.vg is None:
            return False
        b = self.vg.XUSB_BUTTON
        try:
            if action.name == 'patrol_forward':
                self._move(ly=.80)
            elif action.name == 'patrol_look_left':
                self._move(ly=.76, rx=-.32)
            elif action.name == 'patrol_look_right':
                self._move(ly=.76, rx=.32)
            elif action.name == 'patrol_weave_left':
                self._move(lx=-.40, ly=.75, rx=-.15)
            elif action.name == 'patrol_weave_right':
                self._move(lx=.40, ly=.75, rx=.15)
            elif action.name == 'patrol_hop':
                self._move(ly=.78)
                self._press(b.XUSB_GAMEPAD_A)
            elif action.name == 'patrol_harvest':
                self._move(ly=.70)
                self.pad.right_trigger_float(value=.82)
                self.pad.update()
            elif action.name == 'forward':
                self._move(ly=.78)
            elif action.name == 'forward_jump':
                self._move(ly=.72)
                self._press(b.XUSB_GAMEPAD_A)
            elif action.name == 'forward_shoot':
                self._move(ly=.78)
                self.pad.right_trigger_float(value=.9)
                self.pad.update()
            elif action.name == 'strafe_left_shoot':
                self._move(lx=-.7, ly=.6)
                self.pad.right_trigger_float(value=.9)
                self.pad.update()
            elif action.name == 'strafe_right_shoot':
                self._move(lx=.7, ly=.6)
                self.pad.right_trigger_float(value=.9)
                self.pad.update()
            elif action.name == 'reverse':
                self._move(ly=-.65)
            elif action.name == 'strafe_left':
                self._move(lx=-.7, ly=.6)
            elif action.name == 'strafe_right':
                self._move(lx=.7, ly=.6)
            elif action.name == 'turn_left':
                self._move(rx=-.62)
            elif action.name == 'turn_right':
                self._move(rx=.62)
            elif action.name in ('forward_look_left', 'forward_look_right'):
                self._move(ly=.68, rx=-.42 if action.name.endswith('left') else .42)
            elif action.name == 'equip_pickaxe':
                self._press(b.XUSB_GAMEPAD_Y)
            elif action.name == 'forward_harvest':
                self._move(ly=.65)
                self.pad.right_trigger_float(value=.85)
                self.pad.update()
            elif action.name == 'aim':
                if action.point is None:
                    return False
                dx, dy = action.point
                if not (-180 <= dx <= 180 and -110 <= dy <= 110):
                    return False
                self._move(rx=max(-.7, min(.7, dx / 200)),
                           ry=max(-.7, min(.7, -dy / 150)))
            elif action.name == 'jump':
                self._press(b.XUSB_GAMEPAD_A)
            elif action.name == 'crouch':
                self._press(b.XUSB_GAMEPAD_RIGHT_THUMB)
            elif action.name == 'reload':
                self._press(b.XUSB_GAMEPAD_X)
            elif action.name in ('slot_1', 'slot_2'):
                self._press(b.XUSB_GAMEPAD_LEFT_SHOULDER if action.name == 'slot_1'
                            else b.XUSB_GAMEPAD_RIGHT_SHOULDER)
            elif action.name == 'interact':
                self._press(b.XUSB_GAMEPAD_X)
            elif action.name == 'equip':
                self._press(self.rng.choice((b.XUSB_GAMEPAD_LEFT_SHOULDER,
                                              b.XUSB_GAMEPAD_RIGHT_SHOULDER)))
            elif action.name == 'harvest':
                self._press(b.XUSB_GAMEPAD_Y)
                self.pad.right_trigger_float(value=.9)
                self.pad.update()
            elif action.name == 'shoot':
                self.pad.right_trigger_float(value=.9)
                self.pad.update()
            else:
                raise ValueError(f'Azione non supportata dal controller: {action.name}')
            self._sleep(min(3.0, max(.05, action.seconds)))
            return True
        finally:
            self.release()
