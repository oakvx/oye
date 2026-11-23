"""Asynchronous exploratory visual interpreter.

OCR and frame capture have their own worker; heavy Ollama inference runs separately,
with one pending image at most. Insights are hypotheses, not trusted training labels.
"""
from __future__ import annotations
from queue import Empty, Queue
import threading
import time
import cv2
from .ollama_vision import LocalVisionModel


class ExplorationWorker:
    def __init__(self, model: LocalVisionModel, store, emit, *, interval: float = 12.,
                 clock=time.monotonic):
        self.model, self.store, self.emit = model, store, emit
        self.interval, self.clock = interval, clock
        self.queue: Queue = Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='OyeExploration')
        self.last_submit = float('-inf')
        self.dropped = 0

    def start(self):
        self.thread.start()

    def submit(self, frame, *, session: str, scene: str, ocr: str = '') -> bool:
        now = self.clock()
        if self.stop_event.is_set() or now - self.last_submit < self.interval:
            return False
        width = min(600, frame.shape[1])
        height = max(1, round(frame.shape[0] * width / frame.shape[1]))
        scaled = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        ok, jpeg = cv2.imencode('.jpg', scaled, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if not ok:
            return False
        job = (jpeg.tobytes(), session, scene, ocr[:500])
        try:
            self.queue.put_nowait(job)
            self.last_submit = now
            return True
        except __import__('queue').Full:
            # A busy model never builds an unbounded, stale backlog.
            self.dropped += 1
            return False

    def _run(self):
        while not self.stop_event.is_set():
            try:
                jpeg, session, scene, ocr = self.queue.get(timeout=.2)
            except Empty:
                continue
            try:
                result = self.model.describe(jpeg, context=scene, ocr=ocr)
                event = str(result.get('event', result.get('evento', 'incerto')))[:100]
                description = str(result.get('description', result.get('descrizione', '')))[:500]
                uncertain = bool(result.get('uncertain', result.get('incerto', True)))
                self.store.record_visual_insight(session=session, event=event,
                                                 description=description, uncertain=uncertain)
                self.emit('exploration_insight', f'Ipotesi: {event} — {description}',
                          info={'exploration_event': event, 'uncertain': uncertain})
            except Exception as exc:
                self.emit('learning_error', 'Esplorazione VLM: ' + str(exc)[:160])
            finally:
                self.queue.task_done()

    def stop(self, timeout=.5):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout)
