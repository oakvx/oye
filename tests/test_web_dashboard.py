from _helpers import put_template
"""Regressions for v25 local web controller, calibration and mouse mapping."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import cv2
import numpy as np

from observer.config import load, save, validate
from observer.models import Action
from observer.driver import Driver
from oye_web_app import Dashboard, LocalServer


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'templates').mkdir()
        save(self.root/'config.json', validate({}))
        self.dashboard = Dashboard(self.root)

    def tearDown(self):
        self.dashboard.shutdown()
        self.tmp.cleanup()

    def test_readiness_guides_minimal_calibrations(self):
        r = self.dashboard.readiness()
        self.assertFalse(r['ready'])
        self.assertEqual(len(r['missing']), 5 if r['rewards_ocr'] else 6)
        for name in ('gioca','continua','torna_lobby','spettatore_prossimo','spettatore_precedente','hud_minimappa'):
            put_template(self.root/'templates', name)
        r = self.dashboard.readiness()
        self.assertEqual(r['missing'], ['hud_salute'])
        put_template(self.root/'templates', 'hud_inventario')
        self.assertTrue(self.dashboard.readiness()['ready'])

    def test_live_requires_consent_and_calibration(self):
        with self.assertRaisesRegex(ValueError,'Conferma'):
            self.dashboard.start('single_match', False)
        with self.assertRaisesRegex(ValueError,'calibrazione'):
            self.dashboard.start('single_match', True)
        self.assertFalse(self.dashboard.get_status()['running'])

    def test_settings_are_validated_and_do_not_enable_live(self):
        values = self.dashboard.update_settings({'jump_seconds': 150,'jump_interval':2.4,'profile':'esploratore',
                                                  'harvest':False,'camera_pixels':200,'ocr_enabled':True})
        self.assertEqual(values['settings']['jump_seconds'],150)
        self.assertEqual(values['settings']['keys']['forward'],'w')
        self.assertEqual(values['settings']['mouse']['secondary'],'right')
        self.assertFalse(load(self.root/'config.json')['autonomous_enabled'])
        with self.assertRaisesRegex(ValueError,'Impostazioni'):
            self.dashboard.update_settings({'autonomous_enabled':True})
        with self.assertRaisesRegex(ValueError,'non valido'):
            self.dashboard.update_settings({'jump_seconds':200})

    def test_calibration_upload_crop_and_status(self):
        rng = np.random.default_rng(42)
        frame = rng.integers(0, 255, (270,440,3), dtype=np.uint8)
        ok, encoded = cv2.imencode('.png',frame)
        self.assertTrue(ok)
        self.dashboard.upload_calibration(encoded.tobytes())
        self.assertEqual(self.dashboard.get_status()['calibration']['image_serial'],1)
        self.dashboard.save_calibration('gioca',[.2,.2,.6,.5])
        self.assertTrue((self.root/'templates'/'gioca.png').exists())
        self.assertTrue((self.root/'templates'/'_rois.json').exists())
        with self.assertRaises(ValueError):
            self.dashboard.save_calibration('gioca',[.7,.5,.3,.2])

    def test_local_http_authentication_and_control(self):
        server = LocalServer(('127.0.0.1',0),self.dashboard,'secret-token')
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        conn = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            conn.request('GET','/')
            res = conn.getresponse()
            self.assertEqual(res.status,200)
            self.assertIn(b'Una partita',res.read())
            conn.request('GET','/api/status')
            res = conn.getresponse();self.assertEqual(res.status,403);res.read()
            conn.request('GET','/api/status',headers={'X-Oye-Token':'secret-token'})
            res = conn.getresponse();self.assertEqual(res.status,200);self.assertIn(b'"running": false',res.read())
            conn.request('POST','/api/settings',body='{"jump_seconds":140}',headers={
                'X-Oye-Token':'secret-token','Content-Type':'application/json',
                'Origin':'https://evil.example'})
            res = conn.getresponse();self.assertEqual(res.status,403);res.read()
            conn.request('POST','/api/settings',body='{"jump_seconds":140}',headers={
                'X-Oye-Token':'secret-token','Content-Type':'application/json'})
            res = conn.getresponse();self.assertEqual(res.status,200);res.read()
            self.assertEqual(load(self.root/'config.json')['single_match_jump_seconds'],140)
        finally:
            conn.close();server.shutdown();server.server_close();thread.join(2)


class MouseTests(unittest.TestCase):
    def test_right_click_is_released_exactly(self):
        class GUI:
            FAILSAFE=True
            def __init__(self):self.events=[]
            def mouseDown(self, **kw):self.events.append(('down',kw.get('button','left')))
            def mouseUp(self, **kw):self.events.append(('up',kw.get('button','left')))
        gui = GUI()
        driver=Driver(live=False,allowed=lambda:True,config={'keys':{}},
                      stop=threading.Event(),paused=threading.Event())
        driver.gui=gui
        driver._sleep=lambda _: None
        self.assertTrue(driver.run(Action('secondary_click', .1)))
        self.assertEqual(gui.events,[('down','right'),('up','right')])
        self.assertFalse(driver.mouse_is_down)


if __name__=='__main__':unittest.main()
