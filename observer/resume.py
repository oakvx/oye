"""Crash-safe checkpoint and read-only screen reconciliation after restart.

Saved files are never a source of input authority. A restarted process must
observe THREE distinct fresh and authorized frames before restoring a phase.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time

VERSION = 1
MAX_AGE_SECONDS = 48 * 3600
SAFE_STAGES = {'await_play', 'joining', 'jumping', 'exploring', 'ending', 'post_lobby', 'claim_rewards'}


def load_checkpoint(path: Path, *, now: float | None = None) -> dict | None:
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        at = time.time() if now is None else now
        if not isinstance(value, dict) or value.get('version') != VERSION or value.get('mode') != 'auto_cycle':
            return None
        if type(value.get('matches_completed')) is not int or not 0 <= value['matches_completed'] <= 10_000_000:
            return None
        if value.get('stage') not in SAFE_STAGES or type(value.get('active')) is not bool:
            return None
        updated = value.get('updated_at')
        if not isinstance(updated, (int, float)) or not 0 <= at - updated <= MAX_AGE_SECONDS:
            return None
        return value
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def save_checkpoint(path: Path, *, stage: str, matches_completed: int, active: bool,
                    now: float | None = None) -> None:
    if stage not in SAFE_STAGES:
        # 'done' is never an active checkpoint.
        stage = 'await_play'
    if type(matches_completed) is not int or matches_completed < 0:
        raise ValueError('Invalid checkpoint counter')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {'version': VERSION, 'mode': 'auto_cycle', 'stage': stage,
             'matches_completed': matches_completed, 'active': bool(active),
             'updated_at': time.time() if now is None else now}
    temporary = path.with_name(path.name + '.tmp')
    try:
        temporary.write_text(json.dumps(value, separators=(',', ':')), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass
class ResumeGate:
    """After a crash, prevent all input until the *current* scene is confirmed."""
    active: bool = False
    streak: int = 0
    candidate: str = ''
    last_frame: float = -1.0
    detail: str = ''
    previous_stage: str = ''

    def reconcile(self, controller, *, frame_at: float, now: float, scene: str,
                  menu: str | None, authorized: bool, locked: bool, spectator: bool,
                  bus_confirmed: bool = False, reward_overlay: bool = False) -> bool:
        if not self.active:
            return True
        if frame_at <= self.last_frame:
            self.detail = 'Ripristino: fotogramma già elaborato'
            return False
        self.last_frame = frame_at
        if locked or spectator:
            target = ''
        elif reward_overlay and scene in ('results','lobby','unknown'):
            target = 'claim_rewards'
        elif scene == 'match' and authorized:
            target = 'exploring'
        elif bus_confirmed and not spectator and not locked:
            target = 'joining'
        elif scene == 'results' and menu in {'riscatta','continua','torna_lobby'}:
            target = 'ending'
        elif scene == 'lobby' and menu == 'gioca':
            # Returning from results, or after an interrupted reward stage,
            # always observe the reward window before authorizing PLAY.
            target = ('post_lobby' if self.previous_stage in
                      ('ending','post_lobby','claim_rewards') else 'await_play')
        else:
            target = ''
        if not target:
            self.candidate, self.streak = '', 0
            self.detail = 'Ripristino: osservo senza inviare input; aspetto HUD/lobby/risultati verificati'
            return False
        if target == self.candidate:
            self.streak += 1
        else:
            self.candidate, self.streak = target, 1
        if self.streak < 3:
            self.detail = f'Ripristino: conferma {self.candidate} ({self.streak}/3 fotogrammi)'
            return False
        controller.stage = target
        controller.has_entered = target in ('exploring', 'ending')
        controller.last_tick = now if target == 'exploring' else None
        controller.last_frame_at = frame_at
        controller.play_disappeared = target != 'await_play'
        if target == 'ending':
            controller.results_at = now
            controller.stable_results = 3
        if target == 'post_lobby':
            controller.post_lobby_at = now
            controller.stable_lobby = 0
            if self.previous_stage == 'ending':
                controller.matches_completed += 1
        if target == 'claim_rewards':
            controller.reward_streak = 2  # restored only after three fresh overlay frames
            controller.stable_lobby = 0
        if target == 'await_play':
            if self.previous_stage == 'ending':
                # A previously verified results screen followed by a newly
                # confirmed lobby finishes the interrupted cycle once.
                controller.matches_completed += 1
            controller.next_play_at = now + controller.lobby_cooldown_seconds
        self.active = False
        self.detail = f'Ripristino verificato dalle schermate attuali: {target}'
        return True
