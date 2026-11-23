"""Reproduce asynchronous lobby delivery; never click a genuinely unknown scene."""
import unittest
from observer.state_recognition import LobbySceneEvidence
from observer.menu_supervisor import MenuSupervisor
from observer.models import Match
from observer.runner import _cached_play_match


class LobbyDeliveryTests(unittest.TestCase):
    def run_sequence(self, period):
        scene_gate = LobbySceneEvidence()
        supervisor = MenuSupervisor()
        cached = {}
        clicks = []
        # Each sample represents a new positive OCR reading and confirmed fusion.
        for i in range(round((period * 2 + .6) / .15) + 1):
            t = round(i * .15, 3)
            hit = any(abs(t - k * period) <= .075 for k in (0, 1, 2))
            if hit:
                scene_gate.confirm(t, 'window')
                cached['gioca'] = (t, {'score': .9, 'center': (20, 30)})
            scene = scene_gate.resolve('unknown', t, 'window')
            evidence = _cached_play_match(t, scene, cached, None, 0, 0)
            matches = {'gioca': evidence[1]} if evidence else {}
            times = {'gioca': evidence[0]} if evidence else {}
            p = supervisor.offer(now=t, frame_at=t, stage='joining', scene=scene,
                matches=matches, repeat=True, evidence_times=times)
            if p.action.name == 'menu_click':
                clicks.append(t)
                break
        self.assertEqual(len(clicks), 1)
        self.assertGreaterEqual(clicks[0], period * 2 - .075)

    def test_per_frame_scene_alternation_reproduces_original_block(self):
        supervisor = MenuSupervisor()
        last = None
        clicks = []
        for i in range(76):
            t = round(i * .15, 3)
            delivery = i in (0, 37, 75)
            if delivery:
                last = t
            matches = {'gioca': Match('gioca', .9, (20, 30))}
            proposal = supervisor.offer(now=t, frame_at=t, stage='joining',
                scene='lobby' if delivery else 'unknown', matches=matches, repeat=True,
                evidence_times={'gioca': last})
            if proposal.action.name == 'menu_click':
                clicks.append(t)
        self.assertEqual(clicks, [])

    def test_delivery_gaps(self):
        for period in (1.4, 5.6):
            with self.subTest(period=period):
                self.run_sequence(period)

    def test_expiry_conflicts_and_focus_loss(self):
        gate = LobbySceneEvidence()
        gate.confirm(10, 'window')
        self.assertEqual(gate.resolve('unknown', 12, 'window'), 'lobby')
        self.assertEqual(gate.resolve('results', 12, 'window'), 'results')
        self.assertEqual(gate.resolve('unknown', 12.1, 'window'), 'unknown')
        gate.confirm(20, 'window')
        self.assertEqual(gate.resolve('unknown', 28, 'window'), 'unknown')
        gate.confirm(30, 'window')
        gate.invalidate()
        self.assertEqual(gate.resolve('unknown', 30.1, 'window'), 'unknown')
        gate.confirm(40, 'window')
        self.assertEqual(gate.resolve('unknown', 40.1, 'moved'), 'unknown')

if __name__ == '__main__':
    unittest.main()

class TelemetryTests(unittest.TestCase):
    def test_template_throttles_unchanged_state_but_records_change(self):
        from observer.runner import Runner
        r = object.__new__(Runner)
        r._button_logged_at = {}
        r._button_logged_status = {}
        r._button_positive_at = {}
        events = []
        r._emit = lambda kind, message, info: events.append(info)
        for i in range(10):
            r._record_button_evidence('gioca', 'template', i * .15, .9, 'present', i * .15)
        self.assertEqual(len(events), 2)
        r._record_button_evidence('gioca', 'template', 1.5, None, 'absent', 1.5)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[-1]['status'], 'absent')

class RecoveryTests(unittest.TestCase):
    def test_loss_or_negative_restarts_confirmation(self):
        gate = LobbySceneEvidence()
        m = MenuSupervisor()
        matches = {'gioca': Match('gioca', .9, (20, 30))}
        for t in (1., 2.):
            gate.confirm(t, 'window')
            scene = gate.resolve('unknown', t, 'window')
            self.assertEqual(m.offer(now=t, frame_at=t, stage='joining', scene=scene,
                matches=matches, repeat=True).action.name, 'wait')
        # Represents a lost focus or fresh contradictory negative OCR result.
        gate.invalidate()
        m.invalidate_observations()
        self.assertEqual(gate.resolve('unknown', 3, 'window'), 'unknown')
        p = m.offer(now=3, frame_at=3, stage='joining', scene='unknown',
                    matches=matches, repeat=True)
        self.assertEqual(p.action.name, 'wait')
        # New independent readings are needed again after focus/OCR recovery.
        for t in (4., 5., 6.):
            gate.confirm(t, 'window')
            p = m.offer(now=t, frame_at=t, stage='joining',
                        scene=gate.resolve('unknown', t, 'window'),
                        matches=matches, repeat=True)
            self.assertEqual(p.action.name, 'menu_click' if t == 6 else 'wait')

class PhaseOCRAvailabilityTests(unittest.TestCase):
    def test_outage_resets_stability_and_recovery_requires_new_reads(self):
        from observer.runner import _phase_ocr_available
        m = MenuSupervisor()
        match = {'gioca': Match('gioca', .9, (20, 30))}
        for t in (1., 2.):
            m.offer(now=t, frame_at=t, stage='joining', scene='lobby', matches=match,
                    repeat=True, locked=not _phase_ocr_available(t, 1., False))
        p = m.offer(now=3, frame_at=3, stage='joining', scene='lobby', matches=match,
                    repeat=True, locked=not _phase_ocr_available(3, 1, True))
        self.assertEqual(p.action.name, 'wait')
        for t in (4., 5., 6.):
            p = m.offer(now=t, frame_at=t, stage='joining', scene='lobby', matches=match,
                        repeat=True, locked=not _phase_ocr_available(t, 4., False))
            self.assertEqual(p.action.name, 'menu_click' if t == 6 else 'wait')
        self.assertFalse(_phase_ocr_available(20, 4, False))
        self.assertFalse(_phase_ocr_available(3, 4, False))
