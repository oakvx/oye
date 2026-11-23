"""Training review API with local authentication and a real SQLite file."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from oye_web_app import Dashboard, LocalServer
from observer.learning import LearningStore


class TrainingHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        cls.store=LearningStore(cls.root/'learning')
        frame=np.zeros((90,160,3),dtype=np.uint8)
        frame[:,:,1]=np.tile(np.arange(160,dtype=np.uint8),(90,1))
        cls.frame_id=cls.store.record_demonstration(frame,session='train01',action='jump',
            process='Fortnite.exe',scene='unknown')[0]
        cls.dash=Dashboard(cls.root)
        cls.token='v44-demo-local-test'
        cls.server=LocalServer(('127.0.0.1',0),cls.dash,cls.token)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.dash.shutdown();cls.server.shutdown();cls.server.server_close()
        cls.thread.join(2);cls.temp.cleanup()

    @classmethod
    def get(cls,path,token=True):
        req=urllib.request.Request(cls.url+path,headers={'X-Oye-Token':cls.token} if token else {})
        try:
            with urllib.request.urlopen(req,timeout=4) as response:
                return response.status,response.read(),response.headers.get('Content-Type','')
        except urllib.error.HTTPError as e:
            return e.code,e.read(),e.headers.get('Content-Type','')

    @classmethod
    def post(cls,path,data,token=True):
        req=urllib.request.Request(cls.url+path,
            data=json.dumps(data).encode('utf-8'),
            headers=({'X-Oye-Token':cls.token} if token else {})|{'Content-Type':'application/json'},
            method='POST')
        try:
            with urllib.request.urlopen(req,timeout=4) as response:
                return response.status,json.loads(response.read())
        except urllib.error.HTTPError as e:
            return e.code,json.loads(e.read())

    def test_01_static_training_assets(self):
        for path,expected in (('/studio',b'training44'),('/training44.js',b'Oye 44'),('/training44.css',b'training44')):
            status,data,content_type=self.get(path,token=False)
            self.assertEqual(status,200)
            self.assertIn(expected,data)
            self.assertIn('text/',content_type)

    def test_02_review_requires_token(self):
        self.assertEqual(self.get('/api/learning/review',token=False)[0],403)
        self.assertEqual(self.post('/api/learning/review',{'session':'train01','id':self.frame_id,'decision':'exclude'},token=False)[0],403)

    def test_03_review_image_approve_and_exclude(self):
        code,data,_=self.get('/api/learning/review?session=train01')
        self.assertEqual(code,200)
        result=json.loads(data)
        self.assertEqual(result['pending'],1)
        self.assertTrue(result['items'][0]['has_image'])
        self.assertIn('jump',result['available_actions'])
        status,content,mime=self.get('/api/learning/image?id='+str(self.frame_id))
        self.assertEqual(status,200);self.assertIn('jpeg',mime);self.assertGreater(len(content),50)
        code,ack=self.post('/api/learning/review',dict(id=self.frame_id,session='train01',decision='accept'))
        self.assertEqual(code,200,ack)
        self.assertEqual(len(self.store.action_training_samples()),1)
        code,ack=self.post('/api/learning/review',dict(id=self.frame_id,session='train01',decision='exclude'))
        self.assertEqual(code,200,ack)
        self.assertEqual(len(self.store.action_training_samples()),0)

    def test_04_old_bulk_confirmation_disallowed(self):
        code,data=self.post('/api/learning/confirm-latest',{})
        self.assertEqual(code,400)
        self.assertIn('blocco',data['error'])

if __name__=='__main__':unittest.main()
