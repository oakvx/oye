"""Offline API integration checks for Oye's v40 scanner page, without LIVE inputs."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import numpy as np

from oye_web_app import Dashboard, LocalServer


class ScannerHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dashboard = Dashboard(Path(cls.tmp.name))
        cls.token = 'test-v40-only-token'
        cls.server = LocalServer(('127.0.0.1', 0), cls.dashboard, cls.token)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:%d' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.dashboard.shutdown()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.tmp.cleanup()

    @classmethod
    def request(cls, path, data=None, authorized=True):
        headers = {'X-Oye-Token': cls.token} if authorized else {}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(cls.url + path,
            data=json.dumps(data).encode('utf8') if data is not None else None,
            headers=headers, method='POST' if data is not None else 'GET')
        try:
            with urllib.request.urlopen(req, timeout=6) as res:
                payload = res.read()
                return res.status, payload if res.headers['Content-Type'].startswith('image/') else json.loads(payload)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_01_scanner_routes_static_auth_and_initialization(self):
        with urllib.request.urlopen(self.url + '/scanners', timeout=6) as res:
            self.assertEqual(res.status, 200)
            self.assertIn(b'Scanner', res.read())
        self.assertEqual(self.request('/api/scanners', authorized=False)[0], 403)
        code, result = self.request('/api/scanners')
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(result['specs']), 12)
        self.assertTrue(all(not v['enabled'] for v in result['specs']))
        self.assertEqual(result['debug']['status'], 'offline')

    def test_02_config_test_example_export_and_delete(self):
        self.dashboard.calibration_frame = np.zeros((150,300,3),dtype=np.uint8)
        code, spec = self.request('/api/scanners/save', {'id':'test_40','name':'Test 40',
            'method':'structure','roi':[.2,.1,.8,.9],'state':'IN_MATCH',
            'enabled':True,'threshold':.6,'weight':1.0,'interval':.5})
        self.assertEqual(code, 200)
        self.assertEqual(spec['id'], 'test_40')
        self.assertEqual(self.request('/api/scanners/test', {'id':'test_40'})[0], 200)
        code, ex = self.request('/api/scanners/example', {'id':'test_40','positive':False})
        self.assertEqual(code, 200)
        self.assertEqual(self.request('/api/scanners/evaluate?id=test_40')[1]['total'], 1)
        self.assertEqual(self.request('/api/scanners/example/image?id=%d'%ex['id'])[0], 200)
        self.assertEqual(self.request('/api/scanners/crop?id=test_40')[0], 200)
        exported=self.request('/api/scanners/export')[1]
        self.assertEqual(exported['schema'], 1)
        self.assertEqual(self.request('/api/scanners/example/delete',{'id':ex['id']})[0],200)
        self.assertEqual(self.request('/api/scanners/delete',{'id':'test_40'})[1]['removed'],True)
        self.assertEqual(self.request('/api/scanners/import',exported)[0],200)
        self.assertEqual(self.request('/api/scanners',authorized=False)[0],403)

if __name__ == '__main__': unittest.main()
