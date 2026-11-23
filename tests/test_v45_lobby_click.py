"""Regression: real lobby PLAY must result in a bounded, observable click.

The previous version reported 'Vedo GIOCA' forever when Brain proposed WAIT.
No target point => no click. Stale/unknown frames do not start loading.
"""
import unittest
from observer.models import Action
from observer.single_match import SingleMatchController


def cnew():
    c = SingleMatchController(quick_start=True, quick_wait_play_exit=True,
                              quick_loading_seconds=120, repeat=True)
    c.arm_quick(0)
    return c


def step(c, t, visible=True, *, point=(310, 905), scene='lobby', menu='gioca', cand=None):
    return c.decide(now=t,frame_at=t,scene=scene, menu=menu,
                    candidate=cand or Action('wait'), authorized=False,
                    locked=False,spectator=False,
                    play_visible=visible,play_point=point)


class LobbyClickTests(unittest.TestCase):
    def test_wait_from_brain_does_not_block_confirmed_click(self):
        c=cnew()
        self.assertEqual(step(c,1).name,'wait')
        self.assertEqual(step(c,2).name,'wait')
        a=step(c,3)
        self.assertEqual(a.name,'menu_click')
        self.assertEqual(a.point,(310,905))
        self.assertIn('GIOCA',a.reason)
        c.on_executed(a,'gioca',3)
        self.assertEqual(step(c,4).name,'wait')
        self.assertEqual(step(c,14.9).name,'wait')
        retry=step(c,15.1)
        self.assertEqual(retry.name,'menu_click')
        c.on_executed(retry,'gioca',15.1)
        self.assertEqual(step(c,27.2).name,'menu_click')
        c.on_executed(Action('menu_click',point=(310,905)),'gioca',27.2)
        exhausted=step(c,40)
        self.assertEqual(exhausted.name,'wait')
        self.assertIn('3 clic',exhausted.reason)

    def test_lobby_has_no_coordinates_no_click(self):
        c=cnew()
        for t in range(1,7):
            a=step(c,t,point=None)
            self.assertEqual(a.name,'wait')
        self.assertIn('coordinate', a.reason)
        self.assertEqual(c.quick_play_click_attempts,0)

    def test_unknown_is_not_absence(self):
        c=cnew()
        step(c,1);step(c,2)
        sent=step(c,3)
        c.on_executed(sent,'gioca',3)
        for t in (4,5,6,130):
            a=step(c,t,visible=None,scene='unknown',menu=None,point=None)
            self.assertEqual(a.name,'wait')
        self.assertFalse(c.quick_departed)
        for t in (131,132,133):
            a=step(c,t,visible=False,scene='unknown',menu=None,point=None)
            self.assertEqual(a.name,'wait')
        self.assertTrue(c.quick_departed)
        self.assertEqual(step(c,252,visible=False,scene='unknown',menu=None,point=None).name,'wait')
        self.assertEqual(step(c,253,visible=False,scene='unknown',menu=None,point=None).name,'jump')

    def test_never_click_when_non_lobby_or_other_button(self):
        c=cnew()
        for t in (1,2,3,4):
            a=step(c,t,scene='loading',menu='gioca')
            self.assertEqual(a.name,'wait')
        self.assertEqual(c.quick_play_click_attempts,0)
        a=step(c,5,menu='continua')
        self.assertEqual(a.name,'wait')
        self.assertEqual(c.quick_play_click_attempts,0)

    def test_click_rejected_by_driver_is_not_counted(self):
        c=cnew()
        step(c,1);step(c,2)
        a=step(c,3)
        # Omit on_executed(): driver rejected the input.
        self.assertEqual(step(c,4).name,'menu_click')
        self.assertEqual(c.quick_play_click_attempts,0)


if __name__ == '__main__':
    unittest.main()
