"""Offline diagnostic for the user-supplied 136 screenshot archive.

Run python verify_real_shots.py /path/to/frames.zip --output /path/to/report.json
No input is sent to the game; all labels below are from reviewing a single
recording. They must NOT be considered independent match validation.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse,json,zipfile,cv2,hashlib
from observer.scanner_store import ScannerStore
from observer.visual_scanners import scan
from observer.state_recognition import StateRecognitionEngine
from observer.scanner_scheduler import ScannerScheduler,result_ttl

def manual_label(i):
    if i<=6:return 'MATCHMAKING'
    if i<=11:return 'LOADING'
    if i<=17:return 'IN_MATCH' # pre-game island; no separate state in current enum
    if i<=21:return 'BATTLE_BUS'
    if i<=27:return 'SKYDIVING'
    if i<=59:return 'IN_MATCH'
    if i<=60:return 'ELIMINATED' # short handoff
    if i<=125:return 'SPECTATING' # overlay sometimes disappears while watching team
    if i<=132:return 'RETURNING_TO_LOBBY'
    if i<=134:return 'LOADING'
    return 'LOBBY'

KNOWN_SHA256='b0d3cf384ded579169f654e4d7f23c1378808e3e51839db9ab9201c73ef6c74a'

def inspect_archive(zip_path:Path,output:Path|None=None):
    # Human-reviewed frame labels refer ONLY to the exact supplied archive.
    # Do not silently apply them to unrelated user recordings.
    with open(zip_path,'rb') as stream:
        actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=KNOWN_SHA256:
        raise ValueError('Archivio differente da frames.zip originale: le etichette richiedono una nuova revisione')
    rows=[]
    with TemporaryDirectory() as td:
        base=Path(td)
        (base/'AVVIA_OYE.bat').write_text('v42 profile seed for offline replay')
        store=ScannerStore(base);specs=store.list();enabled=[s for s in specs if s['enabled']]
        scheduler=ScannerScheduler(max_visual=6,max_ocr=0)
        fusion=StateRecognitionEngine();recent={}
        with zipfile.ZipFile(zip_path) as archive:
            files=sorted([f for f in archive.infolist() if f.filename.lower().endswith(('.jpg','.jpeg','.png'))],key=lambda f:(f.date_time,f.filename))
            if not files:raise ValueError('Archivio privo di fotogrammi')
            start=datetime(*files[0].date_time)
            for i,info in enumerate(files):
                data=archive.read(info);frame=cv2.imdecode(__import__('numpy').frombuffer(data,__import__('numpy').uint8),cv2.IMREAD_COLOR)
                if frame is None:raise ValueError('JPEG illeggibile: '+info.filename)
                at=(datetime(*info.date_time)-start).total_seconds()
                stage='await_play' if i<=6 or i>=133 else 'joining' if i<=27 else 'exploring'
                picked=scheduler.pick(enabled,at,stage,allow_ocr=False)
                observations={}
                for s in picked:
                    r=scan(frame,s,at=at,reference=store.reference(s['id']))
                    observations[s['id']]=r
                recent.update(observations)
                by_id={s['id']:s for s in enabled}
                recent={k:v for k,v in recent.items() if k in by_id and 0<=at-v.at<=result_ttl(by_id[k])}
                decision=fusion.evaluate(recent,enabled,at)
                rows.append({'index':i,'time':info.date_time,'filename':info.filename,
                             'ground_truth_reviewed':manual_label(i),'predicted':decision.state,
                             'candidate':decision.candidate,'scores':decision.alternatives,
                             'observations':{k:{'status':v.status,'score':round(v.confidence,3)} for k,v in observations.items()}})
    truth=Counter(x['ground_truth_reviewed'] for x in rows)
    distribution=Counter(x['predicted'] for x in rows)
    matrix=defaultdict(Counter)
    for row in rows:matrix[row['ground_truth_reviewed']][row['predicted']]+=1
    targeted={'LOBBY','MATCHMAKING','SPECTATING','RETURNING_TO_LOBBY'}
    false_targets=[x['index'] for x in rows if x['predicted'] in targeted and x['predicted']!=x['ground_truth_reviewed']]
    report={'image_count':len(rows),'start_timestamp':rows[0]['time'],'end_timestamp':rows[-1]['time'],
            'origin':'One continuous recording plus a later lobby screenshot, not three independent matches',
            'label_source':'Manual review of contact sheets; phases approximate and spectator overlay may disappear',
            'truth_counts':dict(truth),'predicted_counts':dict(distribution),
            'confusion':{k:dict(v) for k,v in matrix.items()},'wrong_confirmed_targets':false_targets,
            'rows':rows}
    if output:
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('zip',type=Path);a.add_argument('--output',type=Path);args=a.parse_args()
    report=inspect_archive(args.zip,args.output)
    print('Frames:',report['image_count'])
    print('truth:',report['truth_counts'])
    print('predicted:',report['predicted_counts'])
    print('confusion:',report['confusion'])
    print('WRONG CONFIRMED TARGETS:',report['wrong_confirmed_targets'])
