#!/usr/bin/env python3
"""Offline contract tests for fail-closed Git input locking and source evidence."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'source_lock.py'
SPEC = importlib.util.spec_from_file_location('source_lock', SCRIPT)
lockmod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lockmod)


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


class SourceLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        git(self.repo, 'config', 'user.name', 'Source lock test')
        (self.repo / 'tracked').write_text('original\n')
        git(self.repo, 'add', 'tracked')
        git(self.repo, 'commit', '-qm', 'base')
        self.commit = git(self.repo, 'rev-parse', 'HEAD')
        self.url = 'https://github.com/Example/Source.git'
        git(self.repo, 'remote', 'add', 'origin', self.url)
        self.lock = {'schema_version': 1, 'sources': [
            {'url': self.url, 'ref': '25.12', 'commit': self.commit}]}

    def test_committed_lock_covers_wrapper_literal_git_sources(self):
        actual = json.loads(SCRIPT.with_name('sources.lock.json').read_text())
        self.assertEqual(sum(row.get('role') == 'feed' for row in actual['sources']), 7)
        self.assertEqual(lockmod.lookup(actual, 'https://github.com/chasey-dev/immortalwrt-mt798x-rebase'),
                         '9f397975c7b47ce6986a6e39529d2f72fd5c729d')
        import re
        for path in (SCRIPT.parents[1] / 'wrt_core/modules').glob('*.sh'):
            for url in re.findall(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git', path.read_text()):
                lockmod.lookup(actual, url)

    def test_url_canonicalization_strips_git_and_case(self):
        self.assertEqual(lockmod.lookup(self.lock, 'https://github.com/example/source'), self.commit)

    def test_unknown_repository_ref_and_non_commit_fail(self):
        for url, ref in [('https://github.com/example/other.git', None),
                         (self.url, 'main')]:
            with self.assertRaises(lockmod.LockError):
                lockmod.lookup(self.lock, url, ref)
        self.lock['sources'][0]['commit'] = 'main'
        with self.assertRaises(lockmod.LockError):
            lockmod.lookup(self.lock, self.url)

    def test_duplicate_ambiguous_repository_fails(self):
        self.lock['sources'].append(dict(self.lock['sources'][0]))
        with self.assertRaises(lockmod.LockError):
            lockmod.lookup(self.lock, self.url)

    def test_pin_feeds_preserves_comments_and_local_sources(self):
        text = '# feeds\nsrc-git packages https://github.com/example/source.git;25.12 # note\nsrc-link custom /tmp/custom\n'
        expected = '# feeds\nsrc-git packages https://github.com/example/source.git^' + self.commit + ' # note\nsrc-link custom /tmp/custom\n'
        self.assertEqual(lockmod.pin_feeds_text(text, self.lock), expected)
        self.assertEqual(lockmod.pin_feeds_text(expected, self.lock), expected)

    def test_pin_feed_rejects_unlocked_or_different_pins(self):
        for text in ['src-git f https://github.com/example/other.git;main\n',
                     'src-git f ' + self.url + '^' + '0'*40 + '\n',
                     'src-git f ' + self.url + ';main\n']:
            with self.assertRaises(lockmod.LockError):
                lockmod.pin_feeds_text(text, self.lock)

    def test_capture_clean_repo_and_verify(self):
        evidence = lockmod.capture(self.repo, self.lock)
        self.assertEqual(evidence['head'], self.commit)
        self.assertEqual(evidence['dirty_files'], [])
        lockmod.verify_checkout(self.repo, self.lock)
        lockmod.verify_capture(self.repo, self.lock, evidence)

    def test_dirty_edits_and_untracked_content_are_hashed(self):
        (self.repo / 'tracked').write_text('changed\n')
        (self.repo / 'new').write_text('added\n')
        (self.repo / 'link').symlink_to('new')
        evidence = lockmod.capture(self.repo, self.lock)
        self.assertEqual({x['path'] for x in evidence['dirty_files']}, {'tracked', 'new', 'link'})
        with self.assertRaises(lockmod.LockError):
            lockmod.verify_checkout(self.repo, self.lock)
        lockmod.verify_checkout(self.repo, self.lock, allow_dirty=True)
        (self.repo / 'new').write_text('tampered\n')
        with self.assertRaises(lockmod.LockError):
            lockmod.verify_capture(self.repo, self.lock, evidence)

    def test_deleted_file_and_filemode_are_recorded(self):
        (self.repo / 'tracked').unlink()
        evidence = lockmod.capture(self.repo, self.lock)
        self.assertEqual(evidence['dirty_files'][0]['type'], 'missing')
        (self.repo / 'tracked').write_text('original\n')
        (self.repo / 'tracked').chmod(0o755)
        evidence = lockmod.capture(self.repo, self.lock)
        self.assertEqual(evidence['dirty_files'][0]['mode'], '0755')

    def test_wrong_head_and_wrong_remote_fail(self):
        (self.repo / 'tracked').write_text('new commit\n')
        git(self.repo, 'commit', '-qam', 'wrong HEAD')
        with self.assertRaises(lockmod.LockError):
            lockmod.capture(self.repo, self.lock)
        git(self.repo, 'reset', '--hard', self.commit)
        git(self.repo, 'remote', 'set-url', 'origin', 'https://github.com/evil/source.git')
        with self.assertRaises(lockmod.LockError):
            lockmod.capture(self.repo, self.lock)

    def test_source_capture_rejects_parent_symlink_escape(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'file').write_text('do not read outside checkout')
        (self.repo / 'escape').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(lockmod.LockError):
            lockmod.file_record(self.repo, 'escape/file')

    def test_cli_failure_keeps_feeds_file_unchanged(self):
        lock_path = self.base / 'lock.json'
        lock_path.write_text(json.dumps(self.lock))
        feeds = self.base / 'feeds.conf'
        original = 'src-git f https://github.com/unlocked/repo.git;main\n'
        feeds.write_text(original)
        result = subprocess.run(['python3', str(SCRIPT), '--lock', str(lock_path),
                                 'pin-feeds', str(feeds)], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(feeds.read_text(), original)

    def test_cli_capture_roundtrip_and_in_tree_output_rejection(self):
        lock_path = self.base / 'lock.json'
        lock_path.write_text(json.dumps(self.lock))
        snapshot = self.base / 'evidence.json'
        result = subprocess.run(['python3', str(SCRIPT), '--lock', str(lock_path),
                                 'capture', str(self.repo), '--output', str(snapshot)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(['python3', str(SCRIPT), '--lock', str(lock_path),
                                 'verify-capture', str(self.repo), str(snapshot)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(['python3', str(SCRIPT), '--lock', str(lock_path),
                                 'capture', str(self.repo), '--output', str(self.repo / 'evidence.json')],
                                capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.repo / 'evidence.json').exists())

    def test_staged_then_unstaged_changes_alter_evidence(self):
        (self.repo / 'tracked').write_text('staged\n')
        git(self.repo, 'add', 'tracked')
        a = lockmod.capture(self.repo, self.lock)
        git(self.repo, 'reset', '-q', 'HEAD', 'tracked')
        b = lockmod.capture(self.repo, self.lock)
        self.assertNotEqual(a['source_state_sha256'], b['source_state_sha256'])


if __name__ == '__main__':
    unittest.main()
