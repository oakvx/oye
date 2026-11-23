"""Oye v43: actual OCR discoveries are distinct from repeated OCR frames."""
from pathlib import Path
import tempfile

from observer.ocr_detection_counter import OCRDetectionCounter


def test_confirmed_appearance_counted_once_even_for_many_frames():
    with tempfile.TemporaryDirectory() as td:
        meter=OCRDetectionCounter(Path(td))
        for i in range(30):
            assert meter.observe('gioca',at=i+1.,status='ok',matched=True,
                text='GIOCA',confidence=.95,label='Gioca') == (i == 1)
        status=meter.snapshot()
        assert status['session_scans']==30
        assert status['session_hits']==30
        assert status['session_total']==1
        assert status['all_time_total']==1
        assert status['by_target'][0]['target']=='gioca'


def test_new_appearance_after_confirmed_disappearance():
    with tempfile.TemporaryDirectory() as td:
        meter=OCRDetectionCounter(Path(td))
        for at in (1., 2., 3.):
            meter.observe('riscatta',at=at,status='ok',matched=True)
        for at in (4., 5.):
            meter.observe('riscatta',at=at,status='ok',matched=False)
        assert not meter.observe('riscatta',at=6.,status='ok',matched=True)
        assert meter.observe('riscatta',at=7.,status='ok',matched=True)
        assert meter.snapshot()['session_total']==2


def test_uncertain_ocr_not_interpreted_as_absent():
    with tempfile.TemporaryDirectory() as td:
        meter=OCRDetectionCounter(Path(td))
        for at in (1., 2.):
            meter.observe('spettatore',at=at,status='ok',matched=True)
        meter.observe('spettatore',at=3.,status='ocr_error:TimeoutExpired',matched=False)
        meter.observe('spettatore',at=4.,status='unknown',matched=False)
        for at in (5., 6., 7.):
            meter.observe('spettatore',at=at,status='ok',matched=True)
        assert meter.snapshot()['session_total']==1


def test_duplicate_frame_timestamp_never_double_counts():
    with tempfile.TemporaryDirectory() as td:
        meter=OCRDetectionCounter(Path(td))
        for _ in range(200):
            meter.observe('gioca',at=1.,status='ok',matched=True)
        assert meter.snapshot()['session_scans']==1
        assert meter.snapshot()['session_total']==0


def test_legacy_stable_gate_and_persistent_counts_survive_restart():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        meter=OCRDetectionCounter(root)
        assert not meter.observe('reward_title',at=1.,status='ok',matched=True,stable=False)
        assert meter.observe('reward_title',at=2.,status='ok',matched=True,stable=True,
                            text='RICOMPENSE OTTENUTE')
        assert OCRDetectionCounter(root).snapshot()['all_time_total']==1
        assert OCRDetectionCounter(root).snapshot()['session_total']==0
        assert meter.snapshot()['recent'][0]['text']=='RICOMPENSE OTTENUTE'


def test_broken_sqlite_does_not_crash_ocr_counter():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        (root/'learning').mkdir()
        (root/'learning'/'observer.sqlite3').write_text('BROKEN DATABASE')
        meter=OCRDetectionCounter(root)
        meter.observe('gioca',at=1.,status='ok',matched=True)
        assert meter.observe('gioca',at=2.,status='ok',matched=True)
        assert meter.snapshot()['session_total']==1
        assert meter.snapshot()['storage_error']
        assert meter.snapshot()['all_time_total']==0


def test_modular_unrecognized_is_not_a_discovery():
    with tempfile.TemporaryDirectory() as td:
        meter=OCRDetectionCounter(Path(td))
        meter.observe('inventory',at=1.,status='unknown',matched=False)
        meter.observe('inventory',at=2.,status='unknown',matched=False)
        assert meter.snapshot()['session_total']==0
        assert meter.snapshot()['session_scans']==0


def test_worker_counts_new_region_ocr_events_without_tesseract():
    import time
    from unittest.mock import patch
    import numpy as np
    from observer.region_ocr import ScreenRegionOCRWorker, RegionReading

    with tempfile.TemporaryDirectory() as td:
        worker=ScreenRegionOCRWorker(Path(td))
        worker.rules={'gioca': {'target':'gioca','region':[.15,.20,.45,.40],
                                'expected_text':'GIOCA'}}
        fake=lambda frame,rule,at: RegionReading('gioca',at,'GIOCA',.98,True,False,(50,35),'ok')
        frame=np.zeros((120,200,3), dtype=np.uint8)
        with patch('observer.region_ocr.read_single_region', side_effect=fake):
            worker.start()
            try:
                for at in (1.,2.,3.):
                    assert worker.submit(frame,at,'await_play')
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline:
                        if worker.debug_snapshot.get('frame_at')==at:
                            break
                        time.sleep(.025)
                    else:
                        raise AssertionError('OCR worker non ha consumato il frame')
                assert worker.discovery.snapshot()['session_total']==1
                assert worker.debug_snapshot['detections']['session_scans']==3
            finally:
                worker.stop()


def test_dashboard_reports_ocr_counter_with_no_live_session():
    from oye_web_app import Dashboard
    with tempfile.TemporaryDirectory() as td:
        dashboard=Dashboard(Path(td))
        try:
            assert dashboard.get_status()['ocr_detections']['all_time_total']==0
            assert dashboard.scanner_status()['debug']['detections']['all_time_total']==0
            meter=OCRDetectionCounter(Path(td))
            meter.observe('gioca',at=1.,status='ok',matched=True)
            meter.observe('gioca',at=2.,status='ok',matched=True)
            assert dashboard.get_status()['ocr_detections']['all_time_total']==1
            assert dashboard.scanner_status()['debug']['detections']['all_time_total']==1
        finally:
            dashboard.shutdown()


def test_phase_and_reward_ocr_emit_only_once_and_share_counter():
    from types import SimpleNamespace
    from observer.runner import Runner
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        counter=OCRDetectionCounter(root)
        fake=object.__new__(Runner)
        fake.region_ocr=SimpleNamespace(discovery=counter)
        sent=[]
        fake._emit=lambda kind,message,**kwargs: sent.append((kind,message))
        # Phase OCR checks several candidates against one Tesseract operation.
        for at in (11.,12.,13.):
            fake._count_external_ocr('fase_spettatore',at=at,matched=True,
                                    text='SPETTATORE',label='Spettatore')
            fake._count_external_ocr('fase_attesa_riavvio',at=at,
                                    matched=False,label='In attesa del riavvio',count_scan=False)
        assert counter.snapshot()['session_total']==1
        assert counter.snapshot()['session_scans']==3
        assert len(sent)==1
        assert sent[0][0]=='ocr_found'
        for at in (14.,15.):
            fake._count_external_ocr('fase_spettatore',at=at,
                                    matched=False,label='Spettatore')
        for at in (16.,17.):
            fake._count_external_ocr('fase_spettatore',at=at,
                                    matched=True,text='SPETTATORE',label='Spettatore')
        assert counter.snapshot()['session_total']==2
