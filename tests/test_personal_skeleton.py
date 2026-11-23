import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np
from tools.personal_skeleton import inspect, main


class PersonalSkeletonTests(unittest.TestCase):
    def test_missing_data_is_a_clear_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(['--manifest', str(Path(tmp) / 'missing.json')]), 0)

    def test_independent_frames_and_missing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cv2.imwrite(str(root/'frame.png'), np.zeros((200, 300, 3), dtype=np.uint8))
            manifest = root/'manifest.json'
            manifest.write_text(json.dumps({'images': [
                {'file':'frame.png','phase_provisional':'lobby'}, {'file':'missing.png'}]}))
            report = inspect(manifest, root/'templates')
            self.assertEqual(report['vision_templates_loaded'], [])
            self.assertEqual(report['frames'][0]['vision_scene'], 'unknown')
            self.assertEqual(report['frames'][0]['resolution'], [300, 200])
            self.assertIn('error', report['frames'][1])
            self.assertNotIn('frame.png', json.dumps(report))
