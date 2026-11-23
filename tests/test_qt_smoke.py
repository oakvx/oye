"""Optional graphical smoke tests, run automatically when PySide6 is installed."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PySide6.QtWidgets import QApplication
    import studio_qt
except ImportError:
    QApplication = None

@unittest.skipIf(QApplication is None,'PySide6 not installed: UI runtime test requires requirements-ui.txt')
class StudioSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old_root=studio_qt.ROOT
        studio_qt.ROOT=Path(self.temp.name)
        self.window=studio_qt.OyeStudio()

    def tearDown(self):
        self.window.close()
        studio_qt.ROOT=self.old_root
        self.temp.cleanup()

    def test_navigation_and_safe_start_mode(self):
        self.assertEqual(self.window.pages.count(),4)
        self.assertEqual(self.window.mode,'observe')
        self.assertFalse(self.window.start_button.isEnabled() is False)
        self.window._navigate(1)
        self.assertEqual(self.window.pages.currentIndex(),1)
        self.window._navigate(2)
        self.assertEqual(self.window.pages.currentIndex(),2)
        self.window._select_mode('train')
        self.assertEqual(self.window.mode,'train')
        self.assertTrue(self.window.mode_buttons['train'].isChecked())

if __name__=='__main__':unittest.main()
