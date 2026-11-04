"""Frozen Wi-Fi diagnostics: real C accounting, exact inputs and image binding."""
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
DATA = CI / 'wifi-logging/20261104'
ARCHIVE = Path(os.environ.get('S20P_WIFI_TEST_ARCHIVE', '/nonexistent'))


class WifiLoggingTests(unittest.TestCase):
    def module(self):
        path = CI / 'wifi_logging.py'
        self.assertTrue(path.is_file(), 'Wi-Fi logging needs a mandatory source/image gate')
        spec = importlib.util.spec_from_file_location('wifi_logging', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def source(self, root, mod):
        checkout = patch.object(mod, 'checkout_commit', return_value=mod.profile()['upstream_commit'])
        checkout.start()
        self.addCleanup(checkout.stop)
        recipe = root / mod.RECIPE
        recipe.parent.mkdir(parents=True)
        recipe.write_bytes((DATA / 'Makefile.before').read_bytes())
        (recipe.parent / 'config.in').write_bytes((DATA / 'config.in.before').read_bytes())
        shutil.copytree(DATA / 'upstream-patches', root / mod.PATCH_DIR)
        return root

    def tree(self, root):
        return {str(p.relative_to(root)): (os.readlink(p) if p.is_symlink() else p.read_bytes())
                for p in root.rglob('*') if p.is_file() or p.is_symlink()}

    def test_profile_pins_recipe_complete_inventory_archive_and_exact_release_delta(self):
        mod = self.module()
        value = mod.profile()
        self.assertEqual(value['upstream_commit'], '9f397975c7b47ce6986a6e39529d2f72fd5c729d')
        self.assertEqual(len(value['upstream_patches']), 41)
        self.assertEqual(value['archive']['sha256'], 'b029d7b43c498193092dc6a3c857361c8835ce34bb306561c550ac0539784a78')
        before = (DATA / 'Makefile.before').read_bytes()
        after = mod.source_payloads()[mod.RECIPE]
        self.assertEqual(after, before.replace(b'PKG_VERSION:=7.6.7.3\n', b'PKG_VERSION:=7.6.7.3\nPKG_RELEASE:=3\n', 1))
        self.assertEqual(mod.PACKAGE_VERSION, '6.12.112.7.6.7.3-r3')
        self.assertEqual(mod.FOLLOWUP_PATCH_NAME, '101-s20p-gtk-rekey-start-info.patch')

    def test_r3_profile_rejects_missing_or_tampered_ap_source_input(self):
        mod = self.module()
        self.assertEqual(mod.profile_id(), 's20p-wifi-logging-20261104')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve() / 'profile'
            shutil.copytree(DATA, root)
            (root / 'source-before/ap_sec.c.gz').unlink()
            with patch.object(mod, 'HERE', root), self.assertRaisesRegex(ValueError, 'inventory'):
                mod.profile()
            shutil.copytree(DATA, root, dirs_exist_ok=True)
            source = root / 'source-before/ap_cfg.c.gz'
            source.write_bytes(source.read_bytes() + b'\n')
            with patch.object(mod, 'HERE', root), self.assertRaisesRegex(ValueError, 'SHA256'):
                mod.profile()

    def test_prepare_accepts_exact_inputs_and_rejects_duplicate_without_mutation(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = self.source(Path(name).resolve(), mod)
            output = root / 'evidence.json'
            self.assertEqual(mod.prepare(root, output), mod.preparation_report())
            self.assertEqual(json.loads(output.read_text()), mod.preparation_report())
            self.assertEqual((root / mod.PATCH_PATH).read_bytes(), (DATA / mod.PATCH_NAME).read_bytes())
            before = self.tree(root)
            with self.assertRaises(ValueError):
                mod.prepare(root, root / 'again.json')
            self.assertEqual(self.tree(root), before)

    def test_recipe_config_patch_drift_and_extra_missing_duplicate_symlink_fail_before_writes(self):
        mod = self.module()
        for change in ('recipe', 'config', 'patch', 'extra', 'missing', 'duplicate', 'symlink', 'parent', 'output', 'custom-patch'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                root = self.source(Path(name).resolve(), mod)
                patches = root / mod.PATCH_DIR
                first = sorted(patches.iterdir())[0]
                output = root / 'report.json'
                if change == 'recipe':
                    (root / mod.RECIPE).write_bytes((root / mod.RECIPE).read_bytes() + b'\n')
                elif change == 'config':
                    (patches.parent / 'config.in').write_bytes(b'changed')
                elif change == 'patch':
                    first.write_bytes(first.read_bytes() + b'\n')
                elif change in ('extra', 'duplicate'):
                    (patches / '999-extra.patch').write_bytes(first.read_bytes() if change == 'duplicate' else b'unreviewed')
                elif change == 'missing':
                    first.unlink()
                elif change == 'symlink':
                    first.unlink()
                    first.symlink_to(sorted((DATA / 'upstream-patches').iterdir())[0])
                elif change == 'custom-patch':
                    (patches / mod.FOLLOWUP_PATCH_NAME).write_bytes(b'unreviewed custom patch')
                elif change == 'parent':
                    saved = patches.with_name('real-patches')
                    patches.rename(saved)
                    patches.symlink_to(saved, target_is_directory=True)
                else:
                    output.symlink_to(root / 'absent')
                before = self.tree(root)
                with self.assertRaises(ValueError):
                    mod.prepare(root, output)
                self.assertEqual(self.tree(root), before)

    def test_different_actual_upstream_checkout_is_rejected_before_mutation(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = self.source(Path(name).resolve(), mod)
            before = self.tree(root)
            with patch.object(mod, 'checkout_commit', return_value='a' * 40), self.assertRaisesRegex(ValueError, 'upstream checkout'):
                mod.prepare(root, root / 'report.json')
            self.assertEqual(self.tree(root), before)

    def test_prepare_output_collision_and_regular_parent_fail_before_source_mutation(self):
        mod = self.module()
        for change in ('new-patch', 'recipe', 'regular-parent', 'nested-below-patch', 'patch-dir', 'dotdot'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                root = self.source(Path(name).resolve(), mod)
                if change == 'new-patch':
                    output = root / mod.PATCH_PATH
                elif change == 'recipe':
                    output = root / mod.RECIPE
                elif change == 'nested-below-patch':
                    output = root / mod.PATCH_PATH / 'report.json'
                elif change == 'patch-dir':
                    output = root / mod.PATCH_DIR / 'report.json'
                elif change == 'dotdot':
                    output = root / mod.PATCH_DIR / '..' / Path(mod.PATCH_PATH).name
                else:
                    parent = root / 'regular-parent'
                    parent.write_bytes(b'not a directory')
                    output = parent / 'report.json'
                before = self.tree(root)
                with self.assertRaises((ValueError, OSError)):
                    mod.prepare(root, output)
                self.assertEqual(self.tree(root), before)

    def apply_fixture(self, root, mod):
        for source in ('pmf.c', 'cmm_wpa.c'):
            target = root / 'mt_wifi/embedded/security' / source
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((DATA / 'source-before' / source).read_bytes())
        for source in ('ap_sec.c', 'ap_cfg.c'):
            target = root / 'mt_wifi/embedded/ap' / source
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.decompress((DATA / 'source-before' / (source + '.gz')).read_bytes()))
        subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(DATA / mod.PATCH_NAME)],
                       cwd=root, check=True, capture_output=True)
        subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(DATA / mod.FOLLOWUP_PATCH_NAME)],
                       cwd=root, check=True, capture_output=True)
        return root / 'mt_wifi'

    def test_actual_patch_changes_only_ap_action_diagnostic_and_three_gtk_levels(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            sources = self.apply_fixture(Path(name).resolve(), mod)
            before = (DATA / 'source-before/cmm_wpa.c').read_text()
            after = (sources / 'embedded/security/cmm_wpa.c').read_text()
            # Undo just the approved severity changes; every timeout/retry/MIC/replay byte survives.
            restored = after.replace('DBG_LVL_INFO,\n\t\t\t\t"Rekey interval excess',
                                     'DBG_LVL_ERROR,\n\t\t\t\t"Rekey interval excess')
            restored = restored.replace('DBG_LVL_INFO,\n\t\t\t\t "AP SETKEYS DONE(',
                                        'DBG_LVL_WARN,\n\t\t\t\t "AP SETKEYS DONE(')
            restored = restored.replace('DBG_LVL_INFO, "AP SETKEYS DONE -', 'DBG_LVL_WARN, "AP SETKEYS DONE -')
            self.assertEqual(restored, before)
            self.assertEqual(after.count('DBG_LVL_INFO') - before.count('DBG_LVL_INFO'), 3)
            for source in ('ap_sec.c', 'ap_cfg.c'):
                original = gzip.decompress((DATA / 'source-before' / (source + '.gz')).read_bytes())
                patched = (sources / 'embedded/ap' / source).read_bytes()
                needle = b'DBG_LVL_INFO,'
                offset = original.index(b'Rekey interval excess, Update Group Key')
                original_call = original.rfind(b'MTWF_DBG(', 0, offset)
                original_token = original.index(b'DBG_LVL_ERROR,', original_call)
                patched_offset = patched.index(b'Rekey interval excess, Update Group Key')
                patched_call = patched.rfind(b'MTWF_DBG(', 0, patched_offset)
                patched_token = patched.index(needle, patched_call)
                expected = (original[:original_token] + needle +
                            original[original_token + len(b'DBG_LVL_ERROR,'):])
                self.assertEqual(patched, expected)
            original = (DATA / 'source-before/pmf.c').read_text()
            patched = (sources / 'embedded/security/pmf.c').read_text()
            helper_start = patched.index('/* Bound only the AP action-frame diagnostic;')
            helper_end = patched.index('/* End bounded PMF diagnostic. */') + len('/* End bounded PMF diagnostic. */\n\n')
            patched = patched[:helper_start] + patched[helper_end:]
            old = (DATA / 'pmf-diagnostic.before').read_text()
            new = (DATA / 'pmf-diagnostic.after').read_text()
            self.assertEqual(patched.replace(new, old), original)
            self.assertIn('if (FC->SubType == SUBTYPE_ACTION)', new)
            self.assertIn('DBG_LVL_ERROR', new)
            self.assertIn('PMF NR_UCAST global suppressed=%u%s SubType=%u WCID=%u', new)
            for relative in ('mt_wifi/embedded/security/pmf.c', 'mt_wifi/embedded/security/cmm_wpa.c',
                             'mt_wifi/embedded/ap/ap_sec.c', 'mt_wifi/embedded/ap/ap_cfg.c'):
                self.assertEqual(mod.digest((sources / relative.removeprefix('mt_wifi/')).read_bytes()),
                                 mod.profile()['patched_sources'][relative])

    def test_real_helper_c_handles_bursts_rollover_saturation_and_concurrent_calls(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            sources = self.apply_fixture(root, mod)
            text = (sources / 'embedded/security/pmf.c').read_text()
            helper = text[text.index('/* Bound only the AP action-frame diagnostic;'):text.index('/* End bounded PMF diagnostic. */')]
            (root / 'helper.c').write_text(helper)
            (root / 'linux').mkdir()
            for header in ('jiffies.h', 'spinlock.h'):
                (root / 'linux' / header).write_text('/* Kernel primitives supplied by the pthread harness. */\n')
            for bits in (32, 64):
                executable = root / ('test-' + str(bits))
                subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread',
                                '-DTEST_BITS=' + str(bits), '-I', str(root), str(DATA / 'helper-test.c'),
                                '-o', str(executable)], check=True, capture_output=True)
                result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
                self.assertIn('burst rollover saturation concurrency: pass', result.stdout)

    def test_effective_dbg_enhance_mapping_uses_kernel_warn_and_info(self):
        mod = self.module()
        evidence = json.loads((DATA / 'logging-evidence.json').read_text())
        self.assertIn('EXTRA_CFLAGS += -DDBG_ENHANCE', evidence['makefile'])
        self.assertIn('mtwf_dbg_prt(pAd,Category,Level', evidence['macro'])
        self.assertIn('case DBG_LVL_INFO:', evidence['dispatch'])
        self.assertIn('MTWF_PRINT_DBG_LVL_INFO("%s", strBuf)', evidence['dispatch'])
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            (root / 'mapping.h').write_text(evidence['mapping'])
            executable = root / 'mapping'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(root),
                            str(DATA / 'logging-test.c'), '-o', str(executable)], check=True, capture_output=True)
            self.assertEqual(subprocess.check_output([str(executable)], text=True).strip(), 'KERN_ERR=3 KERN_WARNING=4 KERN_INFO=6')

    def test_actual_pmf_message_survives_real_logger160_with_default_and_maximum_prefix(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            diagnostic = (DATA / 'pmf-diagnostic.after').read_text()
            start = diagnostic.index('MTWF_DBG(pAd, DBG_CAT_SEC, CATSEC_PMF, DBG_LVL_WARN,')
            end = diagnostic.index(');', start) + 2
            (root / 'diagnostic.h').write_text('#line 1531\n' + diagnostic[start:end] + '\n')
            executable = root / 'logger'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(root), '-I', str(DATA), str(DATA / 'logger-test.c'),
                            '-o', str(executable)], check=True, capture_output=True)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('real logger160 default/max-prefix context count newline: pass', result.stdout)

    def test_profile_rejects_vendored_patch_tampering(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve() / 'profile'
            shutil.copytree(DATA, root)
            (root / mod.PATCH_NAME).write_bytes(b'tampered')
            with patch.object(mod, 'HERE', root), self.assertRaises(ValueError):
                mod.profile()

    def evidence(self, root, mod):
        root = root.resolve()
        provenance = root / 'provenance'
        provenance.mkdir(parents=True)
        configs = root / 'metadata/kernel'
        configs.mkdir(parents=True)
        final_config = root / 'metadata/final.config'
        final_config.write_bytes((DATA / 'driver.config').read_bytes())
        (configs / '.config').write_text('CONFIG_64BIT=y\nCONFIG_ARM64=y\nCONFIG_SMP=y\n')
        (configs / 'kernel.release').write_text(mod.KERNEL_VERSION + '\n')
        (configs / '.vermagic').write_text('b' * 32 + '\n')
        prepared = mod.preparation_report()['prepared_files'] + [mod.file_row('.config', final_config.read_bytes())]
        (provenance / 'prepared-source-files.json').write_text(json.dumps(prepared))
        (provenance / 'wifi-logging-preparation.json').write_text(json.dumps(mod.preparation_report()))
        (provenance / 'wifi-logging-build.json').write_text(json.dumps(mod.expected_build_report()))
        wrapper = mod.wrapper_rows()[0]
        (provenance / 'wrapper-source-files.json').write_text(json.dumps(wrapper))
        (provenance / 'source-lock.json').write_text(json.dumps({
            'upstream_commit': mod.profile()['upstream_commit'], 'wrapper_checkout_commit': 'e' * 40,
            'wrapper_tree_hash': mod.digest(json.dumps(wrapper, sort_keys=True).encode()),
            'prepared_tree_hash': mod.digest(json.dumps(prepared, sort_keys=True).encode()),
            'source_lock_sha256': 'c' * 64, 'build_fingerprint': 'd' * 64,
        }))
        kernel = mod.KERNEL_VERSION + '~' + 'b' * 32 + '-r1'
        packages = {
            'kernel': {'name': 'kernel', 'version': kernel},
            mod.PACKAGE_NAME: {'name': mod.PACKAGE_NAME, 'version': mod.PACKAGE_VERSION,
                               'depends': 'kernel=' + kernel + ' kmod-conninfra kmod-mediatek_hnat kmod-warp wifi-dats',
                               'files': list(mod.MODULES)},
        }
        image = root / 'rootfs'
        database = image / 'lib/apk/db/installed'
        database.parent.mkdir(parents=True)
        database.write_text(json.dumps({'packages': list(packages.values())}))
        for relative in mod.MODULES:
            path = image / relative.lstrip('/')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(test_module(Path(relative).stem, mod))
        payload = root / 'inspection/rootfs.squashfs'
        payload.parent.mkdir()
        payload.write_bytes(b'unit fixture payload')
        return provenance, prepared, packages, image, payload

    def test_inspection_and_independent_verification_bind_modules_package_abi_config_and_source(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            provenance, prepared, packages, image, payload = self.evidence(Path(name).resolve(), mod)
            with patch.object(mod, 'bound_sources', return_value=(None, prepared)):
                report = mod.inspect(image, payload, provenance)
                self.assertEqual({row['path'] for row in report['modules']}, set(mod.MODULES))
                self.assertEqual(report['kernel_abi'], packages['kernel']['version'])
                self.assertFalse(report['runtime_tested'])
                extracted = {'/lib/apk/db/installed': (image / 'lib/apk/db/installed').read_bytes()}
                extracted.update({relative: (image / relative.lstrip('/')).read_bytes() for relative in mod.MODULES})
                with patch.object(mod, 'payload_files', return_value=extracted):
                    mod.verify_release(Path(name).resolve(), report, packages, 'e' * 40)
                    for change in ('module', 'package', 'abi', 'profile', 'payload', 'config', 'source', 'cross-source', 'extra'):
                        bad = copy.deepcopy(report)
                        if change == 'module':
                            bad['modules'][0]['sha256'] = 'a' * 64
                        elif change == 'package':
                            bad['package']['version'] = '6.12.112.7.6.7.3-r1'
                        elif change == 'abi':
                            bad['kernel_abi'] = 'other'
                        elif change == 'profile':
                            bad['profile_sha256'] = 'a' * 64
                        elif change == 'payload':
                            bad['rootfs_sha256'] = 'a' * 64
                        elif change == 'config':
                            bad['configuration']['final_config_sha256'] = 'a' * 64
                        elif change == 'source':
                            bad['build']['archive']['sha256'] = 'a' * 64
                        elif change == 'cross-source':
                            bad['source_binding']['wrapper_checkout_commit'] = 'a' * 40
                        else:
                            bad['unreviewed'] = True
                        with self.subTest(change=change), self.assertRaises(ValueError):
                            mod.verify_release(Path(name).resolve(), bad, packages, 'e' * 40)
                    extracted[mod.MODULES[0]] += b'tampered executable bytes'
                    with self.assertRaises(ValueError):
                        mod.verify_release(Path(name).resolve(), report, packages, 'e' * 40)

    def test_inspect_rejects_wrong_module_package_kernel_config_and_symlink(self):
        mod = self.module()
        for change in ('old', 'missing-proxy', 'symlink', 'vermagic', 'machine', 'marker', 'kernel', 'dependency', 'module-dependency', 'config'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                provenance, prepared, _, image, payload = self.evidence(Path(name).resolve(), mod)
                module = image / mod.MODULES[0].lstrip('/')
                db = image / 'lib/apk/db/installed'
                packages = json.loads(db.read_text())['packages']
                if change == 'old':
                    packages[1]['version'] = '6.12.112.7.6.7.3-r1'
                elif change == 'missing-proxy':
                    (image / mod.MODULES[1].lstrip('/')).unlink()
                elif change == 'symlink':
                    data = module.read_bytes()
                    module.unlink()
                    target = module.with_name('real')
                    target.write_bytes(data)
                    module.symlink_to(target)
                elif change == 'vermagic':
                    module.write_bytes(module.read_bytes().replace(b'vermagic=6.12.112', b'vermagic=6.11.112'))
                elif change == 'machine':
                    data = bytearray(module.read_bytes())
                    struct.pack_into('<H', data, 18, 62)
                    module.write_bytes(data)
                elif change == 'marker':
                    module.write_bytes(module.read_bytes().replace(b'global suppressed=', b'obsolete_marker__='))
                elif change == 'kernel':
                    packages[0]['version'] = packages[0]['version'].replace('b' * 32, 'c' * 32)
                elif change == 'dependency':
                    packages[1]['depends'] += ' unreviewed-driver'
                elif change == 'module-dependency':
                    module.write_bytes(module.read_bytes().replace(b'depends=conninfra,mtkhnat', b'depends=unexpected_driver'))
                else:
                    path = Path(name).resolve() / 'metadata/final.config'
                    path.write_bytes(path.read_bytes().replace(b'CONFIG_MTK_DOT11W_PMF_SUPPORT=y', b'# CONFIG_MTK_DOT11W_PMF_SUPPORT is not set'))
                db.write_text(json.dumps({'packages': packages}))
                with patch.object(mod, 'bound_sources', return_value=(None, prepared)), self.assertRaises((ValueError, OSError)):
                    mod.inspect(image, payload, provenance)

    def test_prepared_source_wrapper_build_evidence_drift_rejected(self):
        mod = self.module()
        for change in ('prepared', 'extra-patch', 'wrapper', 'upstream', 'archive', 'built-source', 'omitted-build'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                provenance, prepared, _, image, payload = self.evidence(Path(name).resolve(), mod)
                if change == 'prepared':
                    prepared[0]['sha256'] = 'a' * 64
                elif change == 'extra-patch':
                    prepared.append(mod.file_row(mod.PATCH_DIR + '/999-unreviewed.patch', b'bad'))
                elif change == 'wrapper':
                    path = provenance / 'wrapper-source-files.json'
                    rows = json.loads(path.read_text()); rows[0]['sha256'] = 'a' * 64
                    path.write_text(json.dumps(rows))
                elif change == 'upstream':
                    path = provenance / 'source-lock.json'
                    value = json.loads(path.read_text()); value['upstream_commit'] = 'a' * 40
                    path.write_text(json.dumps(value))
                elif change == 'omitted-build':
                    (provenance / 'wifi-logging-build.json').unlink()
                else:
                    path = provenance / 'wifi-logging-build.json'
                    value = json.loads(path.read_text())
                    if change == 'archive':
                        value['archive']['sha256'] = 'a' * 64
                    else:
                        key = next(iter(value['patched_sources']))
                        value['patched_sources'][key] = 'a' * 64
                    path.write_text(json.dumps(value))
                with patch.object(mod, 'bound_sources', return_value=(None, prepared)), self.assertRaises((ValueError, OSError)):
                    mod.inspect(image, payload, provenance)

    def test_profile_is_required_by_default_and_cannot_be_disabled_by_deleting_marker(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            provenance, _, _, _, _ = self.evidence(Path(name).resolve(), mod)
            self.assertTrue(mod.required(provenance, 'e' * 40))
            wrapper = json.loads((provenance / 'wrapper-source-files.json').read_text())
            wrapper = [row for row in wrapper if not row['path'].startswith(('ci/wifi_logging.py', 'ci/wifi-logging/'))]
            (provenance / 'wrapper-source-files.json').write_text(json.dumps(wrapper))
            self.assertTrue(mod.required(provenance, 'e' * 40))
            with self.assertRaises(ValueError):
                mod.required(provenance, 'f' * 40)

    def test_real_source_fingerprints_bind_complete_wrapper_prepared_and_requested_lock(self):
        mod = self.module()
        from test_component_gate import ComponentGateTests
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            provenance, prepared, _, image, payload = self.evidence(root, mod)
            ComponentGateTests().fixture(root / 'component-inputs')
            for path in (root / 'component-inputs').iterdir():
                if path.name not in ('source-lock.json', 'prepared-source-files.json', 'wrapper-source-files.json') and path.is_file():
                    shutil.copyfile(path, provenance / path.name)
            source = json.loads((root / 'component-inputs/source-lock.json').read_text())
            source['wrapper_checkout_commit'] = 'e' * 40
            wrapper = json.loads((provenance / 'wrapper-source-files.json').read_text())
            source['prepared_tree_hash'] = mod.digest(json.dumps(prepared, sort_keys=True).encode())
            source['wrapper_tree_hash'] = mod.digest(json.dumps(wrapper, sort_keys=True).encode())
            source['build_fingerprint'] = mod.digest(json.dumps(
                {'prepared': prepared, 'wrapper': wrapper, 'lock': source['source_lock']}, sort_keys=True).encode())
            (provenance / 'source-lock.json').write_text(json.dumps(source))
            report = mod.inspect(image, payload, provenance)
            self.assertEqual(report['source_binding']['build_fingerprint'], source['build_fingerprint'])
            prepared[0]['sha256'] = 'a' * 64
            (provenance / 'prepared-source-files.json').write_text(json.dumps(prepared))
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                mod.inspect(image, payload, provenance)

    def test_host_squashfs_reader_reextracts_exact_module_and_db_paths_and_rejects_failures(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            payload = Path(name).resolve() / 'rootfs.squashfs'
            payload.write_bytes(b'fixture')
            with patch.object(mod.shutil, 'which', return_value='/usr/bin/unsquashfs'), \
                    patch.object(mod.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'actual bytes', b'')) as run:
                value = mod.payload_files(payload)
                self.assertEqual(set(value), set(mod.MODULES) | {'/lib/apk/db/installed'})
                self.assertEqual(run.call_count, 3)
                self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                                 ['lib/apk/db/installed'] + [path.lstrip('/') for path in mod.MODULES])
            with patch.object(mod.shutil, 'which', return_value=None), self.assertRaisesRegex(ValueError, 'host unsquashfs'):
                mod.payload_files(payload)
            with patch.object(mod.shutil, 'which', return_value='/usr/bin/unsquashfs'), \
                    patch.object(mod.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'bad')), \
                    self.assertRaisesRegex(ValueError, 'SquashFS'):
                mod.payload_files(payload)

    def test_exact_historical_wrapper_needs_bound_pipeline_bytes_and_cannot_self_select(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            provenance = Path(name).resolve()
            commit, rows = next(iter(json.loads((DATA / 'historical-wrappers.json').read_text()).items()))
            (provenance / 'source-lock.json').write_text(json.dumps({
                'wrapper_checkout_commit': commit,
                'wrapper_tree_hash': mod.digest(json.dumps(rows, sort_keys=True).encode()),
            }))
            path = provenance / 'wrapper-source-files.json'
            path.write_text(json.dumps(rows))
            self.assertFalse(mod.required(provenance, commit))
            rows[0]['sha256'] = 'a' * 64
            path.write_text(json.dumps(rows))
            with self.assertRaises(ValueError):
                mod.required(provenance, commit)

    def test_pipeline_requires_prepare_capture_inspection_and_independent_release(self):
        self.assertIn('/wifi_logging.py" prepare', (CI.parent / 'wrt_core/update.sh').read_text())
        self.assertIn('wifi_logging.capture(', (CI / 'capture.sh').read_text())
        self.assertIn('ci/wifi_logging.py inspect', (CI / 'build.sh').read_text())
        self.assertIn('wifi_logging.verify_release(', (CI / 'release_candidate.py').read_text())
        self.assertIn('gate.wifi_logging(root, provenance_ok)', (CI / 'preservation.py').read_text())

    @unittest.skipUnless(ARCHIVE.is_file(), 'set S20P_WIFI_TEST_ARCHIVE for full archive/41-patch replay')
    def test_replayed_gtk_start_and_success_callsites_emit_info_through_vendor_logger(self):
        mod = self.module()
        self.assertEqual(mod.payload_digest(ARCHIVE), 'b029d7b43c498193092dc6a3c857361c8835ce34bb306561c550ac0539784a78')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            subprocess.run(['tar', '-xf', str(ARCHIVE), '-C', str(root)], check=True)
            for path in sorted((DATA / 'upstream-patches').iterdir()):
                subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(path)],
                               cwd=root, check=True, capture_output=True)
            subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(DATA / mod.PATCH_NAME)],
                           cwd=root, check=True, capture_output=True)
            subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(DATA / mod.FOLLOWUP_PATCH_NAME)],
                           cwd=root, check=True, capture_output=True)

            starts, successes = [], []
            for source in sorted((root / 'mt_wifi').rglob('*')):
                if source.suffix not in ('.c', '.h'):
                    continue
                text = source.read_text(errors='replace')
                for marker, destination in (("Rekey interval excess, Update Group Key", starts),
                                            ("AP SETKEYS DONE", successes)):
                    offset = 0
                    while (found := text.find(marker, offset)) >= 0:
                        begin = text.rfind('MTWF_DBG(', 0, found)
                        end = text.find(');', found)
                        self.assertGreaterEqual(begin, 0, str(source))
                        self.assertGreaterEqual(end, 0, str(source))
                        destination.append((source, text[begin:end + 2]))
                        offset = found + len(marker)
            self.assertEqual([(p.relative_to(root).as_posix(), call.split('DBG_LVL_', 1)[1].split(',', 1)[0])
                              for p, call in starts], [
                                  ('mt_wifi/embedded/ap/ap_cfg.c', 'INFO'),
                                  ('mt_wifi/embedded/ap/ap_sec.c', 'INFO'),
                                  ('mt_wifi/embedded/security/cmm_wpa.c', 'INFO'),
                              ])
            self.assertEqual(len(successes), 2)

            logger_source = (root / 'mt_wifi/embedded/common/dbg_ctrl.c').read_text()
            logger_start = logger_source.index('void mtwf_dbg_prt(\n')
            logger_end = logger_source.index('#endif /* DBG_ENHANCE */', logger_start)
            with tempfile.TemporaryDirectory() as harness_name:
                harness = Path(harness_name).resolve()
                (harness / 'logger.c').write_text(logger_source[logger_start:logger_end])
                (harness / 'mapping.h').write_text(json.loads((DATA / 'logging-evidence.json').read_text())['mapping'])
                generated_calls = []
                calls = [call for _, call in starts + successes]
                for index, call in enumerate(calls):
                    generated_calls.append(f'''static void emit_{index}(RTMP_ADAPTER *pAd)
{{
    struct net_device dev = {{"wlan0"}};
    struct Entry entry = {{.Addr={{1,2,3,4,5,6}}, .wcid=7}};
    struct Entry *pEntry = &entry;
    struct SecConfig config = {{1,2,3,1}};
    struct SecConfig *sec_cfg_group = &config;
    struct SecConfig *pSecConfig = &config;
    (void)sec_cfg_group;
    (void)pSecConfig;
    (void)pEntry;
    entry.wdev = &pAd->ApCfg.MBSSID[0].wdev;
    pAd->ApCfg.MBSSID[0].wdev.if_dev = &dev;
{call}
}}''')
                driver = r'''#include <assert.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define KERNEL_VERSION(a,b,c) (((a)<<16)+((b)<<8)+(c))
#define LINUX_VERSION_CODE KERNEL_VERSION(6,12,112)
#define DBG_ENHANCE
#define KERN_CRIT "\0012"
#define KERN_ERR "\0013"
#define KERN_WARNING "\0014"
#define KERN_NOTICE "\0015"
#define KERN_INFO "\0016"
#define pr_crit(fmt, ...) capture(2, fmt, __VA_ARGS__)
#define pr_err(fmt, ...) capture(3, fmt, __VA_ARGS__)
#define pr_warn(fmt, ...) capture(4, fmt, __VA_ARGS__)
#define pr_warning(fmt, ...) capture(4, fmt, __VA_ARGS__)
#define pr_notice(fmt, ...) capture(5, fmt, __VA_ARGS__)
#define pr_info(fmt, ...) capture(6, fmt, __VA_ARGS__)
#define IN
#define DBG_PRINT_BUF_SIZE 160
#define MAX_MULTI_STA 2
#define MAX_WDS_ENTRY 2
#define VALID_MBSS(p,i) ((i) < 2)
#define INT_MAIN 0
#define INT_MBSSID 1
#define INT_APCLI 2
#define INT_WDS 3
#define DBG_LVL_OFF 0
#define DBG_LVL_ERROR 1
#define DBG_LVL_WARN 2
#define DBG_LVL_NOTICE 3
#define DBG_LVL_INFO 4
#define DBG_LVL_DEBUG 5
#define DBG_CAT_ALL 0
#define DBG_CAT_SEC 15
#define DBG_SUBCAT_ALL 0x00000001u
#define DBG_LVL_MAX 5
#define CATSEC_PMF 0
#define os_snprintf_error(size,ret) ((ret)<0 || (size_t)(ret)>=(size_t)(size))
#define UINT32 uint32_t
#define INT8 char
#define INT32 int
#define UINT unsigned int
static int emitted_level = -1;
static char output[512];
static UINT32 DebugCategory;
static UINT32 DebugSubCategory[DBG_LVL_MAX + 1][32];
static void capture(int level, const char *fmt, ...)
{
    va_list args;
    emitted_level = level;
    va_start(args, fmt);
    vsnprintf(output, sizeof(output), fmt, args);
    va_end(args);
}
struct net_device { char name[16]; };
struct wifi_dev { struct net_device *if_dev; };
struct vif { struct wifi_dev wdev; };
struct Entry { struct wifi_dev *wdev; unsigned char Addr[6]; unsigned int wcid; };
struct SecConfig { int AKMMap, PairwiseCipher, GroupCipher, GroupKeyId; };
struct os_cookie { unsigned int ioctl_if, ioctl_if_type; };
typedef struct os_cookie *POS_COOKIE;
typedef struct { unsigned int ChipID; void *OS_Cookie; void *hdev_ctrl; struct { struct vif MBSSID[2]; } ApCfg; struct vif StaCfg[2]; struct { struct vif WdsEntry[2]; } WdsTab; } RTMP_ADAPTER;
struct task { char comm[16]; } task = {"gtk-test"};
struct task *current = &task;
static bool mtwf_dbg_prtCatLvl = true, mtwf_dbg_prtIntfName = false;
static bool mtwf_dbg_prtThreadId = false, mtwf_dbg_prtFuncLine = false;
#define MTWF_PRINT(...) do { assert(0); } while(0)
#define MACSTR "%02X:%02X:%02X:%02X:%02X:%02X"
#define MAC2STR(a) (a)[0],(a)[1],(a)[2],(a)[3],(a)[4],(a)[5]
#define PRINT_MAC(a) (a)[0],(a)[1],(a)[2],(a)[3],(a)[4],(a)[5]
#define RTMP_OS_NETDEV_GET_DEVNAME(p) ((p)->name)
static char *GetAuthModeStr(int mode) { (void)mode; return "auth"; }
static char *GetEncryModeStr(int mode) { (void)mode; return "cipher"; }
#include "mapping.h"
#define MTWF_DBG(pAd, Category, SubCategory, Level, ...) \
    do { \
        if (((0x1u << (Category)) & DebugCategory) && \
            ((SubCategory) & DebugSubCategory[(Level)][(Category)])) \
            mtwf_dbg_prt(pAd, (Category), (Level), __func__, __LINE__, __VA_ARGS__); \
    } while (0)
#include "logger.c"
CALLS
int main(void)
{
    struct os_cookie cookie = {0, INT_MAIN};
    RTMP_ADAPTER ad = {.ChipID=0x7981, .OS_Cookie=&cookie, .hdev_ctrl=&ad};
    int expected[] = {6,6,6,6,6};
    void (*emit[])(RTMP_ADAPTER *) = {EMITTERS};
    DebugCategory = 1u << DBG_CAT_SEC;
    for (unsigned int level=0; level<=DBG_LVL_MAX; ++level)
        DebugSubCategory[level][DBG_CAT_SEC] = DBG_SUBCAT_ALL;
    for (unsigned int i=0; i<sizeof(emit)/sizeof(emit[0]); ++i) {
        emitted_level = -1;
        output[0] = '\0';
        emit[i](&ad);
        if (emitted_level != expected[i]) {
            fprintf(stderr, "callsite %u emitted KERN_%s: %s", i,
                    emitted_level == 3 ? "ERR" : "other", output);
            return 1;
        }
        assert(strstr(output, "Rekey interval excess") || strstr(output, "AP SETKEYS DONE"));
    }
    puts("all 3 GTK start and 2 success callsites emit KERN_INFO through vendor logger");
    return 0;
}
'''
                emitter_names = ','.join(f'emit_{index}' for index in range(len(calls)))
                (harness / 'gtk.c').write_text(driver.replace('CALLS', '\n'.join(generated_calls))
                                               .replace('EMITTERS', emitter_names))
                executable = harness / 'gtk-logger'
                compile_result = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(harness),
                                                  str(harness / 'gtk.c'), '-o', str(executable)],
                                                 capture_output=True, text=True)
                self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
                result = subprocess.run([str(executable)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('all 3 GTK start and 2 success callsites emit KERN_INFO', result.stdout)

    def test_release_job_installs_trusted_host_squashfs_before_independent_verification(self):
        workflow = (CI.parent / '.github/workflows/s20p-daily.yml').read_text()
        publish = workflow.split('  publish-rolling-a-prerelease:', 1)[1]
        self.assertIn('runs-on: ubuntu-24.04', publish)
        command = 'sudo apt-get install --yes --no-install-recommends squashfs-tools'
        self.assertIn(command, publish)
        install = publish.index(command)
        verify = publish.index('python3 ci/release_candidate.py prepare')
        self.assertLess(install, verify)

    def test_preservation_requires_exact_report_before_accepting_only_wifi_r3_transition(self):
        mod = self.module()
        import preservation
        with tempfile.TemporaryDirectory() as name:
            provenance, prepared, _, image, payload = self.evidence(Path(name).resolve(), mod)
            gate = preservation.Gate('image', CI / 'baseline')
            gate._source_evidence = (provenance / 'source-lock.json', json.loads((provenance / 'source-lock.json').read_text()))
            with patch.object(mod, 'bound_sources', return_value=(None, prepared)), patch.dict(sys.modules, {'wifi_logging': mod}):
                gate.wifi_logging(image, True)
                self.assertTrue(any(row['category'] == 'wifi_logging_guard' for row in gate.errors))
                gate.errors.clear()
                report = mod.inspect(image, payload, provenance)
                path = provenance.parent / 'reports/wifi-logging.json'
                path.parent.mkdir()
                path.write_text(json.dumps(report))
                gate.wifi_logging(image, True)
                self.assertEqual(gate.errors, [])
                self.assertEqual(gate.checks['wifi_logging'], report)
                for version, verified, disposition in ((mod.PACKAGE_VERSION, True, 'expected'),
                                                        (mod.PACKAGE_VERSION, False, 'review_required'),
                                                        ('6.12.112.7.6.7.3-r2', True, 'review_required')):
                    candidate = preservation.Gate('image', CI / 'baseline')
                    if verified:
                        candidate.checks['wifi_logging'] = report
                    database = image / 'lib/apk/db/installed'
                    values = json.loads(database.read_text())
                    values['packages'][1]['version'] = version
                    database.write_text(json.dumps(values))
                    candidate.packages(image, database)
                    transitions = [row for row in candidate.differences
                                   if row['category'] == 'package_version' and row['subject'] == mod.PACKAGE_NAME]
                    self.assertEqual(len(transitions), 1)
                    self.assertEqual(transitions[0]['disposition'], disposition)

    @unittest.skipUnless(ARCHIVE.is_file(), 'set S20P_WIFI_TEST_ARCHIVE for full archive/41-patch replay')
    def test_exact_archive_replays_complete_stack_and_new_patch(self):
        mod = self.module()
        self.assertEqual(mod.payload_digest(ARCHIVE), mod.profile()['archive']['sha256'])
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            subprocess.run(['tar', '-xf', str(ARCHIVE), '-C', str(root)], check=True)
            for path in sorted((DATA / 'upstream-patches').iterdir()):
                subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(path)],
                               cwd=root, check=True, capture_output=True)
            for source in ('pmf.c', 'cmm_wpa.c'):
                self.assertEqual((root / 'mt_wifi/embedded/security' / source).read_bytes(),
                                 (DATA / 'source-before' / source).read_bytes())
            for source in ('ap_sec.c', 'ap_cfg.c'):
                self.assertEqual((root / 'mt_wifi/embedded/ap' / source).read_bytes(),
                                 gzip.decompress((DATA / 'source-before' / (source + '.gz')).read_bytes()))
            logger = (root / 'mt_wifi/embedded/common/dbg_ctrl.c').read_text()
            start = logger.index('void mtwf_dbg_prt(\n')
            end = logger.index('#endif /* DBG_ENHANCE */', start)
            self.assertEqual((DATA / 'logger.c').read_text(), logger[start:end])
            self.assertIn('#define DBG_PRINT_BUF_SIZE\t160',
                          (root / 'mt_wifi/embedded/include/dbg_ctrl.h').read_text())
            subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(DATA / mod.PATCH_NAME)],
                           cwd=root, check=True, capture_output=True)
            subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(DATA / mod.FOLLOWUP_PATCH_NAME)],
                           cwd=root, check=True, capture_output=True)
            for relative, digest in mod.profile()['patched_sources'].items():
                self.assertEqual(mod.digest((root / relative).read_bytes()), digest)
            build = root / 'build'
            compiled = build / 'build_dir/target-aarch64/linux-mediatek_filogic/mt_wifi'
            compiled.mkdir(parents=True)
            for relative in mod.profile()['patched_sources']:
                target = compiled / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / relative, target)
            archive = build / 'dl' / ARCHIVE.name
            archive.parent.mkdir()
            shutil.copyfile(ARCHIVE, archive)
            evidence = root / 'evidence.json'
            self.assertEqual(mod.capture(build, evidence), mod.expected_build_report())
            with self.assertRaises(ValueError):
                mod.capture(build, evidence)
            path = compiled / 'mt_wifi/embedded/security/pmf.c'
            path.write_bytes(path.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'compiler source'):
                mod.capture(build, root / 'drift.json')
            shutil.copyfile(root / 'mt_wifi/embedded/security/pmf.c', path)
            for relative in ('mt_wifi/embedded/ap/ap_sec.c', 'mt_wifi/embedded/ap/ap_cfg.c'):
                path = compiled / relative
                path.write_bytes(path.read_bytes() + b'\n')
                with self.assertRaisesRegex(ValueError, 'compiler source'):
                    mod.capture(build, root / (Path(relative).stem + '-drift.json'))
                shutil.copyfile(root / relative, path)
            archive.write_bytes(b'bad source archive')
            with self.assertRaisesRegex(ValueError, 'archive'):
                mod.capture(build, root / 'archive-drift.json')


def test_module(name, mod):
    data = bytearray(64)
    data[:7] = b'\x7fELF\x02\x01\x01'
    struct.pack_into('<HHI', data, 16, 1, 183, 1)
    dependencies = 'conninfra,mtkhnat' if name == 'mt_wifi' else 'mtk_warp,mt_wifi'
    data.extend(('\0vermagic=' + mod.KERNEL_VERSION + ' SMP mod_unload aarch64\0name=' + name + '\0depends=' + dependencies + '\0').encode())
    if name == 'mt_wifi':
        data.extend(b'PMF NR_UCAST global suppressed=%u%s SubType=%u WCID=%u\n\0')
    return bytes(data)


if __name__ == '__main__':
    unittest.main()
