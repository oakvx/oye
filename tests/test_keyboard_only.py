import unittest
from observer.config import validate


class KeyboardOnlyTests(unittest.TestCase):
    def test_keyboard_is_default_and_gamepad_rejected(self):
        self.assertEqual(validate({})['input_backend'], 'keyboard')
        with self.assertRaisesRegex(ValueError, 'solo keyboard'):
            validate({'input_backend': 'gamepad'})

if __name__ == '__main__':
    unittest.main()
