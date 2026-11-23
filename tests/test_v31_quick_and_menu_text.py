"""Regression tests: teaching button text, partial OCR, and timed quick loop."""
from __future__ import annotations
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import cv2
import numpy as np

from observer.models import Action
from observer.menu_text import fragment_score, learn_reference, match_reference, load_references, update_label
from observer.reward_ocr import RewardOCRWorker
from observer.single_match import SingleMatchController
from observer.config import validate, save
from oye_web_app import Dashboard


def image():
    out = np.zeros((720, 1280, 3), dtype=np.uint8)
    out[:] = (21,28,36)
    cv2.rectangle(out,(445,510),(810,570),(205,132,61),-1)
    cv2.putText(out,'RITORNA ALLA LOBBY',(465,551),cv2.FONT_HERSHEY_SIMPLEX,
                .8,(250,250,250),2,cv2.LINE_AA)
    return out


class TextAnchors(unittest.TestCase):
    def test_partial_italian_text_only(self):
        self.assertGreater(fragment_score('LOBB', ['torna alla lobby']), .8)
        self.assertGreater(fragment_score('RITOR', ['ritorna alla lobby']), .8)
        self.assertGreater(fragment_score('CONTINU', ['continua']), .8)
        self.assertEqual(fragment_score('LO', ['torna alla lobby']), 0)
        self.assertEqual(fragment_score('premi salto', ['torna alla lobby']), 0)

    def test_stable_region_and_missing_other_region(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t)
            frame=image()
            learn_reference(folder,'torna_lobby',frame.shape,(445,510,810,570),'RITORNA ALLA LOBBY')
            entry=load_references(folder)['torna_lobby']
            hit=match_reference(frame,entry,'torna_lobby',lambda crop:'RITOR LOBB')
            self.assertIsNotNone(hit)
            self.assertEqual(match_reference(frame,entry,'torna_lobby',lambda crop:'EQUIPAGGIA'),None)
            self.assertEqual(match_reference(frame,entry,'torna_lobby',lambda crop:'LO'),None)
            before=entry['rect']
            update_label(folder,'torna_lobby','Ritorna alla lobby')
            self.assertEqual(load_references(folder)['torna_lobby']['rect'],before)

    def test_missing_and_corrupt_data_fail_closed(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);(folder/'_menu_text.json').write_text('{nonsense')
            self.assertEqual(load_references(folder),{})
            with self.assertRaises(ValueError):
                learn_reference(folder,'equipaggia',image().shape,(1,2,60,50))

    def test_actual_tesseract_on_rendered_button(self):
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
        except Exception:
            self.skipTest('Tesseract unavailable')
        with tempfile.TemporaryDirectory() as t:
            frame=image()
            ref=learn_reference(Path(t),'torna_lobby',frame.shape,(445,510,810,570))
            def ocr(crop):
                gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
                return pytesseract.image_to_string(gray,config='--psm 7',timeout=4)
            hit=match_reference(frame,ref,'torna_lobby',ocr)
            self.assertIsNotNone(hit,'Tesseract must read this synthetic sample')


class QuickStart(unittest.TestCase):
    def setUp(self):
        self.c=SingleMatchController(quick_start=True,repeat=True)
        self.clock=10.
    def tick(self,scene='unknown',delta=1.,menu=None,locked=False,spectator=False):
        self.clock+=delta
        return self.c.decide(now=self.clock,frame_at=self.clock,scene=scene,
           menu=menu,candidate=Action('wait'),authorized=False,locked=locked,
           spectator=spectator)

    def test_starts_without_hud_after_play_and_wait(self):
        self.c.on_executed(Action('menu_click'),'gioca',self.clock)
        self.assertEqual(self.tick(delta=19.).name,'wait')
        a=self.tick(delta=1.)
        self.assertEqual(a.name,'jump')
        self.assertEqual(self.c.stage,'jumping')
        self.assertTrue(self.c.has_entered)
        action=self.tick(delta=1.)
        self.assertNotEqual(action.reason,'HUD non confermato: niente movimento alla cieca')

    def test_never_act_in_loading_or_spectator(self):
        self.c.on_executed(Action('menu_click'),'gioca',self.clock)
        # Loading must not delay the explicitly armed timer past 20 seconds.
        self.assertEqual(self.tick('loading',25.).name,'jump')
        self.assertEqual(self.c.stage,'jumping')
        self.assertEqual(self.tick('spectator',1.,spectator=True).name,'wait')
        self.assertEqual(self.tick('unknown',1.,locked=True).name,'wait')
        self.assertNotEqual(self.tick('unknown',1.).name,'wait')
        self.assertEqual(self.tick('spectator',1.,spectator=True).name,'wait')

    def test_lobby_label_cannot_block_a_manually_started_ignorante_timer(self):
        self.c.on_executed(Action('menu_click'),'gioca',self.clock)
        self.assertEqual(self.tick('lobby',25.,menu='gioca').name,'jump')
        self.assertEqual(self.c.stage,'jumping')


class TeachingFlow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        save(self.root/'config.json',validate({}))
        self.d=Dashboard(self.root)
    def tearDown(self):
        self.d.shutdown();self.tmp.cleanup()

    def test_teaching_menu_without_hud(self):
        self.d.teach_start('gioca')
        frame=image()
        self.d._set_calibration(frame,'synthetic')
        with mock.patch('oye_web_app.suggest',return_value=(None,'ocr_non_trovato')):
            self.d._try_teach_frame(frame)
        self.d.teach_pick([.50,.74])
        self.assertEqual(self.d.teach_target,'torna_lobby')
        self.assertTrue((self.root/'templates'/'_menu_text.json').exists())
        self.assertTrue(self.d.readiness()['quick_ready'])
        self.d.save_menu_text('gioca','GIOCA')
        self.assertEqual(self.d.readiness()['menu_text']['gioca']['label'],'gioca')

    def test_reteach_optional_button_after_ready(self):
        from _helpers import put_template
        (self.root/'templates').mkdir(exist_ok=True)
        for name in ('gioca','riscatta','spettatore_prossimo','spettatore_precedente','hud_salute','hud_inventario'):
            put_template(self.root/'templates',name)
        self.assertTrue(self.d.readiness()['ready'])
        status=self.d.teach_start('torna_lobby')
        self.assertEqual(status['teach']['target'],'torna_lobby')
        self.assertTrue(status['teach']['active'])


if __name__=='__main__': unittest.main()
