"""OCR scheduling and freshness regressions for the Ignorante cycle."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
from observer.region_ocr import ScreenRegionOCRWorker, select_legacy_target


class OCRDynamicsTests(unittest.TestCase):
    def test_play_and_requeue_are_prioritized_without_starving_other_regions(self):
        pool = ['reward_title', 'riscatta', 'gioca', 'torna_lobby']
        chosen = [select_legacy_target(pool, 'joining', i) for i in range(8)]
        self.assertEqual(chosen[::2], ['gioca'] * 4)
        self.assertTrue(set(pool).issubset(chosen))
        pool = ['reward_title', 'riscatta', 'torna_lobby', 'gioca_di_nuovo']
        chosen = [select_legacy_target(pool, 'ending', i) for i in range(8)]
        self.assertEqual(chosen[::2], ['gioca_di_nuovo'] * 4)
        self.assertTrue(set(pool).issubset(chosen))

    def test_requeue_in_actual_results_pool_with_many_rules(self):
        with tempfile.TemporaryDirectory() as td:
            worker = ScreenRegionOCRWorker(Path(td))
            worker.rules = {name: {'expected_text': name} for name in
                ('reward_title', 'riscatta', 'torna_lobby', 'continua',
                 'gioca_di_nuovo', 'gioca')}
            self.assertIn('gioca_di_nuovo', worker._targets('ending'))

    def test_duplicate_and_out_of_order_frames_not_submitted(self):
        with tempfile.TemporaryDirectory() as td:
            worker = ScreenRegionOCRWorker(Path(td))
            worker.thread = object()  # submit only checks thread presence
            frame = np.zeros((20, 20, 3), dtype=np.uint8)
            self.assertTrue(worker.submit(frame, 10, 'joining'))
            worker.pending.get_nowait()
            for at in (10, 9, float('nan')):
                self.assertFalse(worker.submit(frame, at, 'joining'))
            self.assertTrue(worker.submit(frame, 11, 'joining'))

if __name__ == '__main__':
    unittest.main()
