"""OpenCV releases can expose Hough line coordinates with different shapes."""
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from observer.visual_scanners import scan


class HoughCompatibilityTests(unittest.TestCase):
    def test_minimap_and_inventory_accept_both_cv2_shapes(self):
        image = np.random.default_rng(22).integers(0, 255, (180, 300, 3), dtype=np.uint8)
        lines = np.array([[5, 5, 295, 5], [5, 174, 295, 174],
                          [5, 5, 5, 174], [294, 5, 294, 174],
                          [60, 20, 60, 160], [120, 20, 120, 160],
                          [180, 20, 180, 160], [240, 20, 240, 160]], dtype=np.int32)
        for kind in ('minimap', 'inventory'):
            spec = {'id': kind, 'method': 'structure', 'roi': [0, 0, 1, 1],
                    'threshold': .25, 'enabled': True, 'options': {}}
            outputs = []
            for shape in (lines, lines.reshape(-1, 1, 4)):
                with patch.object(cv2, 'HoughLinesP', return_value=shape):
                    outputs.append(scan(image, spec))
            self.assertNotEqual(outputs[0].status, 'error')
            self.assertEqual(outputs[0].status, outputs[1].status)
            self.assertEqual(outputs[0].confidence, outputs[1].confidence)
            self.assertEqual(outputs[0].payload, outputs[1].payload)

if __name__ == '__main__':
    unittest.main()
