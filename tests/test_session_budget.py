import unittest
from observer.session_budget import SessionBudget


class SessionBudgetTests(unittest.TestCase):
    def test_cycle_rollover_by_duration_does_not_stop(self):
        b = SessionBudget(max_seconds=60, max_actions=10, repeat=True, started_at=100)
        self.assertEqual(b.check(159, 0).kind, 'ok')
        self.assertEqual(b.check(160, 0).kind, 'rollover')
        self.assertEqual(b.rollovers, 1)
        self.assertEqual(b.check(161, 0).kind, 'ok')
        self.assertEqual(b.check(220, 0).kind, 'rollover')
        self.assertEqual(b.rollovers, 2)

    def test_cycle_rollover_by_actions(self):
        b = SessionBudget(max_seconds=3600, max_actions=5, repeat=True, started_at=100)
        self.assertEqual(b.check(101, 4).kind, 'ok')
        self.assertEqual(b.check(102, 5).kind, 'rollover')
        self.assertEqual(b.check(102, 0).kind, 'ok')

    def test_single_match_still_bounded(self):
        b = SessionBudget(max_seconds=60, max_actions=5, repeat=False, started_at=100)
        self.assertEqual(b.check(160, 0).kind, 'stop')
        self.assertEqual(b.check(120, 5).kind, 'stop')
        self.assertEqual(b.rollovers, 0)

    def test_rollover_does_not_reset_unrelated_match_state(self):
        from observer.single_match import SingleMatchController
        c = SingleMatchController(repeat=True)
        c.stage = 'ending'
        c.matches_completed = 12
        b = SessionBudget(max_seconds=10, max_actions=1, repeat=True, started_at=0)
        self.assertEqual(b.check(11, 1).kind, 'rollover')
        self.assertEqual(c.stage, 'ending')
        self.assertEqual(c.matches_completed, 12)


if __name__ == '__main__': unittest.main()
