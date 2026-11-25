"""Keep bounded movement off the recognition thread; stale evidence releases it."""
import threading
import time


class SustainedInput:
    def __init__(self, driver, *, allowed):
        self.driver = driver
        self.allowed = allowed
        self.cancel = threading.Event()
        self.thread = None
        self.deadline = float('-inf')
        self.error = None
        self.driver.allowed = self._permitted

    def _permitted(self):
        return (not self.cancel.is_set() and self.allowed()
                and (self.thread is None or time.monotonic() < self.deadline))

    @property
    def busy(self):
        if self.error is not None:
            raise RuntimeError('Movimento continuo interrotto') from self.error
        return self.thread is not None and self.thread.is_alive()

    def renew(self, deadline):
        # The caller supplies the observation timestamp plus its freshness limit,
        # never the timestamp of a repeated read of cached evidence.
        self.deadline = deadline

    def _run(self, action):
        try:
            self.driver.run(action)
        except Exception as exc:
            self.error = exc
        finally:
            self.driver.release()

    def release(self):
        self.cancel.set()
        if self.thread is not None:
            self.thread.join(timeout=1.)
            if self.thread.is_alive():
                raise RuntimeError('Il driver non ha confermato il rilascio dei tasti')
            self.thread = None
        self.driver.release()

    def run(self, action):
        self.release()
        if self.error is not None:
            raise RuntimeError('Errore nel driver di movimento') from self.error
        self.cancel.clear()
        if action.seconds <= 2 or action.name not in (
                'patrol_forward', 'strafe_left', 'strafe_right', 'patrol_hop', 'patrol_harvest'):
            return self.driver.run(action)
        if not self.allowed() or time.monotonic() >= self.deadline:
            return False
        self.thread = threading.Thread(target=self._run, args=(action,),
                                       name='SustainedInput', daemon=True)
        self.thread.start()
        return True
