"""Integration regressions for the novice-style, time-driven IGNORANTE mode."""
from __future__ import annotations

from collections import Counter
import threading
from pathlib import Path
import unittest

from observer.config import load
from observer.driver import Driver
from observer.models import Action
from observer.quick_routine import QuickRoutine
from observer.single_match import SingleMatchController


class FakeGUI:
    FAILSAFE = True
    PAUSE = 0

    def __init__(self):
        self.events = []
        self.down_mouse = set()

    def keyDown(self, key): self.events.append(('keyDown', key))
    def keyUp(self, key): self.events.append(('keyUp', key))
    def mouseDown(self, button='left'):
        self.events.append(('mouseDown', button))
        self.down_mouse.add(button)
    def mouseUp(self, button='left'):
        self.events.append(('mouseUp', button))
        self.down_mouse.discard(button)
    def moveRel(self, *args, **kwargs): self.events.append(('moveRel', args))
    def click(self, *args, **kwargs): self.events.append(('click', args))


class NovicePolicyTests(unittest.TestCase):
    def test_novice_has_variety_and_bounded_activity_gaps(self):
        routine = QuickRoutine(seed=17)
        actions = [routine.next_action(harvest=True) for _ in range(120)]
        names = [a.name for a in actions]
        counts = Counter(names)
        self.assertEqual(counts['equip_pickaxe'], 1)
        self.assertIn('reverse', counts)
        self.assertIn('crouch', counts)
        self.assertIn('secondary_click', counts)
        self.assertIn('patrol_harvest', counts)
        self.assertTrue(any(n in counts for n in ('turn_left', 'turn_right')))
        self.assertTrue(any(n in counts for n in ('patrol_hop', 'jump')))
        self.assertTrue(any(n in counts for n in ('patrol_weave_left', 'patrol_weave_right')))
        self.assertTrue(all(a.name != 'wait' and .10 <= a.seconds <= 1.4 for a in actions))
        # Demand repeated activity across the *whole* run, not only in warmup.
        for chunk_start in range(10, len(names) - 16, 8):
            chunk = names[chunk_start:chunk_start + 16]
            self.assertTrue(any(n in ('patrol_harvest', 'forward_harvest') for n in chunk), chunk)
            self.assertTrue(any(n in ('patrol_hop', 'jump', 'forward_jump') for n in chunk), chunk)
            self.assertTrue(any(n in ('patrol_forward', 'patrol_look_left', 'patrol_look_right',
                                     'patrol_weave_left', 'patrol_weave_right', 'strafe_left',
                                     'strafe_right', 'reverse') for n in chunk), chunk)

    def test_later_activity_does_not_repeat_a_fixed_cycle(self):
        a = [x.name for x in (QuickRoutine(seed=17).next_action(harvest=True) for _ in range(1))]
        # No seed externally required; with a fixed seed results are reproducible.
        first = QuickRoutine(seed=17)
        second = QuickRoutine(seed=17)
        names = [first.next_action(harvest=True).name for _ in range(100)]
        self.assertEqual(names, [second.next_action(harvest=True).name for _ in range(100)])
        self.assertNotEqual(names[15:30], names[30:45])
        self.assertNotEqual(names[30:45], names[45:60])
        self.assertEqual(a, ['equip_pickaxe'])

    def test_disabled_harvest_does_not_send_left_click_actions(self):
        q = QuickRoutine(seed=42)
        names = [q.next_action(harvest=False).name for _ in range(90)]
        self.assertNotIn('patrol_harvest', names)
        self.assertNotIn('equip_pickaxe', names)
        self.assertIn('patrol_hop', names)

    def test_reset_prepares_a_new_match_without_erasing_training(self):
        q = QuickRoutine(seed=17)
        for _ in range(30): q.next_action(harvest=True)
        self.assertTrue(q.index > 10)
        q.reset()
        self.assertEqual(q.index, 0)
        self.assertFalse(q.pickaxe_equipped)
        self.assertEqual(q.next_action(harvest=True).name, 'equip_pickaxe')
        self.assertEqual(q.next_action(harvest=True).name, 'patrol_forward')

    def test_no_frame_controller_still_moves_and_harvests_after_120_seconds(self):
        c = SingleMatchController(quick_start=True, repeat=True, jump_window_seconds=120)
        c.arm_quick(100)
        self.assertEqual(c.quick_without_frame(120).name, 'jump')
        seen = []
        # No screenshot, OCR or HUD throughout; virtual clock continues.
        for i in range(140):
            action = c.quick_without_frame(120.1 + i * 1.0)
            seen.append(action.name)
        self.assertEqual(c.stage, 'exploring')
        self.assertIn('patrol_harvest', seen[-45:])
        self.assertIn('patrol_hop', seen[-45:])
        self.assertIn('patrol_look_left', seen[-45:])
        self.assertIn('patrol_forward', seen[-45:])
        self.assertTrue(all(x != 'wait' for x in seen))


class MouseButtonRegressionTests(unittest.TestCase):
    def test_right_click_then_left_harvest_releases_correct_button(self):
        cfg = load(Path('/nonexistent/oye/config.json'))
        driver = Driver(live=False, allowed=lambda: True, config=cfg,
                        stop=threading.Event(), paused=threading.Event())
        gui = FakeGUI()
        driver.gui = gui
        driver._sleep = lambda seconds: None
        for action in (Action('secondary_click', .12), Action('patrol_harvest', .55),
                       Action('forward_harvest', .40), Action('harvest', .20)):
            self.assertTrue(driver.run(action))
            self.assertFalse(gui.down_mouse, f'Button stuck after {action.name}: {gui.events[-6:]}')
            self.assertFalse(driver.down)
        self.assertIn(('mouseDown', 'right'), gui.events)
        self.assertIn(('mouseUp', 'right'), gui.events)
        self.assertEqual(gui.events.count(('mouseDown', 'left')),
                         gui.events.count(('mouseUp', 'left')))

    def test_novice_actions_reach_driver_and_all_keys_release(self):
        cfg = load(Path('/nonexistent/oye/config.json'))
        driver = Driver(live=False, allowed=lambda: True, config=cfg,
                        stop=threading.Event(), paused=threading.Event())
        gui = FakeGUI()
        driver.gui = gui
        driver._sleep = lambda seconds: None
        q = QuickRoutine(seed=17)
        for _ in range(90):
            action = q.next_action(harvest=True)
            self.assertTrue(driver.run(action), action.name)
            self.assertFalse(driver.down, action.name)
            self.assertFalse(driver.mouse_is_down, action.name)
            self.assertFalse(gui.down_mouse, action.name)
        self.assertIn(('keyDown', 'w'), gui.events)
        self.assertIn(('keyDown', 'a'), gui.events)
        self.assertIn(('keyDown', 'd'), gui.events)
        self.assertIn(('keyDown', 'space'), gui.events)
        self.assertIn(('mouseDown', 'left'), gui.events)
        self.assertIn(('mouseDown', 'right'), gui.events)
        self.assertTrue(any(x[0] == 'moveRel' for x in gui.events))


if __name__ == '__main__':
    unittest.main()
