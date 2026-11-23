from pathlib import Path
import tempfile
import unittest

from observer.screen_profiles import ScreenProfiles
from tools.import_calibration import import_rules


class CalibrationImportTests(unittest.TestCase):
    def test_dry_run_and_import_preserve_existing_and_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profiles = ScreenProfiles(root)
            profiles.select(1920, 1080, mode='borderless')
            profiles.add_rule('gioca', [.1, .1, .3, .3], expected_text='MIA', image_size=(1920, 1080))
            database = root / 'learning' / 'observer.sqlite3'
            added, skipped = import_rules(database)
            self.assertEqual(skipped, ['gioca'])
            self.assertEqual(len(added), 4)
            self.assertEqual(len(profiles.rules()), 1)
            import_rules(database, apply=True)
            rules = {r['target']: r for r in profiles.rules()}
            self.assertEqual(len(rules), 5)
            self.assertEqual(rules['gioca']['expected_text'], 'MIA')
            self.assertEqual(import_rules(database, apply=True)[0], [])

    def test_wrong_resolution_or_missing_db_never_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / 'learning' / 'observer.sqlite3'
            with self.assertRaises(ValueError):
                import_rules(database, apply=True)
            self.assertFalse(database.exists())
            profiles = ScreenProfiles(root)
            profiles.select(1280, 720, mode='borderless')
            with self.assertRaises(ValueError):
                import_rules(database, apply=True)
            self.assertEqual(profiles.rules(), [])
