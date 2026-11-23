"""v41 failure-driven regression tests; no Fortnite or synthetic input required."""
from __future__ import annotations
import base64
import tempfile
import unittest
from pathlib import Path
import cv2
import numpy as np
from observer.visual_scanners import scan,ScannerResult
from observer.state_recognition import StateRecognitionEngine
from observer.scanner_store import ScannerStore
from observer.scanner_scheduler import ScannerScheduler
from observer.screen_profiles import ScreenProfiles
from oye_web_app import Dashboard
from replay_scanners import replay

ROI=[.12,.12,.88,.88]

def spec(id_,method='structure',**changes):
    return {'id':id_,'name':id_,'description':'test','roi':ROI,'method':method,
            'enabled':True,'threshold':.72,'interval':.5,'weight':1.,'state':'IN_MATCH',
            'options':{},'expected_text':'',**changes}

def positive(id_,at):
    return ScannerResult(id_,at,at,'present',.97,'structure',tuple(ROI),2)

class StructuralSafetyTests(unittest.TestCase):
    def test_white_noise_is_not_hud(self):
        for seed in range(5):
            frame=np.random.default_rng(seed).integers(0,256,(320,540,3),dtype=np.uint8)
            for id_ in ('compass','minimap','health_shield','inventory'):
                detected=scan(frame,spec(id_))
                self.assertNotEqual(detected.status,'present',f'{id_} seed={seed}')
                self.assertLess(detected.confidence,.72)

    def test_structure_needs_both_labels_before_global_authority(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ScannerStore(folder)
            self.assertNotIn('compass',store.structural_validations())
            store.save(spec('compass'))
            frame=np.zeros((180,320,3),dtype=np.uint8)
            store.add_example('compass',frame,True)
            self.assertNotIn('compass',store.structural_validations())
            store.add_example('compass',frame,False)
            self.assertIn('compass',store.structural_validations())

class ExpirationTests(unittest.TestCase):
    def test_stale_state_expires_without_new_frames(self):
        eng=StateRecognitionEngine(unknown_timeout=9)
        rules=[spec('compass'),spec('minimap')]
        for t in (1.,2.):
            d=eng.evaluate({'compass':positive('compass',t),
                            'minimap':positive('minimap',t)},rules,t)
        self.assertEqual(d.state,'IN_MATCH')
        d=eng.evaluate({'compass':positive('compass',2.),
                        'minimap':positive('minimap',2.)},rules,120.)
        self.assertEqual(d.state,'UNKNOWN')
        self.assertIn('scaduto',d.reason)

    def test_long_processing_delay_never_confirms_old_frame(self):
        eng=StateRecognitionEngine()
        rules=[spec('compass'),spec('minimap')]
        for t in (1.,2.):
            result=eng.evaluate({'compass':positive('compass',t),
                                 'minimap':positive('minimap',t)},rules,t+30.)
        self.assertEqual(result.state,'UNKNOWN')

class PersistenceAtomicityTests(unittest.TestCase):
    def test_import_rejects_all_on_second_bad_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ScannerStore(folder)
            original=store.get('minimap')
            valid={**original,'threshold':.42}
            bad={**store.get('inventory'),'roi':[-1,0,1,1]}
            with self.assertRaises(ValueError):
                store.import_config({'schema':1,'scanners':[valid,bad]})
            self.assertEqual(store.get('minimap')['threshold'],original['threshold'])

    def test_import_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ScannerStore(folder)
            cfg=store.get('minimap')
            with self.assertRaises(ValueError):
                store.import_config({'schema':1,'scanners':[cfg,cfg]})

class UnsavedEditorTests(unittest.TestCase):
    def test_draft_test_uses_new_roi_without_persisting_it(self):
        with tempfile.TemporaryDirectory() as folder:
            dashboard=Dashboard(Path(folder))
            try:
                im=np.zeros((200,400,3),dtype=np.uint8)
                cv2.rectangle(im,(140,50),(330,160),(140,200,245),-1)
                dashboard.calibration_frame=im
                saved=ScannerStore(folder).save(spec('new_sensor',roi=[.02,.02,.22,.22]))
                new={**saved,'roi':[.33,.2,.83,.87]}
                response=dashboard.scanner_test(new)
                self.assertTrue(response['tested_unsaved_draft'])
                blob=base64.b64decode(response['preview_jpeg'])
                preview=cv2.imdecode(np.frombuffer(blob,dtype=np.uint8),cv2.IMREAD_COLOR)
                self.assertEqual(preview.shape[1],200)
                self.assertEqual(preview.shape[0],134)
                self.assertEqual(ScannerStore(folder).get('new_sensor')['roi'],saved['roi'])
            finally:
                dashboard.shutdown()

class SchedulerReplayTests(unittest.TestCase):
    def test_bounded_ocr_and_no_starvation(self):
        planner=ScannerScheduler(max_visual=2,max_ocr=1)
        settings=[spec('lobby','ocr',state='LOBBY',interval=1),
                  spec('reward_title','ocr',state='RETURNING_TO_LOBBY',interval=1),
                  spec('elimination','ocr',state='ELIMINATED',interval=1),
                  spec('compass',interval=1),spec('inventory',interval=1),spec('minimap',interval=1)]
        runs={x['id']:0 for x in settings}
        for t in range(25):
            selected=planner.pick(settings,float(t),'post_lobby')
            self.assertLessEqual(sum(s['method']=='ocr' for s in selected),1)
            self.assertLessEqual(sum(s['method']!='ocr' for s in selected),2)
            for s in selected:runs[s['id']]+=1
        self.assertTrue(all(v>0 for v in runs.values()),runs)

    def test_offline_replay_obeys_live_scheduler_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=ScannerStore(root)
            for name in ('lobby','loading','elimination'):
                store.save(spec(name,'ocr',expected_text='GIOCA',interval=1.0,
                                state='LOBBY' if name=='lobby' else 'LOADING'))
            paths=[]
            for t in range(7):
                image=np.zeros((180,320,3),dtype=np.uint8)
                name=root/f'{t}.png';cv2.imwrite(str(name),image)
                paths.append((name,float(t)))
            rows=replay(paths,root=root,reader=lambda _:'GIOCA')
            self.assertEqual(len(rows),7)
            self.assertTrue(all(row['scheduler']['scan_count']<=1 for row in rows))
            scanned={k for row in rows for k in row['scanners']}
            self.assertEqual(scanned,{'lobby','loading','elimination'})

if __name__=='__main__':unittest.main()
