"""Replay visually labelled local captures. No driver or game process is used."""
from dataclasses import replace
from pathlib import Path
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from observer.ignorante import IgnoranteController
from observer.config import load, save
from observer.ignorante_ocr import match_frame, read_cycle_labels
from observer.models import Match
from observer.quick_routine import QuickRoutine
from observer.phase_ocr import PhaseOCRResult
from observer.reward_ocr import RewardCandidate, is_reward_screen, ocr_available
from observer.runner import Runner
from observer.vision import Reading
from observer.windows import Window


# Labels describe the visible controls. The archive's classifier is intentionally
# excluded; the elimination frame and a playable frame are replayed as spectator.
CASES = {
    'play': ('1ed9b77558884acb9b782fd947925125', {'gioca'}),
    'play_2': ('98e7abbdabb747d69f27931212decec0', {'gioca'}),
    'queue': ('2d0fc05e12234ae7b53a4d10f6383956', {'annulla'}),
    'queue_2': ('d07f09517e9345c2b84e2b5d6600ecf0', {'annulla'}),
    'queue_3': ('13ce25b4eeac41fab03457e1bbbeca33', {'annulla'}),
    'air': ('12b2092e2c9b44c4af03c91f4095ec43', set()),
    'air_2': ('e2cf4e968bc042818b09c6d46ba1e979', set()),
    'air_3': ('072fdaa12c2640deb6ed1dbda69ae5d9', set()),
    'warmup': ('6a41c2b8d6a049c6a4928e648198dabd', set()),
    'warmup_2': ('bfed20152cd6475f85dd3d51b895fe84', set()),
    'elimination': ('488a28a60291445c930eefce26187b68', {'posizione_finale'}),
    'results': ('fd6f92489a594dfa8b046c1ee903e103', {'posizione_finale', 'gioca_di_nuovo', 'torna_lobby'}),
    'results_2': ('889b52b5831546c98c323b1e49e10d55', {'posizione_finale', 'gioca_di_nuovo', 'torna_lobby'}),
    'results_3': ('1bbb4f02d4704c3985aaa0a7c0a81d83', {'posizione_finale', 'gioca_di_nuovo', 'torna_lobby'}),
    'results_4': ('3ddb3f56195748c8bf89ba62902f0351', {'posizione_finale', 'gioca_di_nuovo', 'torna_lobby'}),
}


@pytest.fixture(scope='module')
def captures():
    root = Path(__file__).resolve().parents[1]
    folders = (root/'learning'/'frames', root/'frames', root.parent/'frames')
    folder = next((p for p in folders if (p/(CASES['play'][0]+'.jpg')).is_file()), None)
    if folder is None or not ocr_available():
        pytest.skip('Richiede i fotogrammi locali di regressione e Tesseract')
    return {name: cv2.imread(str(folder/(filename+'.jpg'))) for name, (filename, _) in CASES.items()}


def test_three_match_replay_using_real_ocr(captures):
    scanned = {}
    for name, pixels in captures.items():
        assert pixels is not None, name
        scanned[name] = read_cycle_labels(pixels, 0)
        assert set(scanned[name].matches) == CASES[name][1], name
    assert scanned['air'].phase_hint == 'skydiving'
    assert not is_reward_screen('DA RISCATTARE: 14 · OTTIENI IL PASS BATTAGLIA')
    assert not read_cycle_labels(np.zeros_like(captures['play']), 0).matches
    assert read_cycle_labels(np.zeros_like(captures['play']), 0).status == 'invalid_capture'

    cycle = IgnoranteController(0)
    routine = QuickRoutine(seed=2)
    commands = []

    def show(name, at, scene='unknown', *, playable=False):
        best = {'hud_in_partita': Match('hud_in_partita', .98, (200, 980))} if playable else {}
        reading = Reading(scene, best)
        offer = cycle.observe(match_frame(replace(scanned[name], at=at), reading), now=at)
        if offer.name:
            commands.append(offer.name)
            cycle.on_clicked(offer.name, at)
        if offer.move:
            commands.append(routine.next_action(harvest=True).name)
        return offer

    assert show('play', 1).name is None
    assert show('play_2', 2).name == 'gioca'
    for name, at in (('queue', 3), ('queue_2', 4), ('queue_3', 25)):
        assert show(name, at).action.name == 'wait'
    assert not show('air', 26).move
    assert not show('air_2', 26.5).move
    assert show('air_3', 27, 'spectator').move
    assert not show('elimination', 28, 'spectator', playable=True).move
    assert cycle.matches_completed == 0
    assert show('results', 29, 'spectator').name is None
    assert show('results_2', 30, 'spectator').name == 'gioca_di_nuovo'
    for start in (31, 35):
        assert not show('warmup', start, 'spectator', playable=True).move
        assert show('warmup_2', start+1, 'spectator', playable=True).move
        assert not show('results_3', start+2, 'spectator').move
        assert show('results_4', start+3, 'spectator').name == 'gioca_di_nuovo'
    assert cycle.matches_completed == 3
    assert commands.count('gioca_di_nuovo') == 3
    assert commands.count('gioca') == 1
    assert 'annulla' not in commands


@pytest.mark.parametrize('entry', ['automatic', 'manual_queue', 'in_match'])
def test_runner_dispatches_verified_targets_and_rechecks_after_pause(captures, tmp_path, monkeypatch, entry):
    import observer.runner as runtime

    config = tmp_path / 'config.json'
    cfg = load(config)
    cfg.update(async_capture_enabled=False, learning_enabled=False, audio_enabled=False)
    save(config, cfg)
    runner = Runner(root=tmp_path, live=True, single_match=True, repeat_matches=True, quick_start=True)
    window = Window('FortniteClient-Win64-Shipping.exe', 50, 60, 1920, 1080)
    prefix = (['play', 'play_2'] * 6 if entry == 'automatic' else [])
    if entry != 'in_match':
        prefix += ['queue', 'queue_2', 'queue_3']
    sequence = iter(prefix + ['air', 'air_2', 'air_3'] * 3
                    + ['elimination', 'results', 'results_2'])
    clock = 0.0
    current = ''
    commands = []
    paused_at = None
    released_while_paused = False
    scanned = {name: read_cycle_labels(pixels, 0) for name, pixels in captures.items()}

    def snapshot(_):
        nonlocal clock, current
        clock += .6
        current = next(sequence, '')
        if not current:
            runner.stop_event.set()
            raise RuntimeError('fine del replay')
        return captures[current]

    def wait(seconds):
        if runner.pause_event.is_set() and seconds == .15:
            runner.pause_event.clear()
        return runner.stop_event.is_set()

    class FakeDriver:
        def __init__(self, **kwargs):
            pass

        def run(self, action):
            nonlocal paused_at
            commands.append((current, clock, action))
            if action.name == 'jump' and paused_at is None:
                paused_at = clock
                runner.pause_event.set()
            return True

        def release(self):
            nonlocal released_while_paused
            released_while_paused |= runner.pause_event.is_set()

    class Worker:
        def __init__(self, analyze):
            self.analyze = analyze
            self.result = None

        def start(self):
            pass

        def stop(self, **kwargs):
            pass

        def submit(self, frame, at):
            self.result = self.analyze(at)
            return True

        def take(self):
            result, self.result = self.result, None
            return result

    class Rewards(Worker):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda at: RewardCandidate(
                at, None, status='ok' if entry == 'automatic' else 'OCR unavailable'))
            self.previous = None

        def submit(self, frame, left, top, at, stage):
            return super().submit(frame, at)

        def take(self):
            # The asynchronous reader answers after the capture that submitted it.
            result, self.previous = self.previous, super().take()
            return result

    class NoRegions(Worker):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda at: None)
            self.discovery = SimpleNamespace(observe=lambda *args, **kwargs: False)

        def submit(self, frame, at, stage):
            return False

    monkeypatch.setattr(runtime, 'time', SimpleNamespace(monotonic=lambda: clock,
                                                        perf_counter=time.perf_counter, time=time.time))
    monkeypatch.setattr(runtime, 'foreground_window', lambda _: window)
    monkeypatch.setattr(runtime, 'snapshot', snapshot)
    monkeypatch.setattr(runtime, 'Driver', FakeDriver)
    monkeypatch.setattr(runtime, 'RewardOCRWorker', Rewards)
    monkeypatch.setattr(runtime, 'ScreenRegionOCRWorker', NoRegions)
    monkeypatch.setattr(runtime, 'PhaseOCRWorker', lambda _: Worker(lambda at: PhaseOCRResult(at, 'ok')))
    monkeypatch.setattr(runtime, 'cycle_ocr_worker', lambda _: Worker(lambda at: replace(scanned[current], at=at)))
    monkeypatch.setattr(runner.stop_event, 'wait', wait)
    monkeypatch.setattr(runner.vision, 'recognize', lambda *args:
        Reading('match', {}) if entry == 'in_match' and current.startswith('air')
        else Reading('spectator', {}))
    runner._loop()
    events = list(runner.messages.queue)
    assert not [event for event in events if event['event'] == 'error']
    clicks = [(name, action.point) for name, _, action in commands if action.name == 'menu_click']
    assert len(clicks) == (2 if entry == 'automatic' else 1)
    if entry == 'automatic':
        assert clicks[0][0] in ('play', 'play_2')
    assert clicks[-1][0] == 'results_2'
    expected_points = ((310, 972), (1630, 592)) if entry == 'automatic' else ((1630, 592),)
    for (_, point), expected in zip(clicks, expected_points):
        assert max(abs(a-b) for a, b in zip(point, expected)) < 12
    assert released_while_paused
    moves = [(name, at) for name, at, action in commands if action.name != 'menu_click']
    assert moves[0][0] == ('air_2' if entry == 'in_match' else 'air_3')
    assert all(name.startswith('air') for name, _ in moves)
    assert moves[1][1] - paused_at >= 1.8 - 1e-6
    assert any(event.get('matches_completed') == 1 for event in events)


@pytest.mark.skipif(not ocr_available(), reason='Richiede Tesseract')
def test_play_label_requires_complete_text_and_correct_region():
    pixels = np.full((1080, 1920, 3), 65, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    for (x, y, w, h), label in (
        ((98, 47, 328, 80), 'GIOCA'),
        ((98, 870, 328, 80), 'GIOCA DI NUOVO'),
        ((1343, 486, 480, 88), 'GIOCA ANCORA'),
    ):
        cv2.rectangle(pixels, (x, y), (x+w, y+h), (235, 235, 235), -1)
        (tw, th), _ = cv2.getTextSize(label, font, 1.25, 3)
        cv2.putText(pixels, label, (x+(w-tw)//2, y+(h+th)//2), font, 1.25, (0, 0, 0), 3)
    assert set(read_cycle_labels(pixels, 1).matches) == {'gioca_di_nuovo'}


@pytest.mark.skipif(not ocr_available(), reason='Richiede Tesseract')
@pytest.mark.parametrize('label,expected', [('GIOCA', 'gioca'), ('ANNULLA', 'annulla')])
def test_lobby_controls_without_white_rectangle(label, expected):
    pixels = np.full((1080, 1920, 3), 40, dtype=np.uint8)
    cv2.rectangle(pixels, (98, 870), (426, 950), (120, 65, 20), -1)
    cv2.putText(pixels, label, (130, 925), cv2.FONT_HERSHEY_SIMPLEX,
                1.3, (255, 255, 255), 3, cv2.LINE_AA)
    result = read_cycle_labels(pixels, 1)
    assert expected in result.matches
    assert 100 < result.matches[expected].center[0] < 426
    assert 870 < result.matches[expected].center[1] < 950


@pytest.mark.skipif(not ocr_available(), reason='Richiede Tesseract')
@pytest.mark.parametrize('label,expected', [
    ('SPAZIO PER SALTARE', 'battle_bus'),
    ('SPAZIO PER SCHIERARE ALI', 'skydiving'),
    ('SALTA QUESTA OFFERTA', None),
])
def test_aerial_prompt_requires_complete_instruction(label, expected):
    pixels = np.full((1080, 1920, 3), 40, dtype=np.uint8)
    cv2.putText(pixels, label, (1080, 600), cv2.FONT_HERSHEY_SIMPLEX,
                1., (255, 255, 255), 2, cv2.LINE_AA)
    result = read_cycle_labels(pixels, 1)
    assert result.phase_hint == expected
    observation = match_frame(result, Reading('unknown', {}))
    assert observation.playable == (expected is not None)
    assert match_frame(result, Reading('unknown', {}), locked=True).blocked
