import unittest
from observer.models import Action
from observer.single_match import SingleMatchController
from observer.qt_frontend_logic import mode_flags
from observer.config import validate


class SingleMatchTests(unittest.TestCase):
    def setUp(self):
        self.c = SingleMatchController(jump_window_seconds=120, jump_period_seconds=1.8, harvest=True)
        self.t = 1.0

    def frame(self, scene='unknown', menu=None, authorized=False, locked=False,
              spectator=False, candidate=None, advance=1.0):
        self.t += advance
        return self.c.decide(now=self.t, frame_at=self.t, scene=scene, menu=menu,
                             candidate=candidate or Action('wait'), authorized=authorized,
                             locked=locked, spectator=spectator)

    def enter(self):
        a = self.frame('lobby', 'gioca', candidate=Action('menu_click',point=(500,600)))
        self.assertEqual(a.name,'menu_click')
        self.c.on_executed(a, 'gioca', self.t)
        self.assertEqual(self.c.stage,'joining')
        self.frame('loading')
        for _ in range(3):
            a=self.frame('match', authorized=True)
        self.assertEqual(self.c.stage,'jumping')
        self.assertEqual(a.name,'jump')

    def test_no_blind_jump_when_play_disappears(self):
        self.enter()
        self.assertTrue(self.c.play_disappeared)

    def test_no_jump_until_gameplay_verified(self):
        a=self.frame('lobby','gioca',candidate=Action('menu_click',point=(1,2)))
        self.c.on_executed(a,'gioca',self.t)
        for _ in range(20):
            self.assertEqual(self.frame('loading').name,'wait')
        self.assertEqual(self.c.stage,'joining')
        for _ in range(2): self.assertEqual(self.frame('match',authorized=True).name,'wait')
        self.assertEqual(self.frame('match',authorized=True).name,'jump')

    def test_jump_movement_then_explore(self):
        self.enter()
        a=self.frame('match',authorized=True,advance=.4)
        self.assertNotEqual(a.name,'wait')
        self.assertNotEqual(a.name,'jump')
        a=self.frame('match',authorized=True,advance=1.8)
        self.assertEqual(a.name,'jump')
        for _ in range(60):
            self.frame('match',authorized=True,advance=2.1)
        self.assertEqual(self.c.stage,'exploring')

    def test_secondary_mouse_click_is_periodic_and_match_only(self):
        self.enter()
        # Distinct verified ground frames; the right-click appears only when
        # a real playable scene has accumulated enough activity time.
        commands = [self.frame('match', authorized=True, advance=1.1).name for _ in range(50)]
        self.assertIn('secondary_click', commands)
        self.assertLessEqual(commands.count('secondary_click'), 2)
        for _ in range(35):
            action = self.frame('spectator', locked=True, spectator=True)
            self.assertEqual(action.name, 'wait')

    def test_spectator_veto_and_resume(self):
        self.enter()
        elapsed=self.c.elapsed
        for _ in range(4): self.assertEqual(self.frame('spectator',locked=True,spectator=True).name,'wait')
        self.assertEqual(self.c.elapsed,elapsed)
        self.assertNotEqual(self.frame('match',authorized=True).name,'wait')

    def test_results_then_lobby_stop_without_requeue(self):
        self.enter()
        for _ in range(3): self.frame('results',menu='torna_lobby')
        self.assertEqual(self.c.stage,'ending')
        click=Action('menu_click',point=(42,24))
        self.assertEqual(self.frame('results',menu='torna_lobby',candidate=click).name,'menu_click')
        for _ in range(3): self.assertEqual(self.frame('lobby',menu='gioca').name,'wait')
        self.assertEqual(self.c.stage,'post_lobby')
        self.assertEqual(self.frame('lobby',menu='gioca',candidate=click).name,'wait')
        for _ in range(3): self.frame('lobby',menu='gioca',advance=7)
        self.assertEqual(self.c.stage,'done')

    def test_direct_return_lobby_no_second_game(self):
        self.enter()
        play = Action('menu_click', point=(111,222))
        for _ in range(4):
            self.assertEqual(self.frame('lobby',menu='gioca',candidate=play).name,'wait')
        self.assertEqual(self.c.stage,'jumping')
        self.assertEqual(self.frame('lobby',menu='gioca',candidate=play).name,'wait')
        self.assertEqual(self.c.stage,'post_lobby')
        for _ in range(3): self.frame('lobby',menu='gioca',advance=7)
        self.assertEqual(self.c.stage,'done')

    def test_calibrated_bus_allows_only_jump(self):
        a=self.frame('lobby','gioca',candidate=Action('menu_click',point=(1,1)))
        self.c.on_executed(a,'gioca',self.t)
        self.frame('loading')
        for _ in range(3):
            self.t += 1
            result=self.c.decide(now=self.t,frame_at=self.t,scene='unknown',menu=None,
                                 authorized=False,locked=False,spectator=False,
                                 bus_confirmed=True,candidate=Action('wait'))
        self.assertEqual(self.c.stage,'jumping')
        self.assertEqual(result.name,'jump')
        self.t+=.4
        no_walk=self.c.decide(now=self.t,frame_at=self.t,scene='unknown',menu=None,
                              authorized=False,locked=False,spectator=False,
                              bus_confirmed=True,candidate=Action('wait'))
        self.assertEqual(no_walk.name,'wait')
        self.t+=2
        jump=self.c.decide(now=self.t,frame_at=self.t,scene='unknown',menu=None,
                              authorized=False,locked=False,spectator=False,
                              bus_confirmed=True,candidate=Action('wait'))
        self.assertEqual(jump.name,'jump')

    def test_results_need_confirmation(self):
        self.enter()
        self.assertEqual(self.frame('results',menu='torna_lobby',candidate=Action('menu_click',point=(2,4))).name,'wait')
        self.assertEqual(self.c.stage,'jumping')
        self.assertNotEqual(self.frame('match',authorized=True).name,'wait')

    def test_unknown_never_authorizes_inputs(self):
        self.enter()
        self.assertEqual(self.frame('unknown',candidate=Action('menu_click',point=(1,1))).name,'wait')
        self.assertEqual(self.frame('loading').name,'wait')

    def test_pause_does_not_consume_jump_window(self):
        self.enter()
        self.c.suspend_clock()
        self.frame('match',authorized=True,advance=100)
        self.assertEqual(self.c.elapsed,0.)

    def test_play_timeout(self):
        a=self.frame('lobby','gioca',candidate=Action('menu_click',point=(1,1)))
        self.c.on_executed(a,'gioca',self.t)
        self.frame('loading',advance=241)
        self.assertEqual(self.c.stage,'error')

    def test_menu_click_retries_only_on_verified_lobby(self):
        a=self.frame('lobby','gioca',candidate=Action('menu_click',point=(1,1)))
        self.c.on_executed(a,'gioca',self.t)
        self.assertEqual(self.frame('lobby','gioca',candidate=a).name,'wait')
        self.assertEqual(self.frame('lobby','gioca',candidate=a,advance=14).name,'menu_click')

    def test_stale_frame_not_counted(self):
        self.enter()
        old=self.c.last_frame_at
        result=self.c.decide(now=old+1,frame_at=old,scene='results',menu='torna_lobby',
                   candidate=Action('menu_click',point=(1,2)),authorized=False,locked=False,spectator=False)
        self.assertEqual(result.name,'wait')
        self.assertEqual(self.c.stable_results,0)

    def test_mode_is_opt_in(self):
        flags=mode_flags('single_match')
        self.assertTrue(flags['live'])
        self.assertTrue(flags['guided_explore_enabled'])
        self.assertTrue(flags['autonomous_enabled'])
        self.assertFalse(mode_flags('observe')['live'])

    def test_config(self):
        c=validate({'single_match_jump_seconds':120.0})
        self.assertEqual(c['single_match_jump_seconds'],120.)
        with self.assertRaises(ValueError):validate({'single_match_jump_seconds':250})

if __name__ == '__main__':
    unittest.main()
