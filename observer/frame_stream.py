"""Latest-frame capture stream: no frame backlog and no stale input authorization.

The producer owns its MSS handles; the consumer receives immutable snapshots only
while the same foreground window is still selected. Decisions still require the
Runner's fresh HUD/scene checks and the driver's separate foreground check.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable

import numpy as np
from .windows import Window, close_capture, foreground_window, snapshot


@dataclass(frozen=True)
class CapturedFrame:
    image: np.ndarray
    window: Window
    captured_at: float
    duration_ms: float
    sequence: int


class LatestFrameCapture:
    """Only the newest frame is kept; dropped frames consume no memory queue.

    Capture thread never drives game inputs. Polls foreground independently, and
    clears its last frame on focus loss. max_age guards against stale frames.
    """
    def __init__(self, allowed_names: list[str], *, interval: float = .12,
                 max_age: float = .32,
                 provider: Callable[[list[str]], Window | None] = foreground_window,
                 grab: Callable[[Window], np.ndarray] = snapshot,
                 clock: Callable[[], float] = time.monotonic):
        if not .03 <= interval <= 2.:
            raise ValueError('Intervallo cattura non valido')
        if max_age <= interval or max_age > 3.:
            raise ValueError('Età massima fotogramma non valida')
        self.names = list(allowed_names)
        self.interval, self.max_age = interval, max_age
        self.provider, self.grab, self.clock = provider, grab, clock
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._frame: CapturedFrame | None = None
        self._sequence = 0
        self._consumed = 0
        self.error: str | None = None
        self.captured = 0
        self.skipped = 0
        self.thread = threading.Thread(target=self._loop, name='OyeFrameStream', daemon=True)

    def start(self):
        self.thread.start()

    def _clear(self):
        with self._lock:
            self._frame = None

    def _loop(self):
        # Native screen capture handles MUST be opened and closed on this thread.
        try:
            while not self.stop_event.is_set():
                started = self.clock()
                try:
                    window = self.provider(self.names)
                    if window is None:
                        self._clear()
                    else:
                        frame = self.grab(window)
                        finished = self.clock()
                        if frame is None or frame.ndim != 3:
                            raise ValueError('Fotogramma acquisito non valido')
                        # Confirm focus/geometry again: a screenshot taken while
                        # alt-tabbing must not survive in the stream.
                        confirmed = self.provider(self.names)
                        if confirmed != window or self.stop_event.is_set():
                            self._clear()
                        else:
                            with self._lock:
                                self._sequence += 1
                                self._frame = CapturedFrame(frame, window, finished,
                                    (finished - started) * 1000., self._sequence)
                                self.captured += 1
                            self.error = None
                except Exception as exc:
                    self.error = f'{type(exc).__name__}: {str(exc)[:160]}'
                    self._clear()
                self.stop_event.wait(max(.005, self.interval - (self.clock() - started)))
        finally:
            close_capture()
            self._clear()

    def latest(self, expected_window: Window) -> CapturedFrame | None:
        with self._lock:
            frame = self._frame
            if (frame is None or frame.window != expected_window or
                    frame.sequence <= self._consumed or
                    self.clock() - frame.captured_at > self.max_age):
                self.skipped += 1
                return None
            self._consumed = frame.sequence
            return frame

    def stop(self, timeout: float = 2.):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout)
