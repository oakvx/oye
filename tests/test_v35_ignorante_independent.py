"""Missing acquisitions stop Ignorante at the production Runner entry point."""
from __future__ import annotations

import tempfile
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from observer.config import load, save
from observer.runner import Runner
from oye_web_app import Dashboard


class IgnoranteFullWorkerTests(unittest.TestCase):
    def _run(self, *, async_capture: bool, corrupted: bool = False, learning: bool = False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = load(root/'config.json')
            cfg.update(ocr_enabled=False,learning_enabled=learning,
                       async_capture_enabled=async_capture,audio_enabled=False)
            save(root/'config.json',cfg)
            if corrupted:
                (root/'learning').mkdir(parents=True)
                (root/'learning'/'observer.sqlite3').write_bytes(b'NOT SQLITE, DO NOT OVERWRITE')
                (root/'templates').mkdir()
                (root/'templates'/'gioca.png').write_bytes(b'bad png data')
            original = (root/'learning'/'observer.sqlite3').read_bytes() if corrupted else None
            sent=[]
            released=threading.Event()
            window=SimpleNamespace(executable='FortniteClient-Win64-Shipping.exe',left=0,top=0)

            class FakeDriver:
                def __init__(self, *, live, allowed, config, stop, paused):
                    self.stop=stop;self.allowed=allowed;self.paused=paused
                def run(self, action):
                    if not self.allowed() or self.paused.is_set() or self.stop.is_set():
                        return False
                    sent.append(action.name)
                    return True
                def release(self):
                    released.set()

            class FakeStream:
                def __init__(self,*args,**kwargs): self.error=None;self.skipped=0
                def start(self): pass
                def latest(self, win): return None
                def stop(self,timeout=None): pass

            def fail_snapshot(win):
                raise RuntimeError('Schermata non accessibile')

            with (patch('observer.runner.Driver',FakeDriver),
                  patch('observer.runner.LatestFrameCapture',FakeStream),
                  patch('observer.runner.foreground_window',lambda names:window),
                  patch('observer.runner.snapshot',fail_snapshot),
                  patch('observer.runner.enable_dpi_awareness',lambda:None),
                  patch('observer.runner.close_capture',lambda:None)):
                r=Runner(root=root,live=True,single_match=True,
                         repeat_matches=True,quick_start=True, quick_wait_play_exit=False)
                r.start()
                self.assertTrue(released.wait(3), "Runner did not reach the capture guard")
                time.sleep(.2)
                r.stop()
                r.join(timeout=2)
                events=[]
                while not r.messages.empty(): events.append(r.messages.get_nowait())
                self.assertEqual(sent, [], str(events[-4:]))
                self.assertTrue(any('acquisizione' in e.get('reason', '').casefold() or
                                    'screenshot' in e.get('reason', '').casefold() for e in events))
                if corrupted:
                    self.assertEqual((root/'learning'/'observer.sqlite3').read_bytes(),original)
            return sent

    def test_no_async_frames_releases_input(self):
        self._run(async_capture=True)

    def test_sync_capture_exception_releases_input(self):
        self._run(async_capture=False)

    def test_capture_failure_preserves_corrupt_training(self):
        self._run(async_capture=True,corrupted=True,learning=True)

    def test_dashboard_accepts_bad_templates_for_ignorante_only(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'templates').mkdir()
            (root/'templates'/'gioca.png').write_bytes(b'invalid')
            cfg=load(root/'config.json')
            save(root/'config.json',cfg)
            d=Dashboard(root)
            try:
                with patch('oye_web_app.Runner') as runner_type, patch.object(d, '_start_hotkeys'):
                    runner=runner_type.return_value
                    runner.thread=None
                    runner.start.return_value=None
                    with patch('oye_web_app.ocr_available', return_value=True):
                        d.start('quick_cycle', confirmed=True)
                        self.assertTrue(runner_type.call_args.kwargs['quick_start'])
            finally:
                d.shutdown()


if __name__=='__main__':
    unittest.main()
