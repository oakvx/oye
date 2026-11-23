"""v42 real screenshot profile: conservative source integration checks.

Reference images are exact crops from user-provided screenshots; tests below
are synthetic placement controls, NOT independent visual validation.
"""
from pathlib import Path
import tempfile
import unittest
import cv2
import numpy as np

from observer.scanner_store import ScannerStore
from observer.visual_scanners import scan
from observer.screen_profiles import ScreenProfiles
from observer.state_recognition import StateRecognitionEngine
from observer.visual_scanners import ScannerResult
from observer.v42_profile import SEEDS,REFERENCE_DIR
from observer.config import save,validate
from oye_web_app import Dashboard

class SourceProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def install(self):
        (self.root/'AVVIA_OYE.bat').write_text('@echo off\n',encoding='utf-8')
        return ScannerStore(self.root)

    def test_real_install_seeds_six_targeted_detectors_not_generic_hud(self):
        store=self.install()
        self.assertEqual(len(SEEDS),6)
        for id_ in SEEDS:
            spec=store.get(id_)
            self.assertEqual(spec['method'],'template')
            self.assertTrue(spec['enabled'])
            self.assertEqual(spec['options']['resolution'],[1920,1080])
            ref=store.reference(id_)
            self.assertIsNotNone(ref,id_)
            self.assertGreater(float(np.std(ref)),7)
        self.assertFalse(store.get('compass')['enabled'])
        self.assertFalse(store.get('minimap')['enabled'])
        self.assertFalse(store.get('health_shield')['enabled'])
        self.assertFalse(store.get('inventory')['enabled'])

    def test_existing_custom_scanners_and_training_win(self):
        store=ScannerStore(self.root)
        before=store.get('lobby')
        store.save({**before,'description':'Mi hai insegnato il mio Gioca', 'enabled':False})
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS old_training (action TEXT)')
            db.execute("INSERT INTO old_training VALUES('turn_left')")
        self.install()
        new=ScannerStore(self.root)
        self.assertEqual(new.get('lobby')['description'],'Mi hai insegnato il mio Gioca')
        self.assertFalse(new.get('lobby')['enabled'])
        with new.db() as db:
            self.assertEqual(db.execute('SELECT action FROM old_training').fetchone()[0],'turn_left')

    def test_selected_resolution_defaults_only_when_missing(self):
        self.install()
        profiles=ScreenProfiles(self.root)
        self.assertEqual((profiles.profiles()['selected']['width'], profiles.profiles()['selected']['height']), (1920,1080))
        profiles.select(2560,1440,mode='borderless',source='manual')
        self.assertEqual(ScreenProfiles(self.root).profiles()['selected']['width'],2560)

    def test_can_identify_left_cta_not_navbar_or_black_frame(self):
        store=self.install()
        spec=store.get('lobby');ref=store.reference('lobby')
        img=np.zeros((1080,1920,3),dtype=np.uint8)
        # A copied header/nav GIOCA outside the operational ROI must not
        # trigger the action: exact same pixels but at y=35.
        h,w=ref.shape[:2]
        img[45:45+h,140:140+w]=ref
        r=scan(img,spec,reference=ref)
        self.assertNotEqual(r.status,'present')
        # The left CTA text inside a textured UI ROI is a valid signal.
        rng=np.random.default_rng(420)
        img=rng.integers(45,110,size=(1080,1920,3),dtype=np.uint8)
        img[885:885+h,180:180+w]=ref
        r=scan(img,spec,reference=ref)
        self.assertEqual(r.status,'present')
        self.assertGreaterEqual(r.confidence,spec['threshold'])
        # Unknown OCR vs absence is never inferred from a capture error.
        blank=scan(np.zeros((1080,1920,3),dtype=np.uint8),spec,reference=ref)
        self.assertEqual(blank.status,'unknown')

    def test_resolution_mismatch_is_unknown(self):
        store=self.install();spec=store.get('lobby')
        res=scan(np.zeros((720,1280,3),dtype=np.uint8),spec,reference=store.reference('lobby'))
        self.assertEqual(res.status,'unknown')
        self.assertIn('1920x1080',res.detail)

    def test_dashboard_sees_play_without_tesseract_or_legacy_template(self):
        self.install()
        save(self.root/'config.json',validate({}))
        d=Dashboard(self.root)
        try:
            r=d.readiness()
            self.assertIn('gioca',r['present'])
            self.assertTrue(r['quick_ready'])
        finally:d.shutdown()

    def test_result_state_does_not_survive_long_capture_gap(self):
        eng=StateRecognitionEngine(stable_frames=2,unknown_timeout=8)
        spec={'id':'elimination','name':'final','description':'test','method':'template',
              'roi':[.2,.1,.7,.8],'enabled':True,'state':'RETURNING_TO_LOBBY',
              'weight':2.1,'threshold':.9,'interval':1.,'options':{},'expected_text':''}
        roi=tuple(spec['roi'])
        for t in (1.,2.):
            old=ScannerResult('elimination',t,t,'present',.99,'template',roi,1.)
            out=eng.evaluate({'elimination':old},[spec],t)
        self.assertEqual(out.state,'RETURNING_TO_LOBBY')
        new=ScannerResult('elimination',500.,500.,'unknown',0.,'template',roi,1.)
        out=eng.evaluate({'elimination':new},[spec],500.)
        self.assertEqual(out.state,'UNKNOWN')
        self.assertIn('scadut',out.transition)

if __name__=='__main__':unittest.main()
