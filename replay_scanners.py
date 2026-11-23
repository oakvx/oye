"""Offline, input-free v40 scanner replay.

Usage:
 python replay_scanners.py --folder screenshots/ --interval 0.4 --root . --output replay.jsonl
 python replay_scanners.py --manifest frames.json --root .
Manifest: [{"file":"frame001.png","timestamp":1.0}, ...]

Only evaluates screenshot evidence. Never opens Fortnite, sends inputs, or
claims real-world detection accuracy based on synthetic test images.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import time
from dataclasses import replace
import cv2
from observer.scanner_store import ScannerStore
from observer.visual_scanners import scan
from observer.state_recognition import StateRecognitionEngine
from observer.scanner_scheduler import ScannerScheduler, result_ttl
from observer.screen_profiles import ScreenProfiles
from observer.region_ocr import read_single_region, legacy_target_pool

def replay(frames, *, root, reader=None):
    store=ScannerStore(root)
    specs=store.list()
    structural_ready=store.structural_validations()
    fusion=StateRecognitionEngine()
    recent={}
    scheduler=ScannerScheduler(max_visual=(6 if any('Profilo screenshot v42:' in (s.get('description') or '') for s in specs) else 4),max_ocr=1)
    profiles=ScreenProfiles(root)
    selected=profiles.profiles()['selected']
    legacy_rules={r['target']:r for r in profiles.rules()}
    legacy_cursor=pool_cursor=ocr_turn=0
    rows=[]
    for entry in frames:
        path,at=entry[:2]
        stage=entry[2] if len(entry)>2 else ''
        image=cv2.imread(str(path))
        if image is None:
            rows.append({'file':str(path),'at':at,'error':'Immagine illeggibile'})
            continue
        mismatch=bool(selected and (image.shape[1],image.shape[0])!=
                      (selected['width'],selected['height']))
        outputs={}
        legacy={}
        modular_ocr=any(s['enabled'] and s['method']=='ocr' for s in specs)
        legacy_turn=not modular_ocr or ocr_turn%2==0
        ocr_turn+=1
        if not mismatch and legacy_rules and legacy_turn:
            pool,pool_cursor=legacy_target_pool(legacy_rules,stage,4,pool_cursor)
            if pool:
                target=pool[legacy_cursor % len(pool)]
                legacy_cursor+=1
                legacy[target]=read_single_region(image,legacy_rules[target],reader=reader,at=at).__dict__
        duplicates={'lobby':'gioca','return_to_lobby':'torna_lobby',
                    'reward_title':'reward_title','riscatta':'riscatta'}
        excluded={key for key,value in duplicates.items() if value in legacy}
        selected_scanners=(scheduler.pick(specs,at,stage,allow_ocr=not bool(legacy),
                                           excluded=excluded) if not mismatch else [])
        for spec in selected_scanners:
            result=scan(image,spec,at=at,ocr_reader=reader,
                        reference=store.reference(spec['id']) if spec['method'] in ('template','features') else None)
            if spec['method']=='structure' and spec['id'] not in structural_ready:
                result=replace(result,status='unknown',confidence=0.,
                               detail='Richiesti esempi positivi e negativi reali; '+result.detail)
            outputs[spec['id']]=result
        recent.update(outputs)
        by_id={spec['id']:spec for spec in specs}
        recent={identifier:item for identifier,item in recent.items()
                if identifier in by_id and 0<=at-item.at<=result_ttl(by_id[identifier])}
        if mismatch:recent={}
        state=fusion.evaluate(recent,specs,at)
        rows.append({'file':str(path),'at':at,
                     'frame_size':[image.shape[1],image.shape[0]],
                     'state':state.json(),
                     'scanners':{k:v.json() for k,v in outputs.items()},
                     'legacy_readings':legacy,
                     'scheduler':{'executed':list(outputs),'scan_count':len(outputs),
                                  'legacy_ocr_count':len(legacy),'stage':stage}})
    return rows

def main():
    ap=argparse.ArgumentParser(description='Replay OCR/scanner senza input LIVE')
    src=ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--folder',type=Path)
    src.add_argument('--manifest',type=Path)
    ap.add_argument('--root',type=Path,default=Path('.'))
    ap.add_argument('--interval',type=float,default=.5)
    ap.add_argument('--output',type=Path,default=Path('replay-scanners-v40.jsonl'))
    args=ap.parse_args()
    if args.interval<=0:ap.error('Intervallo deve essere positivo')
    if args.folder:
        images=sorted([p for p in args.folder.iterdir() if p.suffix.lower() in ('.png','.jpg','.jpeg','.webp')])
        frames=[(f,i*args.interval) for i,f in enumerate(images)]
    else:
        data=json.loads(args.manifest.read_text(encoding='utf-8'))
        if not isinstance(data,list):ap.error('Manifest deve essere un array')
        frames=[(args.manifest.parent/item['file'],float(item['timestamp']),item.get('stage','')) for item in data]
    rows=replay(frames,root=args.root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(''.join(json.dumps(x,ensure_ascii=False,default=str)+'\n' for x in rows),encoding='utf-8')
    print(f'Replay: {len(rows)} frame, risultato {args.output}, senza input LIVE')

if __name__=='__main__':main()
