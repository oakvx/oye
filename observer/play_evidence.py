"""Conservative, input-free PLAY evidence accumulator.

A scanner's method, not its name, determines independence. Rectangle and
point use absolute captured-window coordinates. A reading never refreshes by
being reused on a later video frame.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PlaySignal:
    source: str
    method: str
    at: float
    window: object
    status: str
    score: float
    bounds: tuple[int, int, int, int]
    point: tuple[int, int] | None = None


@dataclass(frozen=True)
class PlayConfirmation:
    point: tuple[int, int]
    at: float
    signals: tuple[PlaySignal, PlaySignal]


class PlayEvidence:
    """Count distinct corroborated observations; all inputs are untrusted.

    Only template versus OCR is presently accepted as independent; scanner
    aliases or two templates do not become independent by having different IDs.
    A trusted caller must supply explicit fresh incompatibility observations.
    """
    MAX_AGE = 4.0
    MAX_GAP = 7.5
    VETO_SCENES = frozenset(('results', 'loading', 'spectator', 'rewards', 'match'))

    def __init__(self):
        self.reset()

    def reset(self):
        self.window = None
        self.latest = {}
        self.count = 0
        self.counted_at = {}
        self.last_pair_at = None
        self.veto_at = None

    def update(self, *, now, window, focused, signals=(), scene='unknown'):
        if not math.isfinite(now) or not focused or window is None:
            self.reset()
            return None
        if self.window is not None and window != self.window:
            self.reset()
        self.window = window
        if scene in self.VETO_SCENES:
            self.latest.clear()
            self.count = 0
            self.counted_at.clear()
            self.last_pair_at = None
            self.veto_at = now
            return None
        if self.veto_at is not None and 0 <= now - self.veto_at <= self.MAX_AGE:
            return None
        for signal in signals:
            if (signal.method not in ('template', 'ocr') or signal.window != window or
                signal.status not in ('present', 'absent', 'unknown') or
                not math.isfinite(signal.at) or not math.isfinite(signal.score) or
                not 0 <= now - signal.at <= self.MAX_AGE):
                continue
            previous = self.latest.get(signal.source)
            if previous is not None and signal.at <= previous.at:
                continue
            self.latest[signal.source] = signal
            if signal.status == 'absent':
                self.count = 0
                self.counted_at.clear()
                self.last_pair_at = None
        fresh = [s for s in self.latest.values() if 0 <= now - s.at <= self.MAX_AGE]
        if any(s.status == 'absent' for s in fresh):
            return None
        positive = [s for s in fresh if s.status == 'present']
        for template in (s for s in positive if s.method == 'template' and s.point is not None):
            for ocr in (s for s in positive if s.method == 'ocr' and s.source != template.source):
                p = template.point
                try:
                    valid = (len(p) == 2 and all(type(v) is int for v in p) and
                             all(len(b) == 4 and all(type(v) is int for v in b) and
                                 b[0] <= p[0] < b[2] and b[1] <= p[1] < b[3]
                                 for b in (template.bounds, ocr.bounds)))
                except TypeError:
                    valid = False
                if not valid:
                    continue
                if any(s.at <= self.counted_at.get(s.method, float('-inf')) for s in (template, ocr)):
                    return None
                if self.last_pair_at is not None and now - self.last_pair_at > self.MAX_GAP:
                    self.count = 0
                self.count += 1
                self.last_pair_at = now
                self.counted_at.update({s.method: s.at for s in (template, ocr)})
                if self.count >= 3:
                    return PlayConfirmation(p, min(template.at, ocr.at), (template, ocr))
                return None
        return None
