"""Optional WASAPI loopback/audio template matching.

A user-supplied WAV sample is required; this cannot infer sound direction.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

SAMPLE_RATE = 22050


def _mono(samples: np.ndarray) -> np.ndarray:
    return samples.mean(axis=1).astype(np.float32) if samples.ndim > 1 else samples.astype(np.float32)


def fingerprint(samples: np.ndarray, rate: int = SAMPLE_RATE) -> np.ndarray:
    from scipy.signal import stft
    samples = _mono(np.asarray(samples))
    frequencies, _, spec = stft(samples, fs=rate, nperseg=1024, noverlap=768,
                               boundary=None, padded=False)
    mag = np.abs(spec)
    edges = np.geomspace(240, min(7700, rate / 2 - 1), 25)
    result = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        which = (frequencies >= lo) & (frequencies < hi)
        result.append(np.log1p(2000 * mag[which].mean(axis=0)) if which.any() else np.zeros(mag.shape[1]))
    features = np.stack(result).astype(np.float32)
    # Standardize per frame, favor recurring spectral shape over loudness.
    features -= np.mean(features, axis=0, keepdims=True)
    norms = np.linalg.norm(features, axis=0, keepdims=True)
    return features / np.maximum(norms, 1e-6)


def similarity(reference: np.ndarray, stream: np.ndarray) -> float:
    """Highest normalized spectral pattern similarity across sliding windows."""
    from numpy.lib.stride_tricks import sliding_window_view
    n = reference.shape[1]
    if n < 8 or stream.shape[1] < n or stream.shape[0] != reference.shape[0]:
        return 0.0
    windows = sliding_window_view(stream, n, axis=1)  # bands, positions, frames
    dots = np.einsum('bpt,bt->p', windows, reference, optimize=True)
    squares = np.einsum('bpt,bpt->p', windows, windows, optimize=True)
    reference_norm = max(float(np.linalg.norm(reference)), 1e-6)
    score = dots / (reference_norm * np.sqrt(np.maximum(squares, 1e-6)))
    return float(np.max(score))


def loopback_microphone():
    import soundcard as sc
    speaker = sc.default_speaker()
    if speaker is None:
        raise RuntimeError("Nessun dispositivo di riproduzione predefinito")
    return sc.get_microphone(id=str(speaker.name), include_loopback=True)


def capture_sample(path: Path, seconds: float = 2.0) -> None:
    import soundfile as sf
    mic = loopback_microphone()
    with mic.recorder(samplerate=SAMPLE_RATE) as recorder:
        frames = recorder.record(numframes=int(SAMPLE_RATE * seconds))
    sf.write(str(path), _mono(frames), SAMPLE_RATE)


class SoundWatcher:
    def __init__(self, wav: Path, *, threshold: float = .87, cooldown: float = 18):
        import soundfile as sf
        from scipy.signal import resample_poly
        import math
        samples, rate = sf.read(str(wav), always_2d=False)
        signal = _mono(samples)
        if rate != SAMPLE_RATE:
            common = math.gcd(rate, SAMPLE_RATE)
            signal = resample_poly(signal, SAMPLE_RATE // common, rate // common).astype(np.float32)
        if not .45 <= len(signal) / SAMPLE_RATE <= 3.0:
            raise ValueError("Il file cassa.wav deve durare da 0,45 a 3 secondi")
        self.reference = fingerprint(signal)
        self.threshold = threshold
        self.cooldown = cooldown
        self._event = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None
        self.last_score: float = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="chest-audio", daemon=True)
        self._thread.start()

    def consume_event(self) -> bool:
        if self._event.is_set():
            self._event.clear()
            return True
        return False

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)

    def _run(self) -> None:
        try:
            mic = loopback_microphone()
            window = np.empty(0, dtype=np.float32)
            chunk = 8192
            max_samples = min(SAMPLE_RATE * 4, SAMPLE_RATE * 2 + len(self.reference.T) * 256)
            previous_event = -99999.0
            with mic.recorder(samplerate=SAMPLE_RATE) as recorder:
                while not self._stop.is_set():
                    segment = _mono(recorder.record(numframes=chunk))
                    window = np.concatenate((window, segment))[-max_samples:]
                    if len(window) < len(self.reference.T) * 256:
                        continue
                    features = fingerprint(window)
                    self.last_score = similarity(self.reference, features)
                    now = time.monotonic()
                    if self.last_score >= self.threshold and now - previous_event >= self.cooldown:
                        self._event.set()
                        previous_event = now
        except Exception as exc:
            self.error = f"Audio disattivato: {type(exc).__name__}: {exc}"
