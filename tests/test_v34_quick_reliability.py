"""Tests for user-armed quick mode, without HUD/OCR or user calibration."""
import tempfile
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from observer.config import load, save
from observer.driver import Driver
from observer.models import Action
from observer.runner import Runner
from observer.single_match import SingleMatchController
from oye_web_app import Dashboard


def tick(ctrl, now, scene='unknown', menu=None, candidate=None, **kw):
    options = dict(locked=False, spectator=False)
    options.update(kw)
    return ctrl.decide(now=now, frame_at=now, scene=scene, menu=menu,
                       candidate=candidate or Action('wait'), authorized=False,
                       **options)


class QuickTimerTests(unittest.TestCase):
    def test_manual_pronto_no_play_no_hud_starts_at_20(self):
        c = SingleMatchController(quick_start=True, repeat=True)
        c.arm_quick(100.)
        self.assertEqual(c.stage, 'joining')
        self.assertEqual(tick(c, 119.99).name, 'wait')
        first = tick(c, 120.)
        self.assertEqual(first.name, 'jump')
        self.assertEqual(c.stage, 'jumping')
        # Scripted fallback must not depend on a model prediction or OCR.
        actions = [tick(c, 120.2 + i * .18).name for i in range(18)]
        self.assertIn('equip_pickaxe', actions)
        self.assertIn('patrol_harvest', actions)
        self.assertIn('patrol_forward', actions)
        self.assertIn('strafe_left', actions)
        self.assertIn('strafe_right', actions)
        self.assertEqual(actions.count('equip_pickaxe'), 1)

    def test_pause_and_focus_gap_do_not_use_elapsed_wall_time(self):
        c = SingleMatchController(quick_start=True)
        c.arm_quick(10.)
        tick(c, 18.)
        c.suspend_clock()  # F8 / window lost
        self.assertEqual(tick(c, 190.).name, 'wait')
        self.assertEqual(tick(c, 200.).name, 'wait')
        self.assertEqual(tick(c, 202.).name, 'jump')

    def test_known_results_and_spectator_block_keys(self):
        for scene in ('results', 'spectator'):
            c = SingleMatchController(quick_start=True)
            c.arm_quick(1.)
            d = tick(c, 51., scene=scene, menu='gioca' if scene == 'lobby' else None)
            self.assertEqual(d.name, 'wait', scene)
        c = SingleMatchController(quick_start=True)
        c.arm_quick(1.)
        self.assertEqual(tick(c, 50., locked=True).name, 'wait')

    def test_loading_cannot_delay_a_user_armed_20_second_timer(self):
        c = SingleMatchController(quick_start=True)
        c.arm_quick(1.)
        self.assertEqual(tick(c, 20.9, scene='loading').name, 'wait')
        self.assertEqual(tick(c, 21., scene='loading').name, 'jump')

    def test_if_gioca_is_identified_click_precedes_timer(self):
        c = SingleMatchController(quick_start=True)
        c.arm_quick(10.)
        candidate = Action('menu_click', point=(500, 500))
        # No twelve-second artificial wait when Pronto was explicitly requested.
        action = tick(c, 10.5, scene='lobby', menu='gioca', candidate=candidate)
        self.assertEqual(action.name, 'menu_click')
        c.on_executed(action, 'gioca', 10.6)
        self.assertFalse(c.quick_manual_arm)
        self.assertEqual(tick(c, 30.59).name, 'wait')
        self.assertEqual(tick(c, 30.60).name, 'jump')

    def test_reward_pending_preempts_timeout(self):
        c = SingleMatchController(quick_start=True, repeat=True)
        c.arm_quick(20.)
        self.assertEqual(tick(c, 60., reward_pending=True).name, 'wait')
        self.assertEqual(tick(c, 61., reward_overlay=True).name, 'wait')


class FakeGUI:
    FAILSAFE = True
    PAUSE = 0
    def __init__(self): self.actions = []
    def keyDown(self, name): self.actions.append(('down', name))
    def keyUp(self, name): self.actions.append(('up', name))
    def mouseDown(self, button='left'): self.actions.append(('mouseDown', button))
    def mouseUp(self, button='left'): self.actions.append(('mouseUp', button))
    def moveRel(self, *a, **kw): self.actions.append(('moveRel',a))
    def click(self, *a, **kw): self.actions.append(('click',a))


class DriverTests(unittest.TestCase):
    def test_quick_actions_emit_keys_and_left_click_and_release(self):
        cfg = load(Path('/this/path/does/not/exist'))
        stop, pause = threading.Event(), threading.Event()
        driver = Driver(live=False, allowed=lambda: True, config=cfg,
                        stop=stop, paused=pause)
        fake = FakeGUI()
        driver.gui = fake
        c = SingleMatchController(quick_start=True)
        c.arm_quick(10.)
        actions = [tick(c, 30.)]
        actions += [tick(c, 30.2 + i*.18) for i in range(15)]
        for action in actions:
            if action.name != 'wait':
                self.assertTrue(driver.run(action))
        kinds = [x[0] for x in fake.actions]
        self.assertIn(('down','space'), fake.actions)
        self.assertIn(('down','w'), fake.actions)
        self.assertIn(('down','a'), fake.actions)
        self.assertIn(('down','d'), fake.actions)
        self.assertIn('mouseDown', kinds)
        self.assertIn('mouseUp', kinds)
        self.assertFalse(driver.down)
        self.assertFalse(driver.mouse_is_down)
        before = len(fake.actions)
        pause.set()
        self.assertFalse(driver.run(Action('jump', .16)))
        self.assertEqual(len(fake.actions), before)

    def test_focus_veto_and_stop(self):
        cfg = load(Path('/not-present'))
        stop, pause = threading.Event(), threading.Event()
        driver = Driver(live=False, allowed=lambda: False, config=cfg, stop=stop, paused=pause)
        driver.gui = FakeGUI()
        self.assertFalse(driver.run(Action('jump', .16)))
        self.assertEqual(driver.gui.actions, [])


class WiringTests(unittest.TestCase):
    def test_runner_quick_without_tesseract_or_templates(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            save(root/'config.json', {'autonomous_enabled': True,
                 'guided_explore_enabled': True, 'ocr_enabled': False})
            runner = Runner(root=root, live=True, auto_requeue=True, single_match=True,
                            repeat_matches=True, quick_start=True)
            self.assertTrue(runner.quick_start)
            self.assertTrue(runner.autonomous)
            self.assertTrue(runner.guided)
            self.assertEqual(runner.vision.templates, {})

    def test_explicit_single_match_overrides_stale_observation_flags(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            save(root/'config.json', {'autonomous_enabled': False,
                 'guided_explore_enabled': False, 'ocr_enabled': False})
            runner=Runner(root=root, live=True, auto_requeue=True, single_match=True,
                          repeat_matches=True, quick_start=True)
            self.assertTrue(runner.autonomous)
            self.assertTrue(runner.guided)

    def test_pickaxe_key_is_configurable_without_changing_other_training_settings(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            d=Dashboard(root)
            try:
                d.update_settings({'pickaxe_key': '1'})
                self.assertEqual(load(root/'config.json')['keys']['pickaxe'], '1')
                self.assertEqual(load(root/'config.json')['keys']['forward'], 'w')
            finally:
                d.shutdown()

    def test_dashboard_quick_ready_without_gioca_template(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = Dashboard(root)
            try:
                with patch('oye_web_app.ocr_available', return_value=False):
                    self.assertFalse(d.readiness()['quick_ready'])
                    self.assertFalse(d.readiness()['ready'])
            finally:
                d.shutdown()


if __name__ == '__main__':
    unittest.main()
