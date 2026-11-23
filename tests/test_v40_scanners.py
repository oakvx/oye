"""v40 modular scanner regression suite. Fixtures are SYNTHETIC, not Fortnite validation."""
from __future__ import annotations
from contextlib import closing
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from observer.scanner_store import ScannerStore
from observer.visual_scanners import ScannerResult,scan,crop_roi,PRESETS
from observer.state_recognition import StateRecognitionEngine
from observer.region_ocr import ScreenRegionOCRWorker
from observer.screen_profiles import ScreenProfiles
from observer.single_match import SingleMatchController
from observer.models import Action
from observer.vision import Vision
from observer.qt_frontend_logic import save_calibration
from oye_web_app import Dashboard
from replay_scanners import replay

ROI=[.2,.2,.8,.8]

def frame(size=(300,500)):
    return np.zeros((size[0],size[1],3),dtype=np.uint8)

def spec(id_='compass',method='structure',**kw):
    return {'id':id_,'name':id_,'description':'synthetic','roi':ROI,'method':method,
            'enabled':True,'threshold':.25,'weight':1,'interval':.5,'state':'IN_MATCH',
            'expected_text':'','options':{},**kw}

def evidence(id_,kind='present',conf=.95,at=0):
    return ScannerResult(id_,at,at,kind,conf,'ocr',tuple(ROI),4.,'','',None)

class ScannerVisualTests(unittest.TestCase):
    def test_compass_changes_heading_without_matching_digits(self):
        values=[]
        for label in ('N','NW','320'):
            im=frame();area=crop_roi(im,ROI);area[:]=[12,20,35]
            for x in range(4,area.shape[1]-4,9):cv2.line(area,(x,15),(x,65),(220,220,220),1)
            cv2.putText(area,label,(12,95),cv2.FONT_HERSHEY_SIMPLEX,1,(255,255,255),2)
            values.append(scan(im,spec('compass')).confidence)
        self.assertTrue(all(v>.20 for v in values))
        self.assertLess(max(values)-min(values),.12)

    def test_minimap_changes_terrain_but_preserves_border(self):
        values=[]
        for seed in (2,3):
            im=frame();area=crop_roi(im,ROI)
            rng=np.random.default_rng(seed)
            area[:]=rng.integers(20,255,area.shape,dtype=np.uint8)
            cv2.rectangle(area,(2,2),(area.shape[1]-3,area.shape[0]-3),(245,245,245),3)
            values.append(scan(im,spec('minimap')).confidence)
        self.assertTrue(all(v>.30 for v in values))
        self.assertAlmostEqual(values[0],values[1],delta=.3)

    def test_health_and_shield_dynamic_values(self):
        values=[]
        for width in (.3,.8):
            im=frame();area=crop_roi(im,ROI)
            area[:]=[33,42,48]
            cv2.rectangle(area,(10,20),(int(area.shape[1]*width),31),(40,210,80),-1)
            cv2.rectangle(area,(10,43),(int(area.shape[1]*width),54),(250,120,70),-1)
            values.append(scan(im,spec('health_shield')).confidence)
        self.assertTrue(all(v>0 for v in values))

    def test_inventory_empty_and_partly_occupied(self):
        values=[]
        for occupied in (0,3):
            im=frame();area=crop_roi(im,ROI);area[:]=[15,19,24]
            w=area.shape[1]//5
            for i in range(5):
                cv2.rectangle(area,(i*w+2,45),(min((i+1)*w-2,area.shape[1]-2),115),(210,190,180),3)
                if i<occupied:cv2.line(area,(i*w+8,60),(i*w+w-10,100),(40,210,220),4)
            r=scan(im,spec('inventory'))
            values.append(r)
        self.assertTrue(all(x.confidence>=.35 for x in values))
        self.assertNotEqual(values[0].payload['slots_occupied_estimate'],values[1].payload['slots_occupied_estimate'])

    def test_numeric_scanners_read_ocr_without_using_number_as_state_key(self):
        for name in ('player_counter','elimination_counter'):
            conf=[]
            for num in ('0','38','97'):
                result=scan(frame(),spec(name,'ocr',threshold=.65,expected_text=r'\b\d{1,3}\b',
                           options={'numeric':True}),ocr_reader=lambda _:num)
                self.assertEqual(result.status,'present')
                self.assertEqual(result.payload['value'],int(num))
                conf.append(result.confidence)
            self.assertEqual(len(set(conf)),1)

    def test_unknown_on_blank_hud_or_hidden_interface(self):
        for name in ('health_shield','inventory','minimap','compass'):
            r=scan(frame(),spec(name))
            self.assertEqual(r.status,'unknown',name)

    def test_resolution_independent_normalized_roi(self):
        for size in ((180,320),(360,640),(720,1280)):
            im=frame(size);out=crop_roi(im,ROI)
            self.assertAlmostEqual(out.shape[1]/size[1],.6,delta=.02)
            self.assertAlmostEqual(out.shape[0]/size[0],.6,delta=.02)

    def test_ocr_wrong_text_is_not_presence(self):
        r=scan(frame(),spec('lobby','ocr',expected_text='GIOCA',threshold=.81),ocr_reader=lambda crop:'SCEGLI MODALITA')
        self.assertNotEqual(r.status,'present')
        self.assertEqual(scan(frame(),spec('lobby','ocr',expected_text='GIOCA'),ocr_reader=lambda crop:'').status,'unknown')

    def test_missing_ocr_module_returns_error_not_absence(self):
        with patch('observer.visual_scanners._configure_tesseract',side_effect=ImportError('missing')):
            r=scan(frame(),spec('lobby','ocr',expected_text='GIOCA'))
        self.assertEqual(r.status,'error')

    def test_template_requires_reference_not_hallucinated(self):
        r=scan(frame(),spec('minimap','template'))
        self.assertEqual(r.status,'unknown')
        self.assertIn('mancante',r.detail)

    def test_model_optional_unknown_when_unavailable(self):
        r=scan(frame(),spec('lobby','model'))
        self.assertEqual(r.status,'unknown')

    def test_color_mask_and_options(self):
        im=frame();area=crop_roi(im,ROI);area[:]=(20,200,20)
        r=scan(im,spec('highlight','color',threshold=.65,options={'hsv_min':[30,70,60],'hsv_max':[90,255,255]}))
        self.assertEqual(r.status,'present')

class FusionTests(unittest.TestCase):
    def setUp(self):self.e=StateRecognitionEngine()
    def test_partial_hud_without_inventory_still_in_match(self):
        cfg=[spec('compass'),spec('minimap'),spec('inventory')]
        for t in (1,2):
            d=self.e.evaluate({'compass':evidence('compass',at=t),
                               'minimap':evidence('minimap',at=t),
                               'inventory':evidence('inventory','unknown',at=t)},cfg,t)
        self.assertEqual(d.state,'IN_MATCH')

    def test_no_duplicate_frame_can_confirm_a_transition(self):
        cfg=[spec('compass'),spec('minimap')]
        items={x:evidence(x,at=1) for x in ('compass','minimap')}
        self.assertEqual(self.e.evaluate(items,cfg,1).state,'UNKNOWN')
        self.assertEqual(self.e.evaluate(items,cfg,1).state,'UNKNOWN')
        items={x:evidence(x,at=2) for x in ('compass','minimap')}
        self.assertEqual(self.e.evaluate(items,cfg,2).state,'IN_MATCH')

    def test_lobby_vs_hud_conflict_does_not_ignore_lobby(self):
        cfg=[spec('compass'),spec('minimap'),spec('lobby','ocr',state='LOBBY')]
        for t in (1,2):
            d=self.e.evaluate({k:evidence(k,at=t) for k in ('compass','minimap','lobby')},cfg,t)
        self.assertNotEqual(d.state,'IN_MATCH')

    def test_strong_elimination_and_return_take_priority(self):
        for name,state in [('elimination','ELIMINATED'),('return_to_lobby','RETURNING_TO_LOBBY')]:
            e=StateRecognitionEngine()
            for t in (1,2):d=e.evaluate({name:evidence(name,at=t)},[spec(name,'ocr',state=state,weight=2.)],t)
            self.assertEqual(d.state,state)

    def test_temporal_hysteresis_and_unknown_timeout(self):
        cfg=[spec('compass'),spec('minimap')]
        for t in (1,2):d=self.e.evaluate({k:evidence(k,at=t) for k in ('compass','minimap')},cfg,t)
        self.assertEqual(d.state,'IN_MATCH')
        for t in (3,4):d=self.e.evaluate({k:evidence(k,'unknown',at=t) for k in ('compass','minimap')},cfg,t)
        self.assertEqual(d.state,'IN_MATCH')
        d=self.e.evaluate({k:evidence(k,'unknown',at=13) for k in ('compass','minimap')},cfg,13)
        self.assertEqual(d.state,'UNKNOWN')

    def test_only_one_hud_anchor_stays_unknown(self):
        d=self.e.evaluate({'compass':evidence('compass',at=1)},[spec('compass')],1)
        self.assertEqual(d.state,'UNKNOWN')

class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=ScannerStore(self.root)
    def test_seeded_presets_exist_disabled_by_default(self):
        configs=self.db.list()
        self.assertTrue(all(k in {x['id'] for x in configs} for k in PRESETS))
        self.assertTrue(all(not x['enabled'] for x in configs))
    def test_update_and_reopen_does_not_touch_legacy_training(self):
        import sqlite3
        with closing(sqlite3.connect(self.db.path)) as conn, conn:
            conn.execute('CREATE TABLE old_training (action TEXT)');conn.execute("INSERT INTO old_training VALUES ('forward')")
        self.db.save({**spec('compass'),'threshold':.31})
        self.assertEqual(ScannerStore(self.root).get('compass')['threshold'],.31)
        with closing(sqlite3.connect(self.db.path)) as conn, conn:
            self.assertEqual(conn.execute('SELECT action FROM old_training').fetchone()[0],'forward')
    def test_export_import_restore_defaults(self):
        self.db.save(spec('minimap',threshold=.47))
        exported=self.db.export();new_root=self.root/'other';other=ScannerStore(new_root)
        other.import_config(exported)
        self.assertAlmostEqual(other.get('minimap')['threshold'],.47)
        other.reset('minimap')
        self.assertNotAlmostEqual(other.get('minimap')['threshold'],.47)
    def test_positive_negative_examples_can_be_reviewed_and_deleted(self):
        self.db.save(spec('lobby','ocr',expected_text='GIOCA'))
        image=frame();p=self.db.add_example('lobby',image,True);n=self.db.add_example('lobby',image,False)
        examples=self.db.examples('lobby')
        self.assertEqual(len(examples),2)
        self.assertGreater(len(self.db.example_image(p['id'])),100)
        result=self.db.evaluate('lobby',reader=lambda _: 'GIOCA')
        self.assertEqual(result['total'],2)
        self.assertEqual(result['correct'],1)
        self.assertTrue(self.db.delete_example(n['id']))
        self.assertEqual(len(self.db.examples('lobby')),1)
    def test_wrong_roi_or_id_rejected(self):
        with self.assertRaises(ValueError):self.db.save(spec('bad\nscanner'))
        with self.assertRaises(ValueError):self.db.save(spec('good',roi=[-.4,0,1,1]))

class P0IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def test_quick_gate_does_not_consider_unknown_absent(self):
        c=SingleMatchController(quick_start=True,quick_wait_play_exit=True,repeat=True)
        c.arm_quick(0)
        def step(t,value):
            return c.decide(now=t,frame_at=t,scene='unknown',menu=None,candidate=Action('wait'),
                            authorized=False,locked=False,spectator=False,reward_scan_confirmed=True,play_visible=value)
        step(1,True)
        for t in (2,3,4,5,6):self.assertEqual(step(t,None).name,'wait')
        self.assertFalse(c.quick_departed)
        for t in (7,8,9):self.assertEqual(step(t,False).name,'wait')
        self.assertTrue(c.quick_departed)
    def test_new_profile_only_gioca_is_accepted_by_readiness(self):
        d=Dashboard(self.root)
        self.addCleanup(d.shutdown)
        store=ScreenProfiles(self.root);store.select(1920,1080)
        store.add_rule('gioca',[.7,.7,.8,.8],image_size=(1920,1080),expected_text='GIOCA')
        # The readiness gate requires a working OCR executable. This is a
        # unit test of the taught profile, not of the local Tesseract install.
        with patch('oye_web_app.ocr_available', return_value=True):
            self.assertIn('gioca',d.readiness()['present'])
        with patch('oye_web_app.ocr_available', return_value=False):
            self.assertNotIn('gioca',d.readiness()['present'])
            self.assertFalse(d.readiness()['quick_ready'])
    def test_selected_profile_overrides_corrupted_legacy_png(self):
        legacy=self.root/'templates';legacy.mkdir();(legacy/'gioca.png').write_bytes(b'corrupt')
        store=ScreenProfiles(self.root);store.select(1920,1080)
        selected=store.assets_dir();selected.mkdir(parents=True)
        noisy=np.random.default_rng(123).integers(0,256,(200,200,3),dtype=np.uint8)
        cv2.imwrite(str(selected/'gioca.png'),noisy)
        vision=Vision(legacy,profile_folder=selected)
        self.assertIn('gioca',vision.templates)
    def test_worker_uses_cv_at_any_resolution_without_second_capture(self):
        db=ScannerStore(self.root)
        db.save(spec('compass',enabled=True,threshold=.05,interval=.25))
        ScreenProfiles(self.root).select(1920,1080)
        worker=ScreenRegionOCRWorker(self.root,allow_ocr=False)
        worker.start()
        try:
            im=frame()
            area=crop_roi(im,ROI);area[:]=np.random.default_rng(7).integers(0,255,area.shape,dtype=np.uint8)
            self.assertTrue(worker.submit(im,1))
            out=None
            for _ in range(60):
                out=worker.take()
                if out:break
                time.sleep(.025)
            self.assertIsNotNone(out)
            self.assertEqual(out['status'],'resolution_mismatch')
            self.assertEqual(out['scanner_readings'],{})  # Risoluzione diversa: nessuna evidenza autorevole
        finally:worker.stop()
    def test_replay_offline_stabilizes_across_frames(self):
        self.db=ScannerStore(self.root)
        self.db.save(spec('lobby','ocr',expected_text='GIOCA',state='LOBBY',weight=2,threshold=.72))
        frames=[]
        for i in range(3):
            image=frame();path=self.root/f'{i:03d}.png';cv2.imwrite(str(path),image);frames.append((path,float(i+1)))
        output=replay(frames,root=self.root,reader=lambda _:'GIOCA')
        self.assertEqual(len(output),3)
        self.assertEqual(output[-1]['state']['state'],'LOBBY')
        self.assertEqual(output[-1]['scanners']['lobby']['status'],'present')

if __name__=='__main__':unittest.main()
