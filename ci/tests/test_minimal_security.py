"""Precise HTTP/ACL backports must reject source and evidence drift."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
DATA = CI / 'security/minimal-20261031'


class MinimalSecurityTests(unittest.TestCase):
    def module(self):
        path = CI / 'minimal_security.py'
        self.assertTrue(path.is_file(), 'minimal fixed profile needs a source/image security gate')
        spec = importlib.util.spec_from_file_location('minimal_security', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def source(self, root, mod):
        for relative, source in ((mod.UHTTPD_RECIPE, 'uhttpd-Makefile.before'),
                                 (mod.ACL_SOURCE, 'luci-mod-system.before.json')):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((DATA / source).read_bytes())
        return root

    def test_profile_pins_official_patch_and_only_deletes_mounts_cron_grant(self):
        mod = self.module()
        profile = mod.profile()
        self.assertEqual(mod.profile_id(), 's20p-minimal-security-20261031')
        self.assertEqual(len(mod.profile_sha256()), 64)
        self.assertEqual(profile['uhttpd']['commit'], '64dcf47aa7f84ae60db9a3950e5c4b8fbe5ed6f1')
        before = json.loads((DATA / 'luci-mod-system.before.json').read_text())
        after = json.loads((DATA / 'luci-mod-system.after.json').read_text())
        del before['luci-mod-system-mounts']['write']['file']['/etc/crontabs/root']
        self.assertEqual(before, after)
        self.assertEqual(after['luci-mod-system-cron']['write']['file']['/etc/crontabs/root'], ['write'])
        self.assertEqual(after['luci-mod-system-mounts']['write']['file']['/bin/umount'], ['exec'])

    def test_original_uhttpd_source_accepts_exact_official_patch(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'ubus.c').write_bytes((DATA / 'uhttpd-ubus.before.c').read_bytes())
            subprocess.run(['git', 'apply', '--check', str(DATA / mod.PATCH_NAME)], cwd=root, check=True)
            subprocess.run(['git', 'apply', str(DATA / mod.PATCH_NAME)], cwd=root, check=True)
            self.assertEqual(hashlib.sha256((root / 'ubus.c').read_bytes()).hexdigest(),
                             mod.profile()['uhttpd']['patched_ubus_sha256'])
            self.assertIn(b'if (!uh_ubus_send_list(cl, data.params))', (root / 'ubus.c').read_bytes())

    def test_prepare_exact_sources_and_refuses_second_application(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = self.source(Path(name), mod)
            output = root / 'provenance/minimal-security-preparation.json'
            report = mod.prepare(root, output)
            self.assertEqual(json.loads(output.read_text()), report)
            self.assertEqual(report, mod.preparation_report())
            self.assertIn(b'PKG_RELEASE:=2\n', (root / mod.UHTTPD_RECIPE).read_bytes())
            self.assertEqual((root / mod.ACL_SOURCE).read_bytes(), (DATA / 'luci-mod-system.after.json').read_bytes())
            self.assertEqual((root / mod.PATCH_PATH).read_bytes(), (DATA / mod.PATCH_NAME).read_bytes())
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with self.assertRaises(ValueError):
                mod.prepare(root, root / 'second.json')
            self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_prepare_rejects_all_source_drift_before_writes(self):
        mod = self.module()
        for changed in (mod.UHTTPD_RECIPE, mod.ACL_SOURCE):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as name:
                root = self.source(Path(name), mod)
                (root / changed).write_bytes((root / changed).read_bytes() + b'\n')
                before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
                with self.assertRaisesRegex(ValueError, 'source|SHA'):
                    mod.prepare(root, root / 'report.json')
                self.assertEqual(before, {p: p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_prepare_rejects_unreviewed_existing_patches_and_symlink_parents(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = self.source(Path(name), mod)
            extra = root / mod.PATCH_PATH
            extra.parent.mkdir()
            (extra.parent / '050-extra.patch').write_text('unreviewed')
            with self.assertRaisesRegex(ValueError, 'patch'):
                mod.prepare(root, root / 'report.json')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            real = root / 'real'
            real.mkdir()
            (root / 'feeds').symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                mod.prepare(root, root / 'report.json')

    def test_payload_hash_supports_real_image_sizes_and_rejects_symlinks(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'rootfs.squashfs'
            path.write_bytes(b'large image' * 200000)
            self.assertEqual(mod.payload_digest(path), hashlib.sha256(path.read_bytes()).hexdigest())
            alias = path.with_name('alias')
            alias.symlink_to(path)
            with self.assertRaises(ValueError):
                mod.payload_digest(alias)

    def test_profile_rejects_modified_vendored_input(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for p in DATA.iterdir():
                if p.is_file():
                    (root / p.name).write_bytes(p.read_bytes())
            (root / mod.PATCH_NAME).write_bytes(b'fake patch')
            with patch.object(mod, 'HERE', root), self.assertRaisesRegex(ValueError, 'SHA|digest'):
                mod.profile()

    def evidence(self, root, mod):
        provenance = root / 'provenance'
        provenance.mkdir()
        (provenance / 'minimal-security-preparation.json').write_text(json.dumps(mod.preparation_report()))
        rows = mod.preparation_report()['prepared_files']
        (provenance / 'prepared-source-files.json').write_text(json.dumps(rows))
        wrapper, _ = mod.wrapper_rows()
        (provenance / 'wrapper-source-files.json').write_text(json.dumps(wrapper))
        packages = {name: {'name': name, 'version': version} for name, version in mod.EXPECTED_PACKAGES.items()}
        payload = root / 'rootfs.squashfs'
        payload.write_bytes(b'new rootfs')
        image = root / 'rootfs'
        path = image / mod.ACL_INSTALLED.lstrip('/')
        path.parent.mkdir(parents=True)
        path.write_bytes((DATA / 'luci-mod-system.after.json').read_bytes())
        database = image / 'lib/apk/db/installed'
        database.parent.mkdir(parents=True)
        database.write_text(json.dumps(list(packages.values())))
        return provenance, packages, payload, image

    def test_inspection_binds_acl_packages_payload_and_full_source_provenance(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            provenance, packages, payload, image = self.evidence(Path(name), mod)
            with patch.object(mod, 'bound_sources', return_value=(None, mod.preparation_report()['prepared_files'])), \
                    patch.object(mod, 'parse_packages', return_value=packages):
                value = mod.inspect(image, payload, provenance)
                mod.verify_report(value, hashlib.sha256(payload.read_bytes()).hexdigest(), provenance, packages)
                self.assertFalse(value['runtime_tested'])
                self.assertEqual(value['files'][0]['sha256'],
                                 'b5a005ccc669a3dc74df2f9758a6e55dc43c634f4d1da11143153276ae40d867')
                for change in ('payload', 'acl', 'profile', 'version', 'extra'):
                    bad = copy.deepcopy(value)
                    if change == 'payload':
                        bad['rootfs_sha256'] = 'a' * 64
                    elif change == 'acl':
                        bad['files'][0]['sha256'] = 'a' * 64
                    elif change == 'profile':
                        bad['profile_sha256'] = 'a' * 64
                    elif change == 'version':
                        bad['packages']['uhttpd-mod-ubus'] = '2026.08.24~373145f7-r1'
                    else:
                        bad['unreviewed'] = True
                    with self.subTest(change=change), self.assertRaises(ValueError):
                        mod.verify_report(bad, hashlib.sha256(payload.read_bytes()).hexdigest(), provenance, packages)

    def test_old_uhttpd_package_or_missing_ubus_module_is_rejected(self):
        mod = self.module()
        for change in ('old', 'missing'):
            with tempfile.TemporaryDirectory() as name:
                provenance, packages, payload, _ = self.evidence(Path(name), mod)
                if change == 'old':
                    packages['uhttpd-mod-ubus']['version'] = '2026.08.24~373145f7-r1'
                else:
                    del packages['uhttpd-mod-ubus']
                with patch.object(mod, 'bound_sources', return_value=(None, mod.preparation_report()['prepared_files'])), \
                        self.assertRaisesRegex(ValueError, 'package'):
                    mod.expected_report('a' * 64, provenance, packages)

    def test_evidence_cannot_omit_or_forge_prepared_patch(self):
        mod = self.module()
        for change in ('missing', 'hash', 'mode'):
            with tempfile.TemporaryDirectory() as name:
                provenance, packages, _, _ = self.evidence(Path(name), mod)
                rows = copy.deepcopy(mod.preparation_report()['prepared_files'])
                if change == 'missing':
                    rows = rows[:-1]
                elif change == 'hash':
                    rows[0]['sha256'] = 'a' * 64
                else:
                    rows[0]['mode'] = '0o777'
                with patch.object(mod, 'bound_sources', return_value=(None, rows)), self.assertRaises(ValueError):
                    mod.expected_report('a' * 64, provenance, packages)

    def test_inspect_rejects_old_or_symlinked_acl_and_unsafe_payload(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            provenance, packages, payload, image = self.evidence(Path(name), mod)
            with patch.object(mod, 'bound_sources', return_value=(None, mod.preparation_report()['prepared_files'])), \
                    patch.object(mod, 'parse_packages', return_value=packages):
                acl = image / mod.ACL_INSTALLED.lstrip('/')
                acl.write_bytes((DATA / 'luci-mod-system.before.json').read_bytes())
                with self.assertRaisesRegex(ValueError, 'ACL|SHA'):
                    mod.inspect(image, payload, provenance)
                acl.unlink()
                acl.symlink_to(DATA / 'luci-mod-system.after.json')
                with self.assertRaisesRegex(ValueError, 'symlink|regular'):
                    mod.inspect(image, payload, provenance)
                alias = payload.with_name('alias')
                alias.symlink_to(payload)
                with self.assertRaisesRegex(ValueError, 'symlink|regular'):
                    mod.inspect(image, alias, provenance)


if __name__ == '__main__':
    unittest.main()
