"""Specific v32 workflow tests. No Fortnite executable or user data required."""
from __future__ import annotations
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from observer.models import Action
from observer.single_match import SingleMatchController
from observer.cycle_db import CycleStore
from observer.reward_ocr import is_reward_screen
from observer.resume import ResumeGate, save_checkpoint, load_checkpoint


class RewardCycleSQLTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Path(self.temp.name)/'learning'/'observer.sqlite3'
        self.db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('CREATE TABLE frames (id INTEGER PRIMARY KEY, user_label TEXT)')
            conn.execute("INSERT INTO frames VALUES (123,'jump')")
            conn.execute('CREATE TABLE agent_decisions (id INTEGER PRIMARY KEY, action TEXT)')
            conn.execute("INSERT INTO agent_decisions VALUES (3,'forward')")
        self.store=CycleStore(self.db)
        self.controller=SingleMatchController(repeat=True,policy=self.store.prepare())
        self.now=100.

    def tearDown(self):
        self.temp.cleanup()

    def tick(self, scene='unknown', menu=None, candidate=None, reward=False, seconds=1.):
        self.now+=seconds
        return self.controller.decide(now=self.now,frame_at=self.now,scene=scene,menu=menu,
            candidate=candidate or Action('wait'),authorized=False,locked=False,
            spectator=False,reward_overlay=reward)

    def finish_match(self):
        c=self.controller
        c.stage='ending';c.has_entered=True;c.results_at=self.now-5
        for _ in range(3):self.tick('lobby','gioca')
        self.assertEqual(c.stage,'post_lobby')
        self.assertEqual(c.matches_completed,1)

    def test_existing_training_is_untouched(self):
        self.store.prepare();self.store.prepare()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT id,user_label FROM frames').fetchall(),[(123,'jump')])
            self.assertEqual(conn.execute('SELECT id,action FROM agent_decisions').fetchall(),[(3,'forward')])
            self.assertEqual(conn.execute('SELECT count(*) FROM oye_cycle_rules').fetchone()[0],8)

    def test_rules_are_actually_loaded_from_sqlite(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE oye_cycle_rules SET wait_seconds=25,confirmations=4 "
                         "WHERE stage='post_lobby' AND event='lobby_without_rewards'")
        self.controller.policy=self.store.prepare()
        self.finish_match()
        for _ in range(3):self.tick('lobby','gioca',seconds=8)
        self.assertEqual(self.controller.stage,'post_lobby')
        self.tick('lobby','gioca',seconds=2)
        self.assertEqual(self.controller.stage,'await_play')

    def test_lobby_waits_eighteen_seconds_without_reward(self):
        self.finish_match()
        play=Action('menu_click',point=(10,20))
        for _ in range(3):self.assertEqual(self.tick('lobby','gioca',play,seconds=4).name,'wait')
        self.assertEqual(self.controller.stage,'post_lobby')
        self.tick('lobby','gioca',play,seconds=6)
        self.assertEqual(self.controller.stage,'await_play')
        self.assertEqual(self.tick('lobby','gioca',play).name,'wait')
        self.assertEqual(self.tick('lobby','gioca',play,seconds=6).name,'menu_click')

    def test_reveal_after_lobby_riscatta_once(self):
        self.finish_match()
        claim=Action('menu_click',point=(650,480))
        self.assertEqual(self.tick('results','riscatta',claim,reward=True).name,'wait')
        self.assertEqual(self.controller.stage,'post_lobby')
        self.assertEqual(self.tick('results','riscatta',claim,reward=True).name,'wait')
        self.assertEqual(self.controller.stage,'claim_rewards')
        click=self.tick('results','riscatta',claim,reward=True)
        self.assertEqual(click.name,'menu_click')
        self.controller.on_executed(click,'riscatta',self.now)
        for _ in range(4):
            self.assertEqual(self.tick('results','riscatta',claim,reward=True).name,'wait')
        self.assertEqual(self.controller.claim_attempts,1)
        for _ in range(3):self.assertEqual(self.tick('lobby','gioca').name,'wait')
        self.assertEqual(self.controller.stage,'await_play')
        self.assertEqual(self.controller.matches_completed,1)

    def test_no_reward_title_and_equip_button_cannot_click(self):
        self.finish_match()
        equip=Action('menu_click',point=(320,500))
        for _ in range(8):
            self.assertEqual(self.tick('results','equipaggia',equip).name,'wait')
        self.assertEqual(self.controller.stage,'post_lobby')
        for _ in range(2):self.tick('results','equipaggia',equip,reward=True)
        self.assertEqual(self.controller.stage,'claim_rewards')
        for _ in range(4):
            self.assertEqual(self.tick('results','equipaggia',equip,reward=True).name,'wait')
        self.assertEqual(self.controller.claim_attempts,0)

    def test_claim_click_failure_is_bounded(self):
        self.finish_match()
        claim=Action('menu_click',point=(650,500))
        for _ in range(2):self.tick('results','riscatta',claim,reward=True)
        click=self.tick('results','riscatta',claim,reward=True)
        self.controller.on_executed(click,'riscatta',self.now)
        self.assertEqual(self.tick('results','riscatta',claim,reward=True,seconds=5).name,'wait')
        click=self.tick('results','riscatta',claim,reward=True,seconds=13)
        self.assertEqual(click.name,'menu_click')
        self.controller.on_executed(click,'riscatta',self.now)
        for _ in range(4):
            self.assertEqual(self.tick('results','riscatta',claim,reward=True,seconds=13).name,'wait')
        self.assertEqual(self.controller.claim_attempts,2)

    def test_no_play_on_unknown_screen(self):
        self.finish_match()
        play=Action('menu_click',point=(12,13))
        self.assertEqual(self.tick('unknown','gioca',play,seconds=30).name,'wait')
        self.assertEqual(self.controller.stage,'post_lobby')

    def test_ocr_unavailable_must_not_skip_reward_check(self):
        self.finish_match()
        play=Action('menu_click',point=(12,34))
        for _ in range(8):
            self.now += 3
            decision=self.controller.decide(now=self.now,frame_at=self.now,
                scene='lobby',menu='gioca',candidate=play,authorized=False,
                locked=False,spectator=False,reward_scan_confirmed=False)
            self.assertEqual(decision.name,'wait')
        self.assertEqual(self.controller.stage,'post_lobby')

    def test_rewards_cover_lobby_immediately_after_results(self):
        c=self.controller;c.stage='ending';c.has_entered=True;c.results_at=self.now-2
        for _ in range(2):
            self.assertEqual(self.tick('results','riscatta',reward=True).name,'wait')
        self.assertEqual(c.stage,'post_lobby')
        self.assertEqual(c.matches_completed,1)
        for _ in range(2):self.tick('results','riscatta',reward=True)
        self.assertEqual(c.stage,'claim_rewards')

    def test_sqlite_history_records_stage_and_click(self):
        self.store.record(stage='post_lobby',previous_stage='ending',event='lobby_confirmed_check_rewards',matches_completed=4)
        self.store.record(stage='claim_rewards',previous_stage='post_lobby',event='reward_title_confirmed',matches_completed=4)
        self.store.event(stage='claim_rewards',event='riscatta_clicked',matches_completed=4)
        self.store.record(stage='claim_rewards',previous_stage='claim_rewards',event='no_change',matches_completed=4)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT stage,matches_completed FROM oye_cycle_state').fetchone(),('claim_rewards',4))
            self.assertEqual([x[0] for x in conn.execute('SELECT event FROM oye_cycle_history ORDER BY id')],
                ['lobby_confirmed_check_rewards','reward_title_confirmed','riscatta_clicked'])

    def test_resume_after_lobby_is_post_lobby_not_play(self):
        c=self.controller
        gate=ResumeGate(active=True,previous_stage='claim_rewards')
        for n in range(3):
            result=gate.reconcile(c,now=900+n,frame_at=900+n,scene='lobby',menu='gioca',
                authorized=False,locked=False,spectator=False)
        self.assertTrue(result)
        self.assertEqual(c.stage,'post_lobby')
        self.assertEqual(c.post_lobby_at,902)
        self.assertEqual(self.tick('lobby','gioca',Action('menu_click',point=(10,10))).name,'wait')

    def test_reward_title_singular_plural_and_partial(self):
        for title in ['RICOMPENSA OTTENUTA','RICOMPENSE OTTENUTE',
                      'RICOMPENSA OTTEN','REWARD OBTAINED']:
            self.assertTrue(is_reward_screen(title),title)
        for title in ['RISCATTA OFFERTE', 'EQUIPAGGIA', 'EQUIPAGGIA RISCATTA', 'RITORNA ALLA LOBBY']:
            self.assertFalse(is_reward_screen(title),title)


if __name__=='__main__':unittest.main()
