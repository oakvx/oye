"""Opt-in, foreground-only demonstration of approved gameplay controls.

Never records arbitrary keycodes, typed text, game chat, or a global key log.
The runner independently verifies that the allowed game process has focus.
"""
from __future__ import annotations
import ctypes
import sys
import threading
import time
from typing import Callable

VIRTUAL_KEYS = {
    'space': 0x20, 'ctrl': 0x11, 'lctrl': 0xA2, 'rctrl': 0xA3,
    'shift': 0x10, '1': 0x31, '2': 0x32,
    **{chr(code).casefold(): code for code in range(0x41, 0x5B)},
}

# The policy currently learns one short action at a time. Preserve all
# simultaneous approved controls in the trace, while using one dominant label.
PRIORITY = ('shoot', 'interact', 'reload', 'slot_1', 'slot_2', 'jump',
            'harvest', 'forward', 'reverse', 'strafe_left', 'strafe_right', 'crouch')


def primary_action(actions: tuple[str, ...]) -> str | None:
    controls = set(actions)
    if 'forward' in controls and 'jump' in controls:
        # Preserve locomotion even when the mouse is being held down.
        return 'forward_jump'
    if 'forward' in controls and 'strafe_left' in controls:
        return 'strafe_left_shoot' if 'shoot' in controls else 'strafe_left'
    if 'forward' in controls and 'strafe_right' in controls:
        return 'strafe_right_shoot' if 'shoot' in controls else 'strafe_right'
    if 'forward' in controls and 'shoot' in controls:
        return 'forward_shoot'
    return next((name for name in PRIORITY if name in controls), None)


class WinGameInputReader:
    def __init__(self, keys: dict[str, str], *, allow_mouse: bool = False):
        if sys.platform != 'win32':
            raise RuntimeError('Registrazione dimostrazione disponibile solo su Windows')
        mapping = {name: VIRTUAL_KEYS.get(key.casefold())
                   for name, key in keys.items() if isinstance(key, str)}
        self.mapping = {key: code for key, code in mapping.items() if code is not None}
        self.allow_mouse = allow_mouse

    def read_actions(self) -> tuple[str, ...]:
        get_key = ctypes.windll.user32.GetAsyncKeyState
        action_map = {
            'forward': 'forward', 'back': 'reverse', 'left': 'strafe_left',
            'right': 'strafe_right', 'jump': 'jump', 'interact': 'interact',
            'reload': 'reload', 'slot_1': 'slot_1', 'slot_2': 'slot_2',
            'pickaxe': 'harvest', 'crouch': 'crouch',
        }
        result = {action for key, action in action_map.items()
                  if key in self.mapping and get_key(self.mapping[key]) & 0x8000}
        if self.allow_mouse and get_key(0x01) & 0x8000:
            result.add('shoot')
        return tuple(sorted(result))

    def read(self) -> str | None:
        """Backward-compatible single-action reader."""
        return primary_action(self.read_actions())


class DemonstrationWorker:
    """Record transitions plus throttled visual examples, without HUD gating.

    The caller is responsible for checking the foreground process. Changes of
    state are retained even if the screen cannot be classified. Training on
    uncertain scenes requires subsequent human confirmation.
    """
    def __init__(self, reader: Callable[[], str | tuple[str, ...] | None],
                 record: Callable[[str], None], allowed: Callable[[], bool],
                 *, tick: float = .05, gap: float = .60, idle_gap: float = 3.,
                 clock=time.monotonic, on_error: Callable[[str], None] | None = None,
                 record_multi: Callable[[str, tuple[str, ...]], None] | None = None,
                 on_transition: Callable[[tuple[str, ...]], None] | None = None):
        self.reader, self.record, self.allowed = reader, record, allowed
        self.tick, self.gap, self.idle_gap, self.clock = tick, gap, idle_gap, clock
        self.record_multi, self.on_transition = record_multi, on_transition
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='OyeDemonstration')
        self.last_capture = float('-inf')
        self.last_action: str | None = None
        self.last_actions: tuple[str, ...] = ()
        self.idle_since = self.clock()
        self.errors = 0
        self.saved = 0
        self.transitions = 0
        self.last_error = ''
        self.on_error = on_error

    def start(self):
        self.thread.start()

    def _transition(self, actions: tuple[str, ...]):
        if actions == self.last_actions:
            return
        if self.on_transition is not None:
            self.on_transition(actions)
            self.transitions += 1
        self.last_actions = actions

    def _run(self):
        while not self.stop_event.is_set():
            try:
                if not self.allowed():
                    # Record release, not the keys of another application.
                    self._transition(())
                    self.last_action = None
                    self.idle_since = self.clock()
                    self.stop_event.wait(self.tick)
                    continue
                result = self.reader()
                if isinstance(result, str):
                    actions = (result,) if result else ()
                    action = result
                else:
                    actions = tuple(sorted(set(result or ())))
                    action = primary_action(actions)
                self._transition(actions)
                now = self.clock()
                if action is not None or self.last_action is not None:
                    self.idle_since = now
                self.last_action = action
                if action is not None and now - self.last_capture >= self.gap:
                    if self.record_multi is not None:
                        self.record_multi(action, actions)
                    else:
                        self.record(action)
                    self.saved += 1
                    self.last_capture = self.clock()
            except Exception as exc:
                self.errors += 1
                self.last_error = f'{type(exc).__name__}: {exc}'[:260]
                if self.on_error is not None and self.errors in (1, 5, 20):
                    try:
                        self.on_error(self.last_error)
                    except Exception:
                        pass
            self.stop_event.wait(self.tick)
        # The demonstration thread owns its MSS handle; release it here.
        from .windows import close_capture
        close_capture()

    def stop(self, timeout: float = 1.5):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout)
