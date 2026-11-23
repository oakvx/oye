"""Three simulated cycles through the production lifecycle and menu supervisor.

This is a controller simulation, not a real screenshot or Fortnite input test.
"""
import unittest
from observer.models import Action, Match
from observer.menu_supervisor import MenuSupervisor
from observer.single_match import SingleMatchController


class ThreeCycleTests(unittest.TestCase):
    def test_three_consecutive_direct_requeues(self):
        c = SingleMatchController(repeat=True, quick_start=True, quick_wait_play_exit=True,
                                  quick_loading_seconds=120)
        c.arm_quick(0)
        supervisor = MenuSupervisor()
        dispatches = []

        def frame(t, scene, button=None, play=None):
            matches = {button: Match(button, .96, (400, 700))} if button else {}
            proposal = supervisor.offer(now=t, frame_at=t, stage=c.stage, scene=scene,
                matches=matches, repeat=True, prefer_requeue=True)
            if supervisor.last_completed == 'gioca_di_nuovo':
                c.on_requeue_confirmed(t)
            target = proposal.name if proposal.action.name == 'menu_click' else None
            action = c.decide(now=t, frame_at=t, scene=scene, menu=target or button,
                candidate=proposal.action if target else Action('wait'),
                menu_candidate_authorized=target is not None,
                authorized=False, locked=False, spectator=False,
                play_visible=play, play_point=matches['gioca'].center if button == 'gioca' else None)
            if action.name == 'menu_click':
                dispatches.append(target)
                supervisor.on_executed(target, action, now=t, scene=scene)
                c.on_executed(action, target, t)
            return action

        for t in (1, 2, 3):
            frame(t, 'lobby', 'gioca', True)
        self.assertEqual(dispatches, ['gioca'])
        for t in (4, 5, 6):
            frame(t, 'unknown', play=False)
        start = 126
        for cycle in range(3):
            self.assertEqual(frame(start, 'unknown', play=False).name, 'jump')
            for t in (start + 1, start + 2, start + 3):
                frame(t, 'results')
            self.assertEqual(c.stage, 'ending')
            for t in (start + 4, start + 5, start + 6):
                frame(t, 'results', 'gioca_di_nuovo')
            self.assertEqual(dispatches.count('gioca_di_nuovo'), cycle + 1)
            frame(start + 7, 'loading')
            frame(start + 8, 'loading')
            self.assertEqual(c.stage, 'joining')
            self.assertEqual(c.matches_completed, cycle + 1)
            start += 128
        self.assertEqual(dispatches, ['gioca'] + ['gioca_di_nuovo'] * 3)

if __name__ == '__main__':
    unittest.main()
