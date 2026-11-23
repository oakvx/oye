"""OCR diagnostics must close SQLite handles (particularly on Windows)."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from observer.ocr_detection_counter import OCRDetectionCounter


class OCRDatabaseConnectionTests(unittest.TestCase):
    def test_all_connections_close_after_counter_use(self):
        original = sqlite3.connect
        connections = []
        class Tracked(sqlite3.Connection):
            closed = False
            def close(self):
                self.closed = True
                return super().close()
        def connect(*args, **kwargs):
            conn = original(*args, **kwargs, factory=Tracked)
            connections.append(conn)
            return conn
        with tempfile.TemporaryDirectory() as folder:
            with patch('observer.ocr_detection_counter.sqlite3.connect', side_effect=connect):
                counter = OCRDetectionCounter(Path(folder))
                counter.observe('gioca', at=1, status='ok', matched=True)
                counter.observe('gioca', at=2, status='ok', matched=True)
                self.assertEqual(counter.snapshot()['all_time_total'], 1)
                self.assertGreaterEqual(len(connections), 3)
                self.assertTrue(all(c.closed for c in connections))

if __name__ == '__main__':
    unittest.main()
