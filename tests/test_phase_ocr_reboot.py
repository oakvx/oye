from __future__ import annotations
import time
import unittest
import numpy as np
import cv2

from observer.phase_ocr import PhaseOCRResult, PhaseOCRWorker, classify_phase
from observer.phase_gate import PhaseGate
from observer.brain import Brain
from observer.models import Observation
from observer.timed_mode import TimedMatchRoutine


class TestOCRText(unittest.TestCase):
    def test_italian_and_english_statuses(self):
        self.assertEqual(classify_phase('SPETTATORE'), 'spectator')
        self.assertEqual(classify_phase('spettatore'), 'spectator')
        self.assertEqual(classify_phase('SPECTATING'), 'spectator')
        self.assertEqual(classify_phase('IN ATTESA DEL RIAVVIO'), 'reboot_wait')
        self.assertEqual(classify_phase('in attesa di riavvio'), 'reboot_wait')
        self.assertEqual(classify_phase('WAITING FOR REBOOT'), 'reboot_wait')
        self.assertEqual(classify_phase('REBOOTING'), 'rebooting')

    def test_neutral_and_unrelated_text(self):
        for sample in ('', 'PRONTO', 'GIOCA', 'ELIMINATO', 'Riavvio disponibile', 'ATTESA GIOCATORI'):
            self.assertEqual(classify_phase(sample), 'none', sample)

    def test_real_tesseract_on_generated_text(self):
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
        except (ImportError, EnvironmentError):
            self.skipTest('Tesseract non installato su questo host')
        # Draw the texts inside the actual screen ROIs used by the worker.
        im = np.zeros((720, 1280, 3), dtype=np.uint8)
        cv2.putText(im, 'SPETTATORE', (450, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.75, (255,255,255), 4)
        cv2.putText(im, 'IN ATTESA DEL RIAVVIO', (275, 330), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255,255,255), 3)
        extracted = PhaseOCRWorker._read_text(im, 'eng')
        self.assertEqual(classify_phase(extracted), 'reboot_wait', extracted)


class TestPhaseGate(unittest.TestCase):
    @staticmethod
    def sample(at: float, cue: str = 'none', status: str = 'ok') -> PhaseOCRResult:
        return PhaseOCRResult(at, status, cue)

    def test_spectator_vetoes_teammate_match_hud(self):
        gate = PhaseGate()
        item = gate.observe(self.sample(1, 'spectator'), scene='match', menu=None, at=1.1)
        self.assertTrue(item.hold)
        self.assertEqual(item.state, 'spectator')
        item = gate.observe(None, scene='match', menu=None, at=1.2)
        self.assertTrue(item.hold)

    def test_reboot_wait_requires_fresh_negative_ocr_and_ground_evidence(self):
        gate = PhaseGate()
        gate.observe(self.sample(1, 'reboot_wait'), scene='spectator', menu=None, at=1.1)
        for n in range(5):
            got=gate.observe(None, scene='match', menu=None, at=2 + n * .1)
            self.assertTrue(got.hold)
        self.assertTrue(gate.observe(self.sample(3, 'none'), scene='match', menu=None, at=3.1).hold)
        item=gate.observe(self.sample(4.5, 'none'), scene='match', menu=None, at=4.6)
        self.assertTrue(item.recovered)
        self.assertFalse(item.hold)

    def test_no_reentry_if_ocr_says_clean_but_scene_still_spectator(self):
        gate=PhaseGate()
        gate.observe(self.sample(1, 'reboot_wait'),scene='spectator',menu=None,at=1.1)
        gate.observe(self.sample(2, 'none'),scene='spectator',menu=None,at=2.1)
        r=gate.observe(self.sample(3, 'none'),scene='spectator',menu=None,at=3.1)
        self.assertTrue(r.hold)

    def test_ocr_error_cannot_unlock(self):
        gate = PhaseGate()
        gate.observe(self.sample(1, 'reboot_wait'),scene='spectator',menu=None,at=1.1)
        for n in range(8):
            gate.observe(self.sample(2+n, 'none', 'error:timeout'),scene='match',menu=None,at=2+n+.01)
        self.assertTrue(gate.locked)

    def test_stale_ocr_never_unlocks(self):
        gate = PhaseGate()
        gate.observe(self.sample(1, 'reboot_wait'),scene='spectator',menu=None,at=1.1)
        for n in range(8):
            gate.observe(self.sample(1, 'none'),scene='match',menu=None,at=5+n)
        self.assertTrue(gate.locked)

    def test_results_unlock_only_with_verified_cta(self):
        gate=PhaseGate()
        gate.observe(self.sample(1, 'spectator'),scene='spectator',menu=None,at=1.1)
        for n in range(8):
            gate.observe(None,scene='results',menu='torna_lobby',at=2+n*.1)
        gate.observe(self.sample(3,'none'),scene='results',menu='torna_lobby',at=3.1)
        self.assertTrue(gate.observe(self.sample(4.5,'none'),scene='results',menu='torna_lobby',at=4.6).hold)
        for n in range(6):
            item=gate.observe(None,scene='results',menu='continua',at=5+n*.1)
        self.assertTrue(item.recovered)

    def test_solo_can_leave_confirmed_results(self):
        gate=PhaseGate()
        gate.observe(self.sample(1, 'spectator'),scene='spectator',menu=None,at=1.1)
        for n in range(6):
            gate.observe(None,scene='results',menu='torna_lobby',at=2+n*.1,allow_solo_return=True)
        gate.observe(self.sample(3,'none'),scene='results',menu='torna_lobby',at=3.1,allow_solo_return=True)
        result=gate.observe(self.sample(4.5,'none'),scene='results',menu='torna_lobby',at=4.6,allow_solo_return=True)
        self.assertTrue(result.recovered)

    def test_brain_latch_and_timed_resume(self):
        gate=PhaseGate()
        brain=Brain(menu_only=True)
        routine=TimedMatchRoutine()
        routine.arm(0)
        routine.stage='ground'
        g=gate.observe(self.sample(1,'reboot_wait'),scene='match',menu=None,at=1.1)
        self.assertTrue(g.hold)
        routine.stop_for_menu()
        self.assertEqual(routine.stage,'idle')
        act=brain.decide(Observation(True,'spectator'),1.1)
        self.assertEqual(act.name,'wait')
        self.assertTrue(brain.spectator_latched)
        for n in range(6):
            gate.observe(None,scene='match',menu=None,at=2+n*.1)
        gate.observe(self.sample(3,'none'),scene='match',menu=None,at=3.1)
        good=gate.observe(self.sample(4.5,'none'),scene='match',menu=None,at=4.6)
        self.assertTrue(good.recovered)
        for n in range(7):
            brain.decide(Observation(True,'match'),4.7+n*.1)
        self.assertFalse(brain.spectator_latched)
        routine.resume_after_reboot(5.5)
        self.assertEqual(routine.choose(5.5).name,'forward')
        self.assertNotEqual(routine.choose(5.6).name,'wait')


if __name__=='__main__':
    unittest.main()
