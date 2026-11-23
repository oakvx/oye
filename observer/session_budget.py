"""Operational accounting windows; never force a new match on an accounting rollover.

The LIVE safety controls (Stop, focus/scene gates, and abnormal errors) remain
independent of these bookkeeping limits.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetDecision:
    kind: str  # ok | stop | rollover
    reason: str = ''


@dataclass
class SessionBudget:
    max_seconds: float
    max_actions: int
    repeat: bool
    started_at: float
    rollovers: int = 0

    def check(self, now: float, actions: int) -> BudgetDecision:
        if now - self.started_at >= self.max_seconds:
            reason = 'Durata del blocco operativo raggiunta'
        elif actions >= self.max_actions:
            reason = 'Numero di azioni del blocco operativo raggiunto'
        else:
            return BudgetDecision('ok')
        if not self.repeat:
            return BudgetDecision('stop', reason)
        # This is an accounting/log window reset, not a Runner/process restart:
        # preserve the current match state, HUD evidence and pending actions.
        self.started_at = now
        self.rollovers += 1
        return BudgetDecision('rollover', reason)
