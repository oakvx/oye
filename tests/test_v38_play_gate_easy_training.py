"""v38: distinct PLAY disappearance, fixed loading, random jump-only phase, easy training."""
from __future__ import annotations
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
from observer.models import Action
from observer.single_match import SingleMatchController
from observer.learning import LearningStore
from observer.config import load
from oye_web_app import Dashboard


def tick(c, t, *, play=False, scene='unknown', candidate=None, locked=False):
    return c.decide(now=t, frame_at=t, scene='lobby' if play else scene,
                    menu='gioca' if play else None,
                    candidate=candidate or Action('wait'),
                    authorized=False, locked=locked, spectator=False,
                    reward_scan_confirmed=True, play_visible=play)


def controller():
    c=SingleMatchController(quick_start=True, quick_wait_play_exit=True,
                            quick_loading_seconds=120, quick_jump_min_seconds=120,
                            quick_jump_max_seconds=150, repeat=True)
    c.arm_quick(0)
    return c


class LobbyGateTests(unittest.TestCase):
    def test_play_visible_prevents_all_movement_even_after_many_minutes(self):
        c=controller()
        for t in (1,5,19,120,210,440,700):
            self.assertEqual(tick(c,t,play=True).name,'wait')
            self.assertEqual(c.stage,'joining')
        self.assertFalse(c.quick_departed)

    def test_play_requires_a_clickable_position_and_three_frames(self):
        c=controller()
        for t in (1, 2):
            self.assertEqual(tick(c,t,play=True,candidate=Action('menu_click', .2)).name,'wait')
        # An Action without coordinates cannot be clicked, regardless of a menu proposal.
        self.assertEqual(tick(c,3,play=True,candidate=Action('menu_click', .2)).name,'wait')
        self.assertIn('coordinate', tick(c,4,play=True).reason)

    def test_exit_three_fresh_frames_then_120_seconds_no_inputs(self):
        c=controller()
        tick(c,1,play=True)
        for t in (2,3,4):
            self.assertEqual(tick(c,t).name,'wait')
        self.assertTrue(c.quick_departed)
        self.assertEqual(tick(c,123.99).name,'wait')
        a=tick(c,124)
        self.assertEqual(a.name,'jump')
        self.assertEqual(c.stage,'jumping')
        self.assertGreaterEqual(c.quick_jump_target_seconds,120)
        self.assertLessEqual(c.quick_jump_target_seconds,150)

    def test_play_reappearing_resets_loading_clock(self):
        c=controller()
        tick(c,1,play=True)
        for t in (2,3,4):tick(c,t)
        tick(c,70)
        self.assertEqual(tick(c,71,play=True).name,'wait')
        self.assertFalse(c.quick_departed)
        for t in (72,73,74):tick(c,t)
        self.assertTrue(c.quick_departed)
        self.assertEqual(tick(c,140).name,'wait')
        self.assertEqual(tick(c,194).name,'jump')

    def test_cannot_infer_play_disappearance_without_frames(self):
        c=controller()
        tick(c,1,play=True)
        self.assertEqual(c.quick_without_frame(501).name,'wait')
        self.assertEqual(c.stage,'joining')
        self.assertFalse(c.quick_departed)

    def test_unseen_play_never_starts_blind(self):
        c=controller()
        for t in (1,2,3,150,400):
            self.assertEqual(tick(c,t).name,'wait')
        self.assertEqual(c.quick_without_frame(600).name,'wait')
        self.assertEqual(c.stage,'joining')

    def test_after_wait_only_space_for_120_to_150_then_explore(self):
        c=controller()
        tick(c,1,play=True)
        for t in (2,3,4):tick(c,t)
        self.assertEqual(tick(c,124).name,'jump')
        for i in range(1,70):
            # Every result before the individual target is jump or wait, never WASD.
            t=124+i*1.8
            if t-124<c.quick_jump_target_seconds:
                self.assertIn(tick(c,t).name,('jump','wait'))
        self.assertIn(tick(c,275).name,('equip_pickaxe','patrol_forward','strafe_left','strafe_right'))
        self.assertEqual(c.stage,'exploring')
        seq=[tick(c,275.5+i*.5).name for i in range(45)]
        self.assertTrue(any(x in seq for x in ('patrol_forward','strafe_left','strafe_right')))
        self.assertIn('patrol_harvest',seq)

    def test_after_confirmed_exit_screen_loss_cannot_reset_timers(self):
        c=controller()
        tick(c,1,play=True)
        for t in (2,3,4):tick(c,t)
        self.assertEqual(c.quick_without_frame(123).name,'wait')
        self.assertEqual(c.quick_without_frame(124).name,'jump')
        self.assertIn(c.quick_without_frame(125.8).name,('wait','jump'))

    def test_pause_does_not_consume_loading_time(self):
        c=controller()
        tick(c,1,play=True)
        for t in (2,3,4):tick(c,t)
        tick(c,60)
        c.suspend_clock()
        self.assertEqual(tick(c,500).name,'wait')
        self.assertEqual(tick(c,564).name,'jump')

    def test_results_overlay_preempts_input(self):
        c=controller()
        tick(c,1,play=True)
        for t in (2,3,4):tick(c,t)
        self.assertEqual(tick(c,125,scene='spectator').name,'wait')
        self.assertEqual(c.stage,'joining')


class EasyTrainingTests(unittest.TestCase):
    def test_confirm_last_demo_keeps_preexisting_training_and_menus_safe(self):
        with tempfile.TemporaryDirectory() as td:
            store=LearningStore(Path(td)/'learning')
            img=np.tile(np.arange(160,dtype=np.uint8),(90,1))
            frame=np.repeat(img[:,:,None],3,axis=2)
            store.record_demonstration(frame, action='forward',session='previous',process='Fortnite.exe',scene='match')
            store.record_demonstration(frame, action='forward_jump',session='new',process='Fortnite.exe',scene='unknown')
            store.record_demonstration(frame, action='harvest',session='new',process='Fortnite.exe',scene='lobby')
            result=store.confirm_latest_demonstration()
            self.assertEqual(result['session'],'new')
            self.assertEqual(result['confirmed'],1)
            names=[(session,action) for session,action,_,source in store.action_training_samples()]
            self.assertIn(('new','forward_jump'),names)
            self.assertIn(('previous','forward'),names)
            self.assertNotIn(('new','harvest'),names)
            self.assertEqual(store.confirm_latest_demonstration()['confirmed'],0)

    def test_dashboard_ignorable_hud_but_requires_play_training(self):
        with tempfile.TemporaryDirectory() as td:
            d=Dashboard(Path(td))
            try:
                with patch('oye_web_app.Runner') as runner_type,patch.object(d,'_start_hotkeys'):
                    with self.assertRaisesRegex(ValueError,'GIOCA'):
                        d.start('quick_cycle',confirmed=True)
                    runner_type.assert_not_called()
                    with patch.object(d,'readiness',return_value={'present':['gioca'], 'issues':[], 'missing':[], 'ready':False}):
                        runner_type.return_value.thread=None
                        d.start('quick_cycle',confirmed=True)
                        self.assertTrue(runner_type.call_args.kwargs['quick_wait_play_exit'])
            finally:
                d.shutdown()

    def test_train_mode_records_mouse_in_user_config_without_input_driver(self):
        with tempfile.TemporaryDirectory() as td:
            d=Dashboard(Path(td))
            try:
                with patch('oye_web_app.Runner') as runner_type:
                    runner_type.return_value.thread=None
                    d.start('train',confirmed=False)
                    self.assertTrue(load(Path(td)/'config.json')['demonstration_mouse_shoot'])
                    self.assertTrue(runner_type.call_args.kwargs['exploration'])
                    self.assertFalse(runner_type.call_args.kwargs['live'])
            finally:
                d.shutdown()


if __name__ == '__main__':
    unittest.main()
