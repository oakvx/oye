"""Conservative activity-level temporal inference for screenshots inside a match.

Phases (lobby/loading/match/...) and gameplay activities are distinct tracks:
being in a match does not prove that the player is fighting.  Only real
annotations or supported visual predictions assign a specific activity.
No image or SQLite mutation, networking, or model inference occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# The keys are intentionally a small, editable vocabulary. Free-form notes
# remain in the user's annotation and are never silently mapped to activities.
ACTIVITY_NAMES = {
    'combat': 'Combattimento', 'movement': 'Movimento', 'loot': 'Raccolta',
    'landing': 'Atterraggio', 'healing': 'Cura', 'inventory': 'Inventario',
    'exploration': 'Esplorazione', 'bus': 'Bus della battaglia',
    'skydiving': 'Discesa', 'unknown': 'Attività incerta',
}
_ALIASES = {
    'combattimento': 'combat', 'scontro': 'combat', 'sparando': 'combat',
    'movimento': 'movement', 'spostamento': 'movement', 'camminata': 'movement',
    'raccolta': 'loot', 'raccolta risorse': 'loot', 'loot': 'loot',
    'atterraggio': 'landing', 'discesa': 'landing',
    'cura': 'healing', 'guarigione': 'healing', 'uso medikit': 'healing',
    'inventario': 'inventory', 'gestione inventario': 'inventory',
    'esplorazione': 'exploration', 'bus della battaglia': 'bus',
    'discesa dal bus': 'skydiving', 'caduta libera': 'skydiving',
}


def activity_from_label(value: str | None) -> str | None:
    if not value:
        return None
    return _ALIASES.get(' '.join(str(value).casefold().strip().split()))


@dataclass(frozen=True)
class ActivityEstimate:
    frame_id: int
    activity: str
    confidence: float
    source: str
    verified: bool = False
    review_required: bool = False

    @property
    def label(self) -> str:
        return ACTIVITY_NAMES[self.activity]


def infer_activities(frames: Sequence[dict], phases: Sequence) -> list[ActivityEstimate]:
    """Classify only in-match activity, with conservative temporal smoothing.

    A one-frame weak event between two established segments is not promoted.
    Human corrections are never smoothed away, and event predictions do not
    silently become training labels. The original input order is retained.
    """
    if len(frames) != len(phases):
        raise ValueError('Fasi e fotogrammi non allineati')
    if not frames:
        return []
    if any(row.get('session') != frames[0].get('session') for row in frames):
        raise ValueError('Una sessione per volta')
    if any(int(row['id']) != phase.frame_id for row, phase in zip(frames, phases)):
        raise ValueError('L’ordine delle fasi non corrisponde ai fotogrammi')

    ordered = sorted(range(len(frames)), key=lambda i: (frames[i].get('captured_at') or '', frames[i]['id']))
    result: list[ActivityEstimate | None] = [None] * len(frames)
    for i in ordered:
        row, phase = frames[i], phases[i]
        chosen = 'unknown'
        score = 0.0
        verified = False
        source = 'nessuna evidenza'
        if phase.state == 'match':
            annotation = activity_from_label(row.get('user_label'))
            prediction = activity_from_label(row.get('predicted_event'))
            if annotation:
                chosen, score, verified, source = annotation, 0.99, True, 'correzione utente'
            elif prediction:
                try:
                    strength = max(0., min(1., float(row.get('predicted_score') or 0)))
                except (TypeError, ValueError):
                    strength = 0.
                if strength >= 0.62:
                    chosen, score, source = prediction, min(0.89, .30 + .6 * strength), 'modello visivo'
        result[i] = ActivityEstimate(int(row['id']), chosen, score, source, verified,
                                     chosen == 'unknown' and phase.state == 'match')

    # Remove single, unverified weak spikes *inside* another established event.
    # Never hallucinate an activity into an unknown frame; review it instead.
    for j in range(1, len(ordered)-1):
        middle = ordered[j]
        before, after = result[ordered[j-1]], result[ordered[j+1]]
        current = result[middle]
        if current.verified or current.activity == 'unknown':
            continue
        if before.activity == after.activity != 'unknown' and before.activity != current.activity:
            # Do not cross a long observation gap just to smooth a spike.
            from .timeline import elapsed_seconds
            a, b, c = (frames[ordered[k]]['captured_at'] for k in (j-1,j,j+1))
            if elapsed_seconds(a,b) <= 10 and elapsed_seconds(b,c) <= 10:
                result[middle] = ActivityEstimate(current.frame_id, 'unknown', .0,
                                                 'evento isolato non confermato', False, True)
    return result


def activity_spans(frames: Sequence[dict], activities: Sequence[ActivityEstimate]) -> list[dict]:
    if len(frames) != len(activities):
        raise ValueError('Attività e fotogrammi non allineati')
    ordered = sorted(zip(frames, activities), key=lambda pair: (pair[0]['captured_at'], pair[0]['id']))
    spans = []
    prev_activity = None
    for row, activity in ordered:
        name = activity.activity
        if name == 'unknown':
            prev_activity = None
            continue
        if name != prev_activity:
            spans.append({'activity': name, 'label': activity.label,
                          'start': row['captured_at'], 'end': row['captured_at'],
                          'frame_ids': [], 'confidence_sum': 0., 'verified_count': 0})
        span = spans[-1]
        span['frame_ids'].append(activity.frame_id)
        span['end'] = row['captured_at']
        span['confidence_sum'] += activity.confidence
        span['verified_count'] += int(activity.verified)
        prev_activity = name
    for span in spans:
        span['count'] = len(span['frame_ids'])
        span['confidence'] = span.pop('confidence_sum') / span['count']
    return spans
