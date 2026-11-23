"""Responsive, local-only time-lapse review of sampled human-play screenshots.

Source timestamps are real; playback uses the *saved samples*, not interpolated
video frames. Pillow JPEG decoding occurs in one background thread; Tk images
are deliberately constructed by the GUI thread.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import OrderedDict, deque
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from queue import Empty, Full, Queue
import re
from threading import Condition, Thread
from typing import Any

from PIL import Image

from .timeline import elapsed_seconds, parse_time


class FrameIndex:
    """Chronological sample index; O(log N) scrubbing and deterministic ties."""
    def __init__(self, rows: list[dict], origin: str | None):
        self.rows = sorted(rows, key=lambda r: (parse_time(r['captured_at']), int(r['id'])))
        self.times = [elapsed_seconds(origin, row['captured_at']) for row in self.rows] if origin else []

    def __len__(self):
        return len(self.rows)

    def at(self, index: int) -> dict:
        return self.rows[max(0, min(len(self.rows) - 1, index))]

    def nearest(self, seconds: float) -> int:
        if not self.times:
            return -1
        pos = bisect_left(self.times, seconds)
        if pos == 0:
            return 0
        if pos == len(self.times):
            return len(self.times) - 1
        return pos - 1 if seconds - self.times[pos - 1] <= self.times[pos] - seconds else pos

    def within(self, start: float, end: float) -> tuple[int, int]:
        if not self.times or start > end:
            return (-1, -1)
        from bisect import bisect_right
        lo, hi = bisect_left(self.times, start - 1e-6), bisect_right(self.times, end + 1e-6) - 1
        return (lo, hi) if lo <= hi else (-1, -1)


@dataclass(frozen=True)
class DecodedFrame:
    kind: str  # 'preview' or 'thumb'
    token: int
    index: int
    image: Image.Image | None
    error: str = ''


class ReviewImageLoader:
    """Newest preview has priority; thumbnails never delay scrub updates.

    Thread-safe pending work and bounded LRU cache. No calls into Tk from
    workers. Paths are constrained to the local UUID-named capture archive.
    """
    IMAGE_NAME = re.compile(r'^[a-f0-9]{32}\.jpg$')

    def __init__(self, images_dir: Path, *, cache_size: int = 32):
        self.images_dir = Path(images_dir)
        self.cache_size = cache_size
        self._cv = Condition()
        self._preview: tuple | None = None
        self._thumbs: deque[tuple] = deque()
        self._cache: OrderedDict[tuple, Image.Image | None] = OrderedDict()
        self._results: Queue[DecodedFrame] = Queue(maxsize=64)
        self._closed = False
        self._latest_preview_token = 0
        self._thread = Thread(target=self._worker, name='oye-review-decoder', daemon=True)
        self._thread.start()

    def request_preview(self, row: dict, index: int, size: tuple[int, int]) -> int:
        size = self._size(size)
        with self._cv:
            self._latest_preview_token += 1
            token = self._latest_preview_token
            self._preview = ('preview', token, index, dict(row), size)
            self._cv.notify()
        return token

    def request_thumbnails(self, entries: list[tuple[int, dict]], size: tuple[int, int], generation: int = 0):
        size = self._size(size)
        with self._cv:
            self._thumbs.clear()  # Obsolete viewport never consumes decoding time.
            for index, row in entries[:24]:
                self._thumbs.append(('thumb', generation, index, dict(row), size))
            self._cv.notify()

    def poll(self, max_items: int = 40) -> list[DecodedFrame]:
        values = []
        for _ in range(max_items):
            try:
                values.append(self._results.get_nowait())
            except Empty:
                break
        return values

    def invalidate_preview(self) -> int:
        """Discard old requests when switching to an empty session."""
        with self._cv:
            self._latest_preview_token += 1
            self._preview = None
            return self._latest_preview_token

    def close(self):
        with self._cv:
            self._closed = True
            self._thumbs.clear()
            self._preview = None
            self._cv.notify_all()
        self._thread.join(timeout=1.0)

    @staticmethod
    def _size(size):
        return (max(32, min(1920, int(size[0]))), max(24, min(1080, int(size[1]))))

    def _load(self, row: dict, size: tuple[int, int]) -> tuple[Image.Image | None, str]:
        filename = row.get('image_file')
        data = row.get('jpeg')
        if not data and not (filename and self.IMAGE_NAME.fullmatch(filename)):
            return None, 'Immagine non presente nell’archivio'
        key = (filename if data is None else ('bytes', row.get('id')), size)
        if key in self._cache:
            value = self._cache.pop(key)
            self._cache[key] = value
            return value, '' if value else 'Immagine assente o non leggibile'
        try:
            if data:
                handle = BytesIO(data)
            else:
                handle = self.images_dir / filename
            with Image.open(handle) as image:
                image.draft('RGB', size)
                result = image.convert('RGB')
                result.thumbnail(size, Image.Resampling.BILINEAR)
        except (OSError, ValueError) as exc:
            result = None
            error = str(exc)
        else:
            error = ''
        self._cache[key] = result
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return result, error

    def _offer(self, result: DecodedFrame):
        try:
            self._results.put_nowait(result)
        except Full:
            try:
                self._results.get_nowait()
            except Empty:
                pass
            self._results.put_nowait(result)

    def _worker(self):
        while True:
            with self._cv:
                while not self._closed and self._preview is None and not self._thumbs:
                    self._cv.wait()
                if self._closed:
                    return
                if self._preview is not None:
                    task = self._preview
                    self._preview = None
                else:
                    task = self._thumbs.popleft()
            kind, token, index, row, size = task
            image, error = self._load(row, size)
            if kind == 'preview':
                with self._cv:
                    if token != self._latest_preview_token:
                        continue
            self._offer(DecodedFrame(kind, token, index, image, error))


class TimeLapseClock:
    """Play saved frames at user-selected FPS, independently of capture interval."""
    def __init__(self):
        self.playing = False
        self.index = 0
        self.first = 0
        self.last = 0
        self.fps = 4.0
        self._anchor = 0.0
        self._base = 0

    def start(self, *, index: int, first: int, last: int, fps: float, now: float):
        if fps <= 0 or first < 0 or last < first:
            raise ValueError('Intervallo o velocità non validi')
        self.first, self.last, self.fps = first, last, float(fps)
        self.index = first if index < first or index >= last else index
        self._base, self._anchor = self.index, now
        self.playing = True
        return self.index

    def stop(self):
        self.playing = False

    def tick(self, now: float) -> int:
        if not self.playing:
            return self.index
        self.index = min(self.last, self._base + max(0, int((now - self._anchor) * self.fps)))
        if self.index >= self.last:
            self.playing = False
        return self.index
