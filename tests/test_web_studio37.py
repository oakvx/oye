"""Studio 37: end-to-end data flow for the local web editor. No LIVE input."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

import cv2
import numpy as np

from observer.config import save, validate
from observer.learning import LearningStore, Prediction, image_features
from observer.ocr import OCRResult
from oye_web_app import Dashboard, LocalServer

class WebStudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'templates').mkdir()
        save(self.root/'config.json',validate({}))
        self.store=LearningStore(self.root/'learning')
        self.ids=[]
        for n in range(3):
            frame=np.random.default_rng(n+14).integers(0,255,(80,144,3),dtype=np.uint8)
            frame_id,_=self.store.add(session='partita-A',frame=frame,process='test.exe',scene='match',
                prediction=Prediction('movimento',.82,'test'),ocr=OCRResult('',(),'test',0),
                feature=image_features(frame,'','match'),
                captured_at=f'2026-11-22T12:00:0{n}+00:00')
            self.ids.append(frame_id)
        self.dashboard=Dashboard(self.root)
        self.server=LocalServer(('127.0.0.1',0),self.dashboard,'test')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
    def tearDown(self):
        self.conn.close();self.server.shutdown();self.server.server_close();self.thread.join(2)
        self.dashboard.shutdown();self.temp.cleanup()
    def req(self,method,path,payload=None,token=True):
        headers={'X-Oye-Token':'test'} if token else {}
        if payload is not None:
            headers['Content-Type']='application/json';payload=json.dumps(payload,ensure_ascii=False)
        self.conn.request(method,path,body=payload,headers=headers)
        result=self.conn.getresponse();return result.status,result.getheader('Content-Type'),result.read()
    def test_archive_keeps_existing_training_and_returns_usable_images(self):
        status,_,raw=self.req('GET','/api/learning')
        self.assertEqual(status,200)
        data=json.loads(raw)
        self.assertEqual(data['stats']['frames'],3)
        self.assertEqual(data['sessions'],['partita-A'])
        self.assertEqual(len(data['frames']),3)
        self.assertNotIn('feature',data['frames'][0])
        frame=data['frames'][0]
        status,kind,raw=self.req('GET',f'/api/learning/image?id={frame["id"]}')
        self.assertEqual(status,200);self.assertEqual(kind,'image/jpeg')
        self.assertIsNotNone(cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR))
        self.assertEqual(self.store.stats()['frames'],3)
    def test_event_action_interval_and_export_are_real_sqlite_changes(self):
        status,_,_=self.req('POST','/api/learning/edit',{'operation':'event','id':self.ids[0],'label':'Salto','note':'Verificato'})
        self.assertEqual(status,200)
        status,_,_=self.req('POST','/api/learning/edit',{'operation':'action','id':self.ids[1],'action':'forward_jump'})
        self.assertEqual(status,200)
        rows={r['id']:r for r in self.store.recent(10,'partita-A')}
        self.assertEqual(rows[self.ids[0]]['user_label'],'Salto')
        self.assertEqual(rows[self.ids[1]]['action_label'],'forward_jump')
        status,_,_=self.req('POST','/api/learning/edit',{'operation':'interval','session':'partita-A',
            'start':'2026-11-22T12:00:00+00:00','end':'2026-11-22T12:00:02+00:00','event':'movimento'})
        self.assertEqual(status,200)
        self.assertEqual(self.store.stats()['labeled'],3)
        status,kind,raw=self.req('GET','/api/learning/export?session=partita-A')
        self.assertEqual(status,200);self.assertIn('ndjson',kind)
        self.assertEqual(len(raw.strip().splitlines()),3)
        self.assertNotIn(b'feature',raw)
    def test_security_no_read_or_write_without_token(self):
        self.assertEqual(self.req('GET','/api/learning',token=False)[0],403)
        self.assertEqual(self.req('GET',f'/api/learning/image?id={self.ids[0]}',token=False)[0],403)
        self.assertEqual(self.req('POST','/api/learning/edit',{'operation':'event','id':self.ids[0],'label':'test'},token=False)[0],403)
        self.assertEqual(self.req('GET','/api/learning/image?id=-1')[0],404)
        self.assertEqual(self.req('POST','/api/learning/edit',{'operation':'action','id':self.ids[0],'action':'invalid_action'})[0],400)
    def test_editor_assets_and_diagnostics_require_consent(self):
        status,_,js=self.req('GET','/studio37.js',token=False)
        self.assertEqual(status,200);self.assertIn(b'renderTimeline',js)
        status,_,body=self.req('POST','/api/input-test',{'kind':'forward','confirmed':False})
        self.assertEqual(status,400);self.assertIn('Conferma',body.decode())

if __name__=='__main__':unittest.main()
