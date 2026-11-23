"""Resolution-scoped, independent OCR readers for user-taught screen regions.

Each target has its OWN rectangle, phrase, stability counter and evidence age.
The worker operates asynchronously on one RAM copy; it never sends inputs.
Unknown/missing OCR is not proof of the ABSENCE of a button.
"""
from __future__ import annotations
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from pathlib import Path
import queue
import threading
import time
from typing import Callable
import cv2
import numpy as np
from .menu_text import fragment_score
from .screen_profiles import ScreenProfiles
from .scanner_store import ScannerStore
from .visual_scanners import scan, ScannerResult, crop_roi
from .state_recognition import StateRecognitionEngine
from .scanner_scheduler import ScannerScheduler, result_ttl
from .ocr_detection_counter import OCRDetectionCounter
from .teach_ocr import PHRASES, _configure_tesseract, normalize

# Rewards and spectator states take precedence over lobby. Scan a bounded number
# per screenshot to keep Tesseract from slowing down the game capture loop.
PRIORITY = ('reward_title','riscatta','eliminato','spettatore_prossimo',
            'spettatore_precedente','torna_lobby','continua','gioca_di_nuovo','gioca',
            'caricamento','bus_salto','discesa_apri','lobby_indizio')

def legacy_target_pool(rules,stage,limit,cursor):
    """Shared legacy-zone selection for the live worker and offline replay."""
    if stage in ('await_play','joining','post_lobby'):
        first=('reward_title','riscatta','gioca','torna_lobby')
    elif stage in ('jumping','exploring'):
        first=('eliminato','spettatore_prossimo','spettatore_precedente','reward_title')
    else:
        first=('reward_title','riscatta','gioca_di_nuovo','torna_lobby','continua')
    available=[k for k in PRIORITY if k in rules and (rules[k].get('expected_text') or k in PHRASES)]
    chosen=list(dict.fromkeys(k for k in first if k in available))[:limit]
    remaining=[k for k in available if k not in chosen]
    free=limit-len(chosen)
    if free>0 and remaining:
        start=cursor%len(remaining)
        chosen += [remaining[(start+i)%len(remaining)] for i in range(min(free,len(remaining)))]
        cursor+=free
    return chosen,cursor


def select_legacy_target(pool, stage, turn):
    """Prioritize the current CTA while rotating other safety/menu regions.

    At most every other legacy OCR slot is reserved; other targets cannot be
    permanently starved. A turn corresponds to an actual OCR opportunity.
    """
    if not pool:
        return None
    preferred = ('gioca' if stage in ('await_play', 'joining') else
                 'gioca_di_nuovo' if stage == 'ending' else None)
    if preferred not in pool or len(pool) == 1:
        return pool[turn % len(pool)]
    if turn % 2 == 0:
        return preferred
    others = [target for target in pool if target != preferred]
    return others[(turn // 2) % len(others)]


@dataclass(frozen=True)
class RegionReading:
    target: str
    at: float
    text: str
    score: float
    matched: bool
    stable: bool
    center: tuple[int,int]
    status: str


def read_single_region(frame: np.ndarray, rule: dict, *, reader: Callable[[np.ndarray], str] | None = None, at: float = 0.0) -> RegionReading:
    """Read ONLY the taught ROI; no whole-frame fallback or inferred clicks."""
    target = str(rule['target'])
    h, w = frame.shape[:2]
    rect = rule['region']
    x1,y1,x2,y2 = (round(rect[0]*w),round(rect[1]*h),round(rect[2]*w),round(rect[3]*h))
    center=((x1+x2)//2,(y1+y2)//2)
    if x2-x1 < 20 or y2-y1 < 14:
        return RegionReading(target,at,'',0.,False,False,center,'invalid_region')
    crop=frame[y1:y2,x1:x2]
    expected = str(rule.get('expected_text','') or '').strip()
    phrases=[expected] if expected else list(PHRASES.get(target,()))
    # For HUD borders, minimap and images without a phrase, the template matcher
    # is the appropriate reader. OCR must not invent a textual cue.
    if not phrases:
        return RegionReading(target,at,'',0.,False,False,center,'template_only')
    try:
        if reader is None:
            tess=_configure_tesseract()
            gray=cv2.cvtColor(crop,cv2.COLOR_BGRA2GRAY if crop.shape[2]==4 else cv2.COLOR_BGR2GRAY)
            if gray.shape[1]<350:
                factor=min(3,max(1,round(350/gray.shape[1])))
                gray=cv2.resize(gray,None,fx=factor,fy=factor,interpolation=cv2.INTER_CUBIC)
            text=tess.image_to_string(gray,lang='eng',config='--psm 7',timeout=2.0)
        else:
            text=reader(crop)
        score=fragment_score(text,phrases)
        if target=='gioca_di_nuovo':
            t=set(normalize(text).split())
            if not ({'gioca','nuovo'}<=t or {'play','again'}<=t):score=0.
        return RegionReading(target,at,normalize(text)[:120],score,score>=.81,False,center,'ok')
    except Exception as exc:
        return RegionReading(target,at,'',0.,False,False,center,'ocr_error:'+type(exc).__name__)


class ScreenRegionOCRWorker:
    """OCR-specific asynchronous scanner; one slot, no stored screenshots.

    Each ROI has its own 2-confirmation gate and never inherits OCR evidence
    from a different target. Scan order varies by current match stage.
    """
    def __init__(self, root: Path, *, max_per_frame=4, allow_ocr=True):
        self.root=Path(root)
        self.allow_ocr=bool(allow_ocr)
        state=ScreenProfiles(root)
        self.selected=state.profiles()['selected']
        self.rules={x['target']:x for x in state.rules()}
        self.max_per_frame=max(1,min(8,int(max_per_frame)))
        self.pending=queue.Queue(maxsize=1)
        self.results=queue.Queue(maxsize=2)
        self.stop_event=threading.Event()
        self.discovery=OCRDetectionCounter(root)
        self.thread=None
        self.counts=defaultdict(int)
        self.last_seen_at={}
        self.cursor=0
        self.scanner_store=ScannerStore(root)
        self.scanners=self.scanner_store.list()
        self.structural_validations=self.scanner_store.structural_validations()
        self.fusion=StateRecognitionEngine()
        self.scanner_cache={}
        self.scheduler=ScannerScheduler(max_visual=(6 if any('Profilo screenshot v42:' in (s.get('description') or '') for s in self.scanners) else 4),max_ocr=1)
        self.legacy_cursor=0
        self.last_submitted_at=float('-inf')
        self.submit_lock=threading.Lock()
        self.ocr_turn=0
        self._reload_requested=threading.Event()
        self.scan_history=defaultdict(lambda:deque(maxlen=10))
        self.crop_history=defaultdict(lambda:deque(maxlen=4))
        self.debug_snapshot={'status':'waiting','scanners':{},'state':None,'frame_at':0}
        self._updated_rules_at=0.

    def reload(self):
        """Request a worker-owned reload: no concurrent mutation of caches.

        The next processed screenshot uses the new config. No new capture or
        thread is created, and the state fusion restarts after configuration
        changes so old confirmations cannot survive a new ROI.
        """
        if self.thread is not None and self.thread.is_alive():
            self._reload_requested.set()
        else:
            self._apply_reload()

    def _apply_reload(self):
        state=ScreenProfiles(self.root)
        self.selected=state.profiles()['selected']
        self.rules={x['target']:x for x in state.rules()}
        self.scanners=self.scanner_store.list()
        self.structural_validations=self.scanner_store.structural_validations()
        self.counts.clear();self.last_seen_at.clear()
        self.scheduler.reset();self.scanner_cache.clear()
        self.fusion=StateRecognitionEngine()
        self.scan_history.clear();self.crop_history.clear()
        self._updated_rules_at=time.monotonic()
        self._reload_requested.clear()

    def start(self):
        if self.rules or self.scanners:
            self.thread=threading.Thread(target=self._run,name='OyeRegionOCR',daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=2)

    def submit(self, frame, at: float, stage: str = '') -> bool:
        if self.thread is None or self.pending.full() or self.stop_event.is_set():return False
        # Duplicate or out-of-order screenshots must never advance OCR
        # stability, even if a caller accidentally submits them twice.
        with self.submit_lock:
            at = float(at)
            if not np.isfinite(at) or at <= self.last_submitted_at:
                return False
            try:
                self.pending.put_nowait((frame.copy(),at,stage))
            except queue.Full:
                return False
            self.last_submitted_at = at
            return True

    def take(self):
        latest=None
        while True:
            try:latest=self.results.get_nowait()
            except queue.Empty:return latest

    def _targets(self, stage):
        pool,self.cursor=legacy_target_pool(self.rules,stage,max(5,self.max_per_frame),self.cursor)
        return pool

    def previous_crop(self,scanner_id,offset=1):
        if not isinstance(offset,int) or not 0<=offset<=3:
            raise ValueError('Posizione nella cronologia non valida')
        history=self.crop_history.get(scanner_id)
        if history is None or len(history)<=offset:
            raise ValueError('Nessuna ROI precedente da confrontare')
        return history[-1-offset][1]

    @staticmethod
    def _resources():
        try:
            import psutil
            process=psutil.Process()
            return {'rss_mb':round(process.memory_info().rss/(1024*1024),1),
                    'cpu_percent':round(process.cpu_percent(interval=None),1)}
        except (ImportError,OSError):
            return {'rss_mb':None,'cpu_percent':None}

    def _run(self):
        # A single submitted screenshot is shared by all ROI readers. OCR runs
        # on this background thread, never on Runner's live input thread.
        while not self.stop_event.is_set():
            try:frame,at,stage=self.pending.get(timeout=.25)
            except queue.Empty:continue
            if self._reload_requested.is_set():
                try:self._apply_reload()
                except Exception as exc:
                    # Keep the old configuration rather than killing the only
                    # OCR worker on a transient database failure.
                    self.debug_snapshot={'status':'reload_error','error':str(exc)[:160],
                                         'scanners':{},'state':None,'frame_at':at}
                    self._reload_requested.clear()
            mismatch=bool(self.selected and (frame.shape[1],frame.shape[0]) !=
                          (self.selected['width'],self.selected['height']))
            readings={}
            new_detections=[]
            # Legacy semantic zones are consumed here, not in another worker.
            # One OCR + at most one modern OCR per screenshot prevents the
            # N*2s backlog that made old results expire before consumption.
            modular_ocr=any(x.get('enabled') and x.get('method')=='ocr' for x in self.scanners)
            # One costly OCR operation per captured frame, shared fairly
            # between legacy and modern readers. Their timeouts cannot stack.
            legacy_turn=not modular_ocr or self.ocr_turn%2==0
            self.ocr_turn+=1
            if self.allow_ocr and not mismatch and legacy_turn:
                pool=self._targets(stage)
                target=select_legacy_target(pool, stage, self.legacy_cursor)
                targets=[target] if target is not None else []
                self.legacy_cursor+=bool(pool)
            else:targets=[]
            for target in targets:
                if self.stop_event.is_set():break
                r=read_single_region(frame,self.rules[target],at=at)
                if at-self.last_seen_at.get(target,float('-inf'))>max(5.0, len(self.rules)*4.5):
                    self.counts[target]=0
                self.counts[target]=self.counts[target]+1 if r.status=='ok' and r.matched else 0
                self.last_seen_at[target]=at
                readings[target]={**r.__dict__,'stable':self.counts[target]>=2 and r.matched,
                                  'region':list(self.rules[target]['region'])}
                if self.discovery.observe(target,at=at,status=r.status,matched=r.matched,
                    stable=readings[target]['stable'],text=r.text,confidence=r.score,
                    label=target.replace('_',' ').title()):
                    new_detections.append({'target':target,'label':target.replace('_',' ').title(),
                                           'text':r.text})
            # Live and offline replay share the SAME deadline/fairness policy.
            # Never treat a mismatched resolution as a valid scanned frame.
            already={
                'lobby':'gioca','return_to_lobby':'torna_lobby',
                'reward_title':'reward_title','riscatta':'riscatta'
            }
            excluded={key for key,value in already.items() if value in readings}
            ordered=(self.scheduler.pick(self.scanners,at,stage,
                                         allow_ocr=self.allow_ocr and not bool(targets),
                                         excluded=excluded) if not mismatch else [])
            for spec in ordered:
                if self.stop_event.is_set():break
                result=scan(frame,spec,at=at,reference=(self.scanner_store.reference(spec['id']) if spec['method'] in ('template','features') else None))
                if spec['method']=='structure' and spec['id'] not in self.structural_validations:
                    result=replace(result,status='unknown',confidence=0.,
                                   detail='Richiesti esempi positivi e negativi reali; '+result.detail)
                # Discoveries are based only on FRESH OCR samples, never on
                # cached structural/template results or predictions.
                if spec['method']=='ocr':
                    if self.discovery.observe(spec['id'],at=at,status=('ok' if result.status in ('present','absent') else result.status),
                        matched=result.status=='present',confidence=result.confidence,
                        text=result.text,label=spec.get('name') or spec['id']):
                        new_detections.append({'target':spec['id'],
                                               'label':spec.get('name') or spec['id'],'text':result.text})
                self.scanner_cache[spec['id']]=result
                self.scan_history[spec['id']].append({'at':at,'status':result.status,
                    'confidence':round(result.confidence,3),
                    'elapsed_ms':round(result.elapsed_ms,2),'text':result.text})
                try:
                    region=crop_roi(frame,spec['roi'])
                    ok,jpeg=cv2.imencode('.jpg',region,[cv2.IMWRITE_JPEG_QUALITY,70])
                    if ok:self.crop_history[spec['id']].append((at,jpeg.tobytes()))
                except ValueError:pass
            # Drop scanner cache from obsolete screenshots independently.
            specs_by_id={item['id']:item for item in self.scanners}
            recent={k:v for k,v in self.scanner_cache.items()
                    if k in specs_by_id and 0<=at-v.at<=result_ttl(specs_by_id[k])}
            if mismatch:recent={}
            decision=self.fusion.evaluate(recent,self.scanners,at)
            self.debug_snapshot={'status':'ok','frame_at':at,
               'scanners':{k:v.json() for k,v in recent.items()},
               'state':decision.json(), 'captured_size':[frame.shape[1],frame.shape[0]],
               'processing_ms':sum(x.elapsed_ms for x in recent.values()),
               'resources':self._resources(),
               'history':{k:list(v) for k,v in self.scan_history.items()},
               'detections':self.discovery.snapshot()}
            item={'status':'resolution_mismatch' if mismatch else 'ok','at':at,'readings':readings,
                  'expected':[self.selected['width'],self.selected['height']] if mismatch else None,
                  'actual':[frame.shape[1],frame.shape[0]],
                  'scanner_readings':self.debug_snapshot['scanners'],
                  'fusion':self.debug_snapshot['state'], 'new_detections':new_detections}
            try:
                if self.results.full():self.results.get_nowait()
                self.results.put_nowait(item)
            except queue.Empty:pass
