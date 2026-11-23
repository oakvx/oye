"""Replay must not let repeated or reversed captures become new evidence."""
from pathlib import Path
import tempfile
import unittest
import cv2
import numpy as np
from replay_scanners import replay


class ReplayFreshnessTests(unittest.TestCase):
    def test_duplicate_and_reverse_time_are_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / 'frame.png'
            cv2.imwrite(str(image), np.zeros((100, 200, 3), dtype=np.uint8))
            rows = replay([(image, 1.), (image, 1.), (image, .5), (image, 2.)], root=root)
            self.assertIn('state', rows[0])
            self.assertIn('error', rows[1])
            self.assertIn('error', rows[2])
            self.assertIn('state', rows[3])

if __name__ == '__main__':
    unittest.main()
