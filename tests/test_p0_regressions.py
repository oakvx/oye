"""P0 regression tests: truthful readiness, safe continuous recovery, boot smoke."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from _helpers import put_template
from observer.calibration_audit import audit_calibrations
from observer.config import save, validate
from observer.models import Action
from observer.single_match import SingleMatchController
from observer.unknown_watchdog import UnknownWatchdog
from oye_web_app import Dashboard, smoke_test


class ReadinessSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'templates'
        self.folder.mkdir()
        save(self.root / 'config.json', validate({}))
        self.app = Dashboard(self.root)
        for name in ('gioca', 'spettatore_prossimo', 'spettatore_precedente',
                     'hud_salute', 'hud_inventario', 'riscatta'):
            put_template(self.folder, name)

    def tearDown(self):
        self.app.shutdown()
        self.tmp.cleanup()

    def test_valid_templates_really_ready(self):
        self.assertTrue(self.app.readiness()['ready'])

    def test_corrupt_png_cannot_fake_ready(self):
        (self.folder / 'gioca.png').write_bytes(b'not-a-real-png')
        status = self.app.readiness()
        self.assertFalse(status['ready'])
        self.assertIn('gioca', status['missing'])
        self.assertIn('gioca', status['invalid'])
        with self.assertRaisesRegex(ValueError, 'Gioca'):
            self.app.start('auto_cycle', confirmed=True)

    def test_uniform_png_cannot_fake_ready(self):
        import cv2
        import numpy as np
        self.assertTrue(cv2.imwrite(str(self.folder / 'hud_salute.png'),
                                    np.zeros((40, 60, 3), dtype=np.uint8)))
        status = self.app.readiness()
        self.assertFalse(status['ready'])
        self.assertIn('hud_salute', status['invalid'])

    def test_metadata_corruption_blocks_start_even_when_all_png_exist(self):
        (self.folder / '_rois.json').write_text('{invalid:json}', encoding='utf-8')
        status = self.app.readiness()
        self.assertFalse(status['ready'])
        self.assertFalse(status['missing'])
        self.assertIn('_rois.json', status['issues'][0])
        with self.assertRaisesRegex(ValueError, 'Metadati'):
            self.app.start('auto_cycle', confirmed=True)
        teach = self.app.teach_start()
        self.assertEqual(teach['teach']['stage'], 'metadata_error')

    def test_bad_resolution_metadata_cannot_fake_ready(self):
        (self.folder / '_resolution.json').write_text(json.dumps({'gioca': [0,0]}))
        self.assertFalse(self.app.readiness()['ready'])
        self.assertIn('_resolution.json', self.app.readiness()['issues'][0])


class RecoveryTests(unittest.TestCase):
    def new_controller(self, repeat=True):
        return SingleMatchController(repeat=repeat)

    @staticmethod
    def decide(c, now, scene='unknown', menu=None, candidate=None, authorized=False):
        return c.decide(now=now, frame_at=now, scene=scene, menu=menu,
                        candidate=candidate or Action('wait'), authorized=authorized,
                        locked=False, spectator=False)

    def test_join_timeout_repeat_waits_for_verified_lobby_and_retries(self):
        c = self.new_controller()
        play = Action('menu_click', point=(80,100))
        self.assertEqual(self.decide(c, 5, 'lobby', 'gioca', play).name, 'menu_click')
        c.on_executed(play, 'gioca', 5)
        self.decide(c, 7, 'loading')
        res = self.decide(c, 246, 'unknown')
        self.assertEqual(res.name, 'wait')
        self.assertEqual(c.stage, 'joining')
        self.assertEqual(c.recovery_count, 1)
        self.assertEqual(self.decide(c, 247, 'unknown').name, 'wait')
        self.assertEqual(c.recovery_count, 1)
        self.decide(c, 250, 'lobby', 'gioca')
        # Once the next 4 minute watchdog reports a verified lobby,
        # it may go back to await_play; not before.
        self.decide(c, 487, 'lobby', 'gioca')
        self.assertEqual(c.stage, 'await_play')
        self.assertEqual(self.decide(c, 488, 'lobby', 'gioca', play).name, 'wait')
        self.assertEqual(self.decide(c, 495, 'lobby', 'gioca', play).name, 'menu_click')

    def test_join_timeout_one_shot_still_stops(self):
        c = self.new_controller(repeat=False)
        play = Action('menu_click', point=(1,1))
        self.decide(c, 1, 'lobby','gioca',play)
        c.on_executed(play, 'gioca', 1)
        self.decide(c, 242, 'unknown')
        self.assertEqual(c.stage, 'error')

    def test_results_timeout_repeat_waits_and_can_finish(self):
        c = self.new_controller()
        play = Action('menu_click',point=(5,5))
        self.decide(c,1,'lobby','gioca',play)
        c.on_executed(play,'gioca',1)
        self.decide(c,2,'loading')
        for t in (3,4,5): self.decide(c,t,'match',authorized=True)
        for t in (6,7,8): self.decide(c,t,'results')
        self.assertEqual(c.stage,'ending')
        self.assertEqual(self.decide(c,249,'unknown').name,'wait')
        self.assertEqual(c.stage,'ending')
        self.assertEqual(c.recovery_count,1)
        for t in (250,251,252): self.decide(c,t,'lobby','gioca')
        self.assertEqual(c.stage,'post_lobby')
        self.assertEqual(c.matches_completed,1)
        # The lobby is not complete until the delayed reward overlay has had
        # time to appear; no blind PLAY can be sent yet.
        self.assertEqual(self.decide(c,253,'lobby','gioca',play).name,'wait')
        for t in (270,271,272): self.decide(c,t,'lobby','gioca')
        self.assertEqual(c.stage,'await_play')

    def test_unknown_watchdog_repeat_reports_but_never_stops(self):
        g = UnknownWatchdog()
        self.assertEqual(g.observe(now=0,scene='unknown',live=True,timed=False,repeat=True),'ok')
        self.assertEqual(g.observe(now=181,scene='unknown',live=True,timed=False,repeat=True),'recover')
        self.assertEqual(g.observe(now=182,scene='unknown',live=True,timed=False,repeat=True),'waiting')
        self.assertEqual(g.observe(now=362,scene='unknown',live=True,timed=False,repeat=True),'recover')
        self.assertEqual(g.observe(now=363,scene='match',live=True,timed=False,repeat=True),'ok')
        self.assertEqual(g.observe(now=364,scene='unknown',live=True,timed=False,repeat=True),'ok')

    def test_unknown_watchdog_one_shot_stops(self):
        g = UnknownWatchdog()
        self.assertEqual(g.observe(now=0,scene='unknown',live=True,timed=False,repeat=False),'ok')
        self.assertEqual(g.observe(now=181,scene='unknown',live=True,timed=False,repeat=False),'stop')


class PackagedAppSmoke(unittest.TestCase):
    def test_local_server_boots_and_enforces_token(self):
        # The exact same smoke routine is called by the installed Windows EXE.
        self.assertEqual(smoke_test(), 0)


if __name__ == '__main__':
    unittest.main()
