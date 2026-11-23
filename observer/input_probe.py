"""Explicit, bounded Windows input diagnostic; never used by autonomous policy.

Being able to send W via pyautogui DOES NOT prove that a protected game has
accepted it. The user observes movement directly and reports the result.
"""
from __future__ import annotations
import threading
from typing import Callable
from .driver import Driver
from .models import Action
from .windows import foreground_window


def probe_forward(config: dict, *, duration: float = .75,
                  foreground: Callable | None = None,
                  driver_factory=Driver) -> tuple[bool, str]:
    """Send one short W only to the selected, focused game; release on exit.

    Called only after explicit UI confirmation and a countdown on another thread.
    Never queues keyboard commands, changes game configuration or bypasses AC.
    """
    if not .15 <= duration <= 1.0:
        raise ValueError('Durata test non consentita')
    foreground = foreground or foreground_window
    names = config['window_executables']
    if foreground(names) is None:
        return False, 'Nessun gioco autorizzato in primo piano: W non inviato.'
    stop = threading.Event()
    pause = threading.Event()
    allowed = lambda: foreground(names) is not None
    driver = None
    try:
        driver = driver_factory(live=True, allowed=allowed, config=config,
                                stop=stop, paused=pause)
        sent = driver.run(Action('forward', duration, reason='test W richiesto dall’utente'))
        if sent:
            return True, ('Il driver ha inviato W e lo ha rilasciato. '
                          'Verifica visivamente se Fortnite si è mosso: invio NON significa ricezione.')
        return False, 'Il driver non ha inviato W: controlla finestra attiva e autorizzazioni.'
    finally:
        stop.set()
        if driver is not None:
            driver.release()


def probe_forward_visual(config: dict, *, duration: float = .75,
                         foreground: Callable | None = None,
                         grab: Callable | None = None,
                         driver_factory=Driver) -> tuple[bool, str]:
    """User-triggered W test with temporary BEFORE/AFTER frames in memory.

    Visual flow is only a clue, *not* proof that a protected game accepted W.
    Neither screenshot is stored. Focus/geometry is reverified after the action.
    """
    from .windows import snapshot, close_capture
    from .vision import motion_between
    from .feedback import evaluate
    if not .15 <= duration <= 1.0:
        raise ValueError('Durata test non consentita')
    foreground = foreground or foreground_window
    grab = grab or snapshot
    names = config['window_executables']
    target = foreground(names)
    if target is None:
        return False, 'Fortnite non in primo piano: W non inviato.'
    before = None
    try:
        before = grab(target)
    except Exception:
        # A screenshot failure does not justify bypassing the focus check.
        pass
    try:
        sent, report = probe_forward(config, duration=duration,
                                      foreground=foreground,
                                      driver_factory=driver_factory)
        if not sent:
            return sent, report
        if foreground(names) != target:
            return True, report + ' Dopo il comando la finestra è cambiata: confronto visivo annullato.'
        if before is None:
            return True, report + ' Prima immagine non disponibile: confronto visivo impossibile.'
        try:
            after = grab(target)
            outcome = evaluate('forward', motion_between(before, after))
            return True, report + ' Indizio visivo: ' + outcome.detail
        except Exception as exc:
            return True, report + f' Confronto visivo non disponibile ({type(exc).__name__}).'
    finally:
        # Closes the caller's thread-local capture handles.
        close_capture()


def probe_camera_visual(config: dict, *, foreground: Callable | None = None,
                        grab: Callable | None = None,
                        driver_factory=Driver) -> tuple[bool, str]:
    """Explicit camera diagnostic; never run by the autonomous controller.

    Uses normal OS mouse APIs only, does not attempt to emulate hardware or
    bypass protected games. Compares two RAM-only images for a *visual clue*.
    """
    from .windows import snapshot, close_capture
    from .vision import motion_between
    foreground = foreground or foreground_window
    grab = grab or snapshot
    names = config['window_executables']
    target = foreground(names)
    if target is None:
        return False, 'Fortnite non in primo piano: nessun movimento mouse inviato.'
    before = None
    driver = None
    try:
        try:
            before = grab(target)
        except Exception:
            pass
        allowed = lambda: foreground(names) is not None
        driver = driver_factory(live=True, allowed=allowed, config=config,
                                stop=threading.Event(), paused=threading.Event())
        sent = driver.run(Action('aim', .14, (150, 0), reason='test visuale autorizzato'))
        if not sent:
            return False, 'Il sistema non ha inviato il movimento mouse: controlla focus e driver.'
        message = 'Mouse: movimento relativo inviato tramite API standard Windows.'
        if foreground(names) != target:
            return True, message + ' Finestra cambiata: verifica visiva annullata.'
        if before is None:
            return True, message + ' Impossibile confrontare le immagini.'
        try:
            motion = motion_between(before, grab(target))
            if motion is not None and motion.reliable:
                message += f' Variazione scena: {motion.pixels:.2f} px (indizio, non conferma telecamera).'
                if motion.pixels < .55:
                    message += ' Visuale apparentemente immobile: il gioco potrebbe ignorare il mouse sintetico.'
            else:
                message += ' Tracce visive insufficienti; osserva direttamente se la visuale ruota.'
        except Exception:
            message += ' Confronto visivo non disponibile.'
        return True, message + ' Non tenta alcun aggiramento delle restrizioni del gioco.'
    finally:
        if driver is not None:
            driver.release()
        close_capture()
