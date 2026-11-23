"""Presentation-only timeline of stored human-play captures.

Event boundaries are inferred from sampled frames: they are not ground truth.
UTC ISO timestamps are stored, and converted only for local display.
"""
from __future__ import annotations

from datetime import datetime, timezone


def parse_time(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def display_time(value: str | None) -> str:
    if not value:
        return '—'
    return parse_time(value).astimezone().strftime('%H:%M:%S')


def elapsed_seconds(start: str, current: str) -> float:
    return max(0.0, (parse_time(current) - parse_time(start)).total_seconds())


def display_elapsed(seconds: float) -> str:
    value = max(0, int(seconds))
    return f'{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}'


def effective_event(frame: dict) -> str:
    return frame.get('user_label') or frame.get('predicted_event') or 'incerto'


def review_state(frame: dict) -> str:
    if frame.get('user_label') is None:
        return 'Da rivedere'
    if frame['user_label'] == frame.get('predicted_event'):
        return 'Confermato'
    return 'Corretto'


def event_spans(frames: list[dict]) -> list[dict]:
    """Consecutive equal effective labels form segments, chronologically.

    We know only the timestamps of recorded samples. The actual beginning/end
    of an event can fall between these instants; never extrapolate timestamps.
    """
    rows = sorted(frames, key=lambda row: (parse_time(row['captured_at']), row['id']))
    if not rows:
        return []
    zero = rows[0]['captured_at']
    spans = []
    for row in rows:
        event = effective_event(row)
        if not spans or spans[-1]['event'] != event:
            spans.append({'event': event, 'first_id': row['id'], 'last_id': row['id'],
                          'frame_ids': [row['id']], 'start': row['captured_at'],
                          'end': row['captured_at'], 'start_elapsed': elapsed_seconds(zero, row['captured_at']),
                          'end_elapsed': elapsed_seconds(zero, row['captured_at']),
                          'sources': set(), 'scores': [], 'corrected': 0, 'confirmed': 0})
        span = spans[-1]
        span['last_id'] = row['id']
        span['frame_ids'].append(row['id']) if row['id'] != span['frame_ids'][-1] else None
        span['end'] = row['captured_at']
        span['end_elapsed'] = elapsed_seconds(zero, row['captured_at'])
        span['sources'].add(row.get('predicted_source') or '—')
        span['scores'].append(float(row.get('predicted_score') or 0))
        if row.get('user_label') is not None:
            span['confirmed'] += 1
            if review_state(row) == 'Corretto':
                span['corrected'] += 1
    for span in spans:
        span['count'] = len(span['frame_ids'])
        span['average_score'] = sum(span.pop('scores')) / span['count']
        span['sources'] = ', '.join(sorted(span['sources']))
    return spans


def session_window(frames: list[dict], markers: list[dict] | None = None) -> dict | None:
    timeline = []
    for row in frames:
        timeline.append(row['captured_at'])
    for entry in markers or []:
        if entry['kind'] in ('start', 'end'):
            timeline.append(entry['occurred_at'])
    if not timeline:
        return None
    start, end = min(timeline, key=parse_time), max(timeline, key=parse_time)
    return {'start': start, 'end': end, 'duration': elapsed_seconds(start, end),
            'exact_start': any(x['kind'] == 'start' for x in (markers or [])),
            'exact_end': any(x['kind'] == 'end' for x in (markers or []))}


def timecode(seconds: float, *, precision: int = 1) -> str:
    """Editor timecode in HH:MM:SS.d (not a clock time)."""
    if not 0 <= seconds < 60 * 60 * 48:
        raise ValueError('Tempo fuori intervallo')
    hours = int(seconds // 3600)
    minutes = int(seconds // 60) % 60
    sec = seconds - hours * 3600 - minutes * 60
    return f'{hours:02d}:{minutes:02d}:{sec:0{3 + precision}.{precision}f}'


def parse_timecode(value: str) -> float:
    """Accept MM:SS[.d] or HH:MM:SS[.d]; reject rollover and negatives."""
    import re
    parts = str(value).strip().split(':')
    if len(parts) not in (2, 3) or not all(re.fullmatch(r'\d+(?:\.\d+)?', p) for p in parts):
        raise ValueError('Usa MM:SS oppure HH:MM:SS (ad esempio 00:01:14.5)')
    if len(parts) == 2:
        minutes, seconds = float(parts[0]), float(parts[1])
        hours = 0
    else:
        hours, minutes, seconds = map(float, parts)
    if int(minutes) != minutes or minutes >= 60 or seconds >= 60 or hours >= 48 or (len(parts) == 3 and int(hours) != hours):
        raise ValueError('Minuti e secondi devono essere inferiori a 60')
    return hours * 3600 + minutes * 60 + seconds


def sampled_in_range(frames: list[dict], origin: str, start: float, end: float) -> list[int]:
    """Samples whose timestamps fall inside the inclusive editor IN/OUT region."""
    if start < 0 or end <= start:
        raise ValueError('Intervallo temporale non valido')
    return [int(row['id']) for row in frames
            if start - 1e-6 <= elapsed_seconds(origin, row['captured_at']) <= end + 1e-6]
