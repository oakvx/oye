"""Local dataset, visual/OCR features, few-shot retrieval and optional supervised ML.

No gameplay automation and no uploaded images. Predictions are hypotheses that the
user can correct. Training data is persisted as numeric features in SQLite, not
unsafe serialized models. The optional scikit-learn estimator is rebuilt locally.
"""
from __future__ import annotations
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid

import cv2
import numpy as np

from .ocr import LocalOCR, OCRResult
from .ollama_vision import LocalVisionModel
from .temporal import TemporalTracker

SCENE_EVENTS = {
    'match': 'partita in corso', 'spectator': 'modalità spettatore',
    'lobby': 'lobby', 'results': 'risultati partita',
    'loading': 'caricamento', 'unknown': 'incerto', 'unfocused': 'non visibile',
}
FEATURE_SIZE = 16 * 9 * 2 + 12 + 8 + 8 + 64 + len(SCENE_EVENTS)


def image_features(frame: np.ndarray, text: str = '', scene: str = 'unknown') -> np.ndarray:
    """Resolution-independent spatial appearance + edges + color + hashed OCR text.

    No hard-coded Fortnite-specific screenshots or pretrained neural model.
    """
    if frame is None or frame.ndim != 3 or frame.shape[2] not in (3, 4):
        raise ValueError('Screenshot deve avere 3 o 4 canali')
    # Resize the native BGRA frame before dropping alpha: identical visual
    # representation but no full-resolution BGR allocation on each scan.
    tiny_native = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
    tiny = cv2.cvtColor(tiny_native, cv2.COLOR_BGRA2BGR) if frame.shape[2] == 4 else tiny_native
    gray = cv2.cvtColor(tiny, cv2.COLOR_BGR2GRAY)
    spatial = cv2.resize(gray, (16, 9), interpolation=cv2.INTER_AREA).reshape(-1).astype('float32') / 255
    edges = cv2.Canny(gray, 65, 140)
    spatial_edges = cv2.resize(edges, (16, 9), interpolation=cv2.INTER_AREA).reshape(-1).astype('float32') / 255
    hsv = cv2.cvtColor(tiny, cv2.COLOR_BGR2HSV)
    hist = []
    for idx, bins, maximum in ((0, 12, 180), (1, 8, 256), (2, 8, 256)):
        values = cv2.calcHist([hsv], [idx], None, [bins], [0, maximum]).reshape(-1)
        hist.extend((values / max(values.sum(), 1)).tolist())
    tokens = re.findall(r'[\wÀ-ÿ]{2,}', text.casefold())[:250]
    hashed = np.zeros(64, dtype='float32')
    for token in tokens:
        digest = hashlib.blake2s(token.encode('utf-8'), digest_size=4).digest()
        hashed[int.from_bytes(digest, 'little') % len(hashed)] += 1
    if np.any(hashed):
        hashed /= np.linalg.norm(hashed)
    scene_flags = np.array([1.0 if scene == key else 0.0 for key in SCENE_EVENTS], dtype='float32')
    # Visuals dominate; OCR and template labels provide complementary information.
    features = np.concatenate([spatial * 0.8, spatial_edges * .85,
                               np.array(hist, dtype='float32') * 3.0,
                               hashed * 2., scene_flags * 2.])
    return features.astype('float32')


def ocr_event(text: str) -> str | None:
    text = ' '.join(text.casefold().split())
    # Only strong textual indicators; do not confuse a kill counter with elimination.
    patterns = (
        (r'\b(vittoria reale|victory royale)\b', 'vittoria'),
        (r'\b(sei stato eliminato|you were eliminated|eliminated by|eliminato da)\b', 'eliminazione'),
        (r'\b(spectating|spettatore|stai guardando)\b', 'modalità spettatore'),
        (r'\b(matchmaking|connessione al server|connecting to server)\b', 'ricerca partita'),
        (r'\b(battle bus|bus della battaglia|salta dal bus|space to jump)\b', 'bus della battaglia'),
        (r'\b(deploy glider|open glider|apri il deltaplano|caduta libera)\b', 'discesa'),
        # This CTA can appear while spectating; never use it as results proof.
        (r'\b(match results|risultati partita|match summary)\b', 'risultati partita'),
    )
    for pattern, label in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return None


@dataclass(frozen=True)
class Prediction:
    event: str
    score: float
    source: str
    description: str = ''


class LearningStore:
    def __init__(self, root: Path, *, max_saved_frames: int = 3600):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.images = self.root / 'frames'
        self.images.mkdir(exist_ok=True)
        self.path = self.root / 'observer.sqlite3'
        self.max_saved_frames = max_saved_frames
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS frames (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session TEXT NOT NULL,
                  captured_at TEXT NOT NULL,
                  image_file TEXT,
                  process_name TEXT NOT NULL,
                  hud_scene TEXT NOT NULL,
                  predicted_event TEXT NOT NULL,
                  predicted_score REAL NOT NULL,
                  predicted_source TEXT NOT NULL,
                  description TEXT NOT NULL DEFAULT '',
                  ocr_text TEXT NOT NULL DEFAULT '',
                  ocr_status TEXT NOT NULL DEFAULT '',
                  screen_change REAL NOT NULL DEFAULT 0,
                  user_label TEXT,
                  user_note TEXT NOT NULL DEFAULT '',
                  feature BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS frames_session ON frames(session, id);
                CREATE INDEX IF NOT EXISTS frames_label ON frames(user_label);
                CREATE TABLE IF NOT EXISTS session_notes (
                  session TEXT PRIMARY KEY,
                  narrative TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_decisions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session TEXT NOT NULL,
                  observed_at TEXT NOT NULL,
                  proposed_action TEXT NOT NULL,
                  executed INTEGER NOT NULL,
                  confidence REAL NOT NULL,
                  event TEXT NOT NULL,
                  description TEXT NOT NULL,
                  reasoning TEXT NOT NULL,
                  message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS decisions_session ON agent_decisions(session,id);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO meta (key, value) VALUES ('revision', 0);
            ''')
            # Additive migration: retain all v7/v8 frames and existing labels.
            columns = {row[1] for row in db.execute('PRAGMA table_info(frames)')}
            if 'action_label' not in columns:
                db.execute('ALTER TABLE frames ADD COLUMN action_label TEXT')
            if 'action_source' not in columns:
                db.execute("ALTER TABLE frames ADD COLUMN action_source TEXT NOT NULL DEFAULT ''")
            if 'observed_controls' not in columns:
                db.execute("ALTER TABLE frames ADD COLUMN observed_controls TEXT NOT NULL DEFAULT '[]'")
            if 'demo_verified' not in columns:
                db.execute("ALTER TABLE frames ADD COLUMN demo_verified INTEGER NOT NULL DEFAULT 0")
            # V44 additive migration. Rejected recordings remain in the archive,
            # but must NEVER re-enter the action policy on the next refresh.
            if 'training_excluded' not in columns:
                db.execute("ALTER TABLE frames ADD COLUMN training_excluded INTEGER NOT NULL DEFAULT 0")
            if 'training_reviewed_at' not in columns:
                db.execute("ALTER TABLE frames ADD COLUMN training_reviewed_at TEXT")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS input_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    actions TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS input_transitions_session
                    ON input_transitions(session, observed_at, id);
            ''')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS session_markers (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session TEXT NOT NULL,
                  occurred_at TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  title TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS session_markers_by_session
                  ON session_markers(session,occurred_at,id);
            ''')
            db.executescript("""
                CREATE TABLE IF NOT EXISTS range_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    frames_labeled INTEGER NOT NULL DEFAULT 0,
                    applied_revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS range_annotations_session
                    ON range_annotations(session,start_at,id);
                CREATE TABLE IF NOT EXISTS range_before (
                    annotation_id INTEGER NOT NULL REFERENCES range_annotations(id),
                    frame_id INTEGER NOT NULL,
                    user_label TEXT,
                    user_note TEXT NOT NULL,
                    PRIMARY KEY(annotation_id,frame_id)
                );
            """)
            db.execute('''CREATE TABLE IF NOT EXISTS visual_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session TEXT NOT NULL, captured_at TEXT NOT NULL,
                event TEXT NOT NULL, description TEXT NOT NULL,
                uncertain INTEGER NOT NULL
            )''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        try:
            db.execute('PRAGMA busy_timeout = 15000')
            db.execute('PRAGMA journal_mode = WAL')
            with db:
                yield db
        finally:
            db.close()

    def revision(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def confirmed_stage_transitions(self):
        """Transition frequencies only from contiguous *human-confirmed* samples.

        Never promote model-only guesses to truth or connect labels across
        missing/unreviewed frames. The Viterbi model uses these as small priors.
        """
        from .temporal import event_stage
        from .timeline import elapsed_seconds
        with self._connect() as db:
            rows = db.execute("SELECT session,captured_at,user_label FROM frames ORDER BY session,captured_at,id").fetchall()
        counts = Counter()
        previous = None
        for session, captured, label in rows:
            state = event_stage(label) if label is not None else None
            if state == 'unknown':
                state = None
            if previous is not None and state is not None:
                prev_session, prev_time, prev_state = previous
                if (session == prev_session and prev_state and state != prev_state
                        and 0 <= elapsed_seconds(prev_time, captured) <= 10):
                    counts[(prev_state, state)] += 1
            previous = (session, captured, state)
        return counts

    def labeled(self) -> list[tuple[np.ndarray, str]]:
        with self._connect() as db:
            rows = db.execute('SELECT feature, user_label FROM frames WHERE user_label IS NOT NULL ORDER BY id').fetchall()
        pairs = []
        for blob, label in rows:
            vector = np.frombuffer(blob, dtype=np.float32)
            if vector.size == FEATURE_SIZE:
                pairs.append((vector.copy(), label))
        return pairs

    def add(self, *, session: str, frame: np.ndarray, process: str, scene: str,
            prediction: Prediction, ocr: OCRResult, feature: np.ndarray,
            change: float = 0.0, captured_at: str | None = None) -> tuple[int, bytes]:
        ok, data = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 76])
        if not ok:
            raise ValueError('Impossibile codificare screenshot')
        jpeg = data.tobytes()
        filename = f'{uuid.uuid4().hex}.jpg'
        (self.images / filename).write_bytes(jpeg)
        try:
            with self._connect() as db:
                cur = db.execute('''INSERT INTO frames
                    (session,captured_at,image_file,process_name,hud_scene,predicted_event,
                    predicted_score,predicted_source,description,ocr_text,ocr_status,screen_change,feature)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (session, captured_at or datetime.now(timezone.utc).isoformat(), filename,
                     process[:150], scene, prediction.event, float(prediction.score),
                     prediction.source, prediction.description, ocr.text, ocr.status,
                     float(change), feature.tobytes()))
                frame_id = int(cur.lastrowid)
        except Exception:
            (self.images / filename).unlink(missing_ok=True)
            raise
        self.prune_images()
        return frame_id, jpeg

    def finish_analysis(self, frame_id: int, *, prediction: Prediction, ocr: OCRResult,
                        feature: np.ndarray, change: float) -> None:
        """Enrich a safely archived frame; preserve any human corrections/actions."""
        with self._connect() as db:
            changed = db.execute("""UPDATE frames SET predicted_event=?,predicted_score=?,
                     predicted_source=?,description=?,ocr_text=?,ocr_status=?,
                     screen_change=?,feature=? WHERE id=?""",
                     (prediction.event, float(prediction.score), prediction.source,
                      prediction.description, ocr.text, ocr.status, float(change),
                      feature.tobytes(), int(frame_id)))
            if changed.rowcount != 1:
                raise ValueError(f'Fotogramma #{frame_id} eliminato prima dell’analisi')
            # If somebody corrected this frame while OCR was running, refresh
            # both event and action retrieval caches after the feature update.
            db.execute("""UPDATE meta SET value=value+1 WHERE key='revision' AND
                     EXISTS (SELECT 1 FROM frames WHERE id=? AND
                             (user_label IS NOT NULL OR action_label IS NOT NULL))""", (int(frame_id),))

    def prune_images(self) -> None:
        with self._connect() as db:
            rows = db.execute('SELECT id,image_file FROM frames WHERE image_file IS NOT NULL ORDER BY id DESC LIMIT -1 OFFSET ?',
                              (self.max_saved_frames,)).fetchall()
            for frame_id, file in rows:
                # Store only generated random filenames; never accept paths from DB.
                if re.fullmatch(r'[a-f0-9]{32}\.jpg', file):
                    (self.images / file).unlink(missing_ok=True)
                db.execute('UPDATE frames SET image_file=NULL WHERE id=?', (frame_id,))

    def correct(self, frame_id: int, label: str, note: str = '') -> None:
        label = ' '.join(label.strip().split())[:100]
        note = note.strip()[:2000]
        if not label:
            raise ValueError('Indica un evento da insegnare al modello')
        with self._connect() as db:
            result = db.execute('UPDATE frames SET user_label=?, user_note=? WHERE id=?',
                                (label, note, int(frame_id)))
            if result.rowcount != 1:
                raise ValueError(f'Fotogramma #{frame_id} inesistente')
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")

    def teach_action(self, frame_id: int, action: str) -> None:
        """Only a human may assign training actions: never trust a VLM pseudo-label."""
        from .autopilot import LIMITS
        action = str(action).strip()
        if action not in LIMITS or action == 'aim':
            raise ValueError('Azione non valida o richiede coordinate non disponibili')
        with self._connect() as db:
            result = db.execute("UPDATE frames SET action_label=?, action_source='manual' WHERE id=?",
                                (action, int(frame_id)))
            if result.rowcount != 1:
                raise ValueError(f'Fotogramma #{frame_id} inesistente')
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")

    def bulk_update(self, frame_ids, *, session: str, event: str | None = None,
                    action: str | None = None, note: str | None = None) -> list[dict]:
        '''Atomic, human-authorized review changes; returns snapshots for undo.

        None leaves a field untouched. A non-empty event, an allowed action,
        or an explicit note (including the empty string) are valid changes.
        All IDs must belong to exactly the currently displayed session.
        '''
        from .autopilot import LIMITS
        try:
            ids = sorted({int(i) for i in frame_ids})
        except (TypeError, ValueError) as exc:
            raise ValueError('Selezione non valida') from exc
        if not ids or len(ids) > 10000:
            raise ValueError('Seleziona da 1 a 10.000 fotogrammi')
        if not session:
            raise ValueError('Sessione obbligatoria')
        if event is None and action is None and note is None:
            raise ValueError('Seleziona almeno un campo da modificare')
        if event is not None:
            event = ' '.join(str(event).strip().split())[:100]
            if not event:
                raise ValueError('Scrivi un evento valido')
        if action is not None:
            action = str(action).strip()
            if action not in LIMITS or action == 'aim':
                raise ValueError('Azione non consentita per il training')
        if note is not None:
            note = str(note).strip()[:2000]
        before = []
        with self._connect() as db:
            for index in range(0, len(ids), 400):
                group = ids[index:index + 400]
                params = ','.join('?' for _ in group)
                before.extend(dict(zip(('id','session','user_label','user_note',
                                        'action_label','action_source'), row)) for row in
                    db.execute(f'''SELECT id,session,user_label,user_note,action_label,action_source
                                  FROM frames WHERE id IN ({params})''', group))
            if len(before) != len(ids) or any(item['session'] != session for item in before):
                raise ValueError('Selezione non valida: fotogrammi assenti o di un’altra sessione')
            fields, values = [], []
            if event is not None:
                fields.append('user_label=?')
                values.append(event)
            if action is not None:
                fields.extend(['action_label=?', "action_source='manual'"])
                values.append(action)
            if note is not None:
                fields.append('user_note=?')
                values.append(note)
            for index in range(0, len(ids), 400):
                group = ids[index:index + 400]
                params = ','.join('?' for _ in group)
                db.execute(f"UPDATE frames SET {', '.join(fields)} WHERE session=? AND id IN ({params})",
                           [*values, session, *group])
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return before

    def restore_bulk(self, snapshots: list[dict], *, session: str) -> int:
        '''Undo exactly the last bulk edit, including action provenance.'''
        if not snapshots or any(item.get('session') != session for item in snapshots):
            raise ValueError('Impossibile annullare: sessione diversa')
        with self._connect() as db:
            for item in snapshots:
                changed = db.execute('''UPDATE frames SET user_label=?,user_note=?,
                            action_label=?,action_source=? WHERE id=? AND session=?''',
                    (item['user_label'],item['user_note'],item['action_label'],
                     item['action_source'],item['id'],session))
                if changed.rowcount != 1:
                    raise ValueError('Annullamento incompleto: un fotogramma non esiste più')
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return len(snapshots)

    def range_annotations(self, session: str) -> list[dict]:
        """Verified user-authored interval annotations; distinct from model guesses."""
        with self._connect() as db:
            cursor = db.execute("""SELECT id,session,start_at,end_at,event,note,frames_labeled,
                                     applied_revision,created_at FROM range_annotations
                                     WHERE session=? ORDER BY start_at,id""", (session,))
            return [dict(zip((col[0] for col in cursor.description), row))
                    for row in cursor.fetchall()]

    def annotate_interval(self, *, session: str, start_at: str, end_at: str,
                          event: str, note: str = '') -> dict:
        """Persist exact time interval and label sampled frames atomically.

        A range without samples is a legitimate annotation, but yields no
        training samples. Timestamps are converted to UTC before comparison.
        Predicted labels remain immutable; only the user's labels are changed.
        """
        from .timeline import parse_time
        if not session or not isinstance(session, str):
            raise ValueError('Seleziona una sessione valida')
        try:
            start, end = parse_time(start_at), parse_time(end_at)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError('Orari non validi') from exc
        if end <= start or (end - start).total_seconds() > 60 * 60 * 12:
            raise ValueError('L’intervallo deve durare più di zero e meno di 12 ore')
        event = ' '.join(str(event).split())[:100]
        note = str(note).strip()[:2000]
        if not event:
            raise ValueError('Scrivi cosa è successo nel tratto selezionato')
        start_str = start.astimezone(timezone.utc).isoformat()
        end_str = end.astimezone(timezone.utc).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute("""SELECT id,captured_at,user_label,user_note
                                   FROM frames WHERE session=? ORDER BY id""", (session,))
            selected = [(int(i), label, old_note) for i, at, label, old_note in cursor
                        if start <= parse_time(at) <= end]
            if len(selected) > 10000:
                raise ValueError('Intervallo troppo grande: massimo 10.000 fotogrammi')
            rev = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]) + 1
            inserted = db.execute("""INSERT INTO range_annotations
                (session,start_at,end_at,event,note,frames_labeled,applied_revision,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (session, start_str, end_str, event, note, len(selected), rev, now))
            annotation_id = int(inserted.lastrowid)
            if selected:
                db.executemany("""INSERT INTO range_before
                    (annotation_id,frame_id,user_label,user_note) VALUES (?,?,?,?)""",
                    [(annotation_id, i, label, old_note) for i, label, old_note in selected])
                db.executemany("""UPDATE frames SET user_label=?,user_note=?
                                  WHERE id=? AND session=?""",
                               [(event, note, i, session) for i, _, _ in selected])
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return {'id': annotation_id, 'session': session, 'start_at': start_str,
                'end_at': end_str, 'event': event, 'note': note,
                'frames_labeled': len(selected), 'applied_revision': rev}

    def delete_interval(self, annotation_id: int, *, session: str,
                        undo_labels: bool = False) -> int:
        """Remove one annotation. Undo labels only if no later edits happened.

        Ordinary removal does not erase a user's independently edited labels.
        """
        with self._connect() as db:
            row = db.execute("""SELECT applied_revision FROM range_annotations
                               WHERE id=? AND session=?""", (int(annotation_id), session)).fetchone()
            if row is None:
                raise ValueError('Annotazione inesistente in questa sessione')
            if undo_labels:
                rev = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                if rev != int(row[0]):
                    raise ValueError('Sono state fatte altre revisioni: non posso ripristinare vecchie etichette. Elimina solo l’annotazione.')
                snapshots = db.execute("""SELECT frame_id,user_label,user_note FROM range_before
                                          WHERE annotation_id=?""", (int(annotation_id),)).fetchall()
                db.executemany("""UPDATE frames SET user_label=?,user_note=?
                                  WHERE id=? AND session=?""",
                               [(label, note, fid, session) for fid, label, note in snapshots])
            db.execute('DELETE FROM range_before WHERE annotation_id=?', (int(annotation_id),))
            db.execute('DELETE FROM range_annotations WHERE id=? AND session=?',
                       (int(annotation_id), session))
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return 1

    def mark_session(self, session: str, kind: str, title: str = '',
                     occurred_at: str | None = None) -> None:
        if kind not in ('start', 'end', 'state'):
            raise ValueError('Tipo evento di sessione non valido')
        with self._connect() as db:
            db.execute('''INSERT INTO session_markers (session,occurred_at,kind,title)
                          VALUES (?,?,?,?)''',
                       (session, occurred_at or datetime.now(timezone.utc).isoformat(),
                        kind, str(title)[:160]))

    def session_markers(self, session: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute('''SELECT occurred_at,kind,title FROM session_markers
                    WHERE session=? ORDER BY occurred_at,id''', (session,)).fetchall()
        return [dict(zip(('occurred_at','kind','title'), row)) for row in rows]

    def action_training_samples(self, limit: int = 1200) -> list[tuple[str, str, np.ndarray, str]]:
        """Examples balanced across matches AND action types, not only newest frames.

        Observations of lobby/loading and automated predictions are never valid
        demonstrations of a gameplay command. Human manual and demonstration
        labels may be reviewed or superseded; the latest value wins per frame.
        SQLite does the balanced sampling without loading the entire archive.
        """
        from .autopilot import LIMITS
        from .temporal import event_stage
        wanted = max(1, min(int(limit), 10000))
        with self._connect() as db:
            rows = db.execute('''SELECT session,action_label,feature,action_source,hud_scene,user_label,demo_verified,observed_controls FROM (
                SELECT session,action_label,feature,action_source,hud_scene,user_label,demo_verified,observed_controls,id,
                    ROW_NUMBER() OVER (
                        PARTITION BY session,action_label ORDER BY id DESC
                    ) AS sample_rank
                FROM frames
                WHERE action_label IS NOT NULL AND training_excluded=0
                    AND action_source IN ('manual','demo')
                    AND (hud_scene='match' OR
                         (user_label IS NOT NULL OR demo_verified=1))
            ) ORDER BY sample_rank,id DESC LIMIT ?''', (min(10000, wanted * 2),)).fetchall()
        from .demonstration import primary_action
        samples = []
        for session, action, blob, source, scene, user_label, demo_verified, observed_controls in rows:
            if source == 'demo' and observed_controls:
                try:
                    controls = json.loads(observed_controls)
                    if isinstance(controls, list) and all(isinstance(x, str) for x in controls):
                        # The old recorder may have reduced W + shoot to 'shoot'.
                        # Recover the movement from the persisted whitelist.
                        action = primary_action(tuple(controls)) or action
                except (TypeError, ValueError):
                    pass
            # Never learn from a menu/spectator screen just because someone
            # selected a key. A *human-confirmed* match label can correct an
            # earlier failed HUD recognition without rewriting the original HUD.
            stage = event_stage(user_label) if user_label else None
            valid_scene = (stage not in ('lobby','loading','results','spectator') and
                           (scene == 'match' or stage == 'match' or bool(demo_verified)) and
                           (scene not in ('lobby','loading','results','spectator','unfocused') or stage == 'match'))
            feature = np.frombuffer(blob, dtype=np.float32)
            if valid_scene and action in LIMITS and feature.size == FEATURE_SIZE:
                # The feature's embedded scene flags must match LIVE gameplay,
                # otherwise even a manually fixed screenshot cannot match kNN.
                corrected = feature.copy()
                if scene != 'match':
                    corrected[-len(SCENE_EVENTS):] = np.array(
                        [2. if name == 'match' else 0. for name in SCENE_EVENTS], dtype=np.float32)
                samples.append((session, action, corrected, source))
                if len(samples) >= wanted:
                    break
        return samples

    def action_diagnostics(self) -> dict[str, int]:
        from .temporal import event_stage
        with self._connect() as db:
            rows = db.execute('''SELECT hud_scene,user_label,action_source,action_label,demo_verified,training_excluded
                                 FROM frames WHERE action_label IS NOT NULL''').fetchall()
        total = usable = outside = unknown_source = excluded = 0
        from .autopilot import LIMITS
        for scene, user_label, source, action, demo_verified, is_excluded in rows:
            if is_excluded:
                excluded += 1
                continue
            if source not in ('manual','demo') or action not in LIMITS:
                unknown_source += 1
            elif (event_stage(user_label) if user_label else None) in ('lobby','loading','results','spectator'):
                outside += 1
            elif scene in ('lobby','loading','results','spectator','unfocused') and (event_stage(user_label) if user_label else None) != 'match':
                outside += 1
            elif scene == 'match' or (event_stage(user_label) == 'match') or demo_verified:
                usable += 1
            else:
                outside += 1
        return {'total': len(rows), 'usable': usable, 'outside_match': outside,
                'invalid_source': unknown_source, 'excluded': excluded}

    def action_examples(self, limit: int = 1200) -> list[tuple[np.ndarray, str]]:
        return [(feature, action) for _session, action, feature, _source
                in self.action_training_samples(limit)]

    def record_input_transition(self, *, session: str, actions: tuple[str, ...]) -> None:
        """Timestamped approved *action names*, never raw keyboard keys/text."""
        from .autopilot import LIMITS
        actions = tuple(sorted(set(actions)))
        if len(actions) > 12 or any(action not in LIMITS or action in ('aim', 'wait') for action in actions):
            raise ValueError('Input dimostrazione non consentito')
        with self._connect() as db:
            db.execute('''INSERT INTO input_transitions(session,observed_at,actions)
                          VALUES(?,?,?)''', (session, datetime.now(timezone.utc).isoformat(),
                                           json.dumps(actions)))

    def session_input_transitions(self, session: str, *, limit: int = 3000) -> list[dict]:
        with self._connect() as db:
            rows = db.execute('''SELECT observed_at,actions FROM input_transitions
                WHERE session=? ORDER BY id DESC LIMIT ?''',
                (session, max(1, min(int(limit), 10000)))).fetchall()
        return [{'observed_at': at, 'actions': json.loads(actions)}
                for at, actions in reversed(rows)]

    def confirm_demonstration_range(self, session: str, start_at: str, end_at: str) -> int:
        """Human-confirm only UNKNOWN HUD demos in a reviewed gameplay interval.

        Never promote lobby/loading/spectator/results to training; the original
        HUD classification remains unchanged.
        """
        if not session or not start_at or not end_at or end_at < start_at:
            raise ValueError('Seleziona una sessione e un intervallo valido')
        with self._connect() as db:
            changed = db.execute('''UPDATE frames SET demo_verified=1
                    WHERE session=? AND captured_at>=? AND captured_at<=?
                      AND action_source='demo' AND action_label IS NOT NULL
                      AND hud_scene='unknown' AND demo_verified=0''',
                    (session, start_at, end_at)).rowcount
            if changed:
                db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return changed

    def confirm_latest_demonstration(self) -> dict:
        """One-click user attestation for the latest demonstrated session.

        Only actual demo input samples are confirmed, and explicitly known
        menus/loading/spectator frames are excluded. Previous sessions and
        manually corrected samples are left intact.
        """
        with self._connect() as db:
            row = db.execute("""SELECT session,COUNT(*) FROM frames
                WHERE action_source='demo' GROUP BY session
                ORDER BY MAX(id) DESC LIMIT 1""").fetchone()
            if not row:
                raise ValueError('Nessuna dimostrazione da confermare: prima registra le mosse')
            session, total = row
            changed = db.execute("""UPDATE frames SET demo_verified=1
                WHERE session=? AND action_source='demo'
                AND hud_scene IN ('match','unknown') AND demo_verified=0""", (session,))
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return {'session':session, 'recorded':int(total), 'confirmed':changed.rowcount}

    def latest_demo_session(self) -> str:
        with self._connect() as db:
            row = db.execute("""SELECT session FROM frames WHERE action_source='demo'
                GROUP BY session ORDER BY MAX(id) DESC LIMIT 1""").fetchone()
        return str(row[0]) if row else ''

    @staticmethod
    def _review_class(scene: str, label: str | None, verified: int, excluded: int) -> str:
        from .temporal import event_stage
        if excluded:
            return 'excluded'
        stage = event_stage(label) if label else None
        # A human-defined non-match stage is a veto, even when the old
        # HUD classifier guessed 'match'. Never train a known menu.
        if stage in ('lobby', 'loading', 'results', 'spectator'):
            return 'blocked'
        if scene in ('lobby', 'loading', 'results', 'spectator', 'unfocused') and stage != 'match':
            return 'blocked'
        if stage == 'match' or scene == 'match' or verified:
            return 'usable'
        return 'pending'

    def training_review(self, *, session: str = '', limit: int = 40,
                        filter: str = 'pending', offset: int = 0) -> dict:
        """Review recorded moves without rewriting old frames or predicting labels.

        The queue is a convenience view, not automatic training/confirmation.
        The 'usable' estimate is cross-checked with the actual sample loader.
        """
        if filter not in ('pending', 'all'):
            raise ValueError('Filtro training non valido')
        if not isinstance(offset,int) or offset < 0 or offset > 10000:
            raise ValueError('Pagina training non valida')
        session = session or self.latest_demo_session()
        if not session:
            return dict(session='', sessions=[], total=0, pending=0, usable=0,
                        blocked=0, excluded=0, actions={}, policy_ready=False,
                        items=[], notes='Nessuna registrazione disponibile')
        with self._connect() as db:
            sessions = [r[0] for r in db.execute("""SELECT session FROM frames
                WHERE action_source='demo' GROUP BY session ORDER BY MAX(id) DESC""")]
            if session not in sessions:
                raise ValueError('Registrazione non trovata')
            rows = db.execute("""SELECT id,captured_at,image_file,hud_scene,user_label,
                action_label,action_source,observed_controls,demo_verified,
                training_excluded,training_reviewed_at
                FROM frames WHERE session=? AND action_source IN ('demo','manual')
                AND action_label IS NOT NULL ORDER BY id DESC""", (session,)).fetchall()
        keys = ('id','captured_at','image_file','hud_scene','user_label','action_label',
                'action_source','observed_controls','demo_verified','training_excluded','training_reviewed_at')
        totals = {key:0 for key in ('pending','usable','blocked','excluded')}
        actions = Counter()
        candidates = []
        for raw in rows:
            row = dict(zip(keys,raw))
            status = self._review_class(row['hud_scene'],row['user_label'],
                                        row['demo_verified'],row['training_excluded'])
            totals[status] += 1
            if status == 'usable':
                actions[row['action_label']] += 1
            if filter == 'pending' and status != 'pending':
                continue
            if len(candidates) >= 10000:
                continue
            image_name = row.pop('image_file')
            row['has_image'] = bool(image_name and
                (self.images / image_name).is_file())
            row['status'] = status
            try:
                controls = json.loads(row['observed_controls'] or '[]')
                row['observed_controls'] = controls if isinstance(controls,list) else []
            except (TypeError,ValueError):
                row['observed_controls'] = []
            row.pop('training_reviewed_at',None)
            candidates.append(row)
        # Present diverse actions first, rather than a wall of one repeated
        # key when a human holds W or Space for a long time. Never approve
        # the other samples automatically: only the displayed ID is reviewed.
        buckets = defaultdict(deque)
        for row in candidates:
            buckets[row['action_label']].append(row)
        ordered = []
        while buckets:
            for action in tuple(buckets):
                if not buckets[action]:
                    del buckets[action]
                    continue
                ordered.append(buckets[action].popleft())
        quota = min(max(int(limit),1),100)
        items = ordered[offset:offset+quota]
        # The policy's actual eligibility threshold: 3 samples / 2 action types.
        # It may still reject a scene at inference time because of distance.
        ready = totals['usable'] >= 3 and len(actions) >= 2
        return dict(session=session,sessions=sessions,total=len(rows),
                    **totals,actions=dict(actions),policy_ready=ready,items=items,
                    offset=offset,has_more=offset+quota<len(ordered),
                    notes=('Sono esempi candidati, non una prova di precisione del modello'))

    def review_demonstration(self, frame_id: int, *, session: str,
                             decision: str, action: str | None = None) -> dict:
        """Explicit one-frame review with whitelist and scene vetoes.

        Approving UNKNOWN gameplay requires a real saved frame and human input.
        Rejecting keeps screenshot and controls for possible subsequent review.
        """
        from .autopilot import LIMITS
        from .temporal import event_stage
        if decision not in ('accept','exclude','reset'):
            raise ValueError('Scelta di revisione non valida')
        if not session:
            raise ValueError('Sessione obbligatoria')
        if action is not None:
            action = str(action).strip()
            if action not in LIMITS or action in ('aim','wait'):
                raise ValueError('Azione non consentita')
            if decision != 'accept':
                raise ValueError('Modifica l’azione soltanto quando confermi')
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("""SELECT id,session,image_file,hud_scene,user_label,
                action_label,action_source,training_excluded,demo_verified
                FROM frames WHERE id=? AND session=?""", (int(frame_id),session)).fetchone()
            if not row or row['action_source'] not in ('demo','manual') or not row['action_label']:
                raise ValueError('Esempio non trovato in questa registrazione')
            scene = row['hud_scene']
            stage = event_stage(row['user_label']) if row['user_label'] else None
            if decision == 'accept':
                if stage in ('lobby','loading','results','spectator'):
                    raise ValueError('Schermata etichettata come menu o spettatore: non utilizzabile')
                if scene in ('lobby','loading','results','spectator','unfocused') and stage != 'match':
                    raise ValueError('Schermata non giocabile: non confermare questo esempio')
                if scene == 'unknown' and stage != 'match':
                    name = row['image_file']
                    if not name or not (self.images / name).is_file():
                        raise ValueError('Screenshot mancante: impossibile verificare il contesto')
                new_source = 'manual' if action and action != row['action_label'] else row['action_source']
                db.execute("""UPDATE frames SET demo_verified=1,
                   training_excluded=0,training_reviewed_at=?,action_label=?,action_source=?
                   WHERE id=? AND session=?""",
                   (datetime.now(timezone.utc).isoformat(),action or row['action_label'],
                    new_source,int(frame_id),session))
            elif decision == 'exclude':
                db.execute("""UPDATE frames SET training_excluded=1,demo_verified=0,
                   training_reviewed_at=? WHERE id=? AND session=?""",
                   (datetime.now(timezone.utc).isoformat(),int(frame_id),session))
            else:
                db.execute("""UPDATE frames SET training_excluded=0,demo_verified=0,
                   training_reviewed_at=NULL WHERE id=? AND session=?""",(int(frame_id),session))
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return {'ok':True,'id':int(frame_id),'decision':decision,'session':session}

    def record_demonstration(self, frame: np.ndarray, *, action: str,
                             session: str, process: str, scene: str = 'match',
                             controls: tuple[str, ...] = ()) -> tuple[int, bytes]:
        """Capture the screen and human input together, including uncertain HUD.

        Only 'match' observations train immediately. Unknown ones need review.
        """
        from .autopilot import LIMITS
        if action not in LIMITS or action in ('aim', 'wait'):
            raise ValueError('Comando dimostrato non consentito')
        if scene not in SCENE_EVENTS:
            scene = 'unknown'
        controls = tuple(sorted(set(controls or (action,))))
        if any(name not in LIMITS or name in ('aim', 'wait') for name in controls):
            raise ValueError('Comando dimostrato non consentito')
        feature = image_features(frame, '', scene)
        number, jpeg = self.add(session=session, frame=frame, process=process,
                                scene=scene, prediction=Prediction('azione dimostrata', .5, 'demo'),
                                ocr=OCRResult('', (), 'non acquisito', 0.), feature=feature)
        with self._connect() as db:
            db.execute('''UPDATE frames SET action_label=?, action_source='demo',
                               observed_controls=? WHERE id=?''',
                       (action, json.dumps(controls), number))
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return number, jpeg

    def record_visual_insight(self, *, session: str, event: str,
                              description: str, uncertain: bool = True) -> None:
        with self._connect() as db:
            db.execute('''INSERT INTO visual_insights
                          (session,captured_at,event,description,uncertain)
                          VALUES (?,?,?,?,?)''',
                       (session, datetime.now(timezone.utc).isoformat(),
                        str(event)[:100], str(description)[:500], int(uncertain)))

    def session_insights(self, session: str) -> list[dict]:
        '''Unverified vision observations for the human review timeline.'''
        with self._connect() as db:
            cursor = db.execute('''SELECT captured_at,event,description,uncertain
                                   FROM visual_insights WHERE session=?
                                   ORDER BY captured_at,id''', (session,))
            return [dict(zip([field[0] for field in cursor.description], row))
                    for row in cursor.fetchall()]

    def recent_insights(self, limit: int = 6) -> list[dict]:
        with self._connect() as db:
            rows = db.execute('''SELECT session,event,description,uncertain FROM visual_insights
                                 ORDER BY id DESC LIMIT ?''',
                              (max(1, min(30, int(limit))),)).fetchall()
        return [dict(zip(('session', 'event', 'description', 'uncertain'), row)) for row in rows]

    def annotate_session(self, session: str, narrative: str) -> None:
        narrative = narrative.strip()[:15000]
        if not narrative:
            raise ValueError('Scrivi cosa è successo durante la sessione')
        with self._connect() as db:
            db.execute('''INSERT INTO session_notes(session,narrative,updated_at) VALUES (?,?,?)
                ON CONFLICT(session) DO UPDATE SET narrative=excluded.narrative, updated_at=excluded.updated_at''',
                (session, narrative, datetime.now(timezone.utc).isoformat()))

    def recent(self, limit: int = 30, session: str | None = None) -> list[dict]:
        query = '''SELECT id,session,captured_at,image_file,hud_scene,predicted_event,predicted_score,
                   predicted_source,description,ocr_text,ocr_status,screen_change,user_label,user_note,
                   action_label,action_source,observed_controls,demo_verified
                   FROM frames'''
        args: tuple = ()
        if session:
            query += ' WHERE session=?'
            args = (session,)
        query += ' ORDER BY id DESC LIMIT ?'
        with self._connect() as db:
            cursor = db.execute(query, (*args, min(15000, max(1, limit))))
            return [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]

    def sessions(self) -> list[str]:
        with self._connect() as db:
            return [item[0] for item in db.execute('SELECT DISTINCT session FROM frames ORDER BY id DESC').fetchall()]

    def stats(self) -> dict:
        with self._connect() as db:
            total, labeled, images = db.execute('''SELECT COUNT(*),COUNT(user_label),COUNT(image_file) FROM frames''').fetchone()
            classes = dict(db.execute('SELECT user_label,COUNT(*) FROM frames WHERE user_label IS NOT NULL GROUP BY user_label').fetchall())
            actions = int(db.execute('SELECT COUNT(*) FROM frames WHERE action_label IS NOT NULL').fetchone()[0])
            input_transitions = int(db.execute('SELECT COUNT(*) FROM input_transitions').fetchone()[0])
        return {'frames': total, 'labeled': labeled, 'images': images, 'classes': classes, 'actions': actions, 'input_transitions': input_transitions}

    def export_jsonl(self, destination: Path, session: str | None = None) -> int:
        # Exports metadata and corrections, never image bytes or stored feature blobs.
        rows = self.recent(limit=5000, session=session)
        with Path(destination).open('w', encoding='utf-8') as output:
            for row in reversed(rows):
                output.write(json.dumps(row, ensure_ascii=False) + '\n')
        return len(rows)

    def feedback_context(self, exclude_session: str = '', max_notes: int = 3) -> str:
        """Small, local RAG memory from user-authored match reports and corrections."""
        with self._connect() as db:
            notes = db.execute('''SELECT narrative FROM session_notes WHERE session<>?
                                  ORDER BY updated_at DESC LIMIT ?''',
                               (exclude_session, max(1, min(10, max_notes)))).fetchall()
            labels = db.execute('''SELECT user_label, COUNT(*) FROM frames
                                   WHERE user_label IS NOT NULL GROUP BY user_label
                                   ORDER BY COUNT(*) DESC LIMIT 10''').fetchall()
            ranges = db.execute('''SELECT event,note FROM range_annotations
                WHERE session<>? ORDER BY id DESC LIMIT 5''', (exclude_session,)).fetchall()
            insights = db.execute('''SELECT event,description FROM visual_insights
                          WHERE session<>? AND uncertain=0 ORDER BY id DESC LIMIT 3''',
                                  (exclude_session,)).fetchall()
        chunks = [f'Note sessioni precedenti: {item[0][:260]}' for item in notes]
        if labels:
            chunks.append('Eventi corretti in passato: ' + ', '.join(
                f'{name} ({count})' for name, count in labels))
        if ranges:
            chunks.append('Intervalli confermati: ' + '; '.join(
                f'{event}: {note[:85]}' if note else event for event, note in ranges))
        if insights:
            chunks.append('Ipotesi visive NON verificate: ' + '; '.join(
                f'{event} ({description[:110]})' for event, description in insights))
        return ' | '.join(chunks)[:1200]

    def record_agent_decision(self, *, session: str, action: str, executed: bool,
                              confidence: float, event: str, description: str,
                              reasoning: str, message: str = '') -> None:
        with self._connect() as db:
            db.execute('''INSERT INTO agent_decisions
                (session,observed_at,proposed_action,executed,confidence,event,description,reasoning,message)
                VALUES (?,?,?,?,?,?,?,?,?)''', (
                session, datetime.now(timezone.utc).isoformat(), action[:40],
                int(executed), float(confidence), event[:100], description[:500],
                reasoning[:180], message[:220]))

    def agent_history(self, session: str, limit: int = 200) -> list[dict]:
        with self._connect() as db:
            cursor = db.execute('''SELECT observed_at,proposed_action,executed,confidence,event,
                        description,reasoning,message FROM agent_decisions WHERE session=?
                        ORDER BY id DESC LIMIT ?''', (session, max(1, min(5000, limit))))
            return [dict(zip([field[0] for field in cursor.description], row))
                    for row in cursor.fetchall()]

    def session_summary(self, session: str) -> str:
        with self._connect() as db:
            rows = db.execute('''SELECT captured_at,COALESCE(user_label,predicted_event),
                predicted_source,user_note FROM frames WHERE session=? ORDER BY id''', (session,)).fetchall()
            notes = db.execute('SELECT narrative FROM session_notes WHERE session=?', (session,)).fetchone()
            intervals = db.execute('''SELECT start_at,end_at,event,note,frames_labeled
                FROM range_annotations WHERE session=? ORDER BY start_at,id''', (session,)).fetchall()
            plans = db.execute('''SELECT observed_at,event,description,proposed_action,executed
                FROM agent_decisions WHERE session=? ORDER BY id''', (session,)).fetchall()
        if not rows and not plans:
            return 'Nessuna osservazione per questa sessione.'
        counts = Counter(item[1] for item in rows)
        events = '\n'.join(f'- {key}: {value} fotogrammi' for key, value in counts.most_common())
        transitions = []
        previous = None
        for when, event, source, note in rows:
            if event != previous or note:
                transitions.append(f'- {when[11:19]}  {event}' + (f' — {note}' if note else ''))
                previous = event
        interval_report = '\n'.join(
            f'- {start[11:19]}–{end[11:19]} {event} ({count} fotogrammi)'
            + (f' — {note}' if note else '')
            for start,end,event,note,count in intervals) or '(nessun intervallo confermato)'
        gameplay = []
        last_event = None
        for at, event, description, action, executed in plans:
            if event != last_event and event != 'incerto':
                gameplay.append(f'- {at[11:19]} {event}: {description} (azione: {action}'
                                + (', eseguita)' if executed else ', non eseguita)'))
                last_event = event
        timeline = '\n'.join(gameplay[:150]) or '(nessuna inferenza autonoma)'
        return (f'Sessione: {session}\nFotogrammi: {len(rows)}\nPiani Ollama: {len(plans)}\n\n'
                f'Cronologia semantica del modello (ipotesi, non fatti accertati):\n{timeline}\n\n'
                f'Intervalli confermati da te:\n{interval_report}\n\n'
                f'Eventi stimati/corretti per fotogramma:\n{events}'
                f'\n\nCronologia cambiamenti:\n' + '\n'.join(transitions[:200]) +
                ('\n... (cronologia abbreviata)' if len(transitions) > 200 else '') +
                ('\n\nResoconto fornito dall’utente:\n' + notes[0] if notes else '') +
                '\n\nNota: le ipotesi automatiche non sono eventi confermati dall’utente.')


class LearningEngine:
    def __init__(self, folder: Path, *, ocr_enabled: bool = True,
                 ocr_languages: str = 'eng', max_saved_frames: int = 3600,
                 ollama_enabled: bool = False, ollama_model: str = 'qwen2.5vl:3b',
                 ollama_every: int = 4, ollama_timeout: float = 12.0):
        self.store = LearningStore(folder, max_saved_frames=max_saved_frames)
        self.ocr = LocalOCR(enabled=ocr_enabled, languages=ocr_languages)
        self.vlm = LocalVisionModel(ollama_model, ollama_timeout) if ollama_enabled else None
        self.ollama_every = ollama_every
        self.count = 0
        self._revision = -1
        self._features = np.empty((0, FEATURE_SIZE), dtype='float32')
        self._labels: list[str] = []
        self._clf = None
        self._prev_gray = None
        self._last_vision_frames = deque(maxlen=3)
        self._lock = threading.RLock()
        self._temporal = TemporalTracker()
        self._temporal_learned_revision = -1
        self._temporal_learned = Counter()

    def _refresh(self) -> None:
        current = self.store.revision()
        if current == self._revision:
            return
        pairs = self.store.labeled()
        self._features = np.stack([pair[0] for pair in pairs]) if pairs else np.empty((0, FEATURE_SIZE), dtype='float32')
        self._labels = [pair[1] for pair in pairs]
        self._clf = None
        if len(pairs) >= 8 and len(set(self._labels)) >= 2:
            try:
                from sklearn.linear_model import LogisticRegression
                model = LogisticRegression(max_iter=350, class_weight='balanced', C=3.0)
                model.fit(self._features, self._labels)
                self._clf = model
            except ImportError:
                pass  # Always retain the numpy-based nearest-neighbour learner.
        self._revision = current

    def predict(self, feature: np.ndarray, *, scene: str, text: str = '',
                vlm: dict | None = None) -> Prediction:
        with self._lock:
            self._refresh()
            strong_text = ocr_event(text)
            if strong_text:
                return Prediction(strong_text, 0.93, 'ocr', 'Rilevamento di testo esplicito')
            if len(self._labels):
                diff = self._features - feature[None, :]
                # Euclidean proximity is less sensitive to globally shared screen colors.
                distances = np.linalg.norm(diff, axis=1) / np.sqrt(FEATURE_SIZE)
                closest = np.argsort(distances)[:min(5, len(distances))]
                top_dist = float(distances[closest[0]])
                if top_dist < 0.22:
                    votes: dict[str, float] = defaultdict(float)
                    for idx in closest:
                        d = float(distances[idx])
                        votes[self._labels[idx]] += 1 / max(0.03, d)
                    candidate, total = max(votes.items(), key=lambda item: item[1])
                    share = total / sum(votes.values())
                    score = min(.96, max(.3, (1. - top_dist / .3) * share))
                    if self._clf is not None and len(self._labels) >= 8:
                        probs = self._clf.predict_proba(feature.reshape(1, -1))[0]
                        ml_idx = int(np.argmax(probs))
                        ml_label = str(self._clf.classes_[ml_idx])
                        if float(probs[ml_idx]) >= .65 and top_dist < .18:
                            return Prediction(ml_label, round(float(probs[ml_idx]), 3), 'ml-supervisionato',
                                              f'Addestrato su {len(self._labels)} esempi corretti')
                    if share >= .58 and score >= .4:
                        return Prediction(candidate, round(score, 3), 'ml-esempi',
                                          f'Somiglianza con {len(self._labels)} esempi corretti')
            if vlm and vlm.get('event') and vlm['event'] != 'incerto' and not vlm.get('uncertain', True):
                return Prediction(str(vlm['event'])[:100], .6, 'ollama-vision', str(vlm.get('description', ''))[:400])
            if scene in SCENE_EVENTS and scene != 'unknown':
                return Prediction(SCENE_EVENTS[scene], .55, 'hud', 'Classificazione tramite riferimenti HUD')
            if vlm and vlm.get('description'):
                return Prediction('incerto', 0., 'ollama-incerto', str(vlm['description'])[:400])
            return Prediction('incerto', 0., 'nessuna-evidenza')

    def archive(self, frame: np.ndarray, *, scene: str, process: str,
                session: str, captured_at: str | None = None) -> tuple[int, bytes]:
        """Persist screenshot BEFORE slower OCR/VLM; review never waits for inference."""
        initial = Prediction(SCENE_EVENTS.get(scene, 'incerto'), .55 if scene != 'unknown' else 0.,
                             'hud' if scene != 'unknown' else 'in_attesa',
                             'In attesa di OCR e classificazione')
        return self.store.add(session=session, frame=frame, process=process,
                              scene=scene, prediction=initial,
                              ocr=OCRResult(status='in_attesa'),
                              feature=image_features(frame, '', scene), captured_at=captured_at)

    def analyze(self, frame: np.ndarray, *, scene: str = 'unknown', process: str = '',
                session: str, reading=None, archived_frame_id: int | None = None,
                archived_jpeg: bytes | None = None, captured_at: str | None = None) -> dict:
        """Run in a dedicated worker thread, never in the input-control loop."""
        self.count += 1
        ocr = self.ocr.read(frame)
        features = image_features(frame, ocr.text, scene)
        gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY if frame.shape[2] == 4 else cv2.COLOR_BGR2GRAY),
                          (160, 90), interpolation=cv2.INTER_AREA)
        change = float(np.count_nonzero(cv2.absdiff(gray, self._prev_gray) > 25) / gray.size) if self._prev_gray is not None else 0.
        self._prev_gray = gray
        # Multimodal model receives the current frame and two prior frames for context.
        vlm = None
        vlm_error = ''
        if self.vlm:
            new_width = min(frame.shape[1], 960)
            new_height = max(1, round(frame.shape[0] * new_width / frame.shape[1]))
            scaled = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode('.jpg', scaled, [cv2.IMWRITE_JPEG_QUALITY, 68])
            if ok:
                self._last_vision_frames.append(encoded.tobytes())
        if self.vlm and self.count % self.ollama_every == 0 and self._last_vision_frames:
            history = self.store.recent(4, session=session)
            short_context = scene + ' | Eventi recenti: ' + '; '.join(
                str(item['predicted_event'])[:60] for item in reversed(history))
            try:
                vlm = self.vlm.describe(self._last_vision_frames[-1],
                                        history_images=list(self._last_vision_frames)[:-1],
                                        context=short_context, ocr=ocr.text)
            except Exception as exc:
                vlm_error = f'{type(exc).__name__}: {str(exc)[:180]}'
        prediction = self.predict(features, scene=scene, text=ocr.text, vlm=vlm)
        # Time-aware state recognition uses a tiny six-state dynamic program.
        # It never overwrites the raw visual prediction and cannot send input.
        if self._temporal_learned_revision != self._revision:
            self._temporal_learned = self.store.confirmed_stage_transitions()
            self._temporal_learned_revision = self._revision
        phase = self._temporal.observe(
            session=session, captured_at=captured_at or datetime.now(timezone.utc).isoformat(),
            scene=scene, predicted_event=prediction.event,
            predicted_score=prediction.score, predicted_source=prediction.source,
            ocr_text=ocr.text, learned=self._temporal_learned)
        if archived_frame_id is not None:
            self.store.finish_analysis(archived_frame_id, prediction=prediction,
                                       ocr=ocr, feature=features, change=change)
            number, jpeg = archived_frame_id, archived_jpeg
        else:
            number, jpeg = self.store.add(session=session, frame=frame, process=process,
                                          scene=scene, prediction=prediction, ocr=ocr,
                                          feature=features, change=change, captured_at=captured_at)
        return {'frame_id': number, 'session': session, 'scene': scene,
                'captured_at': captured_at,
                'prediction': prediction.event, 'score': prediction.score,
                'source': prediction.source, 'description': prediction.description,
                'ocr_text': ocr.text, 'ocr_status': ocr.status,
                'change': round(change, 3), 'jpeg': jpeg,
                'phase': phase.label, 'phase_state': phase.state,
                'phase_confidence': phase.confidence, 'phase_reason': phase.reason,
                'vlm_error': vlm_error}

class LearningWorker:
    """Bounded capture + OCR workers with distinct persistence policies.

    Human-play durable archive saves JPEGs BEFORE expensive OCR/VLM. In AI-play
    archive_frames=False only OCRs in RAM and never calls LearningStore.add().
    """
    def __init__(self, engine: LearningEngine, emit, *, archive_frames: bool = True,
                 durable_archive: bool = False):
        from queue import Queue
        self.engine, self.emit = engine, emit
        self.archive_frames = bool(archive_frames)
        self.durable_archive = bool(durable_archive and archive_frames)
        self.pending = Queue(maxsize=3)  # Slow analysis drops old tasks, not saved JPEGs.
        self.archive_pending = Queue(maxsize=12) if self.durable_archive else None
        self.stopping = threading.Event()
        self._archive_done = threading.Event()
        if not self.durable_archive:
            self._archive_done.set()
        self.thread = threading.Thread(target=self._run, name='ObserverLearning', daemon=True)
        self.archive_thread = (threading.Thread(target=self._archive_run,
                               name='ObserverArchive', daemon=True)
                               if self.durable_archive else None)
        self.dropped = 0
        self.dropped_archive = 0

    def start(self):
        if self.archive_thread:
            self.archive_thread.start()
        self.thread.start()

    def _queue_latest(self, job):
        from queue import Empty, Full
        try:
            self.pending.put_nowait(job)
        except Full:
            try:
                self.pending.get_nowait()
                self.pending.task_done()
                self.dropped += 1
            except Empty:
                pass
            try:
                self.pending.put_nowait(job)
            except Full:
                self.dropped += 1

    def submit(self, frame, *, scene, process, session):
        if self.stopping.is_set():
            return
        from queue import Empty, Full
        sampled_at = datetime.now(timezone.utc).isoformat()
        sample = (frame.copy(), scene, process, session, sampled_at)
        if self.archive_pending is None:
            self._queue_latest(sample)
            return
        try:
            self.archive_pending.put_nowait(sample)
        except Full:
            # Disk is unusually slow or full. Keep memory bounded and surface
            # the loss explicitly; OCR congestion alone cannot lose a JPEG.
            try:
                self.archive_pending.get_nowait()
                self.archive_pending.task_done()
                self.dropped_archive += 1
            except Empty:
                pass
            try:
                self.archive_pending.put_nowait(sample)
            except Full:
                self.dropped_archive += 1

    def _archive_run(self):
        from queue import Empty
        try:
            while not self.stopping.is_set() or not self.archive_pending.empty():
                try:
                    frame, scene, process, session, sampled_at = self.archive_pending.get(timeout=.2)
                except Empty:
                    continue
                try:
                    number, jpeg = self.engine.archive(frame, scene=scene,
                                                        process=process, session=session,
                                                        captured_at=sampled_at)
                    self.emit('learning_frame', f'#{number} screenshot salvato (analisi in coda)',
                              info={'frame_id': number, 'session': session,
                                    'scene': scene, 'prediction': SCENE_EVENTS.get(scene, 'incerto'),
                                    'score': .55 if scene != 'unknown' else 0.,
                                    'source': 'hud' if scene != 'unknown' else 'in_attesa',
                                    'description': 'In attesa di OCR e classificazione',
                                    'ocr_text': '', 'ocr_status': 'in_attesa',
                                    'change': 0., 'jpeg': jpeg, 'vlm_error': '',
                                    'captured_at': sampled_at})
                    self._queue_latest((frame, scene, process, session, number, jpeg, sampled_at))
                except Exception as exc:
                    self.emit('learning_error', 'Archivio screenshot: ' +
                              f'{type(exc).__name__}: {exc}')
                finally:
                    self.archive_pending.task_done()
        finally:
            self._archive_done.set()

    def _run(self):
        from queue import Empty
        while not self.stopping.is_set() or not self.pending.empty() or not self._archive_done.is_set():
            try:
                job = self.pending.get(timeout=.2)
            except Empty:
                continue
            try:
                frame, scene, process, session = job[:4]
                if self.archive_frames:
                    archive_info = ({'archived_frame_id': job[4],
                                     'archived_jpeg': job[5], 'captured_at': job[6]}
                                    if len(job) == 7 else {})
                    result = self.engine.analyze(frame, scene=scene, process=process,
                                                 session=session, **archive_info)
                    self.emit('learning_frame',
                              f"#{result['frame_id']} {result['prediction']} ({result['source']}, "
                              f"{result['score']:.2f}) · OCR: {result['ocr_status']}", info=result)
                    if result['vlm_error']:
                        self.emit('learning_error', 'Vision Ollama: ' + result['vlm_error'])
                else:
                    # OCR is useful for navigation but is ephemeral, not a
                    # review sample or a SQLite record or a JSONL log entry.
                    ocr = self.engine.ocr.read(frame)
                    self.emit('runtime_ocr', 'OCR temporaneo per il pilota AI',
                              info={'ocr_text': ocr.text, 'ocr_status': ocr.status})
            except Exception as exc:
                self.emit('learning_error', f'{type(exc).__name__}: {exc}')
            finally:
                self.pending.task_done()

    def stop(self, timeout=2.0):
        self.stopping.set()
        if self.archive_thread and self.archive_thread.is_alive():
            self.archive_thread.join(timeout=max(.1, timeout / 2))
        if self.thread.is_alive():
            self.thread.join(timeout=max(.1, timeout / 2) if self.archive_thread else timeout)
