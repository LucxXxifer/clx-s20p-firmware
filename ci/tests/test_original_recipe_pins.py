#!/usr/bin/env python3
"""Real recipe regressions for source drift unrelated to requested S20P additions."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures/original-recipes'


class OriginalRecipePinsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.feed = self.base / 'feed'
        self.geodata = self.feed / 'v2ray-geodata/Makefile'
        self.axonhub = self.feed / 'axonhub/Makefile'
        for path, fixture in ((self.geodata, 'geodata-current.Makefile'),
                              (self.axonhub, 'axonhub-current.Makefile')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((FIXTURES / fixture).read_bytes())
        self.lock = json.loads((ROOT / 'ci/sources.lock.json').read_text())
        self.lock_path = self.base / 'lock.json'
        # Small test patches exercise exact-set/hash rejection; production keeps
        # the eight recovered upstream patch SHA256 values in its source lock.
        patches = self.feed / 'axonhub/patches'
        patches.mkdir()
        patch = patches / '100-test.patch'
        patch.write_bytes(b'reviewed test patch\n')
        if 'original_recipe_pins' in self.lock:
            self.lock['original_recipe_pins']['axonhub']['patches'] = {
                patch.name: hashlib.sha256(patch.read_bytes()).hexdigest()}

    def invoke(self):
        self.lock_path.write_text(json.dumps(self.lock))
        return subprocess.run(['python3', str(ROOT / 'ci/original_recipe_pins.py'),
                               '--lock', str(self.lock_path), '--feed', str(self.feed)],
                              text=True, capture_output=True, env=os.environ)

    def test_pin_real_recipes_and_keep_unrelated_inputs(self):
        before = self.geodata.read_bytes()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        geodata = self.geodata.read_bytes()
        self.assertIn(b'GEOSITE_VER:=20261015074526\n', geodata)
        self.assertIn(b'405ad108a016ae20c4dfa94cf97384c93ed1bd688121acdc2c5a09c77cca2a4f', geodata)
        self.assertEqual(geodata, before.replace(b'20261023064708', b'20261015074526').replace(
            b'76b0704d7f2630e9b06d83595e9ffa363c946a1a3e19c40c0f43aa9f5d77f399',
            b'405ad108a016ae20c4dfa94cf97384c93ed1bd688121acdc2c5a09c77cca2a4f'))
        self.assertIn(b'PKG_VERSION:=20261016\n', self.axonhub.read_bytes())
        self.assertIn(b'PKG_SOURCE_VERSION:=a946fca495c52344ed073abb6c3562b9ff728b12\n',
                      self.axonhub.read_bytes())
        self.assertIn(b'PKG_HASH:=d767de4e917e3f62de6c71d2b37a1e9865ac835b89fa24aeeb714a5282d65524\n',
                      self.axonhub.read_bytes())
        first = (geodata, self.axonhub.read_bytes())
        self.assertEqual(self.invoke().returncode, 0)
        self.assertEqual(first, (self.geodata.read_bytes(), self.axonhub.read_bytes()))

    def test_changed_recipe_rejected_before_any_write(self):
        self.axonhub.write_bytes(self.axonhub.read_bytes() + b'# drift\n')
        before = (self.geodata.read_bytes(), self.axonhub.read_bytes())
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(before, (self.geodata.read_bytes(), self.axonhub.read_bytes()))

    def test_modified_missing_or_added_patch_rejected_before_any_write(self):
        patch = self.feed / 'axonhub/patches/100-test.patch'
        for operation in ('modified', 'missing', 'added'):
            with self.subTest(operation=operation):
                patch.write_bytes(b'reviewed test patch\n')
                extra = patch.with_name('200-extra.patch')
                extra.unlink(missing_ok=True)
                if operation == 'modified':
                    patch.write_bytes(b'changed\n')
                elif operation == 'missing':
                    patch.unlink()
                else:
                    extra.write_bytes(b'extra\n')
                before = (self.geodata.read_bytes(), self.axonhub.read_bytes())
                self.assertNotEqual(self.invoke().returncode, 0)
                self.assertEqual(before, (self.geodata.read_bytes(), self.axonhub.read_bytes()))

    def test_missing_lock_and_tampered_output_hash_rejected(self):
        pins = self.lock.pop('original_recipe_pins', None)
        self.assertNotEqual(self.invoke().returncode, 0)
        if pins is not None:
            self.lock['original_recipe_pins'] = pins
            pins['geosite']['recipe_sha256_after'] = '0' * 64
            self.assertNotEqual(self.invoke().returncode, 0)

    def test_luci_source_is_recovered_and_hook_precedes_defconfig(self):
        row = next(row for row in self.lock['sources']
                   if row['url'] == 'https://github.com/immortalwrt/luci.git')
        self.assertEqual(row['commit'], '36e3f0d13622f5be6d59d9ba257811914d76e34d')
        self.assertEqual(row['previous_lock']['commit'], '61e876e98f91446fd8968dfede721b98ddf80a32')
        hook = (ROOT / 'build.sh').read_text()
        self.assertLess(hook.index('original_recipe_pins.py'), hook.index('\napply_config\n'))

    def test_patch_directory_symlink_rejected_before_write(self):
        patches = self.feed / 'axonhub/patches'
        moved = self.base / 'outside-patches'
        patches.rename(moved)
        patches.symlink_to(moved, target_is_directory=True)
        before = (self.geodata.read_bytes(), self.axonhub.read_bytes())
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(before, (self.geodata.read_bytes(), self.axonhub.read_bytes()))


if __name__ == '__main__':
    unittest.main()
