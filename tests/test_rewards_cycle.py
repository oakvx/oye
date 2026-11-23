from _helpers import put_template
"""Regressions for the v27 reward -> lobby -> next match workflow."""
import tempfile
from pathlib import Path
import unittest
from unittest import mock
import numpy as np
import cv2
from observer.models import Action
from observer.reward_ocr import is_reward_screen, RewardOCRWorker
from observer.single_match import SingleMatchController
from observer.config import load, save, validate
from oye_web_app import Dashboard


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.c = SingleMatchController(repeat=True, jump_window_seconds=120)
        self.now = 5.

    def tick(self, scene='unknown', menu=None, authorized=False, candidate=None, seconds=1, reward_overlay=False):
        self.now += seconds
        return self.c.decide(now=self.now, frame_at=self.now, scene=scene, menu=menu,
             candidate=candidate or Action('wait'), authorized=authorized,
             locked=False, spectator=False, reward_overlay=reward_overlay)

    def enter(self):
        play = Action('menu_click',point=(100, 100))
        result = self.tick('lobby','gioca',candidate=play)
        self.assertEqual(result.name,'menu_click')
        self.c.on_executed(result, 'gioca', self.now)
        self.tick('loading')
        for _ in range(3): self.tick('match',authorized=True)
        self.assertTrue(self.c.has_entered)

    def test_claim_then_verified_lobby_and_next_match(self):
        self.enter()
        claim = Action('menu_click',point=(510,720))
        for _ in range(3):
            self.assertEqual(self.tick('results','riscatta',candidate=claim).name,'wait')
        self.assertEqual(self.c.stage,'ending')
        play=Action('menu_click',point=(100,100))
        for _ in range(3):
            self.assertEqual(self.tick('lobby','gioca',candidate=play).name,'wait')
        self.assertEqual(self.c.stage,'post_lobby')
        self.assertEqual(self.c.matches_completed,1)
        for _ in range(2):
            self.assertEqual(self.tick('results','riscatta',candidate=claim,
                reward_overlay=True).name,'wait')
        self.assertEqual(self.c.stage,'claim_rewards')
        action=self.tick('results','riscatta',candidate=claim,reward_overlay=True)
        self.assertEqual(action.name,'menu_click')
        self.c.on_executed(action,'riscatta',self.now)
        self.assertEqual(self.c.claim_attempts,1)
        for _ in range(3):
            self.assertEqual(self.tick('lobby','gioca',candidate=play).name,'wait')
        self.assertEqual(self.c.stage,'await_play')
        self.assertFalse(self.c.has_entered)
        self.assertEqual(self.c.elapsed,0)
        self.assertEqual(self.tick('lobby','gioca',candidate=play).name,'wait')
        self.assertEqual(self.tick('lobby','gioca',candidate=play,seconds=10).name,'menu_click')
        self.c.on_executed(play,'gioca',self.now)
        self.assertEqual(self.c.stage,'joining')
        self.tick('loading')
        for _ in range(3): self.tick('match',authorized=True)
        self.assertEqual(self.c.stage,'jumping')
        self.assertEqual(self.c.matches_completed,1)

    def test_no_requeue_from_results_or_unknown(self):
        self.enter()
        for _ in range(3): self.tick('results','riscatta')
        for _ in range(12):
            self.assertEqual(self.tick('unknown','gioca',candidate=Action('menu_click',point=(1,1))).name,'wait')
            self.assertEqual(self.c.stage,'ending')
        self.assertEqual(self.c.matches_completed,0)

    def test_direct_lobby_can_repeat(self):
        self.enter()
        for _ in range(4): self.tick('lobby','gioca')
        self.assertEqual(self.c.stage,'jumping')
        self.tick('lobby','gioca')
        self.assertEqual(self.c.matches_completed,1)
        self.assertEqual(self.c.stage,'post_lobby')
        for _ in range(3): self.tick('lobby','gioca',seconds=7)
        self.assertEqual(self.c.stage,'await_play')

    def test_stale_frame_does_not_increment_lobby_confirmation(self):
        self.enter()
        for _ in range(3): self.tick('results','riscatta')
        a=self.tick('lobby','gioca')
        old=self.c.last_frame_at
        for _ in range(20):
            self.assertEqual(self.c.decide(now=self.now+1,frame_at=old,
                scene='lobby',menu='gioca',candidate=Action('menu_click',point=(2,3)),
                authorized=False, locked=False,spectator=False).name,'wait')
        self.assertEqual(self.c.stable_lobby,1)


class RewardOCRTests(unittest.TestCase):
    def test_claim_equippa_does_not_confuse(self):
        self.assertTrue(is_reward_screen('RICOMPENSE OTTENUTE\nEQUIPAGGIA     RISCATTA'))
        self.assertTrue(is_reward_screen('ITEMS UNLOCKED\nEQUIP    CLAIM'))
        self.assertFalse(is_reward_screen('EQUIPAGGIA'))
        self.assertFalse(is_reward_screen('RIS CATTA'))
        self.assertFalse(is_reward_screen('Premi RISCATTA nel negozio'))

    def test_ocr_worker_on_reward_panel(self):
        # Integration with the real Tesseract binary, if installed.
        from observer.reward_ocr import ocr_available
        if not ocr_available():
            self.skipTest('Tesseract o pytesseract non installato')
        import time
        frame = np.full((650, 1050, 3), (27, 32, 45), dtype=np.uint8)
        cv2.putText(frame, 'RICOMPENSE OTTENUTE', (175, 180), cv2.FONT_HERSHEY_SIMPLEX,
                    1.25, (255,255,255), 3, cv2.LINE_AA)
        cv2.rectangle(frame, (215, 480), (460, 555), (55, 125, 215), -1)
        cv2.rectangle(frame, (590, 480), (855, 555), (50, 165, 215), -1)
        cv2.putText(frame,'EQUIPAGGIA',(230,528),cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),2)
        cv2.putText(frame,'RISCATTA',(623,528),cv2.FONT_HERSHEY_SIMPLEX,.78,(255,255,255),2)
        worker=RewardOCRWorker('eng')
        worker.start()
        try:
            self.assertTrue(worker.submit(frame,0,0,time.monotonic()))
            got=None
            for _ in range(65):
                got=worker.take()
                if got:break
                time.sleep(.1)
            self.assertIsNotNone(got)
            self.assertIsNotNone(got.center,got.status)
            self.assertEqual(got.name,'riscatta')
            self.assertTrue(got.reward_screen)
            self.assertTrue(got.center[0]>540)
        finally:
            worker.stop()


class DashboardModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'templates').mkdir()
        save(self.root/'config.json',validate({}))
        self.d=Dashboard(self.root)
    def tearDown(self):
        self.d.shutdown();self.tmp.cleanup()

    def test_can_teach_claim_as_optional(self):
        for name in ['gioca','spettatore_prossimo','spettatore_precedente',
                     'hud_salute','hud_inventario']:
            put_template(self.root/'templates', name)
        with mock.patch('oye_web_app.ocr_available',return_value=True):
            self.assertTrue(self.d.readiness()['ready'])
        status=self.d.teach_start('riscatta')
        self.assertTrue(status['teach']['active'])
        self.assertEqual(status['teach']['target'],'riscatta')

    def test_no_results_template_when_ocr_available(self):
        with mock.patch('oye_web_app.ocr_available',return_value=True):
            self.assertNotIn('riscatta',self.d.readiness()['missing'])
        with mock.patch('oye_web_app.ocr_available',return_value=False):
            self.assertIn('riscatta',self.d.readiness()['missing'])

class BrainMenuProposalTests(unittest.TestCase):
    def test_unexecuted_play_does_not_consume_retries(self):
        from observer.brain import Brain
        from observer.models import Observation, Match
        brain=Brain(auto_requeue=True,menu_cooldown=16,menu_retries=2)
        menu=Match('gioca',.99,(200,200))
        obs=Observation(True,'lobby',menu,None,False,False,False)
        for tick in range(1,6):
            a=brain.decide(obs,float(tick))
        self.assertEqual(a.name,'menu_click')
        self.assertEqual(brain.menu_attempts['gioca'],1)
        brain.discard_unexecuted_menu_click(a,menu)
        self.assertIsNone(brain.pending_menu)
        self.assertEqual(brain.menu_attempts['gioca'],0)
        # Higher-level 6-second lobby cooldown expires; Brain can propose again.
        for tick in range(6,9):
            a=brain.decide(obs,float(tick))
        self.assertEqual(a.name,'menu_click')
        self.assertEqual(brain.menu_attempts['gioca'],1)
