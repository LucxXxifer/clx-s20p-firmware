"""Daily source proof must not trust report strings or unbound CA content."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
import ca_security
import component_gate
import components
import upstream_sync
from component_fixtures import inputs


class ComponentGateTests(unittest.TestCase):
    def test_omitted_adguard_is_checked_in_the_actual_image(self):
        with tempfile.TemporaryDirectory() as name:
            try:
                result = component_gate.verify_adguard(Path(name), {}, None)
            except (TypeError, ValueError) as error:
                self.fail('Excluded AdGuard should not require files: ' + str(error))
            self.assertEqual(result, {'included': False})
            for package in ('adguardhome', 'luci-app-adguardhome', 'luci-i18n-adguardhome-zh-cn'):
                with self.assertRaisesRegex(ValueError, 'excluded AdGuard'):
                    component_gate.verify_adguard(Path(name), {package: {'version': '0.107.79-r1'}}, None)

    def test_release_without_adguard_still_checks_ca_and_exclusion(self):
        value = inputs()
        value['adguard'] = None
        value['fingerprint'] = components.fingerprint(value)
        ca = {'version': '20260816-r2'}
        packages = {name: {'version': ca['version']} for name in ('ca-bundle', 'ca-certificates')}
        report = {'schema_version': 1, 'status': 'pass', 'rootfs_sha256': 'a' * 64,
                  'component_fingerprint': value['fingerprint'], 'ca': ca, 'adguard': {'included': False},
                  'plugin_sources': component_gate.plugin_sources.identity(value['plugin_sources'])}
        with patch('component_gate.reproduce_preparation', return_value=(value, [], ca)):
            component_gate.verify_release(None, report, 'a' * 64, packages)
            packages['ca-bundle']['version'] = '20210101-r1'
            with self.assertRaisesRegex(ValueError, 'release CA package'):
                component_gate.verify_release(None, report, 'a' * 64, packages)
            packages['ca-bundle']['version'] = ca['version']
            packages['adguardhome'] = {'version': '0.107.79-r1'}
            with self.assertRaisesRegex(ValueError, 'excluded AdGuard'):
                component_gate.verify_release(None, report, 'a' * 64, packages)

    def fixture(self, root):
        value = inputs()
        decision = upstream_sync.resolve_head(upstream_sync.BASE_COMMIT, upstream_sync.BASE_COMMIT,
                                              'identical', upstream_sync.BASE_COMMIT, force=False,
                                              component_inputs=value)
        lock, _, _ = upstream_sync.prepare(CI / 'sources.lock.json', CI / 'baseline', root, decision, value)
        requested = json.loads(lock.read_text())
        prepared = [{'path': 'package/system/ca-certificates/Makefile', 'mode': '0o644',
                     'sha256': value['ca']['files'][0]['sha256'],
                     'size': len(components.file_bytes(value['ca']['files'], 'Makefile'))}]
        wrapper = [{'path': 'ci/components.py', 'mode': '0o644', 'sha256': 'e' * 64, 'size': 42}]
        source = {'upstream_commit': upstream_sync.BASE_COMMIT,
                  'upstream_resolution': json.loads((root / 'upstream-resolution.json').read_text()),
                  'source_lock': requested, 'source_lock_sha256': components.digest(lock.read_bytes()),
                  'sources': [{'url': row['url'], 'commit': row['commit']} for row in requested['sources']],
                  'prepared_tree_hash': components.digest(json.dumps(prepared, sort_keys=True).encode()),
                  'wrapper_tree_hash': components.digest(json.dumps(wrapper, sort_keys=True).encode()),
                  'build_fingerprint': components.digest(json.dumps(
                      {'prepared': prepared, 'wrapper': wrapper, 'lock': requested}, sort_keys=True).encode())}
        (root / 'requested-sources.lock.json').write_bytes(lock.read_bytes())
        for name, data in (('source-lock.json', source), ('prepared-source-files.json', prepared),
                           ('wrapper-source-files.json', wrapper)):
            (root / name).write_text(json.dumps(data))
        return value

    def test_component_and_manifest_bytes_are_bound_to_one_build(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / 'inputs'
            value = self.fixture(root)
            self.assertEqual(component_gate.bound_sources(root)[0], value)
            path = root / 'prepared-source-files.json'
            rows = json.loads(path.read_text())
            rows[0]['sha256'] = 'f' * 64
            path.write_text(json.dumps(rows))
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                component_gate.bound_sources(root)

    def test_actual_plugin_checkout_cannot_use_another_commit(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / 'inputs'
            self.fixture(root)
            path = root / 'source-lock.json'
            data = json.loads(path.read_text())
            data['sources'][1]['commit'] = 'f' * 40
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'checkout'):
                component_gate.bound_sources(root)

    def test_source_manifest_rejects_escaping_or_ancestor_paths(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'manifest.json'
            for names in (('../x',), ('a', 'a/b'), ('a', 'a')):
                path.write_text(json.dumps([{'path': n, 'mode': '0o644', 'size': 1, 'sha256': 'f' * 64}
                                           for n in names]))
                with self.assertRaises(ValueError):
                    component_gate.manifest(path)

    def test_ca_archive_bytes_are_verified_before_extraction_or_execution(self):
        with tempfile.TemporaryDirectory() as name:
            package = Path(name)
            (package / 'Makefile').write_text('PKG_VERSION:=20260816\nPKG_RELEASE:=2\n'
                                            'PKG_HASH:=' + 'a' * 64 + '\n'
                                            'PKG_SOURCE_URL:=@DEBIAN/pool/main/c/ca-certificates\n')
            with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                ca_security.reproduce(package, b'untrusted archive')

    def test_adguard_disabled_persistent_defaults_and_version_are_required(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for path, content in {
                'etc/config/adguardhome': "config adguardhome 'config'\n option enabled '0'\n option work_dir '/etc/adguardhome/work'\n",
                'etc/init.d/adguardhome': 'work_dir:string:/etc/adguardhome/work',
                'lib/upgrade/keep.d/adguardhome': '/etc/adguardhome/\n/etc/config/adguardhome\n',
            }.items():
                dest = root / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content)
            packages = {key: {'version': '0.107.79-r1'} for key in
                        ('adguardhome', 'luci-app-adguardhome', 'luci-i18n-adguardhome-zh-cn')}
            component_gate.verify_adguard(root, packages, '0.107.79')
            path = root / 'etc/config/adguardhome'
            path.write_text(path.read_text().replace("enabled '0'", "enabled '1'"))
            with self.assertRaisesRegex(ValueError, 'defaults'):
                component_gate.verify_adguard(root, packages, '0.107.79')

    def test_ca_image_rejects_extra_trust_roots_or_altered_alias(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            trust = root / 'etc/ssl/certs'
            trust.mkdir(parents=True)
            pem = b'controlled PEM bytes'
            (trust / 'test.crt').write_bytes(pem)
            (trust / 'abc123ef.0').symlink_to('test.crt')
            (trust / 'ca-certificates.crt').write_bytes(pem)
            (trust.parent / 'cert.pem').symlink_to('/etc/ssl/certs/ca-certificates.crt')
            report = {'version': '20260816-r2', 'prepared_files': [{'path': 'Makefile', 'sha256': 'a' * 64}],
                      'files': [{'name': 'test.crt', 'sha256': components.digest(pem), 'size': len(pem)},
                                {'name': 'abc123ef.0', 'target': 'test.crt'}],
                      'bundle_size': len(pem), 'bundle_sha256': components.digest(pem),
                      'revoked_der_sha256': ca_security.REVOKED_DER}
            packages = {k: {'version': report['version']} for k in ('ca-bundle', 'ca-certificates')}
            prepared = [{'path': 'package/system/ca-certificates/Makefile', 'sha256': 'a' * 64}]
            ca_security.verify_image(root, report, packages, prepared)
            (trust / 'extra.crt').write_bytes(b'unreviewed')
            with self.assertRaisesRegex(ValueError, 'trust set'):
                ca_security.verify_image(root, report, packages, prepared)
            (trust / 'extra.crt').unlink()
            (trust / 'abc123ef.0').unlink()
            (trust / 'abc123ef.0').symlink_to('elsewhere.crt')
            with self.assertRaisesRegex(ValueError, 'alias'):
                ca_security.verify_image(root, report, packages, prepared)


if __name__ == '__main__':
    unittest.main()
