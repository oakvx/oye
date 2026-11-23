import unittest
from observer.play_evidence import PlayEvidence, PlaySignal


def signal(source, method, at, *, window='w', status='present', bounds=(10, 20, 40, 50), point=None):
    return PlaySignal(source, method, at, window, status, .9, bounds, point)


class PlayEvidenceTests(unittest.TestCase):
    def test_three_fresh_pairs_with_intermediate_unknown_frames(self):
        for cadence in (1.4, 5.6):
            e = PlayEvidence()
            for i in range(3):
                t = i * cadence
                result = e.update(now=t, window='w', focused=True, signals=(
                    signal('vision', 'template', t, point=(20, 30)), signal('zone', 'ocr', t)))
                self.assertEqual(result is not None, i == 2)
                if i < 2:
                    self.assertIsNone(e.update(now=t+.15, window='w', focused=True))
            self.assertEqual(result.point, (20, 30))
            self.assertEqual(result.at, 2 * cadence)

    def test_one_source_reuse_same_method_or_disagreement_fails(self):
        e = PlayEvidence()
        for t in (1., 2., 3., 4.):
            pair = (signal('vision', 'template', t, point=(20, 30)),
                    signal('scanner', 'template', t, point=(20, 30)))
            self.assertIsNone(e.update(now=t, window='w', focused=True, signals=pair))
        e.reset()
        for t in (1., 2., 3.):
            self.assertIsNone(e.update(now=t, window='w', focused=True, signals=(
                signal('vision', 'template', t, point=(20, 30)),
                signal('zone', 'ocr', 1., bounds=(100, 100, 200, 200)))))
        self.assertEqual(e.count, 0)

    def test_focus_window_negative_and_scenes_reset(self):
        for scene in PlayEvidence.VETO_SCENES:
            e = PlayEvidence()
            pair = lambda t: (signal('v', 'template', t, point=(20, 30)), signal('o', 'ocr', t))
            e.update(now=1, window='w', focused=True, signals=pair(1))
            e.update(now=2, window='w', focused=True, scene=scene)
            self.assertIsNone(e.update(now=3, window='w', focused=True, signals=pair(3)))
            self.assertEqual(e.count, 0)
            e.update(now=7, window='w', focused=True, signals=pair(7))
            e.update(now=8, window='w', focused=False, signals=pair(8))
            self.assertEqual(e.count, 0)
            e.update(now=9, window='w', focused=True, signals=pair(9))
            e.update(now=10, window='other', focused=True)
            self.assertEqual(e.count, 0)
            e.update(now=11, window='w', focused=True, signals=pair(11))
            e.update(now=12, window='w', focused=True, signals=(signal('o', 'ocr', 12, status='absent'),))
            self.assertEqual(e.count, 0)

    def test_old_or_future_readings_do_not_count(self):
        e = PlayEvidence()
        self.assertIsNone(e.update(now=10, window='w', focused=True, signals=(
            signal('v', 'template', 5, point=(20, 30)), signal('o', 'ocr', 11))))
        self.assertEqual(e.count, 0)
