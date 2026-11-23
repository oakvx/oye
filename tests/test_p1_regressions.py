"""P1 upgrade tests: no game execution or synthetic input is required."""
from pathlib import Path
import json
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from observer.guided import GuidedExplorer
from observer.runner import Runner
from observer.driver import Driver
import threading
from observer.models import Motion, Action
from observer.p1_navigation import CameraFeedback, route_hint
from observer.resume import ResumeGate, load_checkpoint, save_checkpoint
from observer.single_match import SingleMatchController
from observer.config import save, validate
from oye_web_app import Dashboard


class CameraP1Tests(unittest.TestCase):
    def test_multiple_isolated_misses_signal_attention_not_success(self):
        verifier = CameraFeedback()
        self.assertEqual(verifier.observe(Motion(.05, 50, True), isolated=True), 'not_observed')
        self.assertEqual(verifier.observe(Motion(.05, 50, True), isolated=True), 'attention')
        self.assertIn('2 prove', verifier.detail)
        self.assertEqual(verifier.observe(Motion(3.4, 50, True), isolated=True), 'observed')
        self.assertEqual(verifier.failed, 0)

    def test_walking_camera_motion_cannot_claim_confirmation(self):
        verifier=CameraFeedback()
        verifier.observe(Motion(10., 50, True),isolated=False)
        self.assertEqual(verifier.good, 0)
        self.assertEqual(verifier.status, 'untested')
        verifier.observe(Motion(0., 0, False),isolated=True)
        self.assertEqual(verifier.status, 'untested')

    def test_driver_turn_uses_relative_mouse_only_when_permitted(self):
        class Mouse:
            FAILSAFE=True
            def __init__(self):self.moves=[]
            def moveRel(self,x,y,duration=0):self.moves.append((x,y,duration))
        gui=Mouse()
        permitted=True
        driver=Driver(live=False,allowed=lambda:permitted,config={'keys':{}},
                      stop=threading.Event(),paused=threading.Event())
        driver.gui=gui
        self.assertTrue(driver.run(Action('turn_left',.12)))
        self.assertLess(gui.moves[0][0],0)
        permitted=False
        self.assertFalse(driver.run(Action('turn_right',.12)))
        self.assertEqual(len(gui.moves),1)

    def test_smooth_planner_schedules_isolated_camera_probe(self):
        explorer=GuidedExplorer(smooth=True,harvest=False,seed=3)
        actions=[explorer.next_action().name for _ in range(35)]
        self.assertTrue(any(n in ('turn_left','turn_right') for n in actions))
        self.assertGreater(actions.count('patrol_forward'),0)


class MovementP1Tests(unittest.TestCase):
    def test_route_hint_rejects_blank(self):
        frame=np.full((720,1280,3),100,dtype=np.uint8)
        self.assertIsNone(route_hint(frame).side)

    def test_route_hint_prefers_less_edge_dense_side(self):
        frame=np.full((720,1280,3),100,dtype=np.uint8)
        for x in range(675,985,12):
            cv2.line(frame,(x,360),(x,530),(250,250,250),3)
        hint=route_hint(frame)
        self.assertEqual(hint.side,'left')
        self.assertGreater(hint.confidence,.23)

    def test_recoveries_cooldown_and_retry_are_bounded(self):
        explorer=GuidedExplorer(smooth=True,harvest=False,max_recoveries=1,seed=12)
        explorer.observe_route(SimpleNamespace(side='right',confidence=.85))
        # Three reliable failed forward probes schedule recovery; three more
        # after it was attempted enter backoff instead of a permanent deadlock.
        for _ in range(3): explorer.observe_motion(.05,True,'patrol_forward')
        recovery=[explorer.next_action().name for _ in range(5)]
        self.assertIn('turn_right',recovery)
        self.assertIn('forward_jump',recovery)
        for _ in range(3): explorer.observe_motion(.05,True,'patrol_forward')
        self.assertTrue(explorer.halted)
        self.assertEqual(explorer.next_action(now=0).name,'wait')
        with patch('observer.guided.time.monotonic',return_value=1000.):
            explorer.cooldown_until=5
            attempt=explorer.next_action(now=1000.)
        self.assertEqual(attempt.name,'reverse')
        self.assertFalse(explorer.halted)

    def test_good_motion_clears_previous_recovery_budget(self):
        explorer=GuidedExplorer(max_recoveries=2)
        for _ in range(3): explorer.observe_motion(0.0,True,'patrol_forward')
        self.assertEqual(explorer.recovery_count,1)
        explorer.observe_motion(3.1,True,'patrol_forward')
        self.assertEqual(explorer.recovery_count,0)


class ResumeP1Tests(unittest.TestCase):
    def test_atomic_checkpoint_roundtrip_stop_and_age(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'state'/'auto_cycle.json'
            save_checkpoint(p,stage='exploring',matches_completed=11,active=True,now=10000.)
            self.assertEqual(load_checkpoint(p,now=10003.)['matches_completed'],11)
            self.assertIsNone(load_checkpoint(p,now=10000+49*3600))
            save_checkpoint(p,stage='exploring',matches_completed=11,active=False,now=10001.)
            self.assertFalse(load_checkpoint(p,now=10002.)['active'])
            self.assertFalse((p.with_name('auto_cycle.json.tmp')).exists())

    def test_corrupt_checkpoint_is_ignored_not_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'bad.json';p.write_text('{broken',encoding='utf-8')
            self.assertIsNone(load_checkpoint(p))

    def test_restore_requires_three_distinct_authorized_frames(self):
        controller=SingleMatchController(repeat=True)
        gate=ResumeGate(active=True)
        self.assertFalse(gate.reconcile(controller,frame_at=10,now=10,scene='match',menu=None,
                                         authorized=True,locked=False,spectator=False))
        self.assertFalse(gate.reconcile(controller,frame_at=10,now=11,scene='match',menu=None,
                                         authorized=True,locked=False,spectator=False))
        self.assertFalse(gate.reconcile(controller,frame_at=11,now=11,scene='spectator',menu=None,
                                         authorized=False,locked=True,spectator=True))
        for frame in (12,13):
            self.assertFalse(gate.reconcile(controller,frame_at=frame,now=frame,scene='match',menu=None,
                                            authorized=True,locked=False,spectator=False))
        self.assertTrue(gate.reconcile(controller,frame_at=14,now=14,scene='match',menu=None,
                                        authorized=True,locked=False,spectator=False))
        self.assertEqual(controller.stage,'exploring')
        self.assertTrue(controller.has_entered)

    def test_restore_bus_needs_three_fresh_calibrated_prompts(self):
        controller=SingleMatchController(repeat=True)
        gate=ResumeGate(active=True)
        for frame in (1,2):
            self.assertFalse(gate.reconcile(controller,frame_at=frame,now=frame,
                scene='unknown',menu=None,authorized=False,locked=False,
                spectator=False,bus_confirmed=True))
        self.assertTrue(gate.reconcile(controller,frame_at=3,now=3,
            scene='unknown',menu=None,authorized=False,locked=False,
            spectator=False,bus_confirmed=True))
        self.assertEqual(controller.stage,'joining')
        self.assertFalse(controller.has_entered)

    def test_restart_from_confirmed_results_to_lobby_counts_once(self):
        controller=SingleMatchController(repeat=True)
        controller.matches_completed=4
        gate=ResumeGate(active=True,previous_stage='ending')
        for frame in (1,2,3):
            gate.reconcile(controller,frame_at=frame,now=frame,scene='lobby',menu='gioca',
                authorized=False,locked=False,spectator=False)
        self.assertEqual(controller.matches_completed,5)
        self.assertFalse(gate.active)
        gate.reconcile(controller,frame_at=4,now=4,scene='lobby',menu='gioca',
            authorized=False,locked=False,spectator=False)
        self.assertEqual(controller.matches_completed,5)

    def test_restore_lobby_keeps_cooldown_and_no_instant_play(self):
        controller=SingleMatchController(repeat=True)
        gate=ResumeGate(active=True)
        for t in (1,2,3):
            gate.reconcile(controller,frame_at=t,now=t,scene='lobby',menu='gioca',
                           authorized=False,locked=False,spectator=False)
        self.assertFalse(gate.active)
        result=controller.decide(now=4,frame_at=4,scene='lobby',menu='gioca',
                                 candidate=Action('menu_click',point=(30,30)),authorized=False,
                                 locked=False,spectator=False)
        self.assertEqual(result.name,'wait')


class CalibrationDiagnosticsP1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'templates').mkdir()
        save(self.root/'config.json',validate({}))
        self.app=Dashboard(self.root)

    def tearDown(self):
        self.app.runner=None
        self.app.shutdown()
        self.tmp.cleanup()

    def test_teaching_available_only_during_passive_session(self):
        mockrunner=SimpleNamespace(thread=SimpleNamespace(is_alive=lambda:True))
        self.app.runner=mockrunner
        self.app.mode='observe'
        self.assertTrue(self.app._can_teach_now())
        frame=np.random.default_rng(50).integers(20,240,(240,420,3),dtype=np.uint8)
        ok,encoded=cv2.imencode('.png',frame)
        self.assertTrue(ok)
        self.app.upload_calibration(encoded.tobytes())
        self.assertEqual(self.app.calibration_serial,1)
        self.app.mode='auto_cycle'
        self.assertFalse(self.app._can_teach_now())
        with self.assertRaisesRegex(ValueError,'input LIVE'):
            self.app.upload_calibration(encoded.tobytes())

    def test_passive_runner_provides_original_resolution_not_preview(self):
        runner=Runner(root=self.root,live=False)
        original=np.random.default_rng(19).integers(0,255,(720,1280,3),dtype=np.uint8)
        runner._calibration_frame=original
        runner._calibration_at=100.
        self.assertIsNone(runner.calibration_snapshot(after=101.))
        self.assertEqual(runner.calibration_snapshot(after=95.).shape,(720,1280,3))

    def test_dashboard_reports_actual_runtime_error_with_next_step(self):
        self.app.runtime_issue='Errore Tesseract'
        self.app.runtime_next_step='Verifica installazione Tesseract'
        status=self.app.get_status()
        self.assertEqual(status['diagnostic']['attention'],'Errore Tesseract')
        self.assertIn('Tesseract',status['diagnostic']['next_step'])
        self.assertIn('resume_available',status['checkpoint'])


if __name__=='__main__': unittest.main()
