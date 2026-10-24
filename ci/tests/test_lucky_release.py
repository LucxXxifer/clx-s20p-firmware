#!/usr/bin/env python3
"""The S20P preservation build must not drift Lucky on a later rebuild."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
LUCKY_URL = 'https://github.com/gdy666/luci-app-lucky.git'
RECOVERED_COMMIT = '9449a5250c61f1aa8f16b2d8e04a9da25c26e735'
CORE_HASH = '44bcbb2fb434a649af65d59cd0ec4e0f4e01b67b9a16c0ec49589b1a5b7fa81a'


class LuckyReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.feed = self.base / 'feed'
        (self.feed / 'lucky/files').mkdir(parents=True)
        (self.feed / 'luci-app-lucky').mkdir()
        self.core = self.feed / 'lucky/Makefile'
        self.core.write_text('PKG_NAME:=lucky\nPKG_VERSION:=2.27.2\nPKG_RELEASE:=3\n'
                             'PKG_SOURCE:=old.tar.gz\nPKG_SOURCE_URL:=https://example.invalid/old\n'
                             'PKG_HASH:=skip\n\ndefine Build/Prepare\n\tkeep-this-recipe\nendef\n')
        (self.feed / 'luci-app-lucky/Makefile').write_text(
            'PKG_VERSION:=3.0.0\nPKG_RELEASE:=26\n')
        self.lock = json.loads((ROOT / 'ci/sources.lock.json').read_text())
        self.lock_path = self.base / 'sources.lock.json'

    def invoke(self, *, clone_fails=False, s20p=True):
        self.lock_path.write_text(json.dumps(self.lock))
        script = r'''
set -euo pipefail
source "$REPO_ROOT/wrt_core/modules/feed_source_fixes.sh"
get_custom_feed_worktree_dir() { printf '%s\n' "$TEST_FEED"; }
git_retry() {
    if [[ ${1:-} == clone ]]; then
        [[ $TEST_CLONE_FAILS == 0 ]] || return 23
        cp -R "$TEST_FEED/lucky" "$TEST_FEED/luci-app-lucky" "${!#}/"
    fi
}
resolve_latest_lucky_release() { echo 'UNEXPECTED dynamic discovery' >&2; return 43; }
update_lucky
'''
        env = dict(os.environ, REPO_ROOT=str(ROOT), TEST_FEED=str(self.feed),
                   TEST_CLONE_FAILS=str(int(clone_fails)), S20P_AUTOBUILD=str(int(s20p)),
                   S20P_CI_DIR=str(ROOT / 'ci'), S20P_SOURCE_LOCK=str(self.lock_path))
        return subprocess.run(['bash', '-c', script], env=env, text=True, capture_output=True)

    def test_recovered_app_source_retains_prior_lock_provenance(self):
        row = next(row for row in self.lock['sources'] if row['url'] == LUCKY_URL)
        self.assertEqual(row['commit'], RECOVERED_COMMIT)
        self.assertEqual(row['provenance'], 'verified-original-rom-source-recovery')
        self.assertEqual(row['previous_lock']['commit'], '69305fef127259892ba4603e98f730f0330e5caa')
        self.assertEqual(row['previous_lock']['provenance'],
                         'resolved-at-lock-creation-not-original-2026-10-17-feeds')

    def test_s20p_pins_core_without_dynamic_discovery_and_preserves_prepare(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('UNEXPECTED dynamic discovery', result.stderr)
        self.assertIn('PKG_VERSION:=3.4.0\n', self.core.read_text())
        self.assertIn('PKG_HASH:=' + CORE_HASH + '\n', self.core.read_text())
        self.assertIn('PKG_SOURCE_URL:=https://release.66666.host/v3.4.0/3.4.0_wanji_docker\n',
                      self.core.read_text())
        self.assertTrue(self.core.read_text().endswith('define Build/Prepare\n\tkeep-this-recipe\nendef\n'))
        first = hashlib.sha256(self.core.read_bytes()).hexdigest()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(hashlib.sha256(self.core.read_bytes()).hexdigest(), first)

    def test_s20p_clone_failure_stops_build(self):
        result = self.invoke(clone_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('UNEXPECTED dynamic discovery', result.stderr)

    def test_s20p_rejects_missing_or_invalid_release_lock_without_rewriting(self):
        row = next(row for row in self.lock['sources'] if row['url'] == LUCKY_URL)
        original = row['release'].copy()
        variants = [None, dict(original, version='latest'),
                    dict(original, source_url='https://example.invalid/core'),
                    dict(original, architecture='x86_64'), dict(original, sha256='skip')]
        for value in variants:
            with self.subTest(value=value):
                row['release'] = value
                before = self.core.read_bytes()
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.core.read_bytes(), before)
                self.assertNotIn('UNEXPECTED dynamic discovery', result.stderr)

    def test_s20p_rejects_ambiguous_recipe_before_rewriting(self):
        self.core.write_text(self.core.read_text() + 'PKG_HASH:=duplicate\n')
        before = self.core.read_bytes()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.core.read_bytes(), before)

    def test_s20p_rejects_wrong_app_version(self):
        (self.feed / 'luci-app-lucky/Makefile').write_text('PKG_VERSION:=3.0.0\nPKG_RELEASE:=28\n')
        before = self.core.read_bytes()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.core.read_bytes(), before)

    def test_other_wrapper_builds_keep_existing_dynamic_discovery_behavior(self):
        before = self.core.read_bytes()
        result = self.invoke(s20p=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('UNEXPECTED dynamic discovery', result.stderr)
        self.assertEqual(self.core.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
