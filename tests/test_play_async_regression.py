"""Known regression: a positive OCR delivery between 0.15 s video frames.

This exercises the current supervisor and controller, not real OCR, independent
signal fusion, a foreground window, a driver, or Fortnite.  The expected failure
must not be mistaken for a verified safe click.
"""
import pytest

from observer.menu_supervisor import MenuSupervisor
from observer.models import Action, Match
from observer.runner import _cached_play_match
from observer.single_match import SingleMatchController


@pytest.mark.xfail(strict=True, reason='Conferma Gioca azzerata sui frame unknown; manca fusione sicura di due segnali')
@pytest.mark.parametrize('cadence', [1.4, 5.6])
def test_play_survives_unknown_frames_between_ocr_deliveries(cadence):
    supervisor = MenuSupervisor()
    controller = SingleMatchController(quick_start=True, quick_wait_play_exit=True)
    controller.arm_quick(0)
    region = {}
    next_delivery = 0.0
    delivered = 0
    proposed = []
    # The worker samples at nominal times; delivery is on the next video frame.
    for i in range(int((cadence * 3 + 1) / .15) + 1):
        now = round(i * .15, 6)
        delivery = now + 1e-9 >= next_delivery and delivered < 3
        if delivery:
            region['gioca'] = (next_delivery, {'score': .9, 'center': (20, 30)})
            next_delivery += cadence
            delivered += 1
        scene = 'lobby' if delivery else 'unknown'
        result = _cached_play_match(now, scene, region, None, 100, 200)
        matches = {'gioca': result[1]} if result else {}
        times = {'gioca': result[0]} if result else {}
        proposal = supervisor.offer(now=now, frame_at=now, stage='joining', scene=scene,
                                   matches=matches, repeat=True, evidence_times=times)
        action = controller.decide(now=now, frame_at=now, scene=scene,
            menu='gioca' if matches else None,
            candidate=proposal.action if proposal.name else Action('wait'),
            menu_candidate_authorized=proposal.name is not None,
            authorized=False, locked=False, spectator=False,
            play_visible=True if matches else None,
            play_evidence_at=result[0] if result else None,
            play_point=matches['gioca'].center if matches else None)
        if action.name == 'menu_click':
            proposed.append(now)
    assert delivered == 3
    assert len(proposed) == 1, f'Atteso un clic proposto dopo tre letture; ottenuti {proposed}'
