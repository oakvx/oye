import os
from pathlib import Path
import tempfile
import unittest
from observer.runtime_paths import data_directory, initialize_data


class InstalledDataTests(unittest.TestCase):
    def test_frozen_files_are_not_written_into_program_files(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(data_directory(frozen=True, base=Path(temp)), Path(temp)/'Oye')

    def test_fresh_install_and_update_preserve_user_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            bundle=base/'program'; bundle.mkdir()
            data=base/'data'; (bundle/'templates').mkdir()
            (bundle/'config.json').write_text('{"new":1}')
            (bundle/'templates'/'gioca.png').write_bytes(b'initial')
            initialize_data(data,bundle)
            self.assertEqual((data/'config.json').read_text(),'{"new":1}')
            (data/'config.json').write_text('{"custom":2}')
            (data/'templates'/'gioca.png').write_bytes(b'user')
            (bundle/'config.json').write_text('{"new":3}')
            (bundle/'templates'/'gioca.png').write_bytes(b'update')
            initialize_data(data,bundle)
            self.assertEqual((data/'config.json').read_text(),'{"custom":2}')
            self.assertEqual((data/'templates'/'gioca.png').read_bytes(),b'user')

    def test_corrupt_bundle_cannot_create_empty_config(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);bundle=base/'program';bundle.mkdir()
            with self.assertRaises(FileNotFoundError):
                initialize_data(base/'data',bundle)


if __name__=='__main__':unittest.main()
