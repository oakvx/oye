"""Session and persistent counters for independent OCR regions.

An event is a *new confirmed appearance*, not every frame containing a label.
A stale/failed OCR is UNKNOWN and cannot create a new event or prove absence.
The counter consumes results from the existing OCR worker; it never captures
screenshots, calls Tesseract, or authorizes gameplay input.
"""
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import closing
from pathlib import Path
import sqlite3
import threading


class OCRDetectionCounter:
    def __init__(self, root: Path, *, confirmations: int = 2, expiry: float = 25.0):
        self.root = Path(root)
        self.path = self.root / 'learning' / 'observer.sqlite3'
        self.confirmations = max(2, int(confirmations))
        self.expiry = max(5.0, float(expiry))
        self.lock = threading.RLock()
        self.tracking: dict[str, dict] = {}
        self.session_scans = 0
        self.session_hits = 0
        self.session_events = 0
        self.events = deque(maxlen=15)
        self.per_target = defaultdict(int)
        self.storage_error = ''
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=3.0)) as db, db:
                db.execute('''CREATE TABLE IF NOT EXISTS oye_ocr_detection_counts (
                    target TEXT PRIMARY KEY, total INTEGER NOT NULL DEFAULT 0,
                    last_at REAL NOT NULL, last_text TEXT NOT NULL DEFAULT '',
                    last_confidence REAL NOT NULL DEFAULT 0)''')
                db.execute('''CREATE TABLE IF NOT EXISTS oye_ocr_detection_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL,
                    label TEXT NOT NULL, at REAL NOT NULL,
                    text TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0)''')
                db.execute('''CREATE INDEX IF NOT EXISTS oye_ocr_detection_recent
                    ON oye_ocr_detection_events(id DESC)''')
        except (sqlite3.Error, OSError) as exc:
            # Quick/observation modes must survive a damaged training database.
            self.storage_error = type(exc).__name__

    def observe(self, target: str, *, at: float, status: str, matched: bool,
                text: str = '', confidence: float = 0., stable: bool | None = None,
                label: str = '', count_scan: bool = True) -> bool:
        """Consume ONE fresh actual OCR acquisition. Return True for new event."""
        target = str(target)[:80]
        if not target:
            return False
        with self.lock:
            entry = self.tracking.setdefault(target, {
                'at': float('-inf'), 'positive_at': float('-inf'),
                'positive_streak': 0, 'negative_streak': 0, 'active': False,
            })
            # Replayed cached results and identical screenshot timestamps do
            # not inflate scans, evidence streaks, or discovery counts.
            if at <= entry['at']:
                return False
            gap = at - entry['at']
            if gap > self.expiry:
                entry.update(active=False, positive_streak=0, negative_streak=0)
            entry['at'] = float(at)
            # Unknown/errors are neither positive nor negative observations.
            if status != 'ok':
                return False
            if count_scan:
                self.session_scans += 1
            if not matched:
                entry['negative_streak'] += 1
                entry['positive_streak'] = 0
                if entry['negative_streak'] >= 2:
                    entry['active'] = False
                return False
            self.session_hits += 1
            entry['negative_streak'] = 0
            if at - entry['positive_at'] > self.expiry:
                entry['positive_streak'] = 0
            entry['positive_at'] = at
            entry['positive_streak'] += 1
            # Legacy region OCR has already applied a per-target 2-frame gate.
            # Modular OCR gets the same 2-frame requirement here.
            confirmed = bool(stable) if stable is not None else entry['positive_streak'] >= self.confirmations
            if not confirmed or entry['active']:
                return False
            entry['active'] = True
            self.session_events += 1
            self.per_target[target] += 1
            event = {'target': target, 'label': str(label or target)[:100],
                     'at': round(float(at), 3), 'text': str(text)[:120],
                     'confidence': round(min(1., max(0., float(confidence))), 3)}
            self.events.appendleft(event)
            try:
                with closing(sqlite3.connect(self.path, timeout=3.0)) as db, db:
                    db.execute('''INSERT INTO oye_ocr_detection_counts
                        (target,total,last_at,last_text,last_confidence)
                        VALUES(?,1,?,?,?) ON CONFLICT(target) DO UPDATE SET
                        total=total+1,last_at=excluded.last_at,
                        last_text=excluded.last_text,last_confidence=excluded.last_confidence''',
                        (target,event['at'],event['text'],event['confidence']))
                    db.execute('''INSERT INTO oye_ocr_detection_events
                        (target,label,at,text,confidence) VALUES(?,?,?,?,?)''',
                        (target,event['label'],event['at'],event['text'],event['confidence']))
                    # Keep the most recent 300 discoveries without losing totals.
                    db.execute('''DELETE FROM oye_ocr_detection_events WHERE id NOT IN
                        (SELECT id FROM oye_ocr_detection_events ORDER BY id DESC LIMIT 300)''')
                self.storage_error = ''
            except (sqlite3.Error, OSError) as exc:
                self.storage_error = type(exc).__name__
            return True

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.saved_summary(self.root),
                    'session_scans': self.session_scans,
                    'session_hits': self.session_hits,
                    'session_total': self.session_events,
                    'session_by_target': dict(self.per_target),
                    'recent_session': list(self.events),
                    'storage_error': self.storage_error}

    @staticmethod
    def saved_summary(root: Path) -> dict:
        path = Path(root) / 'learning' / 'observer.sqlite3'
        empty = {'all_time_total': 0, 'by_target': [], 'recent': []}
        if not path.exists():
            return empty
        try:
            # Read-only: checking a status endpoint must never mutate a database.
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1.5)) as db:
                rows = db.execute('''SELECT target,total,last_at,last_text,last_confidence
                                   FROM oye_ocr_detection_counts ORDER BY total DESC,target''').fetchall()
                history = db.execute('''SELECT target,label,at,text,confidence
                    FROM oye_ocr_detection_events ORDER BY id DESC LIMIT 12''').fetchall()
                return {'all_time_total': sum(int(row[1]) for row in rows),
                        'by_target': [{'target': row[0], 'count': row[1],
                          'last_at': row[2], 'text': row[3], 'confidence': row[4]}
                          for row in rows],
                        'recent': [{'target': row[0], 'label': row[1], 'at': row[2],
                                    'text': row[3], 'confidence': row[4]}
                                   for row in history]}
        except (sqlite3.Error, OSError, ValueError):
            return empty
