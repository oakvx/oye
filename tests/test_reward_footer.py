import numpy as np
import pytest
import cv2

from observer.reward_ocr import RewardOCRWorker, ocr_available


@pytest.mark.parametrize('stage', ['await_play', 'post_lobby', 'claim_rewards', 'returning', 'lobby'])
def test_footer_claim_without_title(monkeypatch, stage):
    result = scan(monkeypatch, stage)
    assert result.status == 'ok'
    assert result.reward_screen
    assert result.name == 'riscatta'
    assert result.center == (860, 850)


@pytest.mark.parametrize('stage,words,y,confidence', [
    ('exploring', ['EQUIPAGGIA', 'RISCATTA'], 100, 95),
    ('post_lobby', ['NEGOZIO', 'RISCATTA'], 100, 95),
    ('post_lobby', ['EQUIPAGGIA', 'RISCATTA'], -400, 95),
    ('post_lobby', ['EQUIPAGGIA', 'RISCATTA'], 100, 40),
])
def test_unrelated_or_unreliable_footer_is_not_reward(monkeypatch, stage, words, y, confidence):
    result = scan(monkeypatch, stage, words, y, confidence)
    assert not result.reward_screen
    assert result.center is None


@pytest.mark.skipif(not ocr_available(), reason='Tesseract unavailable')
def test_real_ocr_footer_without_reward_title():
    frame = np.full((900, 1200, 3), 35, dtype=np.uint8)
    for text, x in [('EQUIPAGGIA', 200), ('RISCATTA', 750)]:
        cv2.putText(frame, text, (x, 780), cv2.FONT_HERSHEY_SIMPLEX,
                    1., (255, 255, 255), 2, cv2.LINE_AA)
    worker = RewardOCRWorker()
    worker.start()
    try:
        worker.submit(frame, 0, 0, 1., 'returning')
        result = worker.results.get(timeout=15)
        assert result.status == 'ok'
        assert result.reward_screen
        assert result.name == 'riscatta'
        assert 750 < result.center[0] < 920
        assert 750 < result.center[1] < 790
    finally:
        worker.stop()


def test_empty_lobby_does_not_scan_all_results_buttons(monkeypatch):
    import pytesseract
    scans = []
    monkeypatch.setattr(pytesseract, 'image_to_string', lambda *a, **k: '')
    def read_data(*args, **kwargs):
        scans.append(args[0].shape)
        return {'text': []}
    monkeypatch.setattr(pytesseract, 'image_to_data', read_data)
    worker = RewardOCRWorker()
    worker.start()
    try:
        worker.submit(np.zeros((900, 1200, 3), dtype=np.uint8), 0, 0, 1., 'await_play')
        assert not worker.results.get(timeout=5).reward_screen
        assert len(scans) == 1
    finally:
        worker.stop()


def scan(monkeypatch, stage, words=None, y=100, confidence=95):
    import pytesseract
    monkeypatch.setattr(pytesseract, 'image_to_string', lambda *a, **k: '')
    monkeypatch.setattr(pytesseract, 'image_to_data', lambda *a, **k: {
        'text': words or ['EQUIPAGGIA', 'RISCATTA'],
        'conf': [confidence, confidence], 'left': [200, 700],
        'top': [y, y], 'width': [120, 120], 'height': [30, 30],
    })
    worker = RewardOCRWorker()
    worker.start()
    try:
        worker.submit(np.zeros((900, 1200, 3), dtype=np.uint8), 100, 150, 1., stage)
        return worker.results.get(timeout=5)
    finally:
        worker.stop()
