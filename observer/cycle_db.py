"""Data-driven, additive SQLite workflow for the match/reward/lobby cycle.

All previous training tables, labels, screenshot paths, and decisions are left
untouched. Rule defaults are inserted only when absent; an existing rule is
never reset on upgrade. This module sends no game input.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from contextlib import contextmanager
import time

SCHEMA_VERSION = 1

@dataclass(frozen=True)
class CycleRule:
    stage: str
    event: str
    next_stage: str
    action: str
    confirmations: int
    wait_seconds: float
    priority: int


# Workflow documented in SQLite, not hidden exclusively in Python.
DEFAULT_RULES = (
    CycleRule('ending', 'lobby_confirmed', 'post_lobby', 'observe_rewards', 3, 0, 100),
    CycleRule('in_match', 'direct_lobby', 'post_lobby', 'observe_rewards', 5, 0, 100),
    CycleRule('post_lobby', 'reward_visible', 'claim_rewards', 'find_riscatta', 2, 0, 200),
    CycleRule('post_lobby', 'lobby_without_rewards', 'await_play', 'wait_then_play', 3, 18, 50),
    CycleRule('claim_rewards', 'riscatta_visible', 'claim_rewards', 'click_riscatta', 2, 0, 200),
    CycleRule('claim_rewards', 'lobby_after_claim', 'await_play', 'verify_lobby', 3, 1, 100),
    CycleRule('await_play', 'reward_visible', 'claim_rewards', 'find_riscatta', 2, 0, 250),
    CycleRule('await_play', 'gioca_visible', 'joining', 'click_gioca', 1, 0, 10),
)

class CyclePolicy:
    def __init__(self, rules=DEFAULT_RULES):
        self.rules = {(r.stage, r.event): r for r in rules}
        for rule in DEFAULT_RULES:
            if (rule.stage, rule.event) not in self.rules:
                raise ValueError(f'Regola mancante: {rule.stage}.{rule.event}')

    def for_event(self, stage: str, event: str) -> CycleRule:
        return self.rules[(stage, event)]


class CycleStore:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)

    @contextmanager
    def _open(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('PRAGMA busy_timeout=5000')
            with db:
                yield db
        finally:
            db.close()

    def prepare(self) -> CyclePolicy:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS oye_cycle_rules (
                    stage TEXT NOT NULL,
                    event TEXT NOT NULL,
                    next_stage TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confirmations INTEGER NOT NULL CHECK (confirmations BETWEEN 1 AND 20),
                    wait_seconds REAL NOT NULL CHECK (wait_seconds BETWEEN 0 AND 300),
                    priority INTEGER NOT NULL,
                    PRIMARY KEY (stage,event)
                );
                CREATE TABLE IF NOT EXISTS oye_cycle_state (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    stage TEXT NOT NULL,
                    matches_completed INTEGER NOT NULL CHECK(matches_completed >= 0),
                    active INTEGER NOT NULL CHECK(active IN (0,1)),
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oye_cycle_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    happened_at TEXT NOT NULL,
                    previous_stage TEXT NOT NULL,
                    next_stage TEXT NOT NULL,
                    event TEXT NOT NULL,
                    matches_completed INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS oye_cycle_history_time
                    ON oye_cycle_history(happened_at,id);
            ''')
            db.executemany('''
                INSERT OR IGNORE INTO oye_cycle_rules
                    (stage,event,next_stage,action,confirmations,wait_seconds,priority)
                VALUES (?,?,?,?,?,?,?)''',
                [(r.stage,r.event,r.next_stage,r.action,r.confirmations,r.wait_seconds,r.priority)
                 for r in DEFAULT_RULES])
            rows = db.execute('''SELECT stage,event,next_stage,action,confirmations,wait_seconds,priority
                                 FROM oye_cycle_rules''').fetchall()
        defaults = {(r.stage, r.event) for r in DEFAULT_RULES}
        rules = [CycleRule(*row) for row in rows if (row[0],row[1]) in defaults]
        # An existing database may contain user-modified thresholds. Preserve
        # valid settings, but never allow malformed/unknown actions as policy.
        allowed = { (r.stage,r.event):(r.next_stage,r.action) for r in DEFAULT_RULES }
        for rule in rules:
            if ((rule.next_stage,rule.action) != allowed[(rule.stage,rule.event)]
                    or not 1 <= rule.confirmations <= 20 or not 0 <= rule.wait_seconds <= 300):
                raise ValueError(f'Regola SQLite incompatibile: {rule.stage}.{rule.event}')
        return CyclePolicy(rules)

    def record(self, *, stage: str, previous_stage: str, event: str,
               matches_completed: int, active: bool = True, now: float | None = None) -> None:
        """Record stage changes, not every OCR frame; never alter training rows."""
        if not stage or matches_completed < 0:
            raise ValueError('Stato del ciclo non valido')
        stamp = time.time() if now is None else now
        with self._open() as db:
            db.execute('''INSERT INTO oye_cycle_state (id,stage,matches_completed,active,updated_at)
                          VALUES (1,?,?,?,?)
                          ON CONFLICT(id) DO UPDATE SET stage=excluded.stage,
                          matches_completed=excluded.matches_completed,
                          active=excluded.active,updated_at=excluded.updated_at''',
                       (stage,matches_completed,int(active),stamp))
            if stage != previous_stage:
                db.execute('''INSERT INTO oye_cycle_history
                              (happened_at,previous_stage,next_stage,event,matches_completed)
                              VALUES (?,?,?,?,?)''',
                           (datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                            previous_stage,stage,event,matches_completed))

    def last_state(self, *, max_age_seconds: float = 48*3600, now: float | None = None):
        if not self.path.exists():
            return None
        with self._open() as db:
            row = db.execute('SELECT stage,matches_completed,active,updated_at FROM oye_cycle_state WHERE id=1').fetchone()
        if not row:
            return None
        stage,matches,active,updated_at=row
        current = time.time() if now is None else now
        if not (0 <= current-updated_at <= max_age_seconds):
            return None
        return {'stage':stage, 'matches_completed':matches,
                'active':bool(active), 'updated_at':updated_at}

    def event(self, *, stage: str, event: str, matches_completed: int,
              now: float | None = None) -> None:
        """Append a meaningful event without changing stage or touching training."""
        if not stage or not event or matches_completed < 0:
            raise ValueError('Evento ciclo non valido')
        stamp = time.time() if now is None else now
        with self._open() as db:
            db.execute('''INSERT INTO oye_cycle_history
                          (happened_at,previous_stage,next_stage,event,matches_completed)
                          VALUES (?,?,?,?,?)''',
                       (datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                        stage,stage,event,matches_completed))
