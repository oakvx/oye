"""Ignorante favours a verified direct requeue, without blind clicks."""
import unittest
from observer.menu_supervisor import MenuSupervisor
from observer.models import Match
from observer.single_match import SingleMatchController


def offer(supervisor, t, matches, **kwargs):
    return supervisor.offer(now=t, frame_at=t, stage='ending', scene='results',
        matches=matches, repeat=True, prefer_requeue=True, **kwargs)


class IgnoranteRequeueTests(unittest.TestCase):
    def test_direct_requeue_has_priority_and_requires_confirmation(self):
        m = MenuSupervisor()
        buttons = {'gioca_di_nuovo': Match('gioca_di_nuovo', .9, (30, 40)),
                   'torna_lobby': Match('torna_lobby', .9, (50, 60))}
        for t in (1, 2):
            self.assertEqual(offer(m, t, buttons).action.name, 'wait')
        p = offer(m, 3, buttons)
        self.assertEqual((p.name, p.action.point), ('gioca_di_nuovo', (30, 40)))
        m.on_executed(p.name, p.action, now=3, scene='results')
        for t in (4, 5):
            m.offer(now=t, frame_at=t, stage='ending', scene='loading', matches={},
                    repeat=True, prefer_requeue=True)
        self.assertEqual(m.last_completed, 'gioca_di_nuovo')
        c = SingleMatchController(repeat=True, quick_start=True, quick_wait_play_exit=True)
        c.stage = 'ending'
        c.on_requeue_confirmed(5)
        self.assertEqual(c.stage, 'joining')
        self.assertEqual(c.matches_completed, 1)
        self.assertTrue(c.quick_departed)

    def test_rewards_and_spectator_veto_requeue(self):
        items = {'gioca_di_nuovo': Match('gioca_di_nuovo', .9, (30, 40))}
        for kw in ({'reward_overlay': True}, {'spectator': True}, {'locked': True}):
            m = MenuSupervisor()
            for t in (1, 2, 3, 4):
                self.assertEqual(offer(m, t, items, **kw).action.name, 'wait')

    def test_lobby_fallback_when_requeue_not_found(self):
        m = MenuSupervisor()
        buttons = {'torna_lobby': Match('torna_lobby', .9, (50, 60))}
        for t in (1, 2, 3):
            p = offer(m, t, buttons)
        self.assertEqual(p.name, 'torna_lobby')

if __name__ == '__main__':
    unittest.main()
