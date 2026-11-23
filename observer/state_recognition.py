"""Evidence fusion with explicit UNKNOWN, hysteresis, and audit trail.

Scores are normalized evidence support, *not* calibrated probabilities. There
is no inferred absence on OCR timeout, and numeric HUD values are irrelevant.
"""
from __future__ import annotations
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Mapping
import math
from .visual_scanners import ScannerResult, STATES

# Cues describe *presence* of persistent UI regions, not particular values.
HUD={'compass','minimap','health_shield','inventory','player_counter','elimination_counter'}
EVENTS={'lobby':'LOBBY','matchmaking':'MATCHMAKING','loading':'LOADING',
        'battle_bus':'BATTLE_BUS','skydiving':'SKYDIVING','gliding':'GLIDING',
        'elimination':'ELIMINATED','spectating':'SPECTATING',
        'return_to_lobby':'RETURNING_TO_LOBBY','reward_title':'RETURNING_TO_LOBBY',
        'riscatta':'RETURNING_TO_LOBBY'}
PRIORITY=('ELIMINATED','SPECTATING','RETURNING_TO_LOBBY','LOBBY','MATCHMAKING',
          'LOADING','BATTLE_BUS','SKYDIVING','GLIDING','IN_MATCH','LANDED')

@dataclass(frozen=True)
class StateDecision:
    state: str
    candidate: str
    confidence: float
    alternatives: dict
    reason: str
    transition: str
    evidence: tuple[str,...]
    timestamp: float
    def json(self):
        return {'state':self.state,'candidate':self.candidate,'confidence':round(self.confidence,3),
                'alternatives':{k:round(v,3) for k,v in self.alternatives.items()},
                'reason':self.reason,'transition':self.transition,'evidence':list(self.evidence),'at':self.timestamp}

class StateRecognitionEngine:
    def __init__(self,*,window=9,stable_frames=2,unknown_timeout=9.0,margin=.13):
        self.history=deque(maxlen=max(3,min(30,window)))
        self.state='UNKNOWN';self.candidate='UNKNOWN';self.frames=0
        self.last_confirmed=0.;self.last_transition='Nessuna'
        self.stable_frames=stable_frames;self.unknown_timeout=unknown_timeout;self.margin=margin
        self._latest_evidence={}
        self._last_input_at=float('-inf')
    def evaluate(self,results:Mapping[str,ScannerResult],specs:list[dict],at:float)->StateDecision:
        # An eight-minute gap between two screenshots is NOT one continuous
        # game state. Invalidate temporal memory even if a new frame arrives
        # immediately after the gap (the old no-frame path wasn't enough).
        expired_gap=(self._last_input_at != float('-inf') and
                     (at-self._last_input_at)>self.unknown_timeout)
        if expired_gap:
            previous=self.state
            self.state='UNKNOWN';self.candidate='UNKNOWN';self.frames=0
            self.history.clear();self._latest_evidence.clear()
            self.last_confirmed=0.
            if previous!='UNKNOWN':
                self.last_transition=previous+' → UNKNOWN (osservazioni scadute)'
        self._last_input_at=at
        fresh=any(r.at>self._latest_evidence.get(id_,float('-inf')) and
                  0 <= at-r.at <= max(2.2,float(next((s.get('interval',1.5)
                           for s in specs if s['id']==id_),1.5))*2.5)
                  for id_,r in results.items())
        if not fresh:
            # Missing/duplicated frames cannot indefinitely preserve a match.
            # No new evidence means UNKNOWN after a bounded grace period.
            transition = ''
            if self.state != 'UNKNOWN' and at-self.last_confirmed > self.unknown_timeout:
                previous=self.state
                self.state='UNKNOWN';self.candidate='UNKNOWN';self.frames=0
                transition=previous+' → UNKNOWN'
                self.last_transition=transition
            return StateDecision(self.state,'UNKNOWN',0.,{},
                                 'Nessun nuovo fotogramma valido; stato scaduto' if transition or expired_gap else
                                 'Nessun nuovo fotogramma; in attesa di conferme',
                                 transition or self.last_transition,(),at)
        for id_,r in results.items():
            self._latest_evidence[id_]=max(self._latest_evidence.get(id_,float('-inf')),r.at)
        config={x['id']:x for x in specs}
        support=defaultdict(float);oppose=defaultdict(float)
        contributors=[];hud=[];unknown=[]
        for id_,result in results.items():
            spec=config.get(id_)
            if spec is None or not spec.get('enabled',True):continue
            # A processed ROI from an old screenshot is not fresh enough.
            if at-result.at>max(2.2,float(spec.get('interval',1.5))*2.5):continue
            if result.status in ('error','unknown','skipped'):
                unknown.append(id_);continue
            weight=max(.1,min(3.,float(spec.get('weight',1.))))
            confidence=max(0,min(1,result.confidence))
            if result.status=='present':
                if id_ in HUD:
                    hud.append((id_,confidence,weight));continue
                state=spec.get('state') or EVENTS.get(id_,'UNKNOWN')
                if state in STATES and state!='UNKNOWN':
                    support[state]+=weight*confidence
                    contributors.append(id_)
            elif result.status=='absent':
                # Only high-confidence *explicit* absence of a discriminant
                # signal penalizes the claimed state. Nonmatch in OCR is weak;
                # don't propagate it as evidence for the opposite state.
                state=spec.get('state') or EVENTS.get(id_,'UNKNOWN')
                if state in EVENTS.values() and id_ not in HUD:
                    oppose[state]+=weight*.25*max(.3,1-confidence)
        # HUD region cues are correlated. Cap their *total* influence and
        # require at least two different types to label IN_MATCH.
        h=[min(1.2,conf*weight) for _,conf,weight in hud if conf>=.45]
        if len(h)>=2:
            support['IN_MATCH']+=min(2.35,sum(h))
            contributors.extend(x[0] for x in hud)
        # Results are evidence strengths, not success probabilities.
        scores={state:0. for state in STATES if state!='UNKNOWN'}
        for state in scores:
            positive=support[state]
            antagonists=max((support[k] for k in scores if k!=state),default=0.)
            if state=='IN_MATCH' and antagonists>0: antagonists*=1.35
            # 1.0 pseudo-evidence protects against one spurious hit.
            scores[state]=max(0.,positive/(positive+1.0+oppose[state]+antagonists*.52)) if positive else 0.
        # A clearly visible lobby/results screen has a stronger semantic
        # signal than a persistent minimap that may remain onscreen behind it.
        if support['LOBBY']>=.72:
            scores['IN_MATCH']*=.17
        if max(support['ELIMINATED'],support['SPECTATING'],support['RETURNING_TO_LOBBY'])>=.72:
            scores['IN_MATCH']*=.08
        ranked=sorted(scores,key=lambda x:(-scores[x],PRIORITY.index(x) if x in PRIORITY else 99))
        first=ranked[0] if ranked else 'UNKNOWN'
        best=scores.get(first,0.)
        second=scores.get(ranked[1],0.) if len(ranked)>1 else 0.
        # HUD-only requires at least two positive independent cues.
        enough = bool(best>=.47 and (best-second)>=self.margin)
        candidate=first if enough else 'UNKNOWN'
        self.history.append((at,candidate,best))
        # Temporary one-frame dropout preserves a recently confirmed state,
        # but never promotes an unrecognized state to a confirmed match.
        if candidate==self.candidate:self.frames+=1
        else:self.candidate=candidate;self.frames=1
        transition=''
        if candidate!='UNKNOWN' and candidate!=self.state:
            confirmed= sum(1 for t,c,s in self.history if c==candidate and at-t<=11.0 and s>=.47)
            fast= candidate in ('ELIMINATED','SPECTATING','RETURNING_TO_LOBBY') and best>=.70
            if confirmed>=self.stable_frames and (self.frames>=self.stable_frames or fast):
                before=self.state;self.state=candidate;self.last_confirmed=at
                transition=before+' → '+candidate;self.last_transition=transition
        elif candidate==self.state and candidate!='UNKNOWN':
            self.last_confirmed=at
        elif candidate=='UNKNOWN' and self.state!='UNKNOWN' and at-self.last_confirmed>self.unknown_timeout:
            before=self.state;self.state='UNKNOWN';transition=before+' → UNKNOWN';self.last_transition=transition
        # If signals contradict, explicitly withhold promotion rather than
        # choosing whichever scanner happened to run last.
        why=('Evidenze insufficienti o contraddittorie' if candidate=='UNKNOWN' else
             'Conferma '+str(self.frames)+'/'+str(self.stable_frames) if self.state!=candidate else
             'Stato confermato')
        if unknown:why+='; scanner incerti: '+', '.join(unknown[:4])
        return StateDecision(self.state,candidate,best,dict(sorted(scores.items(),key=lambda x:-x[1])[:5]),
                             why,transition or self.last_transition,tuple(contributors),at)
