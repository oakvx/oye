"""Oye 40: independently configured ROI detectors on one immutable captured frame.

All output confidence scores are *diagnostic scores*, NOT calibrated probabilities.
No detector authorizes gameplay input: the existing Runner/SingleMatch owns that.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Callable
import math
import re
import time
import cv2
import numpy as np
from .hud import outline_edges, peripheral_mask
from .teach_ocr import normalize, _configure_tesseract
from .menu_text import fragment_score

METHODS={'ocr','structure','template','color','features','model'}
STATES=('UNKNOWN','LOBBY','MATCHMAKING','LOADING','BATTLE_BUS','SKYDIVING','GLIDING',
        'LANDED','IN_MATCH','ELIMINATED','SPECTATING','RETURNING_TO_LOBBY')

# A scanner is only a hypothesis until it has been taught/tested on actual screenshots.
# ROI presets refer to the 1920x1080 borderless client area; normalized coordinates
# permit scaling without touching user data.
PRESETS={
 'compass':('Bussola','structure',[.30,.122,.70,.204],'IN_MATCH',''),
 'minimap':('Minimappa','structure',[.858,.145,.993,.333],'IN_MATCH',''),
 'health_shield':('Vita e scudo','structure',[.12,.87,.37,.991],'IN_MATCH',''),
 'inventory':('Inventario','structure',[.71,.785,.995,.99],'IN_MATCH',''),
 'player_counter':('Giocatori rimasti','ocr',[.85,.165,.925,.260],'IN_MATCH',r'\b\d{1,3}\b'),
 'elimination_counter':('Eliminazioni','ocr',[.88,.22,.96,.29],'IN_MATCH',r'\b\d{1,3}\b'),
 'lobby':('Lobby · Gioca','ocr',[.68,.60,.985,.94],'LOBBY','GIOCA|PLAY'),
 'matchmaking':('Ricerca partita','ocr',[.65,.68,.99,.98],'MATCHMAKING','ANNULLA|MATCHMAKING|RICERCA'),
 'loading':('Caricamento','ocr',[.25,.63,.75,.96],'LOADING','CARICAMENTO|LOADING'),
 'battle_bus':('Bus della battaglia','ocr',[.24,.04,.78,.28],'BATTLE_BUS','SALTA|BATTLE BUS|BUS DELLA BATTAGLIA'),
 'skydiving':('Caduta','ocr',[.29,.37,.74,.73],'SKYDIVING','APRI DELTAPLANO|DEPLOY GLIDER'),
 'gliding':('Deltaplano','ocr',[.28,.35,.73,.72],'GLIDING','CHIUDI DELTAPLANO|SKYDIVE'),
 'elimination':('Eliminato','ocr',[.20,.10,.81,.51],'ELIMINATED','ELIMINATO|ELIMINATED'),
 'spectating':('Spettatore','ocr',[.25,.05,.77,.28],'SPECTATING','SPETTATORE|SPECTATING'),
 'return_to_lobby':('Ritorna alla lobby','ocr',[.55,.60,.99,.99],'RETURNING_TO_LOBBY','RITORNA ALLA LOBBY|TORNA ALLA LOBBY|RETURN TO LOBBY'),
 'reward_title':('Ricompense ottenute','ocr',[.17,.06,.84,.35],'RETURNING_TO_LOBBY','RICOMPENSA OTTENUTA|RICOMPENSE OTTENUTE'),
 'riscatta':('Riscatta','ocr',[.54,.67,.99,.98],'RETURNING_TO_LOBBY','RISCATTA|CLAIM'),
}

@dataclass(frozen=True)
class ScannerResult:
    scanner_id: str
    at: float
    checked_at: float
    status: str  # present, absent, unknown, error, skipped
    confidence: float
    method: str
    roi: tuple[float,float,float,float]
    elapsed_ms: float
    text: str = ''
    detail: str = ''
    payload: dict | None = None
    def json(self):
        return asdict(self)


def valid_roi(roi):
    if not isinstance(roi,(list,tuple)) or len(roi)!=4 or any(type(a) not in (int,float) or not math.isfinite(a) for a in roi):
        raise ValueError('ROI non valida')
    x1,y1,x2,y2=map(float,roi)
    if not (0<=x1<x2<=1 and 0<=y1<y2<=1 and x2-x1>=.01 and y2-y1>=.01):
        raise ValueError('ROI normalizzata fuori schermo')
    return [x1,y1,x2,y2]


def crop_roi(frame, roi):
    h,w=frame.shape[:2]
    x1,y1,x2,y2=valid_roi(roi)
    a,b,c,d=max(0,round(w*x1)),max(0,round(h*y1)),min(w,round(w*x2)),min(h,round(h*y2))
    if c-a<8 or d-b<8:raise ValueError('ROI troppo piccola')
    return frame[b:d,a:c]


def _gray(frame):
    if frame.ndim==2:return frame
    return cv2.cvtColor(frame,cv2.COLOR_BGRA2GRAY if frame.shape[-1]==4 else cv2.COLOR_BGR2GRAY)


def _structural(crop, kind, options):
    """Conservative geometry-based detector, not a generic edge-density test.

    Random texture MUST NOT be a positive signal. These heuristics are only
    candidates; deployment still requires a calibrated ROI and actual examples.
    All uncertain cases are UNKNOWN rather than negative evidence.
    """
    gray=_gray(crop)
    h,w=gray.shape
    if h<16 or w<16 or float(np.std(gray))<8:
        return 'unknown',0.,'ROI uniforme o troppo piccola',{}
    edge=cv2.Canny(gray,65,160)
    density=float(np.mean(edge>0))
    threshold=float(options.get('threshold',.70))
    def result(score,valid,reason,payload):
        score=float(np.clip(score,0,1))
        return ('present' if valid and score>=threshold else 'unknown'),score,reason,payload
    if kind=='compass':
        # Compass has multiple *long, thin, almost vertical* tick marks.
        # Strong vertical-gradient response alone is NOT enough: random pixels
        # generate strong gradients in every orientation.
        size=max(6,round(h*.10))
        upright=cv2.morphologyEx(edge,cv2.MORPH_OPEN,
                                 cv2.getStructuringElement(cv2.MORPH_RECT,(1,size)))
        xs=np.where(np.count_nonzero(upright,axis=0)>=size)[0]
        groups=[]
        for x in xs:
            if not groups or x-groups[-1][-1]>3:groups.append([int(x)])
            else:groups[-1].append(int(x))
        ticks=len(groups)
        plausible=density<.24 and ticks>=3 and ticks<=max(8,w//3)
        score=min(1.,ticks/10) if plausible else 0.
        return result(score,plausible,'tacche verticali coerenti: %d; densità %.3f'%(ticks,density),
                      {'ticks':ticks,'edges':density})
    if kind=='minimap':
        # Look for elongated aligned frame edges near ROI boundaries.
        # A colorful/textured image alone is NEVER a minimap signal.
        lines=cv2.HoughLinesP(edge,1,np.pi/180,threshold=max(18,int(min(h,w)*.20)),
                   minLineLength=max(12,int(min(h,w)*.63)),maxLineGap=4)
        sides=set()
        for line in lines if lines is not None else []:
            x1,y1,x2,y2=map(int,np.asarray(line).reshape(-1));dx=abs(x2-x1);dy=abs(y2-y1)
            if dx>=w*.60 and dy<=max(3,h*.025):
                y=(y1+y2)/2
                if y<h*.15:sides.add('top')
                if y>h*.85:sides.add('bottom')
            if dy>=h*.62 and dx<=max(3,w*.025):
                x=(x1+x2)/2
                if x<w*.15:sides.add('left')
                if x>w*.85:sides.add('right')
        # A real straight border often survives as a near-uniform narrow line,
        # even when Hough cannot trace it through a highly textured minimap.
        def uniform_border(bands, baseline):
            return any(float(np.std(x))<19 and
                       abs(float(np.mean(x))-float(np.mean(baseline)))>28
                       for x in bands)
        n=max(2,min(6,round(min(h,w)*.018)))
        if uniform_border([gray[y,:] for y in range(1,min(n,h//6))], gray[min(h-1,n*3),:]):sides.add('top')
        if uniform_border([gray[h-1-y,:] for y in range(1,min(n,h//6))], gray[max(0,h-1-n*3),:]):sides.add('bottom')
        if uniform_border([gray[:,x] for x in range(1,min(n,w//6))], gray[:,min(w-1,n*3)]):sides.add('left')
        if uniform_border([gray[:,w-1-x] for x in range(1,min(n,w//6))], gray[:,max(0,w-1-n*3)]):sides.add('right')
        aligned=len(sides)
        plausible=aligned>=3
        score=aligned/4 if plausible else 0.
        return result(score,plausible,'cornice allineata %d/4; texture non usata come prova'%aligned,
                      {'border_sides':sorted(sides),'edges':density})
    if kind=='health_shield':
        bgr=cv2.cvtColor(crop,cv2.COLOR_BGRA2BGR) if crop.ndim==3 and crop.shape[2]==4 else crop
        if bgr.ndim!=3:return 'unknown',0.,'Colore HUD non disponibile',{}
        hsv=cv2.cvtColor(bgr,cv2.COLOR_BGR2HSV)
        green=cv2.inRange(hsv,(32,55,45),(91,255,255))>0
        blue=cv2.inRange(hsv,(92,55,45),(135,255,255))>0
        colored=(green|blue).astype(np.uint8)*255
        min_fraction=float(options.get('min_bar_fraction',.18))
        # A horizontal opening rejects isolated saturated pixels / TV noise.
        span=max(12,round(w*max(.20,min_fraction)))
        horizontal=cv2.morphologyEx(colored,cv2.MORPH_OPEN,
                                    cv2.getStructuringElement(cv2.MORPH_RECT,(span,1)))
        rows=(horizontal>0).mean(axis=1)>=max(.16,min_fraction*.70)
        runs=[];beg=None
        for i,flag in enumerate(list(rows)+[False]):
            if flag and beg is None:beg=i
            if not flag and beg is not None:runs.append(i-beg);beg=None
        band=max(runs,default=0)
        colored_fraction=float(np.mean(rows))
        plausible=band>=3 and band<=max(5,h*.24) and colored_fraction<.40
        score=min(1.,band/max(5,h*.07)) if plausible else 0.
        return result(score,plausible,'banda orizzontale coerente %d px'%band,
                      {'longest_bar_rows':band,'colored_rows':int(np.count_nonzero(rows))})
    if kind=='inventory':
        # Slot outlines repeat at approximately fixed distances. Random image
        # edges and object textures should not be treated as inventory slots.
        lines=cv2.HoughLinesP(edge,1,np.pi/180,threshold=22,
                  minLineLength=max(12,int(min(h,w)*.25)),maxLineGap=3)
        vertical=[];horizontal=[]
        for line in lines if lines is not None else []:
            x1,y1,x2,y2=map(int,np.asarray(line).reshape(-1));dx=abs(x2-x1);dy=abs(y2-y1)
            if dy>h*.25 and dx<=max(3,w*.012):vertical.append((x1+x2)/2)
            if dx>w*.105 and dy<=max(3,h*.012):horizontal.append((y1+y2)/2)
        def clusters(points,gap):
            positions=[]
            for value in sorted(points):
                if not positions or value-positions[-1][-1]>gap:positions.append([value])
                else:positions[-1].append(value)
            return [sum(c)/len(c) for c in positions]
        xs=clusters(vertical,max(3,w*.018))
        ys=clusters(horizontal,max(3,h*.025))
        plausible=density<.28 and len(xs)>=5 and len(ys)>=2
        score=min(1.,len(xs)/7*.6+len(ys)/3*.4) if plausible else 0.
        # Slot occupancy is a diagnostic only; never required as state cue.
        slots=np.array_split(gray,5,axis=1)
        interiors=[tile[int(h*.38):int(h*.58),int(tile.shape[1]*.22):int(tile.shape[1]*.78)] for tile in slots]
        occupied=sum(float(np.std(tile))>18 for tile in interiors if tile.size)
        return result(score,plausible,'geometria slot V/H: %d/%d'%(len(xs),len(ys)),
                      {'slots_occupied_estimate':int(occupied),'slots_empty_estimate':5-int(occupied),
                       'lines_vertical':len(xs),'lines_horizontal':len(ys),'edges':density})
    # An unrecognized structure detector must fail closed. A generic amount
    # of edges does NOT identify a user-defined UI object.
    return 'unknown',0.,'Scanner strutturale generico: serve un riferimento specifico',{'edges':density}


def scan(frame: np.ndarray, spec:dict, *, at:float|None=None,
         ocr_reader:Callable|None=None, reference=None, model=None)->ScannerResult:
    started=time.monotonic(); checked=started
    id_=str(spec['id']);method=spec.get('method','ocr');roi=tuple(valid_roi(spec['roi']))
    def ret(status,confidence=0.,detail='',text='',payload=None):
        return ScannerResult(id_,at if at is not None else started,checked,status,
                             float(np.clip(confidence,0,1)),method,roi,
                             (time.monotonic()-started)*1000,text,detail,payload)
    if not spec.get('enabled',True):return ret('skipped',detail='Disabilitato')
    if method not in METHODS:return ret('error',detail='Metodo non supportato')
    try:
        crop=crop_roi(frame,roi)
        options=spec.get('options') or {}
        resolution=options.get('resolution')
        if resolution and (frame.shape[1],frame.shape[0]) != tuple(resolution):
            return ret('unknown',detail='Il riferimento è calibrato per %sx%s, schermata attuale %sx%s'
                       %(resolution[0],resolution[1],frame.shape[1],frame.shape[0]))
        threshold=float(spec.get('threshold',.76))
        if method=='ocr':
            if ocr_reader is None:
                gray=_gray(crop)
                text=_configure_tesseract().image_to_string(gray,lang='eng',config='--psm 6',timeout=1.25)
            else:text=ocr_reader(crop)
            text=normalize(text)[:200]
            phrases=str(spec.get('expected_text','')).split('|')
            phrases=[t.strip() for t in phrases if t.strip()]
            if not phrases:return ret('unknown',detail='Imposta una parola attesa',text=text)
            if options.get('numeric'):
                pattern=spec['expected_text'] or r'\b\d{1,3}\b'
                hits=re.findall(pattern,text)
                return ret('present' if hits else 'unknown',.98 if hits else 0.,'numero letto' if hits else 'numero non affidabile',text,{'value':int(hits[0]) if hits else None})
            score=fragment_score(text,phrases)
            # Blank OCR is UNKNOWN, not confirmation of absence.
            status=('present' if score>=threshold else 'absent' if text and score<.27 else 'unknown')
            return ret(status,score,'testo OCR localizzato',text)
        if method=='structure':
            status,score,detail,payload=_structural(crop,id_,{**options,'threshold':threshold})
            return ret(status,score,detail,payload=payload)
        if method=='color':
            hsv=cv2.cvtColor(crop,cv2.COLOR_BGRA2BGR if crop.shape[-1]==4 else cv2.COLOR_BGRA2BGR)
            hsv=cv2.cvtColor(hsv,cv2.COLOR_BGR2HSV)
            low=tuple(options.get('hsv_min',[0,90,90]));high=tuple(options.get('hsv_max',[179,255,255]))
            coverage=float(np.mean(cv2.inRange(hsv,np.array(low,dtype=np.uint8),np.array(high,dtype=np.uint8))>0))
            return ret('present' if coverage>=threshold else 'absent',min(1,coverage/max(.005,threshold)),f'area colore {coverage:.3f}',payload={'coverage':coverage})
        if method in ('template','features'):
            if reference is None:return ret('unknown',detail='Esempio di riferimento mancante')
            ref=_gray(reference);img=_gray(crop)
            # OpenCV's normalized correlation can return 1 for a featureless
            # ROI; blank/black/loading captures must NOT confirm a real HUD.
            if float(np.std(img))<6:
                return ret('unknown',detail='ROI troppo uniforme per il confronto')
            if method=='template':
                if ref.shape[0]>img.shape[0] or ref.shape[1]>img.shape[1]:return ret('unknown',detail='Template maggiore della ROI')
                if np.std(ref)<7:return ret('unknown',detail='Template uniforme')
                corr=cv2.matchTemplate(img,ref,cv2.TM_CCOEFF_NORMED)
                score=float(cv2.minMaxLoc(corr)[1])
            else:
                orb=cv2.ORB_create(nfeatures=360)
                kp1,d1=orb.detectAndCompute(ref,None)
                kp2,d2=orb.detectAndCompute(img,None)
                if d1 is None or d2 is None or min(len(kp1),len(kp2))<8:
                    return ret('unknown',detail='Feature insufficienti')
                good=[m for m in cv2.BFMatcher(cv2.NORM_HAMMING).match(d1,d2) if m.distance<55]
                score=min(1.,len(good)/max(8,int(len(kp1)*.35)))
            # An explicit ABSENT result is allowed only for an anchored,
            # calibrated template with verified negative examples. Most
            # template misses remain UNKNOWN, never a signal to start jumping.
            opts=options
            if (method=='template' and opts.get('explicit_absence') is True
                and score<=float(opts.get('absence_threshold',-1))):
                return ret('absent',1.-score,'template non presente nella ROI (riferimento calibrato)')
            return ret('present' if score>=threshold else 'unknown',score,method+' '+str(round(score,3)))
        if model is None:return ret('unknown',detail='Modello opzionale non disponibile')
        prediction=model(crop,spec)
        if not isinstance(prediction,(tuple,list)) or len(prediction)!=2:raise ValueError('Risposta modello non valida')
        present,confidence=prediction
        return ret('present' if present and confidence>=threshold else 'unknown',confidence,'modello opzionale')
    except Exception as exc:
        return ret('error',detail=type(exc).__name__+': '+str(exc)[:130])
