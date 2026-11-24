"""Ignorante-only authored commands, distinct from demonstrated/trained actions.

Runner owns fresh capture, focus, pause and menu checks. This policy supplies
short ground commands only; it never writes examples or infers human keypresses.
"""
from dataclasses import replace
from .models import Action, Match
from .quick_routine import QuickRoutine


class CommandPolicy:
    def __init__(self, *, seed=None, routine=None):
        self.routine = routine if routine is not None else QuickRoutine(seed=seed)
        self.last_interaction = float('-inf')

    def choose(self, *, scene: str, authorized: bool, now: float,
               phase=None, blocked=False, interaction: Match | None = None,
               harvest=True) -> Action:
        if not authorized or scene != 'match' or phase is not None or blocked:
            self.routine.reset()
            return Action('wait', reason='Comandi preimpostati: attendo partita a terra confermata')
        if (interaction is not None and interaction.name == 'interazione'
                and interaction.score >= .89 and now-self.last_interaction >= 3):
            self.last_interaction = now
            return Action('interact', .1, reason='Comandi preimpostati: interagisco con il prompt visibile')
        action = self.routine.next_action(harvest=harvest)
        return replace(action, reason='Comandi preimpostati: movimento, salti e raccolta; non imitazione appresa')
