import unittest
from observer.command_policy import CommandPolicy
from observer.models import Match


class CommandPolicyTests(unittest.TestCase):
    def test_requires_confirmed_ground_and_never_moves_in_menu(self):
        policy = CommandPolicy(seed=4)
        for scene in ('unknown', 'lobby', 'loading', 'spectator', 'results'):
            self.assertEqual(policy.choose(scene=scene, authorized=True, now=1).name, 'wait')
        self.assertEqual(policy.choose(scene='match', authorized=False, now=2).name, 'wait')
        self.assertEqual(policy.choose(scene='match', authorized=True, phase='skydiving', now=3).name, 'wait')
        self.assertEqual(policy.choose(scene='match', authorized=True, blocked=True, now=4).name, 'wait')
        self.assertEqual(policy.choose(scene='match', authorized=True, now=5).name, 'equip_pickaxe')

    def test_interaction_requires_visible_prompt_and_has_cooldown(self):
        policy = CommandPolicy(seed=4)
        prompt = Match('interazione', .95, (100, 100))
        self.assertEqual(policy.choose(scene='match', authorized=True, now=1, interaction=prompt).name, 'interact')
        self.assertNotEqual(policy.choose(scene='match', authorized=True, now=2, interaction=prompt).name, 'interact')
        self.assertEqual(policy.choose(scene='match', authorized=True, now=5, interaction=prompt).name, 'interact')
        weak = Match('interazione', .4, (100, 100))
        self.assertNotEqual(policy.choose(scene='match', authorized=True, now=10, interaction=weak).name, 'interact')

    def test_ready_without_training_and_no_combat_or_menu_clicks(self):
        policy = CommandPolicy(seed=4)
        actions = [policy.choose(scene='match', authorized=True, now=i, harvest=False) for i in range(60)]
        names = {a.name for a in actions}
        self.assertTrue({'patrol_forward', 'strafe_left', 'strafe_right', 'reverse', 'patrol_hop'} <= names)
        self.assertFalse(names & {'shoot', 'menu_click', 'harvest', 'patrol_harvest', 'equip_pickaxe'})
        self.assertTrue(all(a.seconds <= .65 for a in actions))
        self.assertTrue(all('preimpostati' in a.reason for a in actions))
