"""Slow asynchronous OCR must not be mistaken for a missing button."""
import unittest
from observer.runner import _cached_play_match
from observer.menu_supervisor import MenuSupervisor
from observer.models import Match


class SlowOCRTests(unittest.TestCase):
    def test_distinct_ocr_readings_confirm_with_intervening_video_frames(self):
        supervisor = MenuSupervisor()
        region = {}
        offers = []
        for frame in (1., 1.2, 1.4, 2.5, 2.7, 4., 4.2):
            if frame in (1., 2.5, 4.):
                region['gioca'] = (frame, {'score': .9, 'center': (20, 30)})
            at, match = _cached_play_match(frame, 'unknown', region, None, 100, 200)
            offers.append(supervisor.offer(now=frame, frame_at=frame, stage='joining',
                scene='unknown', matches={'gioca': match}, repeat=True,
                evidence_times={'gioca': at}))
        self.assertTrue(all(o.action.name == 'wait' for o in offers[:5]))
        self.assertEqual(offers[5].action.name, 'menu_click')
        self.assertEqual(offers[5].action.point, (120, 230))
        self.assertEqual(offers[6].action.name, 'wait')  # same OCR is not another confirmation

    def test_cached_ocr_expires_and_never_overrides_other_scenes(self):
        scanner = (1., Match('gioca', .9, (4, 5)))
        self.assertEqual(_cached_play_match(2, 'lobby', {}, scanner, 0, 0), scanner)
        self.assertIsNone(_cached_play_match(4.4, 'lobby', {}, scanner, 0, 0))
        self.assertIsNone(_cached_play_match(2, 'results', {}, scanner, 0, 0))
        self.assertIsNone(_cached_play_match(.5, 'lobby', {}, scanner, 0, 0))

if __name__ == '__main__':
    unittest.main()
