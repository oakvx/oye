"""Fail closed on unknown menus and mouse buttons without match evidence."""
import unittest
from observer.menu_supervisor import MenuSupervisor
from observer.models import Action, Match
from observer.runner import _ignorante_mouse_gate
from observer.single_match import SingleMatchController


class IgnoranteMenuSafetyTests(unittest.TestCase):
    def test_unknown_never_authorizes_play(self):
        m = MenuSupervisor()
        c = SingleMatchController(quick_start=True, quick_wait_play_exit=True)
        c.arm_quick(0)
        for t in (1, 2, 3, 4):
            proposal = m.offer(now=t, frame_at=t, stage='joining', scene='unknown',
                matches={'gioca': Match('gioca', .98, (20, 30))}, repeat=True)
            self.assertEqual(proposal.action.name, 'wait')
            action = c.decide(now=t, frame_at=t, scene='unknown', menu='gioca',
                candidate=Action('wait'), authorized=False, locked=False, spectator=False,
                play_visible=True, play_point=(20, 30))
            self.assertEqual(action.name, 'wait')

    def test_mouse_needs_match_and_authorization(self):
        for name in ('patrol_harvest', 'forward_harvest', 'harvest', 'shoot',
                     'forward_shoot', 'strafe_left_shoot', 'strafe_right_shoot',
                     'secondary_click', 'aim_hold'):
            for scene in ('unknown', 'lobby', 'loading', 'results', 'spectator'):
                self.assertEqual(_ignorante_mouse_gate(Action(name), scene=scene,
                                 gameplay_authorized=True).name, 'wait')
            self.assertEqual(_ignorante_mouse_gate(Action(name), scene='match',
                             gameplay_authorized=False).name, 'wait')
            self.assertEqual(_ignorante_mouse_gate(Action(name), scene='match',
                             gameplay_authorized=True).name, name)
        self.assertEqual(_ignorante_mouse_gate(Action('jump'), scene='unknown',
                         gameplay_authorized=False).name, 'jump')

if __name__ == '__main__':
    unittest.main()
