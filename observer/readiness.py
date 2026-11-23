"""Fast, local readiness audit for the teach -> review -> autonomous workflow.

Scores are *diagnostics*, not gameplay success probabilities. Cross-session
holdout deliberately avoids evaluating on training samples from that match.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .fast_policy import FastActionPolicy


@dataclass(frozen=True)
class Readiness:
    matches: int
    screenshots: int
    corrected_events: int
    action_examples: int
    action_types: int
    demonstrated: int
    manually_taught: int
    holdout_checked: int
    holdout_covered: int
    holdout_correct: int
    action_counts: dict[str, int]
    notes: tuple[str, ...]
    total_action_labels: int = 0
    excluded_actions: int = 0

    @property
    def trial_ready(self) -> bool:
        # This indicates only that a *limited local trial* is justified.
        return (self.matches >= 2 and self.action_examples >= 18
                and self.action_types >= 3 and self.holdout_covered >= 5
                and self.holdout_correct / max(1, self.holdout_covered) >= .65)

    def describe(self) -> str:
        lines = [
            'OYE · RIEPILOGO APPRENDIMENTO',
            '',
            f'Partite con azioni dimostrate: {self.matches}',
            f'Screenshot nell’archivio: {self.screenshots}',
            f'Eventi corretti o confermati: {self.corrected_events}',
            f'Azioni utilizzabili: {self.action_examples} ({self.action_types} tipi)',
            f'Azioni etichettate in archivio: {self.total_action_labels}',
            f'Azioni escluse (fuori partita/non confermate): {self.excluded_actions}',
            f'  Da dimostrazione: {self.demonstrated}',
            f'  Insegnate manualmente: {self.manually_taught}',
            'Azioni presenti: ' + (', '.join(f'{k}: {v}' for k, v in
                                    sorted(self.action_counts.items())) or 'nessuna'),
        ]
        if self.holdout_checked:
            lines.extend([
                '',
                'VERIFICA TRA PARTITE DIVERSE (senza usare gli esempi della partita testata)',
                f'Comandi valutati: {self.holdout_checked}',
                f'Comandi riconosciuti con sufficiente confidenza: {self.holdout_covered}',
                f'Comandi coincidenti, tra quelli riconosciuti: {self.holdout_correct}',
                'Queste percentuali NON misurano la probabilità di vincere.',
            ])
        lines += ['', 'Prima prova autonoma limitata: ' +
                    ('DATI SUFFICIENTI PER TENTARE' if self.trial_ready else 'SERVONO ALTRI ESEMPI')]
        lines.extend('· ' + note for note in self.notes)
        lines.extend(['', 'In partita la policy agisce solo con una somiglianza e un accordo sufficienti.',
                      'Quando non riconosce la scena resta ferma o usa il fallback Ollama, se configurato.'])
        return '\n'.join(lines)


def inspect_training(store, *, limit: int = 1200, max_evaluations: int = 90) -> Readiness:
    """Evaluate at most 90 samples, never blocks on VLM or trains a neural net."""
    stats = store.stats()
    diagnostics = store.action_diagnostics()
    samples = store.action_training_samples(limit)
    classes = Counter(action for _session, action, _feature, _source in samples)
    sessions = set(session for session, _action, _feature, _source in samples)
    sources = Counter(source for _session, _action, _feature, source in samples)
    checked = covered = correct = 0
    if len(sessions) >= 2 and len(classes) >= 2:
        # Deterministic subsample across matches; never validate on same match.
        selected = samples[::max(1, len(samples) // max_evaluations)][:max_evaluations]
        models = {}
        for session, action, feature, _source in selected:
            if session not in models:
                others = [(f, a) for s, a, f, _ in samples if s != session]
                policy = FastActionPolicy()
                policy.features = (np.ascontiguousarray(np.stack([f for f, _ in others]), dtype=np.float32)
                                   if others else np.empty((0, 1), dtype=np.float32))
                policy.labels = [a for _f, a in others]
                if others:
                    # Match the OCR-invariant features used by the actual policy.
                    from .fast_policy import _TEXT_START, _TEXT_END
                    policy.features[:, _TEXT_START:_TEXT_END] = 0
                    policy._squared_norms = np.einsum('ij,ij->i', policy.features, policy.features)
                models[session] = policy
            policy = models[session]
            result = policy.predict(feature, allow_shoot=True)
            checked += 1
            if result is not None:
                covered += 1
                correct += result.action.name == action
    notes = []
    if stats['images'] and diagnostics['total'] == 0:
        notes.append('Hai immagini ma ZERO COMANDI: attiva «apprendi dai miei comandi» prima della Fase 1 e verifica Fortnite in primo piano.')
    if stats.get('input_transitions'):
        notes.append(f"Cambi di input registrati: {stats['input_transitions']} (inclusi rilasci; non sono tutti esempi di training).")
    if diagnostics['outside_match']:
        notes.append(f'{diagnostics["outside_match"]} azioni non utilizzate: conferma «partita in corso» sui fotogrammi davvero in partita, poi assegna l’azione.')
    if len(sessions) < 2:
        notes.append('Registra dimostrazioni in almeno 2 partite, preferibilmente 3.')
    if len(samples) < 18 or len(classes) < 3:
        notes.append('Acquisisci più esempi di movimento, interazione e attesa/azioni diverse.')
    if stats['labeled'] == 0:
        notes.append('Rivedi la timeline e correggi le fasi: il contesto migliora le predizioni.')
    if checked and covered < 5:
        notes.append('Le situazioni nelle varie partite sono ancora troppo differenti: la policy spesso aspetterà.')
    elif covered and correct / covered < .65:
        notes.append('Gli esempi tra partite si contraddicono: rivedi le azioni nella timeline.')
    if not notes:
        notes.append('Puoi tentare una prima prova; sorveglia il comportamento e usa F9 per interrompere.')
    return Readiness(len(sessions), stats['images'], stats['labeled'], len(samples),
                     len(classes), sources['demo'], sources['manual'], checked,
                     covered, correct, dict(classes), tuple(notes), diagnostics['total'],
                     max(0, diagnostics['total'] - diagnostics['usable']))
