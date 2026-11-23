"""Offline tests for V44 training review: the old SQLite is preserved.

These tests do not run Fortnite or validate the policy's accuracy on unseen games.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from observer.learning import LearningStore
from observer.fast_policy import FastActionPolicy
from oye_web_app import Dashboard


def image(v: int = 0) -> np.ndarray:
    base = np.indices((90, 160)).sum(axis=0).astype(np.uint8)
    return np.repeat(((base + v) % 255)[:,:,None], 3, axis=2)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'learning'
        self.store = LearningStore(self.folder)

    def demo(self, action='forward', scene='unknown', session='a', v=0):
        return self.store.record_demonstration(image(v), action=action,
            session=session,process='Fortnite.exe',scene=scene)[0]

    def test_new_unknown_requires_explicit_one_frame_review(self):
        fid = self.demo(scene='unknown')
        before = self.store.training_review()
        self.assertEqual((before['pending'],before['usable']), (1,0))
        self.assertNotIn('forward', [a for _,a,_,_ in self.store.action_training_samples()])
        self.assertTrue(before['items'][0]['has_image'])
        self.store.review_demonstration(fid,session='a',decision='accept')
        self.assertEqual(self.store.training_review()['usable'],1)
        self.assertEqual(self.store.training_review()['pending'],0)
        self.assertIn('forward',[a for _,a,_,_ in self.store.action_training_samples()])
        # Persist across process/store restart.
        reopened = LearningStore(self.folder)
        self.assertEqual(reopened.training_review()['usable'],1)

    def test_reject_keeps_image_but_removes_sample_from_policy(self):
        fid = self.demo(scene='match')
        self.assertEqual(len(self.store.action_training_samples()),1)
        frame_file = self.store.recent()[0]['image_file']
        self.store.review_demonstration(fid,session='a',decision='exclude')
        self.assertEqual(self.store.action_training_samples(),[])
        self.assertTrue((self.folder/'frames'/frame_file).exists())
        self.assertEqual(self.store.training_review(filter='all')['excluded'],1)
        self.store.review_demonstration(fid,session='a',decision='reset')
        self.assertEqual(len(self.store.action_training_samples()),1)

    def test_known_menus_cannot_be_promoted_to_training(self):
        for scene in ('lobby','loading','results','spectator','unfocused'):
            fid=self.demo(scene=scene,v=len(scene))
            with self.assertRaisesRegex(ValueError, 'non giocabile'):
                self.store.review_demonstration(fid,session='a',decision='accept')
        self.assertEqual(self.store.training_review()['blocked'],5)
        self.assertFalse(self.store.action_training_samples())

    def test_labeled_spectator_overrides_wrong_match_detection(self):
        fid=self.demo(scene='match')
        self.store.correct(fid,'Spettatore')
        self.assertEqual(self.store.action_training_samples(),[])
        with self.assertRaisesRegex(ValueError,'etichettata'):
            self.store.review_demonstration(fid,session='a',decision='accept')

    def test_action_correction_does_not_revert_to_recorded_controls(self):
        fid=self.store.record_demonstration(image(),action='shoot',scene='unknown',
            session='a',process='Fortnite.exe',controls=('shoot','forward'))[0]
        self.store.review_demonstration(fid,session='a',decision='accept',action='jump')
        samples=self.store.action_training_samples()
        self.assertEqual(len(samples),1)
        self.assertEqual(samples[0][1],'jump')
        self.assertEqual(samples[0][3],'manual')

    def test_never_update_another_session_or_bad_commands(self):
        fid=self.demo(session='one')
        with self.assertRaisesRegex(ValueError,'non trovato'):
            self.store.review_demonstration(fid,session='other',decision='exclude')
        with self.assertRaisesRegex(ValueError,'non consentita'):
            self.store.review_demonstration(fid,session='one',decision='accept',action='aim')
        self.assertEqual(self.store.training_review(session='one')['pending'],1)

    def test_missing_image_must_not_be_confirmed_if_scene_unknown(self):
        fid=self.demo()
        imagefile=self.store.recent()[0]['image_file']
        (self.folder/'frames'/imagefile).unlink()
        with self.assertRaisesRegex(ValueError,'Screenshot mancante'):
            self.store.review_demonstration(fid,session='a',decision='accept')
        self.assertFalse(self.store.training_review()['items'][0]['has_image'])

    def test_review_statistics_and_policies_update_on_revision(self):
        ids = [self.demo(action=a,scene='unknown',v=i*13) for i,a in enumerate(('forward','jump','harvest'))]
        rev=self.store.revision()
        for fid in ids:self.store.review_demonstration(fid,session='a',decision='accept')
        review=self.store.training_review()
        self.assertTrue(review['policy_ready'])
        self.assertGreater(self.store.revision(),rev)
        policy=FastActionPolicy();policy.refresh(self.store)
        self.assertEqual(policy.examples,3)
        self.store.review_demonstration(ids[2],session='a',decision='exclude')
        policy.refresh(self.store)
        self.assertEqual(policy.examples,2)
        self.assertFalse(self.store.training_review()['policy_ready'])

    def test_queue_balances_action_types_instead_of_only_latest_key(self):
        self.demo(action='jump',scene='unknown',v=4)
        for i in range(16):
            self.demo(action='forward',scene='unknown',v=i+25)
        report=self.store.training_review(limit=2)
        self.assertEqual(report['pending'],17)
        self.assertEqual(len(report['items']),2)
        self.assertEqual({r['action_label'] for r in report['items']},{'forward','jump'})

    def test_paginated_review_queue_reaches_older_examples(self):
        for i in range(19):
            self.demo(action='jump' if i%2 else 'forward',scene='unknown',v=i*9)
        first=self.store.training_review(limit=12,offset=0)
        second=self.store.training_review(limit=12,offset=12)
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        ids=[i['id'] for i in first['items']+second['items']]
        self.assertEqual(len(ids),19)
        self.assertEqual(len(set(ids)),19)

    def test_read_only_training_summary_does_not_count_excluded(self):
        from observer.upgrade import training_summary
        fid=self.demo(scene='match')
        self.assertEqual(training_summary(Path(self.temp.name))['trainable_actions'],1)
        self.store.review_demonstration(fid,session='a',decision='exclude')
        self.assertEqual(training_summary(Path(self.temp.name))['trainable_actions'],0)

    def test_additive_migration_does_not_erase_existing_data(self):
        fid=self.demo()
        with sqlite3.connect(self.store.path) as db:
            db.execute('ALTER TABLE frames DROP COLUMN training_excluded')
            db.execute('ALTER TABLE frames DROP COLUMN training_reviewed_at')
        again=LearningStore(self.folder)
        self.assertEqual(again.training_review()['pending'],1)
        self.assertEqual(again.training_review()['items'][0]['id'],fid)
        with sqlite3.connect(self.store.path) as db:
            names={row[1] for row in db.execute('PRAGMA table_info(frames)')}
        self.assertIn('training_excluded',names)
        self.assertIn('training_reviewed_at',names)

    def test_demo_recording_original_fields_preserved(self):
        fid=self.demo()
        old=self.store.recent()[0]
        self.store.review_demonstration(fid,session='a',decision='exclude')
        new=self.store.recent()[0]
        for key in ('captured_at','image_file','feature' if 'feature' in old else 'hud_scene',
                    'action_label','observed_controls','predicted_event'):
            self.assertEqual(old[key],new[key])

    def test_dashboard_report_and_review_uses_same_database(self):
        fid=self.demo()
        dash=Dashboard(Path(self.temp.name))
        result=dash.learning_review()
        self.assertEqual(result['pending'],1)
        self.assertIn('jump',result['available_actions'])
        resp=dash.learning_review_action(dict(id=fid,session='a',decision='accept'))
        self.assertTrue(resp['ok'])
        self.assertEqual(dash.learning_review()['usable'],1)


if __name__ == '__main__':unittest.main()
