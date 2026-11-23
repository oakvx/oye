"""A cached negative OCR sample must not stand in for three screenshots."""
import unittest
from types import SimpleNamespace
from observer.models import Action
from observer.runner import Runner
from observer.single_match import SingleMatchController


class AbsenceEvidenceTests(unittest.TestCase):
    def test_cached_negative_does_not_start_loading(self):
        c = SingleMatchController(quick_start=True, quick_wait_play_exit=True)
        c.arm_quick(0)
        def step(frame, visible, evidence):
            return c.decide(now=frame, frame_at=frame, scene='unknown', menu=None,
                candidate=Action('wait'), authorized=False, locked=False, spectator=False,
                play_visible=visible, play_evidence_at=evidence)
        step(1, True, 1)
        for frame in (2, 2.1, 2.2, 2.3):
            self.assertEqual(step(frame, False, 2).name, 'wait')
        self.assertEqual(c.quick_absent_frames, 1)
        self.assertFalse(c.quick_departed)
        step(3, False, 3)
        self.assertFalse(c.quick_departed)
        step(4, False, 4)
        self.assertTrue(c.quick_departed)

    def test_runner_preserves_original_ocr_timestamp(self):
        r = object.__new__(Runner)
        r._play_region_status = (3.0, 'absent')
        reading = SimpleNamespace(best={}, menu_checks={})
        self.assertIs(r._play_visibility(reading, 4), False)
        self.assertEqual(r._play_evidence_at(reading, 4, 4), 3)
        self.assertIsNone(r._play_visibility(reading, 7))
        self.assertEqual(r._play_evidence_at(reading, 7, 7), 7)

if __name__ == '__main__':
    unittest.main()
