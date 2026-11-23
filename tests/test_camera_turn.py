import unittest
import cv2
import numpy as np
from observer.vision import camera_turn_between

class TestCamera(unittest.TestCase):
    def test_horizontal_pan(self):
        rng=np.random.default_rng(123)
        base=rng.integers(0,256,(540,960,3),dtype=np.uint8)
        transform=np.float32([[1,0,15],[0,1,0]])
        after=cv2.warpAffine(base, transform, (960,540),borderMode=cv2.BORDER_REFLECT)
        motion=camera_turn_between(base,after)
        self.assertIsNotNone(motion)
        self.assertTrue(motion.reliable)
        self.assertGreater(motion.pixels,0.5)

    def test_still_camera(self):
        rng=np.random.default_rng(122)
        base=rng.integers(0,256,(540,960,3),dtype=np.uint8)
        m=camera_turn_between(base,base.copy())
        self.assertTrue(m.reliable)
        self.assertLess(m.pixels,.5)

    def test_missing_texture_inconclusive(self):
        base=np.zeros((540,960,3),dtype=np.uint8)
        m=camera_turn_between(base,base.copy())
        self.assertFalse(m.reliable)

if __name__=='__main__': unittest.main()
