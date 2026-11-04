"""The S20P kernel CVE backport must be pinned, applied, and captured."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1]
DATA = CI / "security/kernel-cve-2026-98432"
sys.path.insert(0, str(CI))


class KernelSecurityTests(unittest.TestCase):
    def module(self):
        path = CI / "kernel_security.py"
        self.assertTrue(path.is_file(), "kernel CVE profile needs a source and artifact gate")
        spec = importlib.util.spec_from_file_location("kernel_security", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def source_files(self):
        return {
            "net/ipv4/af_inet.c": (
                "\n" * 153 +
                "\tWARN_ON_ONCE(sk->sk_wmem_queued);\n"
                "\tWARN_ON_ONCE(sk_forward_alloc_get(sk));\n\n"
                "\tkfree(rcu_dereference_protected(inet->inet_opt, 1));\n"
                "\tdst_release(rcu_dereference_protected(sk->sk_dst_cache, 1));\n"
                "\tdst_release(rcu_dereference_protected(sk->sk_rx_dst, 1));\n}\n"
            ),
            "net/ipv4/tcp_ipv4.c": (
                "\n" * 1216 +
                " */\nstatic void tcp_v4_reqsk_destructor(struct request_sock *req)\n{\n"
                "\tkfree(rcu_dereference_protected(inet_rsk(req)->ireq_opt, 1));\n}\n\n"
                "#ifdef CONFIG_TCP_MD5SIG\n"
            ),
        }

    def write_source_tree(self, root, *, patched):
        for relative, contents in self.source_files().items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        if patched:
            subprocess.run(["git", "apply", str(DATA / self.module().PATCH_NAME)],
                           cwd=root, check=True, capture_output=True, text=True)

    def test_profile_patch_applies_both_ipv4_rcu_free_fixes(self):
        mod = self.module()
        profile = mod.profile()
        self.assertEqual(profile["id"], "s20p-kernel-cve-2026-98432-20261104")
        self.assertEqual(profile["kernel_version"], "6.12.112")
        self.assertEqual(profile["upstream_patch_commit"],
                         "11a03ce3c4a62d84c10ccdfe47f8075006498568")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.write_source_tree(root, patched=False)
            subprocess.run(["git", "apply", "--check", str(DATA / mod.PATCH_NAME)],
                           cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "apply", str(DATA / mod.PATCH_NAME)],
                           cwd=root, check=True, capture_output=True, text=True)
            af_inet = (root / "net/ipv4/af_inet.c").read_text()
            tcp_ipv4 = (root / "net/ipv4/tcp_ipv4.c").read_text()
            self.assertIn("kfree_rcu(rcu_dereference_protected(inet->inet_opt, 1), rcu);", af_inet)
            self.assertIn("kfree_rcu(rcu_dereference_protected(inet_rsk(req)->ireq_opt, 1), rcu);",
                          tcp_ipv4)

    def source_checkout(self, root, kernel_patchver="6.12", kernel_version=".112"):
        patch_dir = root / "target/linux/generic/pending-6.12"
        patch_dir.mkdir(parents=True)
        target = root / "target/linux/mediatek/Makefile"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("KERNEL_PATCHVER:=" + kernel_patchver + "\n")
        version = root / "target/linux/generic/kernel-6.12"
        version.parent.mkdir(parents=True, exist_ok=True)
        version.write_text("LINUX_VERSION-6.12 = " + kernel_version + "\n"
                           "LINUX_KERNEL_HASH-6.12.112 = "
                           "164dc9d1f6c93c61a15e1f071c48379b467f2b17c469cce7223471968208ed03\n")
        return patch_dir

    def test_prepare_stages_exact_patch_and_refuses_reapplication(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.source_checkout(root)
            output = root / "provenance/kernel-security-preparation.json"
            with patch.object(mod, "checkout_commit", return_value=mod.SOURCE_COMMIT):
                report = mod.prepare(root, output)
                self.assertEqual(json.loads(output.read_text()), report)
                self.assertEqual((root / mod.PATCH_DESTINATION).read_bytes(),
                                 (DATA / mod.PATCH_NAME).read_bytes())
                before = {str(path.relative_to(root)): path.read_bytes()
                          for path in root.rglob("*") if path.is_file()}
                with self.assertRaisesRegex(ValueError, "existing|already|patch"):
                    mod.prepare(root, root / "provenance/second.json")
                self.assertEqual(before, {str(path.relative_to(root)): path.read_bytes()
                                          for path in root.rglob("*") if path.is_file()})

    def test_prepare_rejects_kernel_series_drift_before_writing(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.source_checkout(root, "6.13")
            output = root / "provenance/kernel-security-preparation.json"
            with patch.object(mod, "checkout_commit", return_value=mod.SOURCE_COMMIT):
                with self.assertRaisesRegex(ValueError, "kernel|6.12|series"):
                    mod.prepare(root, output)
            self.assertFalse((root / mod.PATCH_DESTINATION).exists())
            self.assertFalse(output.exists())

    def test_prepare_rejects_kernel_version_drift_before_writing(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.source_checkout(root, kernel_version=".111")
            output = root / "provenance/kernel-security-preparation.json"
            with patch.object(mod, "checkout_commit", return_value=mod.SOURCE_COMMIT):
                with self.assertRaisesRegex(ValueError, "kernel|6.12|version"):
                    mod.prepare(root, output)
            self.assertFalse((root / mod.PATCH_DESTINATION).exists())
            self.assertFalse(output.exists())

    def test_capture_requires_patch_in_the_compiled_kernel_source(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            kernel = root / "build_dir/target-a/linux-mediatek_filogic/linux-6.12.112"
            self.write_source_tree(kernel, patched=False)
            (kernel / ".config").write_text("CONFIG_INET=y\n")
            (kernel / "Module.symvers").write_text("symvers\n")
            with self.assertRaisesRegex(ValueError, "patch|applied|source"):
                mod.inspect_kernel_source(root)
            subprocess.run(["git", "apply", str(DATA / mod.PATCH_NAME)], cwd=kernel, check=True)
            result, _ = mod.inspect_kernel_source(root)
            self.assertEqual(result["kernel_version"], "6.12.112")
            self.assertEqual(len(result["source_files"]), 2)
            self.assertTrue(result["patch_verified"])

    def evidence(self, root):
        mod = self.module()
        wrapper_commit = "a" * 40
        kernel_version = "6.12.112~0123456789abcdef0123456789abcdef-r1"
        source = root / "source"
        self.source_checkout(source)
        output = root / "artifact"
        provenance = output / "provenance"
        from test_component_gate import ComponentGateTests
        ComponentGateTests().fixture(provenance)
        with patch.object(mod, "checkout_commit", return_value="9f397975c7b47ce6986a6e39529d2f72fd5c729d"):
            mod.prepare(source, provenance / mod.PREPARATION_NAME)
        prepared = [mod.file_row(mod.PATCH_DESTINATION, mod.patch_bytes())]
        wrapper = mod.wrapper_rows()
        (provenance / "prepared-source-files.json").write_text(json.dumps(prepared))
        (provenance / "wrapper-source-files.json").write_text(json.dumps(wrapper))
        lock_path = provenance / "source-lock.json"
        lock = json.loads(lock_path.read_text())
        lock.update(wrapper_commit="d46704afb327fce99e7d770c08fc46b48d5bb1b7",
                    wrapper_checkout_commit=wrapper_commit)
        lock['prepared_tree_hash'] = mod.digest(json.dumps(prepared, sort_keys=True).encode())
        lock['wrapper_tree_hash'] = mod.digest(json.dumps(wrapper, sort_keys=True).encode())
        lock['build_fingerprint'] = mod.digest(json.dumps(
            {'prepared': prepared, 'wrapper': wrapper, 'lock': lock['source_lock']}, sort_keys=True).encode())
        lock_path.write_text(json.dumps(lock))

        build = root / "build"
        kernel = build / "build_dir/target-test/linux-mediatek_filogic/linux-6.12.112"
        self.write_source_tree(kernel, patched=True)
        (kernel / ".config").write_text("CONFIG_INET=y\n")
        (kernel / "Module.symvers").write_text("symvers\n")
        kernel_config = output / "metadata/kernel/.config"
        kernel_config.parent.mkdir(parents=True, exist_ok=True)
        kernel_config.write_text((kernel / ".config").read_text())
        (output / "metadata/kernel/Module.symvers").write_text("symvers\n")
        fit = output / "inspection/kernel.fit"
        fit.parent.mkdir(parents=True, exist_ok=True)
        fit.write_bytes(b"kernel fit")
        (output / "inspection/installed-packages.json").write_text(json.dumps({
            "packages": [{"name": "kernel", "version": kernel_version}]
        }))
        (output / "reports").mkdir(parents=True, exist_ok=True)
        (output / "reports/capture.json").write_text(json.dumps({"kernel_abi": kernel_version}))
        return mod, build, output, wrapper_commit

    def test_capture_cannot_silently_skip_patch_check_inside_the_build_git_checkout(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            kernel = root / 'build_dir/target-test/linux-mediatek_filogic/linux-6.12.112'
            self.write_source_tree(kernel, patched=False)
            (kernel / '.config').write_text('CONFIG_INET=y\n')
            (kernel / 'Module.symvers').write_text('symvers\n')
            for relative, expression in mod.FIXED_EXPRESSIONS.items():
                path = kernel / relative
                path.write_bytes(path.read_bytes() + b'/* ' + expression + b' */\n')
            with self.assertRaisesRegex(ValueError, 'patch|applied|source'):
                mod.inspect_kernel_source(root)

    def test_capture_binds_patched_sources_to_kernel_metadata_and_candidate(self):
        with tempfile.TemporaryDirectory() as name:
            mod, build, output, wrapper_commit = self.evidence(Path(name))
            report = mod.capture(build, output)
            self.assertTrue(report["patch_verified_in_compiled_sources"])
            self.assertEqual(mod.verify_artifact(output, wrapper_commit, mod.SOURCE_COMMIT), report)

    def test_unknown_wrapper_cannot_disable_gate_by_removing_every_marker(self):
        with tempfile.TemporaryDirectory() as name:
            mod, _, output, wrapper_commit = self.evidence(Path(name))
            provenance = output / 'provenance'
            (provenance / mod.PREPARATION_NAME).unlink()
            (provenance / 'wrapper-source-files.json').write_text('[]')
            with self.assertRaises((OSError, ValueError)):
                mod.verify_artifact(output, wrapper_commit, mod.SOURCE_COMMIT)

    def test_rejects_missing_mutated_or_rebound_source_and_kernel_evidence(self):
        changes = ('preparation', 'build-report', 'prepared-hash', 'wrapper-hash',
                   'source', 'source-rehashed', 'kernel-config', 'symvers', 'fit',
                   'kernel-abi', 'different-build', 'different-upstream')
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                mod, build, output, wrapper_commit = self.evidence(Path(name))
                mod.capture(build, output)
                provenance = output / 'provenance'
                if change in ('preparation', 'build-report'):
                    (provenance / (mod.PREPARATION_NAME if change == 'preparation' else mod.BUILD_NAME)).unlink()
                elif change in ('prepared-hash', 'wrapper-hash'):
                    path = provenance / ('prepared-source-files.json' if change == 'prepared-hash' else 'wrapper-source-files.json')
                    rows = json.loads(path.read_text()); rows[0]['sha256'] = 'f' * 64
                    path.write_text(json.dumps(rows))
                elif change.startswith('source'):
                    path = provenance / 'kernel-source-after/net/ipv4/af_inet.c'
                    data = path.read_bytes().replace(b'kfree_rcu(', b'kfree(')
                    path.write_bytes(data)
                    if change == 'source-rehashed':
                        report_path = provenance / mod.BUILD_NAME
                        report = json.loads(report_path.read_text())
                        report['source_files'][0].update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
                        report_path.write_text(json.dumps(report))
                elif change in ('kernel-config', 'symvers', 'fit'):
                    path = output / {'kernel-config':'metadata/kernel/.config', 'symvers':'metadata/kernel/Module.symvers',
                                     'fit':'inspection/kernel.fit'}[change]
                    path.write_bytes(path.read_bytes() + b'changed')
                elif change == 'kernel-abi':
                    path = output / 'reports/capture.json'
                    path.write_text(json.dumps({'kernel_abi': '6.12.111~wrong-r1'}))
                elif change == 'different-build':
                    wrapper_commit = 'b' * 40
                with self.assertRaises((OSError, ValueError)):
                    mod.verify_artifact(output, wrapper_commit, 'b' * 40 if change == 'different-upstream' else mod.SOURCE_COMMIT)

    def test_only_frozen_historical_pipeline_can_replay_without_claiming_fix(self):
        mod = self.module()
        historical = json.loads((DATA / 'historical-wrappers.json').read_text())
        for commit, rows in historical.items():
            with self.subTest(commit=commit), tempfile.TemporaryDirectory() as name:
                output = Path(name)
                provenance = output / 'provenance'; provenance.mkdir()
                (provenance / 'wrapper-source-files.json').write_text(json.dumps(rows))
                lock = {'wrapper_checkout_commit':commit,
                        'wrapper_tree_hash':mod.digest(json.dumps(rows, sort_keys=True).encode())}
                (provenance / 'source-lock.json').write_text(json.dumps(lock))
                self.assertIsNone(mod.verify_artifact(output, commit, mod.SOURCE_COMMIT))
                (provenance / mod.PREPARATION_NAME).write_text('{}')
                with self.assertRaisesRegex(ValueError, 'historical'):
                    mod.verify_artifact(output, commit, mod.SOURCE_COMMIT)
                (provenance / mod.PREPARATION_NAME).unlink()
                rows[0]['sha256'] = 'a' * 64
                (provenance / 'wrapper-source-files.json').write_text(json.dumps(rows))
                lock['wrapper_tree_hash'] = mod.digest(json.dumps(rows, sort_keys=True).encode())
                (provenance / 'source-lock.json').write_text(json.dumps(lock))
                with self.assertRaisesRegex(ValueError, 'historical'):
                    mod.verify_artifact(output, commit, mod.SOURCE_COMMIT)

    def test_preservation_rechecks_the_kernel_report_and_rejects_missing_check(self):
        from preservation import Gate
        with tempfile.TemporaryDirectory() as name:
            mod, build, output, _ = self.evidence(Path(name))
            report = mod.capture(build, output)
            path = output / 'reports/kernel-security.json'
            path.write_text(json.dumps(report))
            gate = Gate('image', CI / 'baseline')
            lock_path = output / 'provenance/source-lock.json'
            gate._source_evidence = (lock_path, json.loads(lock_path.read_text()))
            gate.kernel_security(True)
            self.assertEqual(gate.errors, [])
            self.assertEqual(gate.checks['kernel_security']['cve'], 'CVE-2026-98432')
            path.unlink()
            gate.kernel_security(True)
            self.assertTrue(any(row['category'] == 'kernel_security_guard' for row in gate.errors))
            self.assertNotIn('kernel_security', gate.checks)


if __name__ == "__main__":
    unittest.main()
