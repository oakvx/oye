"""Synthetic async sequence: two methods, unknown frames, no real input."""
import pytest
from observer.menu_supervisor import MenuSupervisor
from observer.models import Action, Match
from observer.play_evidence import PlayEvidence, PlaySignal
from observer.single_match import SingleMatchController


@pytest.mark.parametrize('cadence', [1.4, 5.6])
def test_play_survives_unknown_frames_between_ocr_deliveries(cadence):
    supervisor = MenuSupervisor()
    evidence = PlayEvidence()
    controller = SingleMatchController(quick_start=True, quick_wait_play_exit=True)
    controller.arm_quick(0)
    next_delivery = 0.0
    delivered = 0
    proposed = []
    for i in range(int((cadence * 3 + 1) / .15) + 1):
        now = round(i * .15, 6)
        delivery = now + 1e-9 >= next_delivery and delivered < 3
        signals = [PlaySignal('vision', 'template', now, 'window', 'present', .9,
                              (120, 230, 121, 231), (120, 230))]
        if delivery:
            signals.append(PlaySignal('zone', 'ocr', next_delivery, 'window', 'present', .9,
                                      (100, 200, 140, 240)))
            next_delivery += cadence
            delivered += 1
        scene = 'lobby' if delivery else 'unknown'
        confirmation = evidence.update(now=now, window='window', focused=True, signals=signals)
        matches = {'gioca': Match('gioca', .9, (120, 230))}
        times = {'gioca': confirmation.at} if confirmation else {}
        proposal = supervisor.offer(now=now, frame_at=now, stage='joining', scene=scene,
            matches=matches, repeat=True, evidence_times=times,
            require_play_confirmation=True, play_confirmation=confirmation)
        action = controller.decide(now=now, frame_at=now, scene=scene, menu='gioca',
            candidate=proposal.action if proposal.name else Action('wait'),
            menu_candidate_authorized=proposal.name is not None,
            play_button_confirmed=bool(proposal.name == 'gioca' and confirmation),
            authorized=False, locked=False, spectator=False, play_visible=True,
            play_evidence_at=confirmation.at if confirmation else None, play_point=(120, 230))
        if action.name == 'menu_click':
            proposed.append(now)
    assert delivered == 3
    assert len(proposed) == 1


def test_real_supervisor_fails_closed_without_confirmation_even_in_lobby():
    supervisor = MenuSupervisor()
    for t in (1., 2., 3., 4.):
        proposal = supervisor.offer(now=t, frame_at=t, stage='joining', scene='lobby',
            matches={'gioca': Match('gioca', .99, (20, 30))}, repeat=True,
            require_play_confirmation=True)
        assert proposal.action.name == 'wait'


def test_incompatible_scene_and_focus_loss_discard_pairs():
    e = PlayEvidence()
    def pair(t):
        return (PlaySignal('vision', 'template', t, 'window', 'present', .9,
                           (20, 30, 21, 31), (20, 30)),
                PlaySignal('zone', 'ocr', t, 'window', 'present', .9, (10, 20, 40, 50)))
    e.update(now=1, window='window', focused=True, signals=pair(1))
    e.update(now=2, window='window', focused=True, signals=pair(2))
    assert e.update(now=3, window='window', focused=True, scene='loading', signals=pair(3)) is None
    assert e.count == 0
    assert e.update(now=4, window='window', focused=True, signals=pair(4)) is None
    assert e.update(now=8, window='window', focused=True, signals=pair(8)) is None
    e.update(now=9, window='window', focused=False)
    assert e.count == 0
    assert e.update(now=10, window='window', focused=True, signals=pair(10)) is None
