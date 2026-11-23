"""Acquire an exact, foreground Windows client area, never a window-title guess."""
from __future__ import annotations
import ctypes
from dataclasses import dataclass
from pathlib import Path
import sys
import threading


@dataclass(frozen=True)
class Window:
    executable: str
    left: int
    top: int
    width: int
    height: int


def enable_dpi_awareness() -> None:
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def foreground_window(allowed_names: list[str]) -> Window | None:
    if sys.platform != 'win32':
        return None
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd or user32.IsIconic(hwnd):
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    hprocess = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not hprocess:
        return None
    try:
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(hprocess, 0, buf, ctypes.byref(size)):
            return None
        exe = Path(buf.value).name
    finally:
        kernel32.CloseHandle(hprocess)
    if exe.casefold() not in {n.casefold() for n in allowed_names}:
        return None

    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 320 or height < 240:
        return None
    return Window(exe, pt.x, pt.y, width, height)


_CAPTURE_LOCAL = threading.local()

def close_capture() -> None:
    """Dispose the native GDI handles in the same thread that opened them."""
    sct = getattr(_CAPTURE_LOCAL, "sct", None)
    if sct is not None:
        try:
            sct.close()
        except Exception:
            # A failed capture cleanup must never prevent input release.
            pass
        finally:
            _CAPTURE_LOCAL.sct = None

def snapshot(window: Window):
    """Snapshot on calling thread. mss instances are not shared between threads."""
    import mss
    import numpy as np
    # An mss instance is thread-affine on Windows. Reusing it per worker
    # avoids allocating/releasing Windows screen-capture handles on each scan.
    sct = getattr(_CAPTURE_LOCAL, 'sct', None)
    if sct is None:
        sct = mss.mss()
        _CAPTURE_LOCAL.sct = sct
    return np.asarray(sct.grab({
        'left': window.left, 'top': window.top,
        'width': window.width, 'height': window.height,
    })).copy()
