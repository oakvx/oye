"""Shared bounded scanner scheduler for live capture and offline replay.

Uses the source frame's monotonic timestamp, not OCR completion time. Each
scanner has a separate deadline; stale results never become observations.
"""
from __future__ import annotations

STAGE_PRIORITY = {
    'await_play': ('lobby','reward_title','riscatta','return_to_lobby','matchmaking','loading'),
    'joining': ('loading','matchmaking','battle_bus','lobby','reward_title','elimination'),
    'post_lobby': ('reward_title','riscatta','lobby','return_to_lobby'),
    'jumping': ('elimination','spectating','return_to_lobby','skydiving','gliding'),
    'exploring': ('elimination','spectating','return_to_lobby','reward_title','lobby'),
}

class ScannerScheduler:
    def __init__(self,*,max_visual=4,max_ocr=1):
        self.max_visual=max(1,int(max_visual))
        self.max_ocr=max(0,int(max_ocr))
        self.last_run={}
        self.started_at=None

    def reset(self):
        self.last_run.clear()
        self.started_at=None

    def pick(self, specs, at, stage='', *, allow_ocr=True, excluded=()):
        at=float(at)
        if self.started_at is None:self.started_at=at
        if at<self.started_at:
            self.reset();self.started_at=at
        excluded=set(excluded)
        priority=STAGE_PRIORITY.get(stage,('reward_title','riscatta','elimination','spectating',
                                            'return_to_lobby','lobby','loading'))
        candidates=[]
        for spec in specs:
            identifier=spec['id']
            if not spec.get('enabled') or identifier in excluded:continue
            method=spec['method']
            if method=='ocr' and not allow_ocr:continue
            interval=max(.25,float(spec.get('interval',1.5)))
            last=self.last_run.get(identifier)
            if last is not None and at-last<interval:continue
            # Overdue age grows even for a scanner never selected, so critical
            # scanners are prioritized without permanently starving the others.
            age=at-(last if last is not None else self.started_at-interval)
            urgency=age/interval
            bonus=1.4 if identifier in priority[:3] else .8 if identifier in priority else 0.
            candidates.append((urgency+bonus,identifier,spec))
        candidates.sort(key=lambda x:(-x[0],x[1]))
        selected=[];visual=ocr=0
        for _,identifier,spec in candidates:
            if spec['method']=='ocr':
                if ocr>=self.max_ocr:continue
                ocr+=1
            else:
                if visual>=self.max_visual:continue
                visual+=1
            selected.append(spec)
            self.last_run[identifier]=at
        return selected


def result_ttl(spec):
    """One cache lifetime shared by worker, fusion and replay."""
    return max(2.0,min(7.0,float(spec.get('interval',1.5))*2.5))
