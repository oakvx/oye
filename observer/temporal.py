"""Fast, explainable sequence-aware interpretation of human-play screenshots.

The model never fabricates a visual event: it weighs HUD/OCR/ML evidence against
plausible game-state transitions. Weak, surprising detections are held for review.
Human corrections remain the only verified truth. Uses O(frames * states^2), with
six states and no neural inference or mandatory external dependencies.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import math
import re

STATES = ('lobby', 'loading', 'match', 'spectator', 'results', 'unknown')
NAMES = {
    'lobby': 'Lobby', 'loading': 'Caricamento', 'match': 'In partita',
    'spectator': 'Spettatore', 'results': 'Risultati', 'unknown': 'Da identificare',
}
NEXT = {
    'lobby': ('loading', 'match'),
    'loading': ('match', 'lobby'),  # Matchmaking can fail or be canceled.
    'match': ('spectator', 'results', 'lobby'),
    'spectator': ('results', 'match', 'lobby'),  # Revives, respawns, exiting.
    'results': ('lobby', 'loading'),
    'unknown': (),
}
# Weights are soft, not rigid transition prohibitions: enough real evidence
# overrules the expected order, including a session beginning mid-match.
_TRANSITIONS = {
    'lobby': {'lobby': 1.15, 'loading': 1.0, 'match': -0.6, 'spectator': -3.2, 'results': -3.6, 'unknown': -0.5},
    'loading': {'lobby': -0.1, 'loading': 1.1, 'match': 1.0, 'spectator': -3.2, 'results': -3.6, 'unknown': -0.5},
    'match': {'lobby': -1.0, 'loading': -1.0, 'match': 1.15, 'spectator': 0.8, 'results': 0.7, 'unknown': -0.5},
    'spectator': {'lobby': -0.1, 'loading': -2.0, 'match': -0.35, 'spectator': 1.15, 'results': 0.85, 'unknown': -0.5},
    'results': {'lobby': 0.9, 'loading': -0.35, 'match': -1.5, 'spectator': -2.0, 'results': 1.15, 'unknown': -0.5},
    'unknown': {'lobby': 0, 'loading': 0, 'match': 0, 'spectator': 0, 'results': -0.25, 'unknown': 0.1},
}
# Exact stage labels (and a small set of clearly gameplay-specific events).
_ALIASES = {
    'lobby': 'lobby', 'nella lobby': 'lobby', 'menu lobby': 'lobby',
    'caricamento': 'loading', 'schermata di caricamento': 'loading',
    'ricerca partita': 'loading', 'matchmaking': 'loading',
    'partita in corso': 'match', 'in partita': 'match', 'gameplay': 'match',
    'combattimento': 'match', 'scontro': 'match', 'sparando': 'match',
    'atterraggio': 'match', 'raccolta risorse': 'match', 'movimento': 'match',
    'bus della battaglia': 'match', 'discesa': 'match',
    'modalità spettatore': 'spectator', 'spettatore': 'spectator',
    'eliminazione': 'spectator', 'sono stato eliminato': 'spectator',
    'risultati partita': 'results', 'risultati': 'results',
    'schermata risultati': 'results', 'vittoria': 'results',
    'incerto': 'unknown', 'non visibile': 'unknown',
}


def event_stage(label: str | None) -> str | None:
    if not label:
        return None
    value = ' '.join(str(label).casefold().strip().split())
    # Do not coerce arbitrary free-text interval descriptions into game states.
    return _ALIASES.get(value)


def _time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed
    except (TypeError, ValueError):
        return None


def _text_stage(text: str) -> str | None:
    low = str(text or '').casefold()
    # The 'return to lobby' button is also visible during spectating:
    # it is NOT proof of final results.
    if re.search(r'\b(victory royale|vittoria reale|risultati partita|match results|match summary)\b', low):
        return 'results'
    if re.search(r'\b(spectating|spettatore|stai guardando|you were eliminated|sei stato eliminato)\b', low):
        return 'spectator'
    if re.search(r'\b(matchmaking|connecting to server|connessione al server)\b', low):
        return 'loading'
    # Gameplay begins before landing: the battle bus and skydiving are not
    # combat but they do belong to a match. This is timeline evidence only.
    if re.search(r'\b(battle bus|bus della battaglia|space to jump|deploy glider|open glider|apri il deltaplano)\b', low):
        return 'match'
    return None


def _emission(row: dict) -> tuple[dict[str, float], list[str], bool]:
    """Return scene evidence, provenance, and whether direct evidence exists."""
    score = {state: 0.0 for state in STATES}
    signals = []
    definite = False
    hud = row.get('hud_scene')
    if hud in STATES and hud != 'unknown':
        strength = 4.3 if hud in ('spectator', 'results') else 3.5
        score[hud] += strength
        signals.append('HUD')
        definite = True
    annotated = event_stage(row.get('user_label'))
    if annotated and annotated != 'unknown':
        score[annotated] += 12.0
        for other in STATES:
            if other != annotated:
                score[other] -= 7.0
        signals.append('correzione utente')
        definite = True
    text = _text_stage(row.get('ocr_text') or '')
    if text is not None:
        score[text] += 4.7
        signals.append('OCR')
        definite = True
    predicted = event_stage(row.get('predicted_event'))
    if predicted and predicted != 'unknown':
        try:
            confidence = max(0.0, min(1.0, float(row.get('predicted_score') or 0)))
        except (TypeError, ValueError):
            confidence = 0.0
        # A raw ML/VLM prediction is never as strong as direct HUD evidence.
        score[predicted] += (0.6 + 1.8 * confidence)
        signals.append('modello visivo')
        definite |= confidence >= .80 and row.get('predicted_source') in ('ml-supervisionato', 'ml-esempi')
    return score, signals, definite


@dataclass(frozen=True)
class TemporalEstimate:
    frame_id: int
    state: str
    confidence: float
    reason: str
    anomaly: bool = False
    verified: bool = False
    review_required: bool = False

    @property
    def label(self):
        return NAMES[self.state]


def infer_states(frames: list[dict], *, learned: Counter | None = None) -> list[TemporalEstimate]:
    """Offline Viterbi over one session; outputs original input order.

    A future frame may improve a prior *hypothesis* when reviewed. Nothing here
    overwrites the model's original prediction or a manual correction.
    """
    if not frames:
        return []
    session = frames[0].get('session')
    if any(row.get('session') != session for row in frames):
        raise ValueError('Elaborare una sessione alla volta')
    ordered = sorted(enumerate(frames), key=lambda pair: (pair[1].get('captured_at') or '', pair[1].get('id', 0)))
    evidence = [_emission(row) for _, row in ordered]
    scores = []
    pointers = []
    previous_time = None
    for index, (_, row) in enumerate(ordered):
        emissions, _, _ = evidence[index]
        # Human interval annotations are authoritative, even if a long history
        # would otherwise make an unlikely transition look attractive.
        user_stage = event_stage(row.get('user_label'))
        if user_stage and user_stage != 'unknown':
            for dest in STATES:
                if dest != user_stage:
                    emissions[dest] -= 1_000_000.0
        current = {}
        back = {}
        stamp = _time(row.get('captured_at'))
        delta = (stamp - previous_time).total_seconds() if stamp and previous_time else 0.
        if stamp:
            previous_time = stamp
        # When samples are far apart, an intermediate transition may not have
        # been captured, so soften rather than enforce direct-transition costs.
        soften = max(0., min(0.85, (delta - 9.) / 45.))
        for dest in STATES:
            if index == 0:
                current[dest] = emissions[dest] + (0.1 if dest != 'unknown' else 0)
                continue
            best = float('-inf')
            parent = 'unknown'
            for origin in STATES:
                transition = _TRANSITIONS[origin][dest]
                if origin != dest:
                    transition *= 1. - soften
                    if learned and origin != 'unknown' and dest != 'unknown':
                        transition += min(0.9, math.log1p(max(0, learned[(origin, dest)])) * .22)
                candidate = scores[-1][origin] + transition
                if candidate > best:
                    best, parent = candidate, origin
            current[dest] = best + emissions[dest]
            back[dest] = parent
        scores.append(current)
        pointers.append(back)
    winner = max(STATES, key=lambda s: scores[-1][s])
    winners = [winner]
    for index in range(len(ordered) - 1, 0, -1):
        winner = pointers[index][winner]
        winners.append(winner)
    winners.reverse()
    output = [None] * len(frames)
    for index, (original_position, row) in enumerate(ordered):
        state = winners[index]
        emission, signals, definite = evidence[index]
        direct_state = max(STATES, key=lambda s: emission[s])
        direct_score = emission[direct_state]
        verified = event_stage(row.get('user_label')) == state and state != 'unknown'
        prior = winners[index - 1] if index else None
        anomaly = (prior is not None and state != prior and state not in NEXT[prior] and
                   prior != 'unknown' and state != 'unknown')
        # Confidence is a qualitative review priority, not a calibrated ML probability.
        if verified:
            confidence = .99
            reason = 'Stato confermato dall’utente'
        elif direct_state == state and direct_score >= 3:
            confidence = min(.95, 0.65 + direct_score * .045)
            reason = ', '.join(signals) or 'indizio visivo'
        elif direct_state == state and direct_score > 0:
            confidence = .55
            reason = 'Ipotesi visiva coerente con la sequenza'
        elif state != 'unknown':
            confidence = .35
            reason = 'Contesto temporale: mancano indizi diretti'
        else:
            confidence = .2
            reason = 'Stato non riconosciuto'
        if anomaly:
            reason += ' · transizione insolita da verificare'
            confidence = min(confidence, .55) if not verified else confidence
        if direct_state != state and direct_score > 0:
            reason += f' · ipotesi «{NAMES[direct_state]}» non confermata'
        # Review weak, inconsistent or structurally surprising transitions,
        # while keeping raw predictions intact for later comparison.
        conflict = direct_state != state and direct_score > .2
        needs_review = (anomaly or conflict or confidence < .60) and not verified
        output[original_position] = TemporalEstimate(int(row.get('id', 0)), state,
                                                        confidence, reason, anomaly, verified,
                                                        needs_review)
    return output


def inferred_spans(frames: list[dict], estimates: list[TemporalEstimate]) -> list[dict]:
    """Create stage ranges bounded by samples, never invented frame timestamps."""
    if len(frames) != len(estimates):
        raise ValueError('Fotogrammi e predizioni non allineati')
    pairs = sorted(zip(frames, estimates), key=lambda pair: (pair[0].get('captured_at') or '', pair[0].get('id', 0)))
    spans = []
    for row, estimate in pairs:
        if not spans or estimate.state != spans[-1]['state']:
            spans.append({'state': estimate.state, 'label': estimate.label,
                          'start': row['captured_at'], 'end': row['captured_at'],
                          'frame_ids': [], 'confidence': 0.0,
                          'verified_count': 0, 'review_count': 0})
        current = spans[-1]
        current['frame_ids'].append(int(row['id']))
        current['end'] = row['captured_at']
        current['confidence'] += estimate.confidence
        current['verified_count'] += bool(estimate.verified)
        current['review_count'] += bool(estimate.review_required)
    for item in spans:
        item['count'] = len(item['frame_ids'])
        item['confidence'] /= item['count']
    return spans


class TemporalTracker:
    """Causal rolling stage estimate for the live observation worker.

    Unlike the editor's offline review, this tracker sees only past/current
    frames. No disk IO, VLM call, input injection, or automatic training.
    """
    def __init__(self, *, window: int = 20):
        from collections import deque
        self._rows = deque(maxlen=max(3, int(window)))
        self._session = None
        self._last = None

    def observe(self, *, session: str, captured_at: str, scene: str,
                predicted_event: str, predicted_score: float = 0.,
                predicted_source: str = '', ocr_text: str = '', learned: Counter | None = None
                ) -> TemporalEstimate:
        if session != self._session:
            self._session = session
            self._rows.clear()
            self._last = None
        row = {'session': session, 'id': len(self._rows) + 1,
               'captured_at': captured_at, 'hud_scene': scene,
               'predicted_event': predicted_event, 'predicted_score': predicted_score,
               'predicted_source': predicted_source, 'ocr_text': ocr_text}
        self._rows.append(row)
        self._last = infer_states(list(self._rows), learned=learned)[-1]
        return self._last
