"""The committed seed contains only preliminary calibration metadata."""
from pathlib import Path
import sqlite3
import unittest


class CalibrationSeedTests(unittest.TestCase):
    def test_seed_is_small_isolated_and_contains_no_personal_images(self):
        path = Path(__file__).resolve().parents[1] / 'assets/calibration/profilo_preliminare_1920x1080.sqlite3'
        self.assertLess(path.stat().st_size, 100_000)
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {'oye_resolution_profiles', 'oye_screen_rules', 'sqlite_sequence'})
            profile = db.execute('SELECT width,height,mode FROM oye_resolution_profiles WHERE selected=1').fetchone()
            self.assertEqual(profile, (1920, 1080, 'borderless'))
            rules = dict(db.execute('SELECT target,expected_text FROM oye_screen_rules'))
            self.assertEqual(rules, {'gioca': 'PRONTO', 'torna_lobby': 'RITORNA ALLA LOBBY',
                'gioca_di_nuovo': 'GIOCA DI NUOVO', 'caricamento': 'CONNESSIONE IN CORSO',
                'discesa_apri': 'PER SCHIERARE COMETA CADENTE'})
        finally:
            db.close()
