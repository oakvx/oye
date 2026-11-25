import threading
import time

from observer.models import Action
from observer.quick_routine import QuickRoutine
from observer.sustained_input import SustainedInput


class HoldingDriver:
    def __init__(self):
        self.started = threading.Event()
        self.released = threading.Event()
        self.allowed = lambda: True

    def run(self, action):
        self.released.clear()
        self.started.set()
        while self.allowed():
            time.sleep(.005)
        self.release()
        return True

    def release(self):
        self.released.set()


def test_long_press_does_not_block_recognition_and_can_be_interrupted():
    raw = HoldingDriver()
    driver = SustainedInput(raw, allowed=lambda: True)
    driver.renew(time.monotonic() + 3)
    try:
        assert driver.run(Action('patrol_forward', 6))
        assert raw.started.wait(.5)
        assert driver.busy
        assert not raw.released.is_set()
        driver.release()  # Results/menu detected on the recognition thread.
        assert raw.released.is_set()
        assert not driver.busy
    finally:
        driver.release()


def test_stale_recognition_and_focus_loss_release_long_press():
    for lose_focus in (False, True):
        focused = threading.Event()
        focused.set()
        raw = HoldingDriver()
        driver = SustainedInput(raw, allowed=focused.is_set)
        driver.renew(time.monotonic() + (.1 if not lose_focus else 3))
        try:
            driver.run(Action('strafe_right', 5))
            assert raw.started.wait(.5)
            if lose_focus:
                focused.clear()
            assert raw.released.wait(.5)
        finally:
            driver.release()


def test_sustained_routine_has_long_travel_jumps_and_pickaxe():
    routine = QuickRoutine(seed=17, sustained=True)
    actions = [routine.next_action(harvest=True) for _ in range(50)]
    travel = [a for a in actions if a.name != 'equip_pickaxe']
    assert all(3 <= a.seconds <= 7 for a in travel)
    assert {'patrol_forward', 'strafe_right', 'strafe_left', 'patrol_hop',
            'patrol_harvest'} <= {a.name for a in travel}
    assert not {'reverse', 'turn_left', 'turn_right'} & {a.name for a in travel}


def test_driver_holds_w_once_for_the_whole_six_second_segment(monkeypatch):
    from observer.config import validate
    from observer.driver import Driver
    clock = [0.]
    events = []
    class GUI:
        FAILSAFE = True
        def keyDown(self, key): events.append(('down', key, clock[0]))
        def keyUp(self, key): events.append(('up', key, clock[0]))
    raw = Driver(live=False, allowed=lambda: True, config=validate({}),
                 stop=threading.Event(), paused=threading.Event())
    raw.gui = GUI()
    monkeypatch.setattr('observer.driver.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('observer.driver.time.sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert raw.run(Action('patrol_forward', 6))
    assert events == [('down', 'w', 0.), ('up', 'w', 6.)]


def test_pickaxe_clicks_repeat_while_forward_stays_held(monkeypatch):
    from observer.config import validate
    from observer.driver import Driver
    clock, keys, clicks = [0.], [], []
    class GUI:
        FAILSAFE = True
        def keyDown(self, key): keys.append(('down', key, clock[0]))
        def keyUp(self, key): keys.append(('up', key, clock[0]))
        def mouseDown(self, button): clicks.append(('down', clock[0]))
        def mouseUp(self, button): clicks.append(('up', clock[0]))
    raw = Driver(live=False, allowed=lambda: True, config=validate({}),
                 stop=threading.Event(), paused=threading.Event())
    raw.gui = GUI()
    monkeypatch.setattr('observer.driver.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('observer.driver.time.sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    raw.run(Action('patrol_harvest', 3))
    assert keys == [('down', 'w', 0.), ('up', 'w', 3.)]
    assert len([event for event, _ in clicks if event == 'down']) >= 4
    assert [event for event, _ in clicks] == ['down', 'up'] * (len(clicks) // 2)
