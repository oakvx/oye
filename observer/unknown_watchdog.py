"""Safe handling of a long unrecognized screen.

Unknown screens never authorize gameplay inputs. Continuous mode reports the
problem periodically without killing the entire process; one-shot mode keeps
the existing fail-closed timeout.
"""
from dataclasses import dataclass


@dataclass
class UnknownWatchdog:
    threshold_seconds: float = 180.0
    unknown_since: float | None = None
    last_notice_at: float | None = None

    def observe(self, *, now: float, scene: str, live: bool, timed: bool,
                repeat: bool) -> str:
        if scene != 'unknown' or not live or timed:
            self.unknown_since = None
            self.last_notice_at = None
            return 'ok'
        if self.unknown_since is None:
            self.unknown_since = now
            return 'ok'
        if now - self.unknown_since < self.threshold_seconds:
            return 'ok'
        if not repeat:
            return 'stop'
        if self.last_notice_at is None or now - self.last_notice_at >= self.threshold_seconds:
            self.last_notice_at = now
            return 'recover'
        return 'waiting'
