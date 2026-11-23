"""Bounded click/verify/retry for EVERY menu CTA; no invented coordinates."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from observer.menu_supervisor import MenuSupervisor
from observer.models import Match, Action
from observer.single_match import SingleMatchController
from observer.vision import Reading
from observer.menu_text import match_reference
from observer.reward_ocr import extract_results_button


def hit(target='gioca', point=(412, 750)):
    return {target: Match(target, .97, point)}


def offer(engine, t, stage='await_play', scene='lobby', matches=None, **kw):
    return engine.offer(now=float(t),frame_at=float(t),stage=stage,scene=scene,
        matches=matches if matches is not None else hit(), repeat=True, **kw)


class AllMenuTests(unittest.TestCase):
    def test_play_requires_3_new_frames_and_actual_driver_send(self):
        m=MenuSupervisor()
        for t in (1,2):
            self.assertEqual(offer(m,t).action.name,'wait')
        offered=offer(m,3)
        self.assertEqual(offered.action.name,'menu_click')
        self.assertEqual(offered.name,'gioca')
        self.assertEqual(offered.action.point,(412,750))
        self.assertIsNone(m.pending)  # just a proposal, not a sent click
        m.on_executed('gioca',offered.action,now=3,scene='lobby')
        self.assertEqual(m.pending.attempts,1)
        self.assertEqual(offer(m,4).action.name,'wait')
        self.assertEqual(offer(m,6,scene='unknown',matches={}).action.name,'wait')
        self.assertIsNotNone(m.pending) # unknown != success
        for t in (10,14):
            self.assertEqual(offer(m,t).action.name,'wait')
        self.assertEqual(offer(m,15.1).action.name,'menu_click')
        # unsuccessful driver run -> no attempt increase
        self.assertEqual(m.pending.attempts,1)
        m.on_executed('gioca',offer(m,15.2).action,now=15.2,scene='lobby')
        self.assertEqual(m.pending.attempts,2)

    def test_three_clicks_max_and_only_when_target_present(self):
        m=MenuSupervisor()
        for t in (1,2,3):a=offer(m,t)
        m.on_executed('gioca',a.action,now=3,scene='lobby')
        for t in (4,5,10,14,16):a=offer(m,t)
        self.assertEqual(a.action.name,'menu_click')
        m.on_executed('gioca',a.action,now=16,scene='lobby')
        for t in (17,18,23,28,29):a=offer(m,t)
        self.assertEqual(a.action.name,'menu_click')
        m.on_executed('gioca',a.action,now=29,scene='lobby')
        self.assertEqual(offer(m,42).action.name,'wait')
        self.assertIn('3 clic',offer(m,43).detail)

    def test_results_have_own_click_and_prioritize_lobby(self):
        m=MenuSupervisor()
        items={**hit('gioca_di_nuovo',(200,500)),**hit('continua',(230,500)),**hit('torna_lobby',(400,500))}
        for t in (1,2,3): p=offer(m,t,stage='ending',scene='results',matches=items)
        self.assertEqual(p.name,'torna_lobby')
        self.assertEqual(p.action.point,(400,500))
        m.on_executed('torna_lobby',p.action,now=3,scene='results')
        for t in (4,5):
            self.assertEqual(offer(m,t,stage='ending',scene='results',matches=items).action.name,'wait')
        self.assertEqual(m.pending.attempts,1)
        # Successful return must be confirmed by TWO distinct subsequent frames
        a=offer(m,6,stage='ending',scene='lobby',matches=hit('gioca'))
        self.assertIsNone(m.last_completed)
        a=offer(m,7,stage='ending',scene='lobby',matches=hit('gioca'))
        self.assertEqual(m.last_completed,'torna_lobby')
        self.assertIsNone(m.pending)

    def test_continue_then_another_result_screen_then_lobby(self):
        m=MenuSupervisor()
        for t in (1,2,3):p=offer(m,t,stage='ending',scene='results',matches=hit('continua',(350,560)))
        self.assertEqual(p.name,'continua')
        m.on_executed('continua',p.action,now=3,scene='results')
        for t in (4,5):
            offer(m,t,stage='ending',scene='results',matches=hit('torna_lobby'))
        self.assertEqual(m.last_completed,'continua')
        for t in (6,7,8):p=offer(m,t,stage='ending',scene='results',matches=hit('torna_lobby'))
        self.assertEqual(p.name,'torna_lobby')
        self.assertEqual(p.action.name,'menu_click')

    def test_claim_requires_reward_title_never_equipaggia(self):
        m=MenuSupervisor()
        for t in (1,2,3,4):
            p=offer(m,t,stage='claim_rewards',scene='results',matches=hit('riscatta'),reward_overlay=False)
            self.assertEqual(p.action.name,'wait')
        p=offer(m,5,stage='claim_rewards',scene='results',matches=hit('equipaggia'),reward_overlay=True)
        self.assertEqual(p.action.name,'wait')
        for t in (6,7,8):p=offer(m,t,stage='claim_rewards',scene='results',matches=hit('riscatta'),reward_overlay=True)
        self.assertEqual(p.name,'riscatta')
        m.on_executed('riscatta',p.action,now=8,scene='results')
        offer(m,9,stage='claim_rewards',scene='lobby',matches=hit('gioca'),reward_overlay=False)
        offer(m,10,stage='claim_rewards',scene='lobby',matches=hit('gioca'),reward_overlay=False)
        self.assertEqual(m.last_completed,'riscatta')

    def test_play_again_when_only_cta_and_repeat_enabled(self):
        m=MenuSupervisor()
        for t in (1,2,3):p=offer(m,t,stage='ending',scene='results',matches=hit('gioca_di_nuovo'))
        self.assertEqual(p.name,'gioca_di_nuovo')
        m.on_executed('gioca_di_nuovo',p.action,now=3,scene='results')
        offer(m,4,stage='ending',scene='loading',matches={})
        offer(m,5,stage='ending',scene='loading',matches={})
        self.assertEqual(m.last_completed,'gioca_di_nuovo')
        c=SingleMatchController(repeat=True, quick_start=True, quick_wait_play_exit=True)
        c.stage='ending';c.has_entered=True
        c.on_requeue_confirmed(5)
        self.assertEqual(c.matches_completed,1)
        self.assertEqual(c.stage,'joining')
        self.assertTrue(c.quick_departed)
        self.assertTrue(c.play_disappeared)
        self.assertEqual(c.quick_wait_elapsed,0.)
        # In single-match mode never use Play Again; it should finish in lobby.
        p=m.offer(now=6,frame_at=6,stage='ending',scene='results',matches=hit('gioca_di_nuovo'),repeat=False)
        self.assertEqual(p.action.name,'wait')

    def test_spectator_stale_missing_and_wrong_stage_fail_closed(self):
        m=MenuSupervisor()
        for t in (1,2,3):
            p=offer(m,t,stage='ending',scene='results',matches=hit('torna_lobby'),spectator=True)
            self.assertEqual(p.action.name,'wait')
        p=offer(m,4,stage='exploring',scene='match',matches=hit('torna_lobby'))
        self.assertEqual(p.action.name,'wait')
        self.assertEqual(offer(m,5,stage='ending',scene='results',matches={},locked=True).action.name,'wait')
        self.assertEqual(offer(m,6,stage='ending',scene='results',matches=hit('torna_lobby')).action.name,'wait')
        self.assertEqual(offer(m,6,stage='ending',scene='results',matches=hit('torna_lobby')).action.name,'wait')
        self.assertEqual(offer(m,11,stage='ending',scene='results',matches=hit('torna_lobby')).action.name,'wait')
        self.assertEqual(offer(m,20,stage='ending',scene='results',matches=hit('torna_lobby')).action.name,'wait')

    def test_a_subsequent_results_menu_not_blocked_by_play_click(self):
        m=MenuSupervisor()
        for t in (1,2,3):p=offer(m,t)
        m.on_executed('gioca',p.action,now=3,scene='lobby')
        # Match controller independently confirms phase after expected loading.
        offer(m,4,stage='jumping',scene='match',matches={})
        self.assertIsNone(m.pending)
        for t in (5,6,7):p=offer(m,t,stage='ending',scene='results',matches=hit('torna_lobby'))
        self.assertEqual(p.name,'torna_lobby')

    def test_cached_ocr_is_not_counted_as_new_observations(self):
        m=MenuSupervisor()
        # Six new runner frames still contain ONE cached OCR result.
        for t in (1,2,3,4,5,6):
            p=m.offer(now=t,frame_at=t,stage='ending',scene='results',
                      matches=hit('torna_lobby'),repeat=True,
                      evidence_times={'torna_lobby':1.0})
            self.assertEqual(p.action.name,'wait')
        self.assertEqual(m.stable_count,1)
        p=m.offer(now=7,frame_at=7,stage='ending',scene='results',
                  matches=hit('torna_lobby'),repeat=True,
                  evidence_times={'torna_lobby':7.0})
        self.assertEqual(p.action.name,'wait')
        p=m.offer(now=8,frame_at=8,stage='ending',scene='results',
                  matches=hit('torna_lobby'),repeat=True,
                  evidence_times={'torna_lobby':8.0})
        self.assertEqual(p.action.name,'menu_click')

    def test_play_again_cannot_match_top_nav_gioca(self):
        fake=np.zeros((110,400,3),np.uint8)
        ref={'rect':[0,0,1,1], 'label':'gioca di nuovo'}
        self.assertIsNone(match_reference(fake,ref,'gioca_di_nuovo',lambda crop:'GIOCA'))
        self.assertIsNotNone(match_reference(fake,ref,'gioca_di_nuovo',lambda crop:'GIOCA DI NUOVO'))
        d={'text':['GIOCA','DI','NUOVO'], 'conf':['91','95','92'],
            'left':[100,175,193],'top':[140,140,140], 'width':[70,12,65],'height':[26,26,26],
            'block_num':[0,0,0],'par_num':[0,0,0],'line_num':[0,0,0]}
        chosen=extract_results_button(d,scale=1,left=0,top=0,crop_y=0,original_height=350)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen[2],'gioca_di_nuovo')


if __name__=='__main__':unittest.main()

class LifecycleIntegrationTests(unittest.TestCase):
    """Controller + common click path, including a complete repeat-cycle replay."""

    def test_complete_ignorante_lobby_match_results_claim_and_play(self):
        c=SingleMatchController(repeat=True,quick_start=True,quick_wait_play_exit=True,
                                quick_loading_seconds=120)
        c.arm_quick(0)
        m=MenuSupervisor()
        def frame(t,scene,items=None,title=False,scan_ok=True,play=None):
            items={} if items is None else items
            prop=m.offer(now=t,frame_at=t,stage=c.stage,scene=scene,matches=items,
                         repeat=True,reward_overlay=title,reward_scan_confirmed=scan_ok)
            if m.last_completed=='gioca_di_nuovo':
                c.on_requeue_confirmed(t)
            active=prop.name if prop.action.name=='menu_click' else None
            observed=next((key for key in ('riscatta','torna_lobby','continua','gioca_di_nuovo','gioca') if key in items),None)
            a=c.decide(now=t,frame_at=t,scene=scene,menu=active or observed,
                       menu_candidate_authorized=active is not None,
                       candidate=prop.action if active else Action('wait'),
                       authorized=False,locked=False,spectator=False,
                       reward_overlay=title,reward_scan_confirmed=scan_ok,
                       play_visible=play,play_point=items.get('gioca').center if 'gioca' in items else None)
            if a.name=='menu_click':
                self.assertIsNotNone(active)
                self.assertEqual(prop.action.point,a.point)
                m.on_executed(active,a,now=t,scene=scene)
                c.on_executed(a,active,t)
            return a
        self.assertEqual(frame(1,'lobby',hit('gioca'),play=True).name,'wait')
        self.assertEqual(frame(2,'lobby',hit('gioca'),play=True).name,'wait')
        self.assertEqual(frame(3,'lobby',hit('gioca'),play=True).name,'menu_click')
        for t in (4,5,6):self.assertEqual(frame(t,'unknown',play=False).name,'wait')
        self.assertEqual(c.stage,'joining')
        self.assertEqual(frame(127,'unknown',play=False).name,'jump')
        self.assertEqual(c.stage,'jumping')
        for t in (130,131,132):frame(t,'results')
        self.assertEqual(c.stage,'ending')
        for t in (133,134,135): a=frame(t,'results',hit('torna_lobby'))
        self.assertEqual(a.name,'menu_click')
        for t in (136,137,138):frame(t,'lobby',hit('gioca'))
        self.assertEqual(c.stage,'post_lobby')
        claims=[]
        for t in (140,141,142,143,144,145):
            action=frame(t,'results',hit('riscatta'),title=True)
            if action.name=='menu_click':claims.append(action)
        self.assertEqual(c.stage,'claim_rewards')
        self.assertEqual(len(claims),1)
        for t in (146,147,148,149):frame(t,'lobby',hit('gioca'))
        self.assertEqual(c.stage,'await_play')
        new_play=[]
        for t in (155,156,157,158):
            a=frame(t,'lobby',hit('gioca'))
            if a.name=='menu_click':new_play.append(a)
        self.assertEqual(len(new_play),1)
        self.assertEqual(c.stage,'joining')
        self.assertEqual(c.matches_completed,1)

    def test_direct_play_again_does_not_claim_reward_or_click_equipaggia(self):
        c=SingleMatchController(repeat=True,quick_start=True,quick_wait_play_exit=True)
        c.stage='ending';c.has_entered=True;c.results_at=0
        m=MenuSupervisor()
        for t in (1,2,3):
            p=m.offer(now=t,frame_at=t,stage=c.stage,scene='results',
                      matches=hit('gioca_di_nuovo'),repeat=True)
            a=c.decide(now=t,frame_at=t,scene='results',menu=p.name,
                       candidate=p.action,authorized=False,locked=False,spectator=False)
        self.assertEqual(a.name,'menu_click')
        m.on_executed('gioca_di_nuovo',a,now=3,scene='results')
        m.offer(now=4,frame_at=4,stage='ending',scene='loading',matches={},repeat=True)
        m.offer(now=5,frame_at=5,stage='ending',scene='loading',matches={},repeat=True)
        self.assertEqual(m.last_completed,'gioca_di_nuovo')
        c.on_requeue_confirmed(5)
        self.assertEqual(c.stage,'joining')
        self.assertEqual(c.matches_completed,1)
        # A visible EQUIPAGGIA cannot be selected as a menu CTA.
        p=m.offer(now=6,frame_at=6,stage='claim_rewards',scene='results',
                  matches=hit('equipaggia'),repeat=True,reward_overlay=True)
        self.assertEqual(p.action.name,'wait')
