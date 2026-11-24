import threading
import unittest
from unittest.mock import patch

from observer.config import validate
from observer.driver import Driver
from observer.models import Action
from observer.quick_routine import QuickRoutine


class SmoothMovementTests(unittest.TestCase):
    def test_routine_uses_longer_segments_without_abrupt_reversals(self):
        routine = QuickRoutine(seed=17)
        actions = [routine.next_action(harvest=False) for _ in range(100)]
        self.assertTrue(all(1.0 <= a.seconds <= 1.5 for a in actions))
        self.assertNotIn('reverse', [a.name for a in actions])
        for first, second in zip(actions, actions[1:]):
            self.assertNotEqual({first.name, second.name}, {'strafe_left', 'strafe_right'})

    def test_camera_is_smooth_bounded_and_interruptible(self):
        for interrupt in (None, 'pause', 'stop', 'focus'):
            with self.subTest(interrupt=interrupt):
                clock = [0.0]
                focused = [True]
                stop, pause = threading.Event(), threading.Event()
                driver = Driver(live=False, allowed=lambda: focused[0],
                                config=validate({}), stop=stop, paused=pause)
                moves, held = [], set()

                class GUI:
                    FAILSAFE = True
                    def keyDown(self, key): held.add(key)
                    def keyUp(self, key): held.discard(key)
                    def moveRel(self, dx, dy, duration=0):
                        moves.append((clock[0], dx, dy))
                        if len(moves) == 3:
                            if interrupt == 'pause': pause.set()
                            if interrupt == 'stop': stop.set()
                            if interrupt == 'focus': focused[0] = False

                driver.gui = GUI()
                def sleep(seconds): clock[0] += seconds
                with patch('observer.driver.time.monotonic', lambda: clock[0]), patch('observer.driver.time.sleep', sleep):
                    driver.run(Action('patrol_look_right', 1.2))
                self.assertFalse(held)
                self.assertFalse(driver.down)
                if interrupt:
                    self.assertEqual(len(moves), 3)
                    self.assertLess(clock[0], .3)
                else:
                    self.assertEqual(sum(dx for _, dx, _ in moves), 150)
                    self.assertEqual(sum(dy for _, _, dy in moves), 0)
                    self.assertGreater(len(moves), 30)
                    self.assertLessEqual(max(abs(dx) for _, dx, _ in moves), 5)
                    self.assertAlmostEqual(clock[0], 1.2)
