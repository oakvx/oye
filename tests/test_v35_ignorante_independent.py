"""IGNORANTE: functional proof without screen frames, OCR or calibrated HUD."""
from __future__ import annotations

import tempfile
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from observer.config import load, save
from observer.models import Action
from observer.runner import Runner
from observer.single_match import SingleMatchController
from oye_web_app import Dashboard


class IgnoranteClockTests(unittest.TestCase):
    def test_no_screen_or_ocr_required_for_key_sequence(self):
        c = SingleMatchController(quick_start=True, repeat=True)
        c.arm_quick(10)
        self.assertEqual(c.quick_without_frame(29.99).name, 'wait')
        self.assertEqual(c.quick_without_frame(30).name, 'jump')
        seq = [c.quick_without_frame(30.2 + i*.18).name for i in range(21)]
        for name in ('equip_pickaxe','patrol_forward','patrol_harvest','strafe_left','strafe_right'):
            self.assertIn(name, seq)

    def test_misleading_lobby_labels_cannot_cancel_blind_start(self):
        c = SingleMatchController(quick_start=True, repeat=True)
        c.arm_quick(1)
        c.decide(now=21,frame_at=21,scene='lobby',menu='gioca',
                 candidate=Action('wait'), authorized=False, locked=False, spectator=False)
        self.assertEqual(c.stage,'jumping')
        actions=[]
        for i in range(20):
            result=c.decide(now=21.2+i*.18,frame_at=21.2+i*.18,
                scene='lobby',menu='gioca',candidate=Action('wait'),
                authorized=False,locked=False,spectator=False)
            actions.append(result.name)
        self.assertIn('patrol_harvest',actions)
        self.assertEqual(c.stage,'jumping')
        self.assertFalse(c.quick_visual_match_seen)

    def test_no_screen_after_gioca_click_still_starts(self):
        c = SingleMatchController(quick_start=True)
        c.arm_quick(4)
        c.on_executed(Action('menu_click'), 'gioca', 5)
        self.assertFalse(c.quick_manual_arm)
        self.assertEqual(c.quick_without_frame(24.99).name, 'wait')
        self.assertEqual(c.quick_without_frame(25).name, 'jump')

    def test_pause_focus_time_does_not_count(self):
        c = SingleMatchController(quick_start=True)
        c.arm_quick(1)
        self.assertEqual(c.quick_without_frame(11).name, 'wait')
        c.suspend_clock()
        self.assertEqual(c.quick_without_frame(100).name, 'wait')
        self.assertEqual(c.quick_without_frame(110).name, 'jump')

    def test_no_automation_before_arming(self):
        c = SingleMatchController(quick_start=True)
        self.assertEqual(c.quick_without_frame(40).name, 'wait')


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
            window=SimpleNamespace(executable='FortniteClient-Win64-Shipping.exe',left=0,top=0)

            class FakeDriver:
                def __init__(self, *, live, allowed, config, stop, paused):
                    self.stop=stop;self.allowed=allowed;self.paused=paused
                def run(self, action):
                    if not self.allowed() or self.paused.is_set() or self.stop.is_set():
                        return False
                    sent.append(action.name)
                    if len(sent) >= 6:
                        self.stop.set()
                    return True
                def release(self):
                    pass

            class FakeStream:
                def __init__(self,*args,**kwargs): self.error=None;self.skipped=0
                def start(self): pass
                def latest(self, win): return None
                def stop(self,timeout=None): pass

            arm=SingleMatchController.arm_quick
            def fast_arm(controller,now):
                arm(controller,now)
                controller.quick_delay_seconds=.12
                controller.jump_period_seconds=.14

            def fail_snapshot(win):
                raise RuntimeError('Schermata non accessibile')

            with (patch('observer.runner.Driver',FakeDriver),
                  patch('observer.runner.LatestFrameCapture',FakeStream),
                  patch('observer.runner.foreground_window',lambda names:window),
                  patch('observer.runner.snapshot',fail_snapshot),
                  patch('observer.runner.enable_dpi_awareness',lambda:None),
                  patch('observer.runner.close_capture',lambda:None),
                  patch.object(SingleMatchController,'arm_quick',fast_arm)):
                r=Runner(root=root,live=True,single_match=True,
                         repeat_matches=True,quick_start=True, quick_wait_play_exit=False)
                r.start()
                r.join(timeout=8)
                r.stop()
                r.join(timeout=2)
                events=[]
                while not r.messages.empty(): events.append(r.messages.get_nowait())
                self.assertTrue(sent, 'No command reached the input driver: '+str(events[-4:]))
                self.assertEqual(sent[0], 'jump',str(events[-4:]))
                self.assertIn('equip_pickaxe',sent,str(events[-4:]))
                self.assertIn('patrol_forward',sent,str(events[-4:]))
                if corrupted:
                    self.assertEqual((root/'learning'/'observer.sqlite3').read_bytes(),original)
            return sent

    def test_no_async_frames_completely_ignored(self):
        self._run(async_capture=True)

    def test_sync_capture_exception_falls_back(self):
        self._run(async_capture=False)

    def test_corrupt_training_and_templates_dont_block_manual_mode(self):
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
                    with self.assertRaisesRegex(ValueError, 'mostrami GIOCA'):
                        d.start('quick_cycle',confirmed=True)
                    runner_type.assert_not_called()
            finally:
                d.shutdown()


if __name__=='__main__':
    unittest.main()
