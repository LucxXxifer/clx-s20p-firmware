"""Fail-closed preparation of rolling A direct-download release assets."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile


CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
import upstream_sync
import go_security
import dashboard
import fixed_inputs
import minimal_security
SPEC = importlib.util.spec_from_file_location("release_candidate", CI / "release_candidate.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


TEST_WRAPPER_COMMIT = "d" * 40
TEST_KERNEL_ABI = "6.12.112~" + "b" * 32 + "-r1"
TEST_PUBLIC_KEY = b"-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----\n"
IMAGE_PATH = "bin/targets/mediatek/filogic/immortalwrt-mediatek-filogic-clx_s20p-squashfs-sysupgrade.bin"


class ReleaseCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact = self.root / "candidate"
        self.artifact.mkdir()
        self.output = self.root / "assets"

    def prepare(self, **options):
        upstream_commit = options.pop("upstream_commit", release.UPSTREAM_BASE_COMMIT)
        return release.prepare_release_assets(
            self.artifact, self.output, run_id="1234", run_attempt="2",
            build_commit=TEST_WRAPPER_COMMIT, upstream_commit=upstream_commit, **options,
        )

    def test_prepares_a_direct_bin_archive_checksums_and_install_guide(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        result = self.prepare()
        image = self.output / "s20p-A-25.12-9f397975-sysupgrade.bin"
        self.assertEqual(result["image"], image.name)
        self.assertEqual(image.read_bytes(), test_sysupgrade_image())
        self.assertEqual(result["archive"], archive.name)
        self.assertTrue((self.output / "SHA256SUMS").is_file())
        self.assertTrue((self.output / "archive.SHA256SUMS").is_file())
        guide = (self.output / "INSTALLATION.md").read_text()
        self.assertIn("1234", guide)
        self.assertIn(TEST_WRAPPER_COMMIT, guide)
        self.assertIn("not a stable release", guide.lower())
        self.assertIn("rolling prerelease candidate", guide.lower())
        self.assertTrue(release.verify_release_assets(
            self.output, run_id="1234", run_attempt="2", build_commit=TEST_WRAPPER_COMMIT,
            upstream_commit=release.UPSTREAM_BASE_COMMIT,
        )["pass"])

    def test_fixed_release_requires_exact_captured_manifest_inputs_and_http_coverage(self):
        from test_fixed_inputs import fixture
        frozen = self.root / 'frozen'
        frozen.mkdir()
        manifest_path, manifest = fixture(frozen)
        provenance = self.root / 'bundle/provenance'
        provenance.mkdir(parents=True)
        shutil.copytree(frozen, provenance / 'fixed')
        shutil.copyfile(frozen / 'components.json', provenance / 'component-resolution.json')
        (provenance / 'http-inputs.jsonl').write_text(json.dumps({
            'urls': [manifest['http_inputs'][0]['url']], 'exit_code': 0,
            'stdout_sha256': manifest['http_inputs'][0]['sha256']}) + '\n')
        with patch.dict(os.environ, {'S20P_FIXED_INPUTS': str(manifest_path)}):
            result = release.verify_fixed_bundle(provenance.parent, manifest['upstream_commit'])
            self.assertEqual(result['source_run'], manifest['source_run'])
            (provenance / 'fixed/payload').write_bytes(b'wrong')
            with self.assertRaises(ValueError):
                release.verify_fixed_bundle(provenance.parent, manifest['upstream_commit'])
            shutil.rmtree(provenance / 'fixed')
            with self.assertRaises(ValueError):
                release.verify_fixed_bundle(provenance.parent, manifest['upstream_commit'])

    def test_fixed_release_metadata_and_installation_guide_remain_explicit(self):
        from test_fixed_inputs import fixture
        frozen = self.root / 'frozen'
        frozen.mkdir()
        path, manifest = fixture(frozen)
        extras = {'provenance/fixed/' + p.relative_to(frozen).as_posix(): p.read_bytes()
                  for p in frozen.rglob('*') if p.is_file()}
        extras['provenance/component-resolution.json'] = (frozen / 'components.json').read_bytes()
        extras['provenance/http-inputs.jsonl'] = json_bytes({
            'urls': [manifest['http_inputs'][0]['url']], 'exit_code': 0,
            'stdout_sha256': manifest['http_inputs'][0]['sha256']})
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, extra_files=extras)
        with patch.dict(os.environ, {'S20P_FIXED_INPUTS': str(path)}):
            result = self.prepare()
            guide = (self.output / 'INSTALLATION.md').read_text()
            self.assertIn('fixed-source prerelease candidate', guide)
            self.assertIn('not a stable release', guide)
            self.assertIn(manifest['source_run'], guide)
            self.assertEqual(result['verified']['fixed_inputs']['source_run'], manifest['source_run'])

    def test_committed_fixed_snapshots_are_selected_by_exact_captured_bytes(self):
        paths = []
        bundles = []
        for policy_id in ("legacy", "minimal-20261031"):
            frozen = self.root / policy_id
            frozen.mkdir()
            path, manifest, extras = fixed_release_fixture(frozen, policy_id)
            paths.append(path)
            bundle = self.root / (policy_id + "-bundle")
            for name, data in extras.items():
                target = bundle / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            bundles.append((bundle, manifest))
        with patch.dict(os.environ, {}, clear=True), patch.object(release, "FIXED_MANIFESTS", tuple(paths)):
            for bundle, manifest in bundles:
                result = release.verify_fixed_bundle(bundle, manifest["upstream_commit"])
                self.assertEqual(result["manifest_sha256"], release.sha256_file(
                    bundle / "provenance/fixed/manifest.json"))
                if manifest["schema_version"] == 3:
                    self.assertEqual(result["profile"], manifest["profile"])
                else:
                    self.assertNotIn("profile", result)
            captured = bundles[1][0] / "provenance/fixed/manifest.json"
            value = json.loads(captured.read_text())
            value["source_run"] = "1234"
            captured.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "reviewed snapshot"):
                release.verify_fixed_bundle(bundles[1][0], value["upstream_commit"])

    def test_artifact_application_evidence_cannot_self_select_minimal_profile(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT,
                        application_policy="minimal-20261031")
        with self.assertRaisesRegex(ValueError, "reviewed application selection"):
            self.prepare()

    def test_trusted_minimal_profile_rejects_legacy_application_evidence(self):
        frozen = self.root / "frozen"
        frozen.mkdir()
        path, _, extras = fixed_release_fixture(frozen, "minimal-20261031")
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, extra_files=extras)
        with patch.dict(os.environ, {"S20P_FIXED_INPUTS": str(path)}):
            with self.assertRaisesRegex(ValueError, "reviewed application selection"):
                self.prepare()

    def test_trusted_minimal_profile_rejects_retained_easytier(self):
        frozen = self.root / "frozen"
        frozen.mkdir()
        path, _, extras = fixed_release_fixture(frozen, "minimal-20261031")
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, extra_files=extras,
                                  application_policy="minimal-20261031")
        rewrite_archive_json(archive, "inspection/installed-packages.json",
                             lambda data: data["packages"][1].update(name="easytier", version="2.7.0"))
        with patch.dict(os.environ, {"S20P_FIXED_INPUTS": str(path)}):
            with self.assertRaisesRegex(ValueError, "omitted application package remains"):
                self.prepare()

    def test_trusted_minimal_profile_requires_independent_security_report(self):
        frozen = self.root / "frozen"
        frozen.mkdir()
        path, _, extras = fixed_release_fixture(frozen, "minimal-20261031")
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, extra_files=extras,
                        application_policy="minimal-20261031")
        with patch.dict(os.environ, {"S20P_FIXED_INPUTS": str(path)}):
            with self.assertRaisesRegex(ValueError, "minimal-security"):
                self.prepare()

    def test_minimal_release_independently_verifies_security_report_and_profile(self):
        path, _, _ = self.minimal_fixture()
        with patch.dict(os.environ, {"S20P_FIXED_INPUTS": str(path)}), \
                patch.object(minimal_security, "bound_sources", return_value=(
                    None, minimal_security.preparation_report()["prepared_files"])):
            result = self.prepare()
        self.assertEqual(result["verified"]["fixed_inputs"]["profile"],
                         fixed_inputs.profile_descriptor("minimal-20261031"))

    def test_minimal_release_rejects_self_consistent_report_tampering(self):
        path, archive, _ = self.minimal_fixture()
        rewrite_archive_json(archive, "reports/minimal-security.json",
                             lambda report: report.update(rootfs_sha256="a" * 64))
        rewrite_archive_json(archive, "reports/preservation-image.json",
                             lambda report: report["checks"]["minimal_security"].update(
                                 rootfs_sha256="a" * 64))
        with patch.dict(os.environ, {"S20P_FIXED_INPUTS": str(path)}), \
                patch.object(minimal_security, "bound_sources", return_value=(
                    None, minimal_security.preparation_report()["prepared_files"])):
            with self.assertRaisesRegex(ValueError, "minimal security release report/source/payload mismatch"):
                self.prepare()

    def test_minimal_release_requires_same_report_in_preservation_evidence(self):
        path, archive, _ = self.minimal_fixture()
        rewrite_archive_json(archive, "reports/preservation-image.json",
                             lambda report: report["checks"]["minimal_security"].update(runtime_tested=True))
        with patch.dict(os.environ, {"S20P_FIXED_INPUTS": str(path)}), \
                patch.object(minimal_security, "bound_sources", return_value=(
                    None, minimal_security.preparation_report()["prepared_files"])):
            with self.assertRaisesRegex(ValueError, "differs from preservation evidence"):
                self.prepare()

    def test_minimal_release_rechecks_wrapper_patch_evidence(self):
        path, archive, _ = self.minimal_fixture()
        rewrite_archive_json(archive, "provenance/wrapper-source-files.json",
                             lambda rows: rows[0].update(sha256="a" * 64))
        with patch.dict(os.environ, {"S20P_FIXED_INPUTS": str(path)}), \
                patch.object(minimal_security, "bound_sources", return_value=(
                    None, minimal_security.preparation_report()["prepared_files"])):
            with self.assertRaisesRegex(ValueError, "wrapper/profile source mismatch"):
                self.prepare()

    def test_new_release_uses_same_build_date_in_name_and_metadata(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT,
                        extra_files={"provenance/build-date.txt": b"20261031\n"})
        result = self.prepare()
        self.assertEqual(result["image"], "clx-s20p-9f397975-20261031-sysupgrade.bin")
        self.assertEqual(result["verified"]["build_date"], "20261031")

    def test_release_rejects_invalid_or_noncanonical_calendar_date(self):
        for raw in (b"20260230\n", b"20261301\n", b"2026103\n", b" 20261031\n",
                    b"20261031\n\n", b"20261031\r\n", b"20261031suffix"):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as name:
                self.artifact = Path(name)
                self.output = self.artifact / "assets"
                release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT,
                                extra_files={"provenance/build-date.txt": raw})
                with self.assertRaisesRegex(ValueError, "build date"):
                    self.prepare()

    def test_build_date_requires_regular_file_and_accepts_real_leap_day(self):
        provenance = self.root / "provenance"
        provenance.mkdir()
        path = provenance / "build-date.txt"
        path.write_bytes(b"20280229")
        self.assertEqual(release.verify_build_date(self.root), "20280229")
        real = self.root / "real-date"
        path.rename(real)
        path.symlink_to(real)
        with self.assertRaisesRegex(ValueError, "regular canonical build date"):
            release.verify_build_date(self.root)

    def test_minimal_profile_requires_build_date_even_with_valid_security_report(self):
        path, archive, _ = self.minimal_fixture()
        # Rebuild an equivalent minimal archive with no date evidence.
        rewrite_archive_entry(archive, "provenance/build-date.txt", None)
        with patch.dict(os.environ, {"S20P_FIXED_INPUTS": str(path)}):
            with self.assertRaisesRegex(ValueError, "build date"):
                self.prepare()

    def test_release_rechecks_date_against_archive_after_asset_renaming(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT,
                        extra_files={"provenance/build-date.txt": b"20261031\n"})
        self.prepare()
        original = self.output / "clx-s20p-9f397975-20261031-sysupgrade.bin"
        original.rename(self.output / "clx-s20p-9f397975-20261101-sysupgrade.bin")
        write_checksums(self.output, exclude={"SHA256SUMS"})
        with self.assertRaisesRegex(ValueError, "release asset set"):
            release.verify_release_assets(self.output, run_id="1234", run_attempt="2",
                build_commit=TEST_WRAPPER_COMMIT, upstream_commit=release.UPSTREAM_BASE_COMMIT)

    def test_release_rejects_date_metadata_tampering(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT,
                        extra_files={"provenance/build-date.txt": b"20261031\n"})
        self.prepare()
        metadata = self.output / "BUILD-VERIFICATION.json"
        value = json.loads(metadata.read_text())
        value["build_date"] = "20261101"
        metadata.write_bytes(json_bytes(value))
        write_checksums(self.output, exclude={"SHA256SUMS"})
        with self.assertRaisesRegex(ValueError, "verification record"):
            release.verify_release_assets(self.output, run_id="1234", run_attempt="2",
                build_commit=TEST_WRAPPER_COMMIT, upstream_commit=release.UPSTREAM_BASE_COMMIT)

    def test_release_rejects_metadata_image_name_tampering(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT,
                        extra_files={"provenance/build-date.txt": b"20261031\n"})
        self.prepare()
        metadata = self.output / "BUILD-VERIFICATION.json"
        value = json.loads(metadata.read_text())
        value["image"] = "clx-s20p-9f397975-20261101-sysupgrade.bin"
        metadata.write_bytes(json_bytes(value))
        write_checksums(self.output, exclude={"SHA256SUMS"})
        with self.assertRaisesRegex(ValueError, "verification record"):
            release.verify_release_assets(self.output, run_id="1234", run_attempt="2",
                build_commit=TEST_WRAPPER_COMMIT, upstream_commit=release.UPSTREAM_BASE_COMMIT)

    def minimal_fixture(self):
        frozen = self.root / "frozen"
        frozen.mkdir()
        path, _, extras = fixed_release_fixture(frozen, "minimal-20261031")
        prepared = minimal_security.preparation_report()
        security_inputs = {
            "minimal-security-preparation.json": prepared,
            "prepared-source-files.json": prepared["prepared_files"],
            "wrapper-source-files.json": minimal_security.wrapper_rows()[0],
        }
        packages = [{"name": name, "version": version}
                    for name, version in minimal_security.EXPECTED_PACKAGES.items()]
        with tempfile.TemporaryDirectory() as name:
            provenance = Path(name)
            for filename, value in security_inputs.items():
                (provenance / filename).write_bytes(json_bytes(value))
            with patch.object(minimal_security, "bound_sources", return_value=(
                    None, prepared["prepared_files"])):
                report = minimal_security.expected_report(
                    hashlib.sha256(TEST_ROOTFS_PAYLOAD).hexdigest(), provenance,
                    {row["name"]: row for row in packages})
        extras.update({"provenance/" + filename: json_bytes(value)
                       for filename, value in security_inputs.items()})
        extras["reports/minimal-security.json"] = json_bytes(report)
        extras["provenance/build-date.txt"] = b"20261031\n"
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, extra_files=extras,
                                  application_policy="minimal-20261031", extra_packages=packages)
        rewrite_archive_json(archive, "reports/preservation-image.json",
                             lambda data: data["checks"].update(minimal_security=report))
        return path, archive, report

    def test_rejects_a_bundle_from_another_wrapper_commit(self):
        release_fixture(self.artifact, commit="a" * 40)
        with self.assertRaisesRegex(ValueError, "wrapper commit"):
            self.prepare()

    def test_rejects_unpatched_go_evidence_even_with_recomputed_archive_checksums(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        rewrite_archive_json(archive, "reports/go-x509-security.json",
                             lambda data: data["binaries"][0].update(go_version="go1.26.9"))
        with self.assertRaisesRegex(ValueError, "unpatched"):
            self.prepare()

    def test_rejects_go_report_for_another_rootfs(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        rewrite_archive_json(archive, "reports/go-x509-security.json",
                             lambda data: data.update(rootfs_sha256="0" * 64))
        with self.assertRaisesRegex(ValueError, "Go security report"):
            self.prepare()

    def test_legacy_quickstart_is_explicit_in_release_metadata_and_guide(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        rewrite_archive_json(archive, "reports/go-x509-security.json", add_legacy_quickstart)
        result = self.prepare()
        security = result["verified"]["go_security"]
        self.assertEqual(security.get("status"), "pass_with_known_exception")
        self.assertEqual(security.get("known_exceptions"), go_security.POLICY["legacy_prebuilt_exceptions"])
        guide = (self.output / "INSTALLATION.md").read_text()
        self.assertIn("QuickStart remains unpatched for GO-2026-6809", guide)
        self.assertIn("go1.25.0", guide)
        self.assertIn(go_security.POLICY["legacy_prebuilt_exceptions"][0]["sha256"], guide)

    def test_release_rejects_hidden_legacy_warning_even_with_new_checksums(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        rewrite_archive_json(archive, "reports/go-x509-security.json", add_legacy_quickstart)
        self.prepare()
        guide = self.output / "INSTALLATION.md"
        guide.write_text(guide.read_text().replace("QuickStart remains unpatched for GO-2026-6809", "All binaries patched"))
        (self.output / "SHA256SUMS").write_text(release.checksum_lines(self.output, exclude={"SHA256SUMS"}))
        with self.assertRaisesRegex(ValueError, "exception"):
            release.verify_release_assets(self.output, run_id="1234", run_attempt="2",
                build_commit=TEST_WRAPPER_COMMIT, upstream_commit=release.UPSTREAM_BASE_COMMIT)

    def test_rejects_a_bundle_with_invalid_internal_checksum(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, tamper_checksum=True)
        with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
            self.prepare()

    def test_rejects_a_bundle_with_wrong_upstream_commit(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, upstream_commit="a" * 40)
        with self.assertRaisesRegex(ValueError, "upstream resolution"):
            self.prepare()

    def test_prepares_rolling_a_assets_for_the_exact_resolved_upstream_sha(self):
        head = "a" * 40
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, upstream_commit=head)
        result = self.prepare(upstream_commit=head)
        image_name = "s20p-A-25.12-aaaaaaaa-sysupgrade.bin"
        self.assertEqual(result["image"], image_name)
        self.assertTrue((self.output / image_name).is_file())
        metadata = release.read_json(self.output, "BUILD-VERIFICATION.json")
        self.assertEqual(metadata["upstream_commit"], head)
        guide = (self.output / "INSTALLATION.md").read_text()
        self.assertIn(head, guide)
        self.assertIn("rolling prerelease candidate", guide.lower())

    def test_rejects_resolution_evidence_for_another_upstream_sha(self):
        head = "a" * 40
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, upstream_commit=head)
        rewrite_archive_json(archive, "provenance/upstream-resolution.json",
                             lambda data: data.update(head_commit="b" * 40))
        with self.assertRaisesRegex(ValueError, "upstream resolution"):
            self.prepare(upstream_commit=head)

    def test_rejects_candidate_claim_for_an_already_attempted_head_without_force(self):
        head = "a" * 40
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, upstream_commit=head)
        rewrite_archive_json(archive, "provenance/upstream-resolution.json",
                             lambda data: data.update(previous_attempted=True))
        with self.assertRaisesRegex(ValueError, "upstream resolution"):
            self.prepare(upstream_commit=head)

    def test_rejects_a_regressed_previous_head_comparison(self):
        head = "a" * 40
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, upstream_commit=head)
        rewrite_archive_json(archive, "provenance/upstream-resolution.json",
                             lambda data: data.update(previous_comparison={
                                 "base_commit": "b" * 40, "compare_status": "behind", "merge_base": head,
                             }))
        with self.assertRaisesRegex(ValueError, "previous"):
            self.prepare(upstream_commit=head)

    def test_rejects_a_bundle_with_forged_resolved_main_source(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        rewrite_archive_json(archive, "provenance/source-lock.json", lambda data: data["sources"][0].update(commit="a" * 40))
        with self.assertRaisesRegex(ValueError, "resolved main-source"):
            self.prepare()

    def test_saved_requested_lock_must_equal_embedded_provenance(self):
        head = "a" * 40
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, upstream_commit=head)
        request = json.loads((CI / "sources.lock.json").read_text())
        next(row for row in request["sources"] if row.get("role") == "main-source")["commit"] = "b" * 40
        rewrite_archive_json(archive, "provenance/requested-sources.lock.json",
                             lambda data: (data.clear(), data.update(request)))
        rewrite_archive_json(archive, "provenance/source-lock.json",
                             lambda data: data.update(source_lock_sha256=hashlib.sha256(json_bytes(request)).hexdigest()))
        with self.assertRaisesRegex(ValueError, "saved requested source lock"):
            self.prepare(upstream_commit=head)

    def test_release_rejects_coherent_drift_of_other_git_source_pins(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        request = json.loads((CI / "sources.lock.json").read_text())
        request["sources"][0]["commit"] = "f" * 40
        rewrite_archive_json(archive, "provenance/requested-sources.lock.json",
                             lambda data: (data.clear(), data.update(request)))
        rewrite_archive_json(archive, "provenance/source-lock.json", lambda data: data.update(
            source_lock=request, source_lock_sha256=hashlib.sha256(json_bytes(request)).hexdigest()))
        with self.assertRaisesRegex(ValueError, "reviewed source pins"):
            self.prepare()

    def test_rejects_a_bundle_with_failed_preservation(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, preservation_status="review_required")
        with self.assertRaisesRegex(ValueError, "preservation"):
            self.prepare()

    def test_rejects_a_bundle_with_failed_signed_feed_or_key_binding(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, signed_feed_status="fail")
        with self.assertRaisesRegex(ValueError, "signed feed"):
            self.prepare()

    def test_rejects_a_public_key_that_does_not_match_signed_feed_evidence(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, wrong_public_key_hash=True)
        with self.assertRaisesRegex(ValueError, "public key does not match"):
            self.prepare()

    def test_rejects_multiple_s20p_sysupgrade_images(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, extra_image=True)
        with self.assertRaisesRegex(ValueError, "exactly one .*S20P sysupgrade image"):
            self.prepare()

    def test_rejects_rom_partition_payload_drift_even_with_valid_outer_checksums(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, payload_mismatch=True)
        with self.assertRaisesRegex(ValueError, "payload differs from preserved inspection payload"):
            self.prepare()

    def test_rejects_a_mismatched_installed_package_count_or_kernel_abi(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, package_count_delta=1)
        with self.assertRaisesRegex(ValueError, "package inventory"):
            self.prepare()

    def test_rejects_missing_lucky_omission_or_other_package_drift(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, include_lucky=True)
        with self.assertRaisesRegex(ValueError, "Lucky package omission"):
            self.prepare()

    def test_rejects_retained_tingreader_with_recomputed_checksums(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        rewrite_archive_json(archive, "inspection/installed-packages.json",
                             lambda data: data['packages'][1].update(
                                 name='luci-app-tingreader', version='2.0.6-r1'))
        with self.assertRaisesRegex(ValueError, 'omitted application package remains'):
            self.prepare()

    def test_rejects_excluded_adguard_with_recomputed_checksums(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        rewrite_archive_json(archive, 'inspection/installed-packages.json',
                             lambda data: data['packages'][1].update(name='adguardhome'))
        with self.assertRaisesRegex(ValueError, 'excluded application'):
            self.prepare()

    def test_rejects_missing_or_tampered_application_selection_evidence(self):
        for path in ('provenance/config-gate.json', 'reports/preservation-image.json'):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as name:
                self.artifact = Path(name)
                self.output = Path(name) / 'release-assets'
                archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
                rewrite_archive_json(archive, path,
                                     lambda data: data['checks']['application_selection'].update(
                                         removed_path_count=0))
                with self.assertRaisesRegex(ValueError, 'reviewed application selection'):
                    self.prepare()

    def test_rejects_tar_path_traversal_and_symlink_members(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, unsafe_member="../outside")
        with self.assertRaisesRegex(ValueError, "unsafe archive path"):
            self.prepare()

    def test_verifies_the_exact_artifact_zip_digest_crc_and_outer_manifest(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        zip_path = make_artifact_zip(self.artifact, self.root / "candidate.zip")
        digest = "sha256:" + hashlib.sha256(zip_path.read_bytes()).hexdigest()
        destination = self.root / "extracted"
        name = release.verify_artifact_zip(zip_path, destination, digest, "1234", "2")
        self.assertEqual(name, archive.name)
        self.assertTrue((destination / archive.name).is_file())

    def test_rejects_an_artifact_zip_with_wrong_api_digest(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        zip_path = make_artifact_zip(self.artifact, self.root / "candidate.zip")
        destination = self.root / "extracted"
        with self.assertRaisesRegex(ValueError, "GitHub artifact API SHA256"):
            release.verify_artifact_zip(zip_path, destination, "sha256:" + "0" * 64,
                                        "1234", "2")
        self.assertFalse(destination.exists())

    def test_rejects_a_crc_broken_artifact_zip_and_cleans_extraction(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        zip_path = make_artifact_zip(self.artifact, self.root / "candidate.zip")
        corrupt_first_zip_member(zip_path)
        digest = "sha256:" + hashlib.sha256(zip_path.read_bytes()).hexdigest()
        destination = self.root / "extracted"
        with self.assertRaisesRegex(ValueError, "CRC"):
            release.verify_artifact_zip(zip_path, destination, digest, "1234", "2")
        self.assertFalse(destination.exists())

    def test_zip_expansion_limit_is_checked_before_crc_decompression(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        zip_path = make_artifact_zip(self.artifact, self.root / "candidate.zip")
        digest = "sha256:" + hashlib.sha256(zip_path.read_bytes()).hexdigest()
        destination = self.root / "extracted"
        with patch.object(release, "MAX_ARCHIVE_EXPANDED", 1), \
             patch.object(zipfile.ZipFile, "testzip", side_effect=AssertionError("must not decompress")):
            with self.assertRaisesRegex(ValueError, "expansion limit"):
                release.verify_artifact_zip(zip_path, destination, digest, "1234", "2")
        self.assertFalse(destination.exists())

    def test_rejects_unsafe_zip_members_and_cleans_extraction(self):
        for unsafe in ("../escape", "symlink"):
            with self.subTest(unsafe=unsafe):
                self.artifact = self.root / unsafe.replace("/", "-")
                self.artifact.mkdir()
                release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
                zip_path = make_artifact_zip(self.artifact, self.root / (unsafe.replace("/", "-") + ".zip"),
                                             unsafe=unsafe)
                digest = "sha256:" + hashlib.sha256(zip_path.read_bytes()).hexdigest()
                destination = self.root / (unsafe.replace("/", "-") + "-extracted")
                with self.assertRaisesRegex(ValueError, "unsafe candidate artifact ZIP member|unsafe archive path"):
                    release.verify_artifact_zip(zip_path, destination, digest, "1234", "2")
                self.assertFalse(destination.exists())

    def test_rejects_symlink_and_hardlink_members(self):
        for member_type in ("symlink", "hardlink"):
            with self.subTest(member_type=member_type):
                self.artifact = self.root / member_type
                self.artifact.mkdir()
                release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT,
                                unsafe_member_type=member_type)
                self.output = self.root / (member_type + "-assets")
                with self.assertRaisesRegex(ValueError, "unsafe archive member type"):
                    self.prepare()

    def test_verifier_rejects_a_changed_direct_rom(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        self.prepare()
        image = self.output / "s20p-A-25.12-9f397975-sysupgrade.bin"
        image.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
            release.verify_release_assets(
                self.output, run_id="1234", run_attempt="2", build_commit=TEST_WRAPPER_COMMIT,
                upstream_commit=release.UPSTREAM_BASE_COMMIT,
            )

    def test_verifier_rejects_an_archive_whose_rom_differs_from_the_direct_asset(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        self.prepare()
        image = self.output / "s20p-A-25.12-9f397975-sysupgrade.bin"
        image.write_bytes(b"a different but checksummed file")
        write_checksums(self.output, exclude={"SHA256SUMS"})
        with self.assertRaisesRegex(ValueError, "does not match the same-build archive"):
            release.verify_release_assets(
                self.output, run_id="1234", run_attempt="2", build_commit=TEST_WRAPPER_COMMIT,
                upstream_commit=release.UPSTREAM_BASE_COMMIT,
            )


def add_legacy_quickstart(report):
    exception = go_security.POLICY["legacy_prebuilt_exceptions"][0]
    report.update(status="pass_with_known_exception", known_exceptions=[exception])
    report["binaries"].append({**{key: exception[key] for key in
        ("path", "sha256", "go_version", "x509_verify_linked", "distrusted_spki_count")},
        "security_status": "legacy-unpatched-prebuilt", "exception_id": exception["id"]})


def fixed_release_fixture(root, policy_id="legacy"):
    from test_fixed_inputs import fixture
    path, manifest = fixture(root)
    if policy_id != "legacy":
        manifest.update(schema_version=3, profile=fixed_inputs.profile_descriptor(policy_id))
        path.write_text(json.dumps(manifest))
    extras = {"provenance/fixed/" + item.relative_to(root).as_posix(): item.read_bytes()
              for item in root.rglob("*") if item.is_file()}
    extras["provenance/component-resolution.json"] = (root / "components.json").read_bytes()
    extras["provenance/http-inputs.jsonl"] = json_bytes({
        "urls": [manifest["http_inputs"][0]["url"]], "exit_code": 0,
        "stdout_sha256": manifest["http_inputs"][0]["sha256"]})
    if policy_id != "legacy":
        extras["provenance/build-date.txt"] = b"20261031\n"
    return path, manifest, extras


def release_fixture(artifact, commit, tamper_checksum=False, extra_image=False,
                    upstream_commit=None, preservation_status="pass", signed_feed_status="pass",
                    package_count_delta=0, include_lucky=False, unsafe_member=None,
                    unsafe_member_type=None, payload_mismatch=False, wrong_public_key_hash=False, extra_files=None,
                    application_policy="legacy", extra_packages=None):
    """Build a same-shape archive with integrity and acceptance records."""
    packages = [
        {"name": "kernel", "version": TEST_KERNEL_ABI},
        {"name": "base-files", "version": "1~b25.12"},
    ]
    selection = release.load_application_selection(CI / "baseline", application_policy)
    packages.extend({'name': name, 'version': '0.107.79-r1'} for name in selection["required_packages"])
    packages.extend(extra_packages or [])
    if include_lucky:
        packages.extend([
            {"name": "lucky", "version": "3.4.0-r3"},
            {"name": "luci-app-lucky", "version": "3.0.0-r26"},
            {"name": "luci-i18n-lucky-zh-cn", "version": "0"},
        ])
    actual_count = len(packages)
    key_hash = hashlib.sha256(TEST_PUBLIC_KEY).hexdigest()
    resolved_upstream = upstream_commit or release.UPSTREAM_BASE_COMMIT
    requested_lock = upstream_sync.update_lock(
        json.loads((CI / "sources.lock.json").read_text()),
        upstream_sync.Resolution(resolved_upstream, True,
            "identical" if resolved_upstream == release.UPSTREAM_BASE_COMMIT else "ahead",
            release.UPSTREAM_BASE_COMMIT, resolved_upstream == release.UPSTREAM_BASE_COMMIT))
    upstream_resolution = {
        "schema_version": 1,
        "repository": release.UPSTREAM_URL,
        "ref": release.UPSTREAM_REF,
        "base_commit": release.UPSTREAM_BASE_COMMIT,
        "head_commit": resolved_upstream,
        "compare_status": "identical" if resolved_upstream == release.UPSTREAM_BASE_COMMIT else "ahead",
        "merge_base": release.UPSTREAM_BASE_COMMIT,
        "should_build": True,
        "forced": resolved_upstream == release.UPSTREAM_BASE_COMMIT,
        "previous_attempted": False,
    }
    files = {
        IMAGE_PATH: test_sysupgrade_image(root_payload=(b"different-rootfs" if payload_mismatch else TEST_ROOTFS_PAYLOAD)),
        "inspection/kernel.fit": TEST_KERNEL_PAYLOAD,
        "inspection/rootfs.squashfs": TEST_ROOTFS_PAYLOAD,
        "reports/build-status.json": json_bytes({
            "status": "candidate", "preservation": "pass", "live_acceptance": "not-run",
            "stable_eligible": False, "bit_identical_claim": False,
        }),
        "reports/preservation-image.json": json_bytes({
            "status": preservation_status, "errors": [], "pending_review_count": 0,
            "checks": {"application_selection": release.application_selection_evidence(
                selection), "packages": {
                "original_count": 483, "actual_count": actual_count, "kernel": TEST_KERNEL_ABI,
                "kmods": 0, "luci_apps": 0,
                "reviewed_optional_absent": [] if include_lucky else sorted(release.B_OPTIONAL_PACKAGE_NAMES),
            }},
        }),
        "reports/go-x509-security.json": json_bytes({
            "schema_version": 2, "status": "pass", "known_exceptions": [],
            "policy_sha256": go_security.sha256(go_security.POLICY_PATH),
            "rootfs_sha256": hashlib.sha256(TEST_ROOTFS_PAYLOAD).hexdigest(),
            "binaries": [{"path": name, "sha256": "a" * 64, "go_version": "go1.26.10",
                          "x509_verify_linked": True, "distrusted_spki_count": 8, "security_status": "patched"}
                         for name in go_security.REQUIRED_BINARIES],
        }),
        "reports/dashboard.json": json_bytes(dashboard.report(hashlib.sha256(TEST_ROOTFS_PAYLOAD).hexdigest())),
        "reports/capture.json": json_bytes({
            "status": "captured", "image": IMAGE_PATH, "kernel_abi": TEST_KERNEL_ABI,
            "same_build_signed_feeds": True, "private_keys": "excluded-and-scanned",
        }),
        "reports/signed-feeds.json": json_bytes({
            "status": signed_feed_status, "installed_packages_matched": actual_count,
            "kernel_abi": TEST_KERNEL_ABI,
            "kmod_packages_kernel_checked": 153,
            "image_trust": {"status": "pass", "comparison": "exact-public-key-bytes",
                            "public_key_sha256": ("0" * 64 if wrong_public_key_hash else key_hash),
                            "matching_key_paths": ["/etc/apk/keys/public-key.pem"]},
            "feeds": [{"path": f"feed-{index}", "signature": "verified",
                       "payload_identity": "native-fetch-verified",
                       "index_metadata": "matches-all-packages", "packages": 1}
                      for index in range(5)],
        }),
        "provenance/source-lock.json": json_bytes({
            "upstream_commit": resolved_upstream,
            "upstream_resolution": upstream_resolution,
            "wrapper_commit": release.WRAPPER_COMMIT,
            "wrapper_checkout_commit": commit,
            "sources": [{
                "role": "main-source", "url": release.UPSTREAM_URL,
                "ref": "25.12", "commit": resolved_upstream,
                "tree_hash": "c" * 64,
            }],
            "source_lock_sha256": hashlib.sha256(json_bytes(requested_lock)).hexdigest(),
            "source_lock": requested_lock,
        }),
        "provenance/upstream-resolution.json": json_bytes(upstream_resolution),
        "provenance/config-gate.json": json_bytes({
            "status": "pass", "errors": [], "checks": {
                "application_selection": release.application_selection_evidence(
                    selection), "config": {
                "b_optional_packages": list(release.B_OPTIONAL_PACKAGE_NAMES),
                "b_optional_package_omission": {
                    "upstream_commit": resolved_upstream,
                    "packages": list(release.B_OPTIONAL_PACKAGE_NAMES),
                    "review_sha256": release.B_OPTIONAL_PACKAGE_REVIEW_SHA256,
                },
            }},
        }),
        "inspection/installed-packages.json": json_bytes({"packages": packages}),
        "inspection/fwtool-metadata.json": json_bytes({
            "supported_devices": ["clx,s20p"],
            "version": {"target": "mediatek/filogic", "board": "clx_s20p"},
        }),
        "reports/image-safety.json": json_bytes({
            "status": "pass", "supported_devices": ["clx,s20p"],
            "partition_layout": "existing GPT kernel32MiB/rootfs2GiB",
            "partitions": test_partition_metadata(),
        }),
        "public-keys/public-key.pem": TEST_PUBLIC_KEY,
        "metadata/kernel/kernel.release": b"6.12.112\n",
        "metadata/kernel/.vermagic": ("b" * 32 + "\n").encode(),
    }
    if package_count_delta:
        files["reports/preservation-image.json"] = json_bytes({
            "status": "pass", "errors": [], "pending_review_count": 0,
            "checks": {"application_selection": release.application_selection_evidence(
                selection), "packages": {
                "original_count": 483, "actual_count": actual_count + package_count_delta,
                "kernel": TEST_KERNEL_ABI,
                "kmods": 0, "luci_apps": 0,
                "reviewed_optional_absent": sorted(release.B_OPTIONAL_PACKAGE_NAMES),
            }},
        })

    if extra_image:
        files["bin/targets/mediatek/filogic/clx_s20p-extra-sysupgrade.bin"] = b"second-image"
    files.update(extra_files or {})
    build = artifact / "fixture"
    for name, data in files.items():
        path = build / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    requested_path = build / "provenance/requested-sources.lock.json"
    requested_path.parent.mkdir(parents=True, exist_ok=True)
    requested_path.write_bytes(json_bytes(requested_lock))
    files["provenance/requested-sources.lock.json"] = requested_path.read_bytes()
    (build / "SHA256SUMS").write_text(checksum_text(files, tamper=tamper_checksum))
    (build / "SHA256SUMS").write_text(checksum_text(files, tamper=tamper_checksum))

    archive = artifact / "s20p-samebuild-1234-2.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(build.rglob("*")):
            tar.add(path, arcname=path.relative_to(build).as_posix(), recursive=False)
        if unsafe_member == "../outside":
            payload = b"unsafe"
            member = tarfile.TarInfo(unsafe_member)
            member.size = len(payload)
            tar.addfile(member, __import__("io").BytesIO(payload))
        if unsafe_member_type == "symlink":
            member = tarfile.TarInfo("./unsafe-link")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside"
            tar.addfile(member)
        elif unsafe_member_type == "hardlink":
            member = tarfile.TarInfo("./unsafe-link")
            member.type = tarfile.LNKTYPE
            member.linkname = "./fixture/" + IMAGE_PATH
            tar.addfile(member)
    shutil.rmtree(build)
    (artifact / "SHA256SUMS").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    return archive


def make_artifact_zip(artifact, output, unsafe=None):
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(Path(artifact).iterdir()):
            archive.write(path, path.name)
        if unsafe == "../escape":
            archive.writestr(unsafe, b"escape")
        elif unsafe == "symlink":
            item = zipfile.ZipInfo("unsafe-link")
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, b"../../outside")
    return output


def corrupt_first_zip_member(path):
    data = bytearray(Path(path).read_bytes())
    if data[:4] != b"PK\x03\x04":
        raise AssertionError("zip local header missing")
    name_size = int.from_bytes(data[26:28], "little")
    extra_size = int.from_bytes(data[28:30], "little")
    payload_offset = 30 + name_size + extra_size
    data[payload_offset] ^= 0x01
    Path(path).write_bytes(data)


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


TEST_KERNEL_PAYLOAD = b"test-kernel-fit-payload"
TEST_ROOTFS_PAYLOAD = b"test-rootfs-squashfs-payload"


def test_sysupgrade_image(root_payload=TEST_ROOTFS_PAYLOAD):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:") as archive:
        for name, payload in (("kernel", TEST_KERNEL_PAYLOAD), ("root", root_payload)):
            member = tarfile.TarInfo("sysupgrade-clx_s20p/" + name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return stream.getvalue()


def test_partition_metadata():
    kernel_size, rootfs_size = len(TEST_KERNEL_PAYLOAD), len(TEST_ROOTFS_PAYLOAD)
    rootfs_capacity = release.PARTITION_CAPACITIES["rootfs"]
    overlay_start = (rootfs_size + 65535) & ~65535
    return {
        "kernel": {"size": kernel_size,
                   "capacity": release.PARTITION_CAPACITIES["kernel"],
                   "remaining": release.PARTITION_CAPACITIES["kernel"] - kernel_size},
        "rootfs": {"size": rootfs_size, "capacity": rootfs_capacity,
                   "remaining": rootfs_capacity - rootfs_size,
                   "overlay_start": overlay_start,
                   "overlay_remaining": rootfs_capacity - overlay_start},
    }


def checksum_text(files, tamper=False):
    lines = [f"{hashlib.sha256(data).hexdigest()}  {name}" for name, data in sorted(files.items())]
    if tamper:
        lines[0] = "0" * 64 + lines[0][64:]
    return "\n".join(lines) + "\n"


def write_checksums(directory, exclude=()):
    rows = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.relative_to(directory).as_posix() not in exclude:
            rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(directory).as_posix()}")
    (directory / "SHA256SUMS").write_text("\n".join(rows) + "\n")


def rewrite_archive_json(archive, name, mutate):
    with tarfile.open(archive, "r:gz") as source:
        member = next(item for item in source.getmembers()
                      if Path(item.name).as_posix().removeprefix("./") == name)
        data = json.load(source.extractfile(member))
    mutate(data)
    rewrite_archive_entry(archive, name, json_bytes(data))


def rewrite_archive_entry(archive, name, replacement):
    temporary = archive.with_suffix(".rewritten.tar.gz")
    with tarfile.open(archive, "r:gz") as source, tarfile.open(temporary, "w:gz") as target:
        rewritten = {}
        for member in source.getmembers():
            member_name = Path(member.name).as_posix().removeprefix("./")
            if member_name == "SHA256SUMS":
                continue
            if member_name == name:
                if replacement is None:
                    continue
                payload = replacement
                member.size = len(payload)
                target.addfile(member, fileobj=__import__("io").BytesIO(payload))
                rewritten[name] = payload
            else:
                stream = source.extractfile(member) if member.isfile() else None
                if member.isfile():
                    payload = stream.read()
                    rewritten[Path(member.name).as_posix().removeprefix("./")] = payload
                    target.addfile(member, __import__("io").BytesIO(payload))
                else:
                    target.addfile(member, stream)
        sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {path}\n"
                        for path, data in sorted(rewritten.items()) if path != "SHA256SUMS")
        info = tarfile.TarInfo("SHA256SUMS")
        payload = sums.encode()
        info.size = len(payload)
        target.addfile(info, __import__("io").BytesIO(payload))
    archive.write_bytes(temporary.read_bytes())
    temporary.unlink()
    checksums = archive.parent / "SHA256SUMS"
    checksums.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")


if __name__ == "__main__":
    unittest.main()
