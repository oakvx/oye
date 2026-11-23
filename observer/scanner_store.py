"""Additive SQLite scanner configuration and labeled ROI dataset.

No automatic retraining is claimed: labels are persisted and used for evaluation.
All files are scoped under learning/ and never replace legacy demonstrations.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3
import re
import cv2
import numpy as np
from .visual_scanners import PRESETS, METHODS, STATES, valid_roi

def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')

class ScannerStore:
    def __init__(self,root):
        self.root=Path(root)
        self.folder=self.root/'learning';self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'observer.sqlite3'
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS oye_v40_scanners (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
              method TEXT NOT NULL, roi_json TEXT NOT NULL, expected_text TEXT NOT NULL DEFAULT '',
              threshold REAL NOT NULL DEFAULT .76, weight REAL NOT NULL DEFAULT 1,
              interval REAL NOT NULL DEFAULT 1.5, enabled INTEGER NOT NULL DEFAULT 1,
              state TEXT NOT NULL DEFAULT 'UNKNOWN', options_json TEXT NOT NULL DEFAULT '{}',
              updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS oye_v40_scanner_examples (
              id INTEGER PRIMARY KEY AUTOINCREMENT, scanner_id TEXT NOT NULL,
              positive INTEGER NOT NULL, image_path TEXT NOT NULL, width INTEGER NOT NULL,
              height INTEGER NOT NULL, roi_json TEXT NOT NULL, created_at TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '', FOREIGN KEY(scanner_id) REFERENCES oye_v40_scanners(id));
            CREATE INDEX IF NOT EXISTS oye_v40_examples_id ON oye_v40_scanner_examples(scanner_id);
            ''')
            n=db.execute('SELECT count(*) FROM oye_v40_scanners').fetchone()[0]
            if not n:
                for id_,(name,method,roi,state,expected) in PRESETS.items():
                    db.execute('''INSERT INTO oye_v40_scanners
                    (id,name,description,method,roi_json,expected_text,threshold,weight,interval,enabled,state,options_json,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(id_,name,'Preset indicativo: da calibrare con screenshot reali',
                     method,json.dumps(roi),expected,.72 if method=='structure' else .81,1.,
                     1.0 if method=='structure' else 2.0,0,state,
                     json.dumps({'numeric':True} if id_ in ('player_counter','elimination_counter') else {}),now()))
            # Install real-shot defaults only when a preset is still untouched.
            # Existing user scanners, calibration examples and edits win.
            # Install the curated profile only in an actual Oye installation.
            # Temporary/library databases remain neutral until explicitly
            # enabled; older unit tests and independent projects stay stable.
            import sys
            if (self.root / 'AVVIA_OYE.bat').is_file() or getattr(sys,'frozen',False):
                self._install_v42_defaults(db)
    def _install_v42_defaults(self,db):
        from .v42_profile import SEEDS, VERSION
        db.execute('''CREATE TABLE IF NOT EXISTS oye_v42_provision
                      (profile TEXT PRIMARY KEY, installed_at TEXT NOT NULL)''')
        if db.execute('SELECT 1 FROM oye_v42_provision WHERE profile=?',(VERSION,)).fetchone():
            return
        for scanner_id,seed in SEEDS.items():
            row=db.execute('SELECT * FROM oye_v40_scanners WHERE id=?',(scanner_id,)).fetchone()
            if row is None:
                values=self._validated({**seed,'id':scanner_id,'enabled':True})
                self._insert(db,values)
            elif str(row['description']).startswith('Preset indicativo:') and not db.execute(
                    'SELECT 1 FROM oye_v40_scanner_examples WHERE scanner_id=? LIMIT 1',(scanner_id,)).fetchone():
                values={**seed,'id':scanner_id,'enabled':True}
                self._insert(db,values)
        db.execute('INSERT INTO oye_v42_provision VALUES(?,?)',(VERSION,now()))

    @contextmanager
    def db(self):
        con=sqlite3.connect(self.path,timeout=5)
        try:
            con.row_factory=sqlite3.Row
            con.execute('PRAGMA busy_timeout=5000')
            yield con
            con.commit()
        finally:con.close()
    def list(self):
        with self.db() as db:
            rows=db.execute('SELECT * FROM oye_v40_scanners ORDER BY id').fetchall()
            return [self._parse(r) for r in rows]
    @staticmethod
    def _parse(r):
        d=dict(r);d['roi']=json.loads(d.pop('roi_json'));d['options']=json.loads(d.pop('options_json'));d['enabled']=bool(d['enabled']);return d
    def get(self,id_):
        with self.db() as db:
            r=db.execute('SELECT * FROM oye_v40_scanners WHERE id=?',(id_,)).fetchone()
            return self._parse(r) if r else None
    def _validated(self,data):
        # Validate a complete draft without modifying SQLite.
        if not isinstance(data,dict):raise ValueError('Configurazione scanner non valida')
        id_=str(data.get('id',''))
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,47}',id_):raise ValueError('ID scanner non valido')
        existing=self.get(id_) or {}
        values={**existing,**data}
        method=values.get('method','ocr')
        if method not in METHODS:raise ValueError('Metodo scanner sconosciuto')
        roi=valid_roi(values['roi'])
        state=values.get('state','UNKNOWN')
        if state not in STATES:raise ValueError('Stato del riconoscitore non valido')
        for name,maximum in (('name',80),('description',350),('expected_text',200)):
            if not isinstance(values.get(name,''),str) or len(values.get(name,''))>maximum:
                raise ValueError(name+' non valido')
        threshold=float(values.get('threshold',.8))
        weight=float(values.get('weight',1.))
        interval=float(values.get('interval',1.5))
        if not(.05<=threshold<=1 and .1<=weight<=3 and .25<=interval<=30):
            raise ValueError('Soglie scanner fuori intervallo')
        enabled=values.get('enabled',True)
        if type(enabled) is not bool:raise ValueError('Stato enabled non valido')
        opts=values.get('options') or {}
        if not isinstance(opts,dict) or len(json.dumps(opts,allow_nan=False))>3000:
            raise ValueError('Opzioni non valide')
        return {'id':id_,'name':values.get('name',id_),
                'description':values.get('description',''),'method':method,
                'roi':roi,'expected_text':values.get('expected_text',''),
                'threshold':threshold,'weight':weight,'interval':interval,
                'enabled':enabled,'state':state,'options':opts}

    @staticmethod
    def _insert(db,values):
        db.execute('''INSERT INTO oye_v40_scanners
            (id,name,description,method,roi_json,expected_text,threshold,weight,interval,enabled,state,options_json,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,description=excluded.description,method=excluded.method,
            roi_json=excluded.roi_json,expected_text=excluded.expected_text,
            threshold=excluded.threshold,weight=excluded.weight,interval=excluded.interval,
            enabled=excluded.enabled,state=excluded.state,options_json=excluded.options_json,
            updated_at=excluded.updated_at''',
            (values['id'],values['name'],values['description'],values['method'],
             json.dumps(values['roi']),values['expected_text'],values['threshold'],
             values['weight'],values['interval'],int(values['enabled']),
             values['state'],json.dumps(values['options']),now()))

    def validate(self,data):
        # Validation for unsaved editor drafts; never alters persisted rules.
        return self._validated(data)

    def save(self,data):
        values=self._validated(data)
        with self.db() as db:
            self._insert(db,values)
        return self.get(values['id'])
    def delete(self,id_):
        with self.db() as db:
            cursor=db.execute('DELETE FROM oye_v40_scanners WHERE id=?',(id_,));return cursor.rowcount>0
    def reset(self,id_):
        if id_ not in PRESETS:raise ValueError('Preset non trovato')
        name,method,roi,state,expected=PRESETS[id_]
        return self.save({'id':id_,'name':name,'description':'Preset ripristinato, richiede verifica',
                          'method':method,'roi':roi,'state':state,'expected_text':expected,
                          'enabled':False,'threshold':.72 if method=='structure' else .81,'weight':1.,'interval':1.5,'options':{'numeric':True} if id_ in ('player_counter','elimination_counter') else {}})
    def export(self):
        return {'schema':1,'scanners':self.list()}
    def import_config(self,config):
        if (not isinstance(config,dict) or config.get('schema')!=1 or
                not isinstance(config.get('scanners'),list) or len(config['scanners'])>128):
            raise ValueError('File scanner non valido')
        # Preflight every entry before writing anything. All writes share one
        # transaction, so an invalid preset cannot partially replace settings.
        staged=[self._validated(item) for item in config['scanners']]
        ids=[item['id'] for item in staged]
        if len(ids)!=len(set(ids)):
            raise ValueError('ID scanner duplicati nel preset')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in staged:
                self._insert(db,item)
        return ids
    def structural_validations(self):
        """Only labeled positive AND negative samples allow an HUD heuristic
        to influence global state. A disabled/untrained preset remains debugger
        data, never authoritative evidence for the game controller.
        """
        with self.db() as db:
            rows=db.execute('''SELECT scanner_id,
                 sum(CASE WHEN positive=1 THEN 1 ELSE 0 END) AS positives,
                 sum(CASE WHEN positive=0 THEN 1 ELSE 0 END) AS negatives
                 FROM oye_v40_scanner_examples GROUP BY scanner_id''').fetchall()
        return {r['scanner_id'] for r in rows if r['positives']>=1 and r['negatives']>=1}

    def examples(self,scanner_id=None):
        with self.db() as db:
            if scanner_id:rows=db.execute('SELECT * FROM oye_v40_scanner_examples WHERE scanner_id=? ORDER BY id DESC',(scanner_id,)).fetchall()
            else:rows=db.execute('SELECT * FROM oye_v40_scanner_examples ORDER BY id DESC LIMIT 500').fetchall()
            return [{**dict(r),'roi':json.loads(r['roi_json'])} for r in rows]
    def add_example(self,scanner_id,frame,positive,*,note=''):
        spec=self.get(scanner_id)
        if spec is None:raise ValueError('Scanner non trovato')
        if type(positive) is not bool:raise ValueError('Etichetta positiva/negativa mancante')
        if not isinstance(note,str) or len(note)>500:raise ValueError('Nota non valida')
        from .visual_scanners import crop_roi
        crop=crop_roi(frame,spec['roi']);h,w=frame.shape[:2]
        from uuid import uuid4
        loc=self.folder/'scanner_examples'/scanner_id;loc.mkdir(parents=True,exist_ok=True)
        filename=uuid4().hex+'.png';path=loc/filename
        if not cv2.imwrite(str(path),crop):raise OSError('Impossibile salvare la ROI')
        try:
            with self.db() as db:
                cur=db.execute('''INSERT INTO oye_v40_scanner_examples
                 (scanner_id,positive,image_path,width,height,roi_json,created_at,note)
                 VALUES(?,?,?,?,?,?,?,?)''',(scanner_id,int(positive),str(path.relative_to(self.folder)),w,h,
                                               json.dumps(spec['roi']),now(),note))
                number=cur.lastrowid
        except Exception:
            path.unlink(missing_ok=True);raise
        return {'id':number,'scanner_id':scanner_id,'positive':positive}
    def delete_example(self,id_):
        with self.db() as db:
            row=db.execute('SELECT image_path FROM oye_v40_scanner_examples WHERE id=?',(id_,)).fetchone()
            if row is None:return False
            db.execute('DELETE FROM oye_v40_scanner_examples WHERE id=?',(id_,))
        # Remove only managed relative paths, never arbitrary user-supplied paths.
        path=(self.folder/row['image_path']).resolve()
        if (self.folder/'scanner_examples').resolve() in path.parents:path.unlink(missing_ok=True)
        return True
    def reference(self,scanner_id):
        """First positive taught ROI; used for template/features, not for OCR."""
        for ex in self.examples(scanner_id):
            if not ex['positive']:continue
            path=(self.folder/ex['image_path']).resolve()
            if (self.folder/'scanner_examples').resolve() not in path.parents:continue
            image=cv2.imread(str(path))
            if image is not None:return image
        # Packaged real-game template is read-only and NEVER replaces a
        # user-taught example or a scanner that the user has customized.
        from .v42_profile import MARKER, packaged_reference
        spec=self.get(scanner_id)
        if spec and spec['method']=='template' and spec['description'].startswith(MARKER):
            file=packaged_reference(scanner_id)
            if file is not None:
                return cv2.imread(str(file),cv2.IMREAD_COLOR)
        return None

    def example_image(self,id_):
        with self.db() as db:
            r=db.execute('SELECT image_path FROM oye_v40_scanner_examples WHERE id=?',(id_,)).fetchone()
            if r is None:raise ValueError('Esempio inesistente')
            path=(self.folder/r['image_path']).resolve()
            if (self.folder/'scanner_examples').resolve() not in path.parents:raise ValueError('Percorso non valido')
            return path.read_bytes()

    def evaluate(self,id_,reader=None):
        from .visual_scanners import scan
        spec=self.get(id_)
        if not spec:raise ValueError('Scanner non trovato')
        positives=negatives=correct=unknown=0
        for item in self.examples(id_):
            path=(self.folder/item['image_path']).resolve()
            if (self.folder/'scanner_examples').resolve() not in path.parents:continue
            crop=cv2.imread(str(path))
            if crop is None:continue
            # ROI image itself: evaluate with full-frame ROI without inflating score.
            trial={**spec,'roi':[0,0,1,1]}
            reference=self.reference(id_)
            r=scan(crop,trial,ocr_reader=reader,reference=reference)
            positives+=int(bool(item['positive']));negatives+=int(not item['positive'])
            correct+=int((r.status=='present')==bool(item['positive']) and r.status not in ('unknown','error'))
            unknown+=int(r.status in ('unknown','error'))
        total=positives+negatives
        return {'total':total,'positives':positives,'negatives':negatives,
                'correct':correct,'unknown':unknown,'accuracy_on_labeled':correct/total if total else None,
                'note':'Valutazione sul dataset etichettato, non aggiornamento automatico del modello'}
