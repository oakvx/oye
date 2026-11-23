from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from observer.qt_frontend_logic import mode_flags, phase_display, save_calibration
from observer.vision import Vision

class ModeTests(unittest.TestCase):
    def test_safe_default_never_live(self):
        f=mode_flags('observe')
        self.assertFalse(f['live']);self.assertFalse(f['autonomous_enabled'])
        self.assertFalse(f['guided_explore_enabled'])

    def test_training_does_not_send_inputs(self):
        f=mode_flags('train')
        self.assertFalse(f['live']);self.assertTrue(f['exploration'])
        self.assertTrue(f['demonstration_enabled'])

    def test_guided_is_live_only_with_explicit_mode(self):
        f=mode_flags('guided')
        self.assertTrue(f['live']);self.assertTrue(f['autonomous_enabled'])
        self.assertTrue(f['guided_explore_enabled'])

    def test_autonomous_differs_from_guided(self):
        f=mode_flags('autonomous')
        self.assertTrue(f['live']);self.assertFalse(f['guided_explore_enabled'])

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):mode_flags('something')

    def test_phase_names(self):
        self.assertEqual(phase_display('normal'),'Nessun avviso')
        self.assertEqual(phase_display('reboot_wait'),'Attesa riavvio')

class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.folder=Path(self.tmp.name)
        rng=np.random.default_rng(123)
        self.frame=rng.integers(0,255,size=(720,1280,3),dtype=np.uint8)

    def tearDown(self):self.tmp.cleanup()

    def test_roundtrip_with_legacy_vision(self):
        out=save_calibration(self.frame,(45,80,260,195),'hud_salute',self.folder)
        self.assertTrue(out.exists())
        self.assertEqual(cv2.imread(str(out)).shape,(115,215,3))
        self.assertEqual(json.loads((self.folder/'_resolution.json').read_text())['hud_salute'],[1280,720])
        rois=json.loads((self.folder/'_rois.json').read_text())
        self.assertTrue(all(0<=x<=1 for x in rois['hud_salute']))
        vision=Vision(self.folder)
        self.assertIn('hud_salute',vision.templates)

    def test_existing_metadata_is_preserved(self):
        save_calibration(self.frame,(10,20,50,60),'gioca',self.folder)
        save_calibration(self.frame,(125,215,240,345),'continua',self.folder)
        self.assertEqual(set(json.loads((self.folder/'_rois.json').read_text())),{'gioca','continua'})

    def test_invalid_selection_refused(self):
        for selection in (None,(0,0,2,3),(-2,0,90,90),(100,90,80,45)):
            with self.subTest(selection=selection),self.assertRaises(ValueError):
                save_calibration(self.frame,selection,'gioca',self.folder)
        self.assertFalse((self.folder/'gioca.png').exists())

    def test_reject_unknown_template(self):
        with self.assertRaises(ValueError):
            save_calibration(self.frame,(10,20,50,60),'not_a_template',self.folder)

if __name__=='__main__':unittest.main()
