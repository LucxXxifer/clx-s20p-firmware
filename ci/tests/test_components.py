"""Security floors and exact source combinations drive the daily build."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
import components
import go_security
import upstream_sync
from component_fixtures import inputs, files


class ComponentTests(unittest.TestCase):
    def test_omitted_adguard_does_not_resolve_or_download_a_release(self):
        value = inputs()
        releases = [{'stable': True, 'version': row['version'],
                     'files': [{'kind': 'source', 'filename': row['version'] + '.src.tar.gz',
                                'sha256': row['sha256']}]} for row in value['go']]
        def fetch(path):
            self.assertEqual(path, 'repos/' + components.CA_REPOSITORY + '/commits/master')
            return {'sha': value['ca']['commit']}
        with patch('components.plugin_sources.resolve', return_value=value['plugin_sources']), \
                patch('components.api', side_effect=fetch), \
                patch('components.package_files', side_effect=[value['ca']['files'], value['quickstart']['files']]), \
                patch('components.download', return_value=json.dumps(releases).encode()) as download:
            resolved = components.resolve()
        self.assertIsNone(resolved['adguard'])
        download.assert_called_once_with('https://go.dev/dl/?mode=json&include=all')

    def test_preparation_without_adguard_still_reproduces_ca(self):
        import ca_security
        value = inputs()
        value['adguard'] = None
        value['fingerprint'] = components.fingerprint(value)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for path, rows in (('package/system/ca-certificates', value['ca']['files']),
                               ('feeds/custom_feed/quickstart', value['quickstart']['files'])):
                package = root / path
                package.mkdir(parents=True)
                components.write_package(package, rows)
            selected = root / 'components.json'
            selected.write_text(json.dumps(value))
            output = root / 'evidence/preparation.json'
            ca = {'version': '20260816-r2'}
            with patch('components.download', return_value=b'controlled archive') as download, \
                    patch.object(ca_security, 'reproduce', return_value=ca) as reproduce:
                result = components.prepare(root, selected, output)
            reproduce.assert_called_once_with(root / 'package/system/ca-certificates', b'controlled archive')
            download.assert_called_once_with(
                'https://deb.debian.org/debian/pool/main/c/ca-certificates/ca-certificates_20260816.tar.xz')
            self.assertEqual(result['ca_version'], ca['version'])
            self.assertIsNone(json.loads(output.read_text())['adguard_version'])
            self.assertFalse((root / 'feeds/custom_feed/adguardhome').exists())

    def test_changed_security_or_plugin_input_has_a_new_identity(self):
        original = inputs()
        for change in (lambda v: v['go'][0].update(version='go1.26.11'),
                       lambda v: v['adguard'].update(version='0.107.80'),
                       lambda v: v['plugin_sources'][0].update(head_commit='f' * 40)):
            value = copy.deepcopy(original)
            change(value)
            self.assertNotEqual(components.fingerprint(value), original['fingerprint'])

    def test_history_and_unrelated_ca_repo_commits_do_not_rebuild(self):
        value = inputs()
        original = value['fingerprint']
        value['ca']['commit'] = 'f' * 40
        value['plugin_sources'][0]['previous_comparison'] = dict(value['plugin_sources'][0]['comparison'])
        value['fingerprint'] = components.fingerprint(value)
        self.assertEqual(value['fingerprint'], original)
        components.validate(value)

    def test_hash_tampering_or_version_regression_is_rejected(self):
        for mutate in (lambda v: v['go'][0].update(version='go1.26.9'),
                       lambda v: v['go'][1].update(version='go1.27.2'),
                       lambda v: v['adguard'].update(version='0.107.1'),
                       lambda v: v['ca']['files'][0].update(content='aW52YWxpZA==')):
            value = inputs()
            mutate(value)
            value['fingerprint'] = components.fingerprint(value)
            with self.assertRaises(ValueError):
                components.validate(value)

    def test_unsafe_source_paths_cannot_escape_prepared_package(self):
        for path in ('../Makefile', '/Makefile', './Makefile'):
            value = inputs()
            value['ca']['files'][0]['path'] = path
            value['fingerprint'] = components.fingerprint(value)
            with self.assertRaises(ValueError):
                components.validate(value)

    def resolution(self, value, state=None):
        return upstream_sync.resolve_head(upstream_sync.BASE_COMMIT, upstream_sync.BASE_COMMIT,
                                          'identical', upstream_sync.BASE_COMMIT, force=False,
                                          wrapper_commit='c' * 40, previous_state=state,
                                          component_inputs=value)

    def test_component_change_builds_once_without_a_main_source_update(self):
        value = inputs()
        self.assertTrue(self.resolution(value).should_build)
        with tempfile.TemporaryDirectory() as name:
            state = upstream_sync.write_state(Path(name) / 'state.json', wrapper_commit='c' * 40,
                                               upstream_commit=upstream_sync.BASE_COMMIT,
                                               build_started=True, outcome='failure', run_id='1', run_attempt='1',
                                               component_inputs=value)
            self.assertFalse(self.resolution(value, state).should_build)
            value['go'][0]['version'] = 'go1.26.11'
            value['fingerprint'] = components.fingerprint(value)
            self.assertTrue(self.resolution(value, state).should_build)

    def test_istore_openclash_and_docker_updates_each_trigger_one_build(self):
        value = inputs()
        with tempfile.TemporaryDirectory() as name:
            state = upstream_sync.write_state(Path(name) / 'state.json', wrapper_commit='c' * 40,
                                               upstream_commit=upstream_sync.BASE_COMMIT,
                                               build_started=True, outcome='success', run_id='1', run_attempt='1',
                                               component_inputs=value)
            for repo in ('linkease/istore', 'kenzok8/small-package', 'immortalwrt/packages',
                         'lisaac/luci-app-dockerman', 'lisaac/luci-lib-docker'):
                with self.subTest(repository=repo):
                    changed = copy.deepcopy(value)
                    row = next(r for r in changed['plugin_sources'] if r['repository'] == repo)
                    row['head_commit'] = 'f' * 40
                    row['comparison']['compare_status'] = 'ahead'
                    changed['fingerprint'] = components.fingerprint(changed)
                    self.assertTrue(self.resolution(changed, state).should_build)
                    attempt = Path(name) / (repo.replace('/', '-') + '.json')
                    attempted = upstream_sync.write_state(attempt, wrapper_commit='c' * 40,
                                                          upstream_commit=upstream_sync.BASE_COMMIT,
                                                          build_started=True, outcome='failure',
                                                          run_id='2', run_attempt='1', component_inputs=changed)
                    self.assertFalse(self.resolution(changed, attempted).should_build)

    def test_runtime_lock_and_component_bytes_are_both_bound(self):
        value = inputs()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            lock, baseline, evidence = upstream_sync.prepare(CI / 'sources.lock.json', CI / 'baseline',
                                                             root / 'inputs', self.resolution(value), value)
            upstream_sync.verify_prepared(CI / 'sources.lock.json', CI / 'baseline', lock, baseline, evidence)
            value['go'][0]['version'] = 'go1.26.11'
            value['fingerprint'] = components.fingerprint(value)
            evidence.with_name('component-resolution.json').write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'component resolution differs'):
                upstream_sync.verify_prepared(CI / 'sources.lock.json', CI / 'baseline', lock, baseline, evidence)

    def test_newer_go_patch_updates_both_recipes_and_refuses_a_downgrade(self):
        import shutil
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            parent = root / 'feeds/packages/lang/golang'
            for series in ('1.26', '1.27'):
                dest = parent / ('golang' + series) / 'Makefile'
                dest.parent.mkdir(parents=True)
                shutil.copyfile(CI / 'tests/fixtures/go-security' / (series + '.mk'), dest)
            alias = parent / 'golang/Makefile'
            alias.parent.mkdir()
            shutil.copyfile(CI / 'tests/fixtures/go-security/default-alias.mk', alias)
            value = inputs()
            value['go'][0]['version'] = 'go1.26.11'
            value['go'][1]['version'] = 'go1.27.4'
            value['fingerprint'] = components.fingerprint(value)
            selected = root / 'components.json'
            selected.write_text(json.dumps(value))
            result = go_security.prepare(root, selected)
            self.assertEqual([row['version'] for row in result['recipes']], ['go1.26.11', 'go1.27.4'])
            before = {p: p.read_bytes() for p in parent.rglob('Makefile')}
            # Identical inputs are idempotent; a stale resolver cannot rewind
            # either compiler or partially write the other recipe.
            go_security.prepare(root, selected)
            value['go'][1]['version'] = 'go1.27.3'
            value['fingerprint'] = components.fingerprint(value)
            selected.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'refusing downgrade'):
                go_security.prepare(root, selected)
            self.assertEqual(before, {p: p.read_bytes() for p in parent.rglob('Makefile')})


if __name__ == '__main__':
    unittest.main()
