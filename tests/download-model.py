#!/usr/bin/env python3
"""Check explicit model provisioning without fetching the large public model."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/download-model.py'


class DownloadModelTests(unittest.TestCase):
    def test_download_verify_and_keep_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b'GGUF test fixture\n' * 32
            source = root / 'source.gguf'
            source.write_bytes(payload)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({
                'filename': 'Laya-Q8_0.gguf',
                'url': source.as_uri(),
                'size_bytes': len(payload),
                'sha256': hashlib.sha256(payload).hexdigest(),
            }))
            output = root / 'resources' / 'Laya-Q8_0.gguf'
            command = [sys.executable, str(SCRIPT), str(output.parent), '--manifest', str(manifest)]

            first = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(output.stat().st_mode & 0o777, 0o644)
            second = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn('Already verified', second.stdout)

            output.write_bytes(b'existing user file')
            mismatch = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertEqual(output.read_bytes(), b'existing user file')

            output.unlink()
            manifest.write_text(json.dumps({
                'filename': 'Laya-Q8_0.gguf',
                'url': source.as_uri(),
                'size_bytes': len(payload),
                'sha256': '0' * 64,
            }))
            corrupt = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(corrupt.returncode, 0)
            self.assertFalse(output.exists())
            self.assertEqual(list(output.parent.glob('.*.part')), [])

            # An installed copy finds the manifest beside the helper.
            installed = root / 'installed'
            installed.mkdir()
            shutil.copy(SCRIPT, installed / 'download-model.py')
            manifest.write_text(json.dumps({
                'filename': 'Laya-Q8_0.gguf',
                'url': source.as_uri(),
                'size_bytes': len(payload),
                'sha256': hashlib.sha256(payload).hexdigest(),
            }))
            shutil.copy(manifest, installed / 'manifest.json')
            installed_output = root / 'installed-resources' / 'Laya-Q8_0.gguf'
            packaged = subprocess.run([sys.executable, str(installed / 'download-model.py'),
                                       str(installed_output.parent)], text=True, capture_output=True)
            self.assertEqual(packaged.returncode, 0, packaged.stderr)
            self.assertEqual(installed_output.read_bytes(), payload)

            npm = root / 'npm'
            npm.mkdir()
            shutil.copy(SCRIPT, npm / 'download-model.py')
            shutil.copy(manifest, npm / 'model-manifest.json')
            npm_output = root / 'npm-resources' / 'Laya-Q8_0.gguf'
            packaged = subprocess.run([sys.executable, str(npm / 'download-model.py'),
                                       str(npm_output.parent)], text=True, capture_output=True)
            self.assertEqual(packaged.returncode, 0, packaged.stderr)
            self.assertEqual(npm_output.read_bytes(), payload)


if __name__ == '__main__':
    unittest.main()
