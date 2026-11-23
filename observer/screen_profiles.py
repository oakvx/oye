"""Resolution-scoped user explanations for existing Oye recognizers.

This is an additive migration of learning/observer.sqlite3. It never deletes or
rewrites previous demonstrations. Templates remain in templates/ and are used by
Vision; this database stores their meaning and selected resolution, not a
second input engine.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from .config import TEMPLATE_NAMES
from .teach_ocr import PHRASES

# Only actions already understood by the controller. A free-form description is
# documentation/training context, never Python or an executable instruction.
BEHAVIOUR = {
    'gioca': ('lobby', 'Riconosci il pulsante Gioca in basso a sinistra'),
    'annulla': ('loading', 'Riconosci la ricerca partita'),
    'posizione_finale': ('results', 'Riconosci la fine della partita'),
    'lobby_indizio': ('lobby', 'Riconosci la lobby, non muovere il personaggio'),
    'caricamento': ('loading', 'Aspetta la fine del caricamento'),
    'bus_salto': ('bus', 'Indizio del bus; rispetta la sequenza della modalità scelta'),
    'discesa_apri': ('falling', 'Indizio discesa; rispetta la sequenza della modalità scelta'),
    'hud_in_partita': ('match', 'Riconosci la partita solo con altri segnali coerenti'),
    'hud_salute': ('match', 'Indizio HUD: barra salute'),
    'hud_minimappa': ('match', 'Indizio HUD: minimappa'),
    'hud_inventario': ('match', 'Indizio HUD: inventario'),
    'eliminato': ('spectator', 'Ferma il movimento: personaggio eliminato'),
    'spettatore_prossimo': ('spectator', 'Ferma il movimento: spettatore'),
    'spettatore_precedente': ('spectator', 'Ferma il movimento: spettatore'),
    'torna_lobby': ('results', 'Clicca Torna alla lobby quando la fase è confermata'),
    'continua': ('results', 'Clicca Continua solo nella fase finale confermata'),
    'reward_title': ('rewards', 'Blocca il movimento e cerca Riscatta, non Equipaggia'),
    'riscatta': ('rewards', 'Clicca Riscatta dopo conferma ricompense'),
    'gioca_di_nuovo': ('results', 'Riconosci il comando di nuova partita'),
}
MODES = ('borderless', 'fullscreen', 'windowed')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def validate_resolution(width, height) -> tuple[int, int]:
    if type(width) is not int or type(height) is not int or not (640 <= width <= 7680 and 480 <= height <= 4320):
        raise ValueError('Risoluzione non valida (larghezza 640–7680, altezza 480–4320)')
    return width, height


def validate_region(region):
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        raise ValueError('Disegna un rettangolo sullo screenshot')
    import math
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in region):
        raise ValueError('Coordinate non valide')
    a, b, c, d = map(float, region)
    if not (0 <= a < c <= 1 and 0 <= b < d <= 1) or c-a < .012 or d-b < .012:
        raise ValueError('Seleziona un riquadro più grande')
    return [round(a, 6), round(b, 6), round(c, 6), round(d, 6)]


class ScreenProfiles:
    def __init__(self, root: Path):
        self.database = Path(root) / 'learning' / 'observer.sqlite3'
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS oye_resolution_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    source TEXT NOT NULL,
                    selected INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(width, height, mode)
                );
                CREATE TABLE IF NOT EXISTS oye_screen_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL REFERENCES oye_resolution_profiles(id),
                    target TEXT NOT NULL,
                    state TEXT NOT NULL,
                    action_description TEXT NOT NULL,
                    user_explanation TEXT NOT NULL DEFAULT '',
                    region_json TEXT NOT NULL,
                    expected_text TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(profile_id, target)
                );
            ''')
            # Additive upgrade for existing v39 databases, no data deletion.
            names={row[1] for row in db.execute('PRAGMA table_info(oye_screen_rules)')}
            if 'expected_text' not in names:
                db.execute("ALTER TABLE oye_screen_rules ADD COLUMN expected_text TEXT NOT NULL DEFAULT ''")
        # User explicitly supplied 1920x1080 borderless as the profile for
        # this distribution. Initialize ONLY if this is a real Oye installation
        # with no previously chosen resolution; never override older profiles.
        import sys
        if (Path(root)/'AVVIA_OYE.bat').is_file() or getattr(sys,'frozen',False):
            if self.profiles()['selected'] is None:
                self.select(1920,1080,mode='borderless',source='manual')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA busy_timeout=5000')
            yield db
        finally:
            db.close()

    def profiles(self) -> dict:
        with self._connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM oye_resolution_profiles ORDER BY selected DESC, updated_at DESC')]
            current = next((row for row in rows if row['selected']), None)
            return {'selected': current, 'profiles': rows,
                    'allowed_targets': {k:{'state':s, 'next':a} for k,(s,a) in BEHAVIOUR.items()}}

    def select(self, width: int, height: int, *, mode='borderless', source='manual') -> dict:
        validate_resolution(width, height)
        if mode not in MODES or source not in ('manual','detected'):
            raise ValueError('Tipo di profilo non valido')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE oye_resolution_profiles SET selected=0 WHERE selected=1')
            db.execute('''INSERT INTO oye_resolution_profiles(width,height,mode,source,selected,updated_at)
                VALUES(?,?,?,?,1,?) ON CONFLICT(width,height,mode) DO UPDATE SET
                selected=1, source=excluded.source, updated_at=excluded.updated_at''',
                (width, height, mode, source, _now()))
            db.commit()
        return self.profiles()

    def add_rule(self, target: str, region, *, explanation='', expected_text='', image_size: tuple[int,int]) -> dict:
        if target not in TEMPLATE_NAMES or target not in BEHAVIOUR:
            raise ValueError('Elemento non supportato dal motore: scegli uno dei riferimenti proposti')
        rect = validate_region(region)
        if not isinstance(expected_text,str) or len(expected_text)>120:
            raise ValueError('Testo OCR massimo 120 caratteri')
        if not isinstance(explanation, str) or len(explanation) > 500:
            raise ValueError('Spiegazione troppo lunga (massimo 500 caratteri)')
        with self._connect() as db:
            profile = db.execute('SELECT * FROM oye_resolution_profiles WHERE selected=1').fetchone()
            if profile is None:
                raise ValueError('Prima conferma la risoluzione')
            if (profile['width'], profile['height']) != image_size:
                raise ValueError(f'Lo screenshot è {image_size[0]}×{image_size[1]}, ma il profilo è {profile["width"]}×{profile["height"]}. Correggi la risoluzione o acquisisci una nuova immagine.')
            state, next_action = BEHAVIOUR[target]
            db.execute('''INSERT INTO oye_screen_rules
                (profile_id,target,state,action_description,user_explanation,region_json,expected_text,updated_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(profile_id,target) DO UPDATE SET
                state=excluded.state, action_description=excluded.action_description,
                user_explanation=excluded.user_explanation, region_json=excluded.region_json,
                expected_text=excluded.expected_text, updated_at=excluded.updated_at''',
                (profile['id'], target, state, next_action, explanation.strip(), json.dumps(rect), expected_text.strip(), _now()))
            db.commit()
        return {'target':target,'state':state,'next':next_action,'region':rect,'explanation':explanation.strip(),'expected_text':expected_text.strip()}

    def rules(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute('''SELECT r.* FROM oye_screen_rules r
                JOIN oye_resolution_profiles p ON p.id=r.profile_id WHERE p.selected=1 ORDER BY r.id''').fetchall()
            return [{**dict(row), 'region':json.loads(row['region_json'])} for row in rows]

    def assets_dir(self) -> Path | None:
        """Resolution-specific templates stay under learning/, NEVER overwrite
        older templates/ from previous Oye installations."""
        selected=self.profiles()['selected']
        if not selected:return None
        return self.database.parent / 'screen_profiles' / (
            f"{selected['width']}x{selected['height']}_{selected['mode']}")

    def remove_rule(self, target: str) -> bool:
        if target not in BEHAVIOUR:
            raise ValueError('Riferimento sconosciuto')
        assets=self.assets_dir()
        with self._connect() as db:
            cur=db.execute("""DELETE FROM oye_screen_rules WHERE target=? AND profile_id IN
                (SELECT id FROM oye_resolution_profiles WHERE selected=1)""",(target,))
            db.commit()
        if assets is not None:
            history=assets/'_removed'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
            file=assets/(target+'.png')
            if file.is_file():
                history.mkdir(parents=True,exist_ok=True)
                file.rename(history/file.name)
        return cur.rowcount > 0
