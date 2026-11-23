"""v39 regression tests: independent OCR crops and additive resolution profiles."""
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import cv2

from observer.region_ocr import read_single_region, ScreenRegionOCRWorker
from observer.screen_profiles import ScreenProfiles, validate_resolution, validate_region
from observer.vision import Vision

class ScreenProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_resolution_detection_and_manual_alternatives_are_independent(self):
        store=ScreenProfiles(self.root)
        self.assertIsNone(store.profiles()['selected'])
        store.select(1920,1080,mode='borderless',source='detected')
        rect=[.7,.72,.89,.79]
        store.add_rule('gioca',rect,image_size=(1920,1080),expected_text='GIOCA')
        store.select(2560,1440,mode='borderless',source='manual')
        self.assertEqual(store.rules(),[])
        store.add_rule('gioca',rect,image_size=(2560,1440),expected_text='PLAY')
        store.select(1920,1080,mode='borderless')
        self.assertEqual(store.rules()[0]['expected_text'],'GIOCA')
        self.assertEqual(len(store.profiles()['profiles']),2)

    def test_existing_learning_tables_untouched(self):
        db=self.root/'learning'/'observer.sqlite3';db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('CREATE TABLE old_training (id INTEGER PRIMARY KEY, action TEXT)')
            conn.execute('INSERT INTO old_training(action) VALUES (\'forward\')')
        store=ScreenProfiles(self.root);store.select(1920,1080)
        store.add_rule('reward_title',[.2,.2,.6,.3],expected_text='RICOMPENSE OTTENUTE',image_size=(1920,1080))
        with closing(sqlite3.connect(db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT action FROM old_training').fetchone()[0],'forward')
            self.assertIsNotNone(conn.execute('SELECT expected_text FROM oye_screen_rules').fetchone())

    def test_migration_of_old_screen_rules_adds_expected_text(self):
        db=self.root/'learning'/'observer.sqlite3';db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('CREATE TABLE oye_screen_rules (id INTEGER PRIMARY KEY,profile_id INTEGER,target TEXT,state TEXT,action_description TEXT,user_explanation TEXT,region_json TEXT,updated_at TEXT)')
        ScreenProfiles(self.root)
        with closing(sqlite3.connect(db)) as conn, conn:
            self.assertIn('expected_text',{x[1] for x in conn.execute('PRAGMA table_info(oye_screen_rules)')})

    def test_ocr_reads_only_its_own_rectangle(self):
        frame=np.zeros((1080,1920,3),dtype='uint8')
        frame[:,:960]=100
        frame[:,960:]=200
        def reader(crop):
            self.assertLessEqual(crop.shape[1],400)
            self.assertEqual(int(crop[0,0,0]),200)
            return 'RITORNA'
        rule={'target':'torna_lobby','region':[.70,.50,.90,.60], 'expected_text':'RITORNA ALLA LOBBY'}
        a=read_single_region(frame,rule,reader=reader,at=13)
        self.assertTrue(a.matched)
        self.assertEqual(a.text,'ritorna')
        self.assertLess(a.center[0],1920)
        a2=read_single_region(frame,{**rule,'expected_text':'RISCATTA'},reader=reader)
        self.assertFalse(a2.matched)

    def test_different_targets_get_independent_stability_and_priorities(self):
        store=ScreenProfiles(self.root);store.select(1920,1080)
        store.add_rule('gioca',[.72,.8,.9,.86],expected_text='GIOCA',image_size=(1920,1080))
        store.add_rule('reward_title',[.15,.12,.55,.2],expected_text='RICOMPENSE OTTENUTE',image_size=(1920,1080))
        worker=ScreenRegionOCRWorker(self.root)
        self.assertIn('gioca',worker._targets('joining'))
        self.assertIn('reward_title',worker._targets('joining'))
        self.assertEqual(worker.counts['gioca'],0)
        self.assertEqual(worker.counts['reward_title'],0)
        mismatch=read_single_region(np.zeros((720,1280,3),np.uint8), {'target':'gioca','region':[.7,.8,.9,.9],'expected_text':'GIOCA'},reader=lambda c:'')
        self.assertFalse(mismatch.matched)

    def test_reject_invalid_input(self):
        with self.assertRaises(ValueError):validate_resolution(True,1080)
        with self.assertRaises(ValueError):validate_region([-.5,0,.4,1])
        with self.assertRaises(ValueError):validate_region([0,0,float('nan'),1])
        store=ScreenProfiles(self.root);store.select(1920,1080)
        with self.assertRaises(ValueError):store.add_rule('gioca',[0,0,.2,.2],image_size=(1280,720))

    def test_corrupt_database_is_nonfatal_for_dashboard_status(self):
        db=self.root/'learning'/'observer.sqlite3';db.parent.mkdir(parents=True)
        db.write_bytes(b'BROKEN DATABASE')
        from oye_web_app import Dashboard
        dashboard=Dashboard(self.root)
        try:
            status=dashboard.screen_profile_status()
            self.assertIsNone(status['selected'])
            self.assertIn('error',status)
        finally:dashboard.shutdown()

    def test_existing_template_does_not_get_replaced_by_profile_asset(self):
        from observer.qt_frontend_logic import save_calibration
        orig=self.root/'templates';orig.mkdir()
        rng=np.random.default_rng(123)
        frame=rng.integers(0,256,(1080,1920,3),dtype=np.uint8)
        save_calibration(frame,(1400,810,1540,865),'gioca',orig)
        original=(orig/'gioca.png').read_bytes()
        store=ScreenProfiles(self.root);store.select(1920,1080)
        override=store.assets_dir();self.assertNotEqual(orig,override)
        save_calibration(frame,(1120,730,1240,788),'gioca',override)
        vision=Vision(orig,profile_folder=override)
        self.assertTrue(vision.templates['gioca'].size)
        self.assertEqual((orig/'gioca.png').read_bytes(),original)

if __name__=='__main__':unittest.main()
