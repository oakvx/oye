from _helpers import put_template
"""Wizard regression tests: never silently calibrate the wrong screen."""
from pathlib import Path
import tempfile
import time
import threading
import http.client
import json
import unittest
from unittest import mock

import cv2
import numpy as np

from observer.config import save, validate
from observer.teach_ocr import _tokens_to_suggestions, point_crop, OCRSuggestion
from oye_web_app import Dashboard, LocalServer


def frame():
    rng = np.random.default_rng(36)
    f = rng.integers(25, 180, (720, 1280, 3),dtype=np.uint8)
    return f


def mock_ocr(*words):
    text = [w[0] for w in words]
    xs = [w[1] for w in words]
    return { 'text':text, 'left':xs, 'top':[350]*len(words), 'width':[max(28,len(w[0])*15) for w in words],
             'height':[26]*len(words), 'conf':[w[2] if len(w)>2 else 94 for w in words],
             'block_num':[1]*len(words), 'par_num':[1]*len(words),'line_num':[1]*len(words)}


class OCRDetection(unittest.TestCase):
    def test_exact_high_confidence_button(self):
        o = _tokens_to_suggestions(mock_ocr(('GIOCA',820)), 'gioca', (720,1280))
        self.assertEqual(len(o),1)
        self.assertGreater(o[0].confidence,80)
        self.assertLess(o[0].rect[0],o[0].rect[2])

    def test_multiple_buttons_ambiguous(self):
        o = _tokens_to_suggestions(mock_ocr(('GIOCA',200),('GIOCA',820)),'gioca',(720,1280))
        self.assertEqual(len(o),2)

    def test_contiguous_phrases_only(self):
        o = _tokens_to_suggestions(mock_ocr(('TORNA',800),('ALLA',885),('LOBBY',960)),
                                   'torna_lobby',(720,1280))
        self.assertEqual(len(o),1)
        o = _tokens_to_suggestions(mock_ocr(('TORNA',100),('ALLA',450),('LOBBY',800)),
                                   'torna_lobby',(720,1280))
        self.assertEqual(o,[])

    def test_spectator_and_hud_not_ocr_guessed(self):
        for name in ('spettatore_prossimo','hud_salute','hud_minimappa'):
            self.assertFalse(_tokens_to_suggestions(mock_ocr(('GIOCA',820)),name,(720,1280)))

    def test_point_crop_bounded(self):
        f=frame()
        x1,y1,x2,y2 = point_crop(f,'hud_salute',[1.,1.])
        self.assertTrue(0<=x1<x2<=f.shape[1] and 0<=y1<y2<=f.shape[0])
        with self.assertRaises(ValueError):point_crop(f,'gioca',[2.,.3])
        with self.assertRaises(ValueError):point_crop(f,'gioca',[float('nan'),.3])


class TeachingDashboard(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'templates').mkdir()
        save(self.root/'config.json',validate({}))
        self.app=Dashboard(self.root)

    def tearDown(self):
        self.app.shutdown();self.tmp.cleanup()

    def test_progress_and_manual_point(self):
        start=self.app.teach_start()
        self.assertTrue(start['teach']['active'])
        self.assertEqual(start['teach']['target'],'gioca')
        with self.assertRaises(ValueError):self.app.teach_pick([.6,.5])
        self.app._set_calibration(frame(),'test screenshot')
        with mock.patch('oye_web_app.suggest',return_value=(None,'ocr_non_trovato')):
            self.app._try_teach_frame(self.app.calibration_frame)
        self.assertEqual(self.app.get_status()['teach']['stage'],'needs_point')
        after=self.app.teach_pick([.65,.55])
        self.assertEqual(after['teach']['last_completed'],'gioca')
        self.assertEqual(after['teach']['target'],'spettatore_prossimo')
        self.assertTrue((self.root/'templates'/'gioca.png').exists())
        self.assertIn('gioca',after['readiness']['present'])
        with self.assertRaisesRegex(ValueError,'Prima usa'):
            self.app.teach_pick([.2,.2])

    def test_auto_ocr_and_no_false_positive(self):
        self.app.teach_start('gioca')
        f=frame();self.app._set_calibration(f,'test')
        with mock.patch('oye_web_app.suggest',return_value=(OCRSuggestion((.60,.40,.80,.48),'gioca',94),'ocr')):
            self.app._try_teach_frame(f)
        self.assertEqual(self.app.get_status()['teach']['target'],'torna_lobby')
        self.assertTrue((self.root/'templates'/'gioca.png').exists())
        before=(self.root/'templates'/'continua.png')
        self.app._set_calibration(f,'test2')
        with mock.patch('oye_web_app.suggest',return_value=(None,'ocr_ambiguo')):
            self.app._try_teach_frame(f)
        self.assertFalse(before.exists())
        self.assertEqual(self.app.get_status()['teach']['stage'],'needs_point')

    def test_stale_ocr_result_must_not_save_after_switch(self):
        self.app.teach_start('gioca')
        f=frame();self.app._set_calibration(f,'test')
        def stale(*args):
            self.app.teach_start('continua')
            return OCRSuggestion((.4,.3,.7,.4),'gioca',98),'ocr'
        with mock.patch('oye_web_app.suggest',side_effect=stale):
            self.app._try_teach_frame(f)
        self.assertFalse((self.root/'templates'/'gioca.png').exists())
        self.assertEqual(self.app.get_status()['teach']['target'],'continua')

    def test_manual_advanced_save_advances_wizard(self):
        self.app.teach_start()
        self.app._set_calibration(frame(),'test')
        with self.assertRaisesRegex(ValueError,'screenshot precedente'):
            self.app.save_calibration('gioca',[.2,.2,.5,.5])
        with mock.patch('oye_web_app.suggest',return_value=(None,'ocr_non_trovato')):
            self.app._try_teach_frame(self.app.calibration_frame)
        self.app.save_calibration('gioca',[.2,.2,.5,.5])
        self.assertEqual(self.app.get_status()['teach']['target'],'spettatore_prossimo')

    def test_later_does_not_permit_live_inputs(self):
        self.app.teach_start();self.app.teach_later()
        self.assertFalse(self.app.get_status()['teach']['active'])
        self.assertFalse(self.app.readiness()['ready'])
        with self.assertRaisesRegex(ValueError,'Mi manca ancora'):
            self.app.start('single_match',True)

    def test_all_ready_finishes_and_never_starts_match(self):
        for name in ['gioca','continua','torna_lobby','spettatore_prossimo','spettatore_precedente',
                     'hud_salute','hud_inventario']:
            put_template(self.root/'templates', name)
        ret=self.app.teach_start()
        self.assertTrue(ret['readiness']['ready'])
        self.assertEqual(ret['teach']['stage'],'complete')
        self.assertFalse(ret['running'])

    def test_web_teach_endpoints_and_auth(self):
        server=LocalServer(('127.0.0.1',0),self.app,'test-token')
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        con=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
        try:
            con.request('POST','/api/teach/start',body='{}',headers={'Content-Type':'application/json'})
            r=con.getresponse();self.assertEqual(r.status,403);r.read()
            con.request('POST','/api/teach/start',body='{}',headers={'X-Oye-Token':'test-token'})
            r=con.getresponse();self.assertEqual(r.status,200);self.assertIn(b'"teach"',r.read())
            con.request('POST','/api/teach/pick',body=json.dumps({'point':[.5,.5]}),headers={'X-Oye-Token':'test-token'})
            r=con.getresponse();self.assertEqual(r.status,400);self.assertIn(b'Prima usa',r.read())
        finally:
            con.close();server.shutdown();server.server_close();worker.join(2)


if __name__=='__main__':unittest.main()
