"""Local vision-led gameplay planner with strict allowlists and cancellation epochs.

The local multimodal model proposes short, reversible actions; this module never
issues input. Only Runner may authorize execution after fresh foreground/HUD checks.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from queue import Empty, Queue
import threading
import time
from typing import Callable

from .models import Action
from .ollama_vision import LocalVisionModel

# An AI cannot supply arbitrary key names, mouse coordinates, or scripts.
LIMITS: dict[str, tuple[float, float]] = {
    'wait': (0., 0.),
    'forward': (.1, 1.5), 'forward_jump': (.1, .9),
    'forward_shoot': (.1, .35),
    'strafe_left_shoot': (.1, .35), 'strafe_right_shoot': (.1, .35),
    'reverse': (.1, .7), 'strafe_left': (.1, 1.), 'strafe_right': (.1, 1.),
    'turn_left': (.08, .5), 'turn_right': (.08, .5),
    'interact': (.08, .13), 'equip': (.08, .13),
    'harvest': (.1, .65), 'shoot': (.08, .25),
    'jump': (.08, .14), 'crouch': (.1, .8), 'reload': (.08, .14),
    'slot_1': (.08, .14), 'slot_2': (.08, .14),
    'aim': (.08, .55),
}


@dataclass(frozen=True)
class Proposal:
    action: Action
    confidence: float
    event: str
    description: str
    reasoning: str
    scene: str


def parse_proposal(payload: dict, *, shoot_allowed: bool, min_confidence: float = .55) -> Proposal:
    """Treat the model's JSON as untrusted and restrict it to fixed safe primitives."""
    if not isinstance(payload, dict):
        raise ValueError('Ollama: JSON azione non valido')
    name = payload.get('azione', 'wait')
    if not isinstance(name, str) or name not in LIMITS:
        name = 'wait'
    if (name == 'shoot' or name.endswith('_shoot')) and not shoot_allowed:
        # A model may not circumvent the explicit user permission for firing.
        name = 'wait'
    try:
        confidence = float(payload.get('confidenza', 0))
    except (TypeError, ValueError):
        confidence = 0.
    if not math.isfinite(confidence):
        confidence = 0.
    confidence = max(0., min(1., confidence))
    if confidence < min_confidence:
        name = 'wait'
    lo, hi = LIMITS[name]
    try:
        duration = float(payload.get('durata', lo))
    except (TypeError, ValueError):
        duration = lo
    if not math.isfinite(duration):
        duration = lo
    duration = max(lo, min(hi, duration))
    point = None
    if name == 'aim':
        # Relative camera offsets only, never absolute screen coordinates.
        try:
            dx, dy = float(payload.get('dx', 0)), float(payload.get('dy', 0))
            if not (math.isfinite(dx) and math.isfinite(dy)):
                raise ValueError('non-finite camera delta')
            point = (int(max(-180, min(180, dx))), int(max(-110, min(110, dy))))
            if point == (0, 0):
                name, duration = 'wait', 0.
        except (TypeError, ValueError):
            name, duration = 'wait', 0.
    def clean(key: str, length: int) -> str:
        value = payload.get(key, '')
        return ' '.join(value.split())[:length] if isinstance(value, str) else ''
    reason = clean('motivazione', 180)
    return Proposal(Action(name, duration, point=point if name == 'aim' else None,
                           reason=reason or 'piano Ollama locale'), confidence,
                    clean('evento', 100) or 'incerto', clean('descrizione', 500), reason,
                    clean('scena', 32).casefold())


@dataclass(frozen=True)
class PlannerResult:
    epoch: int
    submitted_at: float
    received_at: float
    proposal: Proposal | None
    error: str = ''


class AutonomousPlanner:
    """One local vision inference at a time, with no queued outdated screenshots.

    Epochs let Runner invalidate an inference when focus, pause or game state changes.
    The worker never calls the input driver. No model request can block screenshot capture.
    """
    def __init__(self, model: LocalVisionModel, *, shoot_allowed: bool = False,
                 min_confidence: float = .55, clock: Callable[[], float] = time.monotonic):
        self.model, self.shoot_allowed = model, shoot_allowed
        self.min_confidence, self.clock = min_confidence, clock
        self._jobs: Queue = Queue(maxsize=1)
        self._answers: Queue[PlannerResult] = Queue()
        self._lock = threading.Lock()
        self._busy = False
        self._working = False
        self._epoch = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='ObserverAutopilot', daemon=True)

    def start(self):
        self._thread.start()

    def submit(self, jpeg: bytes, *, context: str, ocr: str,
               history_images: list[bytes] | None = None) -> bool:
        with self._lock:
            if self._busy or self._stop.is_set():
                return False
            self._busy = True
            epoch = self._epoch
        self._jobs.put_nowait((epoch, self.clock(), jpeg, context[:1200], ocr[:1200],
                               list(history_images or [])[-2:]))
        return True

    def invalidate(self) -> None:
        with self._lock:
            self._epoch += 1
            if not self._working:
                # An unconsumed result must not leave the worker permanently busy.
                self._busy = False
        # Pending jobs and answers belong to an old scene/focus epoch.
        while True:
            try:
                self._jobs.get_nowait()
                self._jobs.task_done()
            except Empty:
                break
        while True:
            try:
                self._answers.get_nowait()
            except Empty:
                break

    def get_result(self) -> PlannerResult | None:
        try:
            result = self._answers.get_nowait()
        except Empty:
            return None
        with self._lock:
            if result.epoch == self._epoch:
                self._busy = False
                return result
            return None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def _run(self):
        while not self._stop.is_set():
            try:
                epoch, submitted, jpeg, context, ocr, history = self._jobs.get(timeout=.2)
            except Empty:
                continue
            with self._lock:
                if epoch != self._epoch or self._stop.is_set():
                    self._jobs.task_done()
                    continue
                self._working = True
            proposal = None
            error = ''
            try:
                data = self.model.plan(jpeg, context=context, ocr=ocr, history_images=history)
                proposal = parse_proposal(data, shoot_allowed=self.shoot_allowed,
                                          min_confidence=self.min_confidence)
            except Exception as exc:
                error = f'{type(exc).__name__}: {str(exc)[:220]}'
            finally:
                with self._lock:
                    self._working = False
                    if epoch == self._epoch and not self._stop.is_set():
                        # Keep busy until Runner consumes the result. This prevents
                        # a second request sneaking in before the first is applied.
                        self._answers.put(PlannerResult(epoch, submitted, self.clock(), proposal, error))
                    else:
                        self._busy = False
                self._jobs.task_done()

    def stop(self, timeout: float = .5):
        self._stop.set()
        self.invalidate()
        if self._thread.is_alive():
            self._thread.join(timeout)
