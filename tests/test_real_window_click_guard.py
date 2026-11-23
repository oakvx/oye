"""Prevent clicks calculated for a different or expired game window."""
import unittest
from observer.runner import _menu_click_guard
from observer.windows import Window
from observer.menu_supervisor import MenuSupervisor
from observer.models import Action


class RealWindowClickGuardTests(unittest.TestCase):
    def test_only_current_window_and_fresh_evidence(self):
        win = Window('FortniteClient-Win64-Shipping.exe', 100, 50, 1920, 1080)
        args = ((300, 200), win, win, 10.0, 9.5, 11.0)
        self.assertIsNone(_menu_click_guard(*args))
        self.assertIsNotNone(_menu_click_guard((300, 200), win, None, 10, 10, 11))
        moved = Window(win.executable, 110, 50, 1920, 1080)
        self.assertIsNotNone(_menu_click_guard((300, 200), win, moved, 10, 10, 11))
        self.assertIsNotNone(_menu_click_guard((300, 200), win, win, 6, 10, 11))
        self.assertIsNotNone(_menu_click_guard((300, 200), win, win, 10, 6, 11))
        self.assertIsNotNone(_menu_click_guard((50, 200), win, win, 10, 10, 11))
        self.assertIsNotNone(_menu_click_guard((2020, 200), win, win, 10, 10, 11))
        self.assertIsNotNone(_menu_click_guard((300.5, 200), win, win, 10, 10, 11))

    def test_geometry_change_does_not_erase_click_retry_history(self):
        supervisor = MenuSupervisor()
        supervisor.on_executed('gioca', Action('menu_click', point=(200, 200)),
                               now=1, scene='lobby')
        supervisor.stable_name = 'gioca'
        supervisor.stable_count = 2
        supervisor.invalidate_observations()
        self.assertIsNotNone(supervisor.pending)
        self.assertEqual(supervisor.pending.attempts, 1)
        self.assertEqual(supervisor.stable_count, 0)

if __name__ == '__main__':
    unittest.main()
