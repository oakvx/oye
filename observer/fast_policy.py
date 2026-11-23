"""Fast, conservative imitation policy trained ONLY on user-approved actions.

This is a small non-parametric supervised learner: no cloud service, GPU or slow
LLM at decision time. Unknown screens always return WAIT; a screenshot alone
cannot tell us which controls a human demonstrator pressed.
"""
from __future__ import annotations
from dataclasses import dataclass
import time
import numpy as np
from .autopilot import LIMITS
from .models import Action
from .learning import FEATURE_SIZE, SCENE_EVENTS

# OCR can change every second and demonstration frames have no OCR. The action
# learner uses stable visual cues; OCR remains available to the event learner.
_TEXT_END = FEATURE_SIZE - len(SCENE_EVENTS)
_TEXT_START = _TEXT_END - 64

SAFE_ACTIONS = tuple(name for name in LIMITS if name != 'shoot') + ('shoot',)


@dataclass(frozen=True)
class FastDecision:
    action: Action
    confidence: float
    support: int
    distance: float
    latency_ms: float


class FastActionPolicy:
    """Bounded nearest-neighbor voting with cached squared norms."""
    def __init__(self, *, max_examples: int = 1200, max_distance: float = .105,
                 agreement: float = .79, action_seconds: float = .12):
        self.max_examples = max_examples
        self.max_distance = max_distance
        self.agreement = agreement
        if not .10 <= action_seconds <= .50:
            raise ValueError('Durata azioni ML non valida')
        self.action_seconds = action_seconds
        self.features = np.empty((0, 1), dtype=np.float32)
        self.labels: list[str] = []
        self._revision = -1
        self._squared_norms: np.ndarray | None = None
        self.last_reason = 'non ancora valutata'

    def refresh(self, store) -> None:
        rev = store.revision()
        if rev == self._revision:
            return
        examples = store.action_examples(limit=self.max_examples)
        self.labels = [label for _, label in examples]
        if examples:
            self.features = np.ascontiguousarray(np.stack([f for f, _ in examples]), dtype=np.float32)
            self.features[:, _TEXT_START:_TEXT_END] = 0.0
        else:
            self.features = np.empty((0, 1), dtype=np.float32)
        # Precompute once on updates: each prediction avoids allocating a
        # full example-by-feature difference matrix.
        self._squared_norms = np.einsum('ij,ij->i', self.features, self.features)
        self._revision = rev

    @property
    def examples(self) -> int:
        return len(self.labels)

    def predict(self, feature: np.ndarray, *, allow_shoot: bool = False) -> FastDecision | None:
        start = time.perf_counter()
        self.last_reason = 'nessuna decisione'
        # At least three independent demonstrations and two actions prevent
        # blindly copying one isolated screenshot to an entire match.
        if len(self.labels) < 3 or len(set(self.labels)) < 2:
            self.last_reason = 'servono almeno 3 esempi e 2 azioni differenti'
            return None
        sample = np.asarray(feature, dtype=np.float32)
        if sample.ndim != 1 or sample.shape[0] != self.features.shape[1]:
            self.last_reason = 'feature visive incompatibili con il dataset'
            return None
        sample = sample.copy()
        sample[_TEXT_START:_TEXT_END] = 0.0
        # Restrict the vote to the nearest examples, without full sorting.
        # Squared Euclidean distance: ||x-y||² = ||x||² + ||y||² - 2x·y.
        # einsum avoids BLAS thread-start overhead on small (<1200) datasets.
        # Legacy tests may assign .features directly; recompute norms in that case.
        norms = self._squared_norms
        if norms is None or norms.shape[0] != len(self.features):
            norms = np.einsum('ij,ij->i', self.features, self.features)
        distances_sq = norms + float(np.dot(sample, sample)) - 2.0 * np.einsum(
            'ij,j->i', self.features, sample, optimize=False)
        np.maximum(distances_sq, 0.0, out=distances_sq)
        neighbors = min(5, len(self.labels))
        idx = np.argpartition(distances_sq, neighbors - 1)[:neighbors]
        near = np.sqrt(distances_sq[idx]) / np.sqrt(sample.size)
        closest = float(np.min(near))
        if closest > self.max_distance:
            self.last_reason = f'nessuna scena simile (distanza {closest:.3f} > {self.max_distance:.3f})'
            return None
        votes: dict[str, float] = {}
        counts: dict[str, int] = {}
        for i, dist in zip(idx, near):
            if float(dist) > self.max_distance:
                continue
            name = self.labels[int(i)]
            if name not in LIMITS:
                continue
            if not allow_shoot:
                if name == 'shoot':
                    continue
                if name.endswith('_shoot'):
                    # Still learn how to walk from a W + shoot demonstration,
                    # even when the user has disabled automatic firing.
                    name = name.removesuffix('_shoot')
            weight = 1.0 / max(.02, float(dist))
            votes[name] = votes.get(name, 0.) + weight
            counts[name] = counts.get(name, 0) + 1
        if not votes:
            self.last_reason = 'esempi vicini non consentiti dalle impostazioni'
            return None
        choice = max(votes, key=votes.get)
        share = votes[choice] / sum(votes.values())
        if counts[choice] < 2 or share < self.agreement:
            self.last_reason = f'accordo insufficiente: {counts[choice]} esempi, {share:.2f} < {self.agreement:.2f}'
            return None
        lo, hi = LIMITS[choice]
        # Short, bounded actions; no unverified cursor or aim coordinates.
        if choice == 'aim':
            self.last_reason = 'puntamento senza coordinate dimostrate non riproducibile'
            return None
        # A 120 ms key pulse every 150 ms can produce nearly invisible,
        # intermittent walking. Movement uses a bounded >=280 ms hold instead.
        movement = choice in ('forward', 'forward_jump', 'forward_shoot', 'reverse',
                              'strafe_left', 'strafe_right', 'strafe_left_shoot', 'strafe_right_shoot')
        duration = 0. if choice == 'wait' else min(hi, max(lo, self.action_seconds,
                                                            .28 if movement else lo))
        confidence = min(.97, max(0., share * (1. - closest / (self.max_distance * 1.4))))
        self.last_reason = f'{choice}, confidenza {confidence:.2f}, supporto {counts[choice]}'
        return FastDecision(Action(choice, duration, reason='imitazione supervisionata locale'),
                            round(confidence, 3), counts[choice], round(closest, 4),
                            round((time.perf_counter() - start) * 1000, 3))
