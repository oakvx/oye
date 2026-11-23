"""Oye 33 integration regression: actual controller, OCR, packaging, existing data.

These tests are deliberately offline; they do not claim that Fortnite accepts
Windows synthetic input, nor do they try to bypass anti-cheat.
"""
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from observer.models import Action
from observer.single_match import SingleMatchController
from observer.menu_text import fragment_score, learn_reference, match_reference
from observer.reward_ocr import extract_results_button, is_reward_screen
from observer.upgrade import prepare_update, training_summary

ROOT = Path(__file__).resolve().parents[1]


class IntegratedMatchTests(unittest.TestCase):
    def decide(self, c, now, **kw):
        defaults = dict(frame_at=now, scene='match', menu=None, candidate=Action('wait'),
                        authorized=True, locked=False, spectator=False, reward_overlay=False,
                        reward_scan_confirmed=True, learned_hint=None)
        defaults.update(kw)
        return c.decide(now=now, **defaults)

    def test_reward_title_stops_movement_even_without_results_classifier(self):
        c = SingleMatchController(repeat=True)
        c.stage = 'jumping'
        c.has_entered = True
        blocked = self.decide(c, 101., scene='unknown', authorized=False, reward_pending=True)
        self.assertEqual(blocked.name, 'wait')
        self.assertEqual(c.stage, 'jumping')
        decision = self.decide(c, 102., scene='unknown', authorized=False, reward_overlay=True)
        self.assertEqual(decision.name, 'wait')
        self.assertEqual(c.stage, 'claim_rewards')
        self.assertEqual(c.matches_completed, 1)
        self.assertEqual(self.decide(c, 103., scene='results', authorized=False,
                                     reward_overlay=True).name, 'wait')
        self.assertEqual(c.matches_completed, 1, 'Claim view must not count the same match again')

    def test_reward_title_ignored_before_match_has_ever_started(self):
        c = SingleMatchController(repeat=True)
        c.stage = 'jumping'
        c.has_entered = False
        result = self.decide(c, 100., scene='unknown', authorized=False, reward_overlay=True)
        self.assertEqual(result.name, 'wait')
        self.assertEqual(c.matches_completed, 0)
        self.assertNotEqual(c.stage, 'claim_rewards')

    def test_learned_hint_reaches_explorer_after_authorization(self):
        c = SingleMatchController(repeat=True)
        c.stage = 'exploring'
        c.has_entered = True
        c.explorer.step = 2  # GuidedExplorer tests learned actions once per third step.
        actual = self.decide(c, 100., learned_hint=Action('strafe_left', .55))
        self.assertEqual(actual.name, 'strafe_left')
        self.assertIn('dimostrazioni', actual.reason)

    def test_learned_hint_never_overrides_spectator_or_menu(self):
        c = SingleMatchController(repeat=True)
        c.stage = 'exploring'
        c.has_entered = True
        c.explorer.step = 2
        for at, scene in ((100., 'spectator'), (101., 'lobby'), (102., 'loading')):
            actual = self.decide(c, at, scene=scene, learned_hint=Action('forward', .5), spectator=scene == 'spectator')
            self.assertEqual(actual.name, 'wait')

    def test_learned_shoot_is_not_used_by_navigation(self):
        c = SingleMatchController(repeat=True)
        c.stage = 'exploring'
        c.has_entered = True
        c.explorer.step = 2
        actual = self.decide(c, 100., learned_hint=Action('shoot', .2))
        self.assertNotEqual(actual.name, 'shoot')

    def test_real_sqlite_demonstrations_feed_the_match_policy(self):
        import numpy as np
        from observer.learning import LearningStore, image_features
        from observer.fast_policy import FastActionPolicy
        # The actual policy reads frames from the old SQLite dataset through
        # LearningStore, not from a separate v33 training file.
        with tempfile.TemporaryDirectory() as t:
            store = LearningStore(Path(t))
            frame = np.zeros((360, 640, 3), dtype='uint8')
            frame[60:200, 60:450] = (110, 140, 220)
            for i, action in enumerate(('strafe_left','strafe_left','strafe_left',
                                        'forward','forward')):
                store.record_demonstration(frame, session=f'old_{i}',
                    action=action, process='FortniteClient-Win64-Shipping.exe', scene='match')
            policy = FastActionPolicy(max_distance=.25, agreement=.60)
            policy.refresh(store)
            self.assertEqual(policy.examples, 5)
            suggestion = policy.predict(image_features(frame, '', 'match'))
            self.assertIsNotNone(suggestion)
            self.assertEqual(suggestion.action.name, 'strafe_left')
            controller = SingleMatchController(repeat=True)
            controller.stage = 'exploring'
            controller.has_entered = True
            controller.explorer.step = 2
            result = self.decide(controller, 200., learned_hint=suggestion.action)
            self.assertEqual(result.name, 'strafe_left')

    def test_obstacle_recovery_has_precedence_over_learning(self):
        c = SingleMatchController(repeat=True)
        c.stage = 'exploring'
        c.has_entered = True
        c.explorer.step = 2
        c.explorer.recovery_plan.append(Action('reverse', .3))
        actual = self.decide(c, 100., learned_hint=Action('forward', .5))
        self.assertEqual(actual.name, 'reverse')


class OCRAndPackagingTests(unittest.TestCase):
    def test_results_ocr_accepts_ritorna_alla_lobby(self):
        words = ['RITORNA', 'ALLA', 'LOBBY']
        x = [100, 220, 290]
        data = dict(text=words, conf=['94'] * 3, left=x, top=[20]*3,
                    width=[110, 60, 100], height=[32]*3,
                    block_num=[1]*3, par_num=[1]*3, line_num=[1]*3)
        result = extract_results_button(data, scale=1, left=0, top=0, crop_y=480, original_height=800)
        self.assertIsNotNone(result)
        self.assertEqual(result[2], 'torna_lobby')

    def test_reward_title_is_teachable_non_clickable_reference(self):
        import numpy as np
        frame = np.zeros((800, 1280, 3), dtype='uint8')
        with tempfile.TemporaryDirectory() as t:
            teach = learn_reference(Path(t), 'reward_title', frame.shape, (400, 110, 860, 160), 'RICOMPENSE OTTENUTE')
            match = match_reference(frame, teach, 'reward_title', lambda _: 'RICOMPENSE OTTENUTE')
            self.assertIsNotNone(match)
            self.assertTrue(is_reward_screen('RICOMPENSA OTTENUTA'))
            self.assertFalse(is_reward_screen('RISCATTA') )
            self.assertEqual(fragment_score('EQUIPAGGIA', ['ricompense ottenute']), 0)

    def test_clean_build_has_no_existing_user_template_dependency(self):
        script = (ROOT/'build_exe_windows.bat').read_text(encoding='utf-8')
        self.assertNotIn('--add-data "templates;templates"', script)
        self.assertIn('--add-data "defaults/config.json;."', script)
        self.assertIn('Oye-v'+(ROOT/'VERSION.txt').read_text().strip().split('.')[0]+'-Setup-Windows-x64.exe', script)
        self.assertIn('defaults/config.json', script)

    def test_upgrade_does_not_overwrite_existing_training_or_references(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root/'defaults').mkdir()
            (root/'defaults'/'config.json').write_text('{}')
            (root/'templates').mkdir()
            (root/'templates'/'gioca.png').write_bytes(b'keep template')
            (root/'learning').mkdir()
            db = root/'learning'/'observer.sqlite3'
            with closing(sqlite3.connect(db)) as con, con:
                con.execute('CREATE TABLE frames(id INTEGER PRIMARY KEY, user_label TEXT)')
                con.execute('INSERT INTO frames VALUES (12, "forward")')
            result = prepare_update(root, defaults=root/'defaults')
            self.assertTrue(result['prepared'])
            self.assertTrue(result['backup'])
            self.assertEqual((root/'templates'/'gioca.png').read_bytes(), b'keep template')
            with closing(sqlite3.connect(db)) as con, con:
                self.assertEqual(con.execute('SELECT user_label FROM frames').fetchone()[0], 'forward')
            self.assertEqual(len(list((root/'_OYE_BACKUP').glob('*.zip'))), 1)

    def test_training_summary_does_not_claim_all_frames_are_actions(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root/'learning').mkdir()
            with closing(sqlite3.connect(root/'learning'/'observer.sqlite3')) as db, db:
                db.execute('CREATE TABLE frames(id INTEGER PRIMARY KEY, user_label TEXT, '
                           'action_label TEXT, action_source TEXT, hud_scene TEXT, demo_verified INTEGER)')
                db.executemany('INSERT INTO frames VALUES (?,?,?,?,?,?)', [
                    (1, 'a', None, '', 'match', 0),
                    (2, 'a', 'forward', 'manual', 'match', 0),
                    (3, None, 'strafe_left', 'demo', 'unknown', 0),
                    (4, None, 'strafe_right', 'demo', 'unknown', 1),
                ])
            summary = training_summary(root)
            self.assertEqual(summary['frames'], 4)
            self.assertEqual(summary['trainable_actions'], 2)

if __name__ == '__main__':
    unittest.main()
