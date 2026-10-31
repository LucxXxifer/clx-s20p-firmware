"""Requested application removal must not relax unrelated preservation gates."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
import preservation


class ApplicationSelectionTests(unittest.TestCase):
    def test_new_profile_approvals_do_not_apply_to_legacy_builds(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'approved.json'
            path.write_text(json.dumps({'approved': [{'id': 'f' * 64, 'reason': 'scoped test',
                                                     'application_policy': 'minimal-20261031'}]}))
            legacy = preservation.Gate('image', CI / 'baseline', path, application_selection=True)
            current = preservation.Gate('image', CI / 'baseline', path, application_selection=True,
                             application_policy='minimal-20261031')
            self.assertNotIn('f' * 64, legacy.approvals)
            self.assertEqual(current.approvals['f' * 64], 'scoped test')

    def test_minimal_policy_is_separate_from_unchanged_legacy_review(self):
        legacy = preservation.load_application_selection(CI / "baseline")
        minimal = preservation.load_application_selection(CI / "baseline", "minimal-20261031")
        self.assertEqual(len(legacy["removed_records"]), 415)
        self.assertEqual(len(minimal["removed_records"]), 445)
        self.assertEqual(minimal["package_names"], legacy["package_names"] + [
            "easytier", "luci-app-easytier", "luci-i18n-easytier-zh-cn"])
        self.assertEqual(minimal["excluded_packages"], legacy["excluded_packages"])
        self.assertEqual(minimal["required_packages"], legacy["required_packages"])
        legacy_evidence = preservation.application_selection_evidence(legacy)
        minimal_evidence = preservation.application_selection_evidence(minimal)
        self.assertEqual(legacy_evidence["review_sha256"], preservation.APPLICATION_REVIEW_SHA256)
        self.assertNotEqual(minimal_evidence["review_sha256"], legacy_evidence["review_sha256"])
        self.assertEqual(minimal_evidence["policy_id"], "minimal-20261031")
        self.assertNotIn("policy_id", legacy_evidence)

    def test_application_policy_rejects_unknown_id(self):
        for policy_id in ("unknown", "../application-image-selection.json", ""):
            with self.subTest(policy_id=policy_id), self.assertRaises(ValueError):
                preservation.load_application_selection(CI / "baseline", policy_id)

    def test_minimal_policy_rejects_modified_review_and_inactive_selection(self):
        policy_id = "minimal-20261031"
        policy = preservation.APPLICATION_POLICIES[policy_id]
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "review.json"
            review = json.loads(policy["path"].read_text())
            review["removed_records"].append({"path": "/etc/init.d/dnsmasq"})
            path.write_text(json.dumps(review))
            with patch.dict(preservation.APPLICATION_POLICIES,
                            {policy_id: {**policy, "path": path}}):
                with self.assertRaisesRegex(ValueError, "digest mismatch"):
                    preservation.load_application_selection(CI / "baseline", policy_id)
        with self.assertRaisesRegex(ValueError, "requires application selection"):
            preservation.Gate("config", CI / "baseline", application_policy=policy_id)

    def test_minimal_policy_rejects_retained_easytier_and_preserves_shared_tun(self):
        review = preservation.load_application_selection(CI / "baseline", "minimal-20261031")
        baseline = preservation.file_index(json.loads(
            (CI / "baseline/full-rootfs-manifest.json").read_text())["files"])
        removed = {row["path"] for row in review["removed_records"]}
        self.assertIn("/usr/bin/easytier-core", removed)
        self.assertIn("/etc/rc.d/S99easytier", removed)
        self.assertFalse(any("/lib/modules/" in path for path in removed))
        actual = {path: row for path, row in baseline.items() if path not in removed}
        self.assertEqual(preservation.application_file_absences(review, baseline, actual), removed)
        actual["/etc/easytier"] = baseline["/etc/easytier"]
        self.assertNotIn("/etc/easytier", preservation.application_file_absences(review, baseline, actual))
        actual["/usr/bin/easytier-core"] = baseline["/usr/bin/easytier-core"]
        with self.assertRaisesRegex(ValueError, "must be absent"):
            preservation.application_file_absences(review, baseline, actual)

    def test_minimal_config_requires_three_easytier_selectors_disabled(self):
        source = CI.parent / "wrt_core/deconfig/S20P-WIFI-YES.config"
        names = ("easytier", "luci-app-easytier", "luci-i18n-easytier-zh-cn")
        # The repository input is a fragment, not a post-defconfig fixture.
        # Expand the original preserved selectors before testing this delta.
        policy = json.loads((CI / "baseline/policy.json").read_text())
        values = preservation.parse_config(source)
        for package in json.loads((CI / "baseline/original-package-manifest.json").read_text()):
            values["CONFIG_PACKAGE_" + package["name"]] = "y"
        for package in policy["required_packages"]:
            values["CONFIG_PACKAGE_" + package] = "y"
        values.update(policy["required_config"])
        values.update(preservation.parse_config(CI / "baseline/baseline-mtk-config.txt"))
        for symbol in policy["unsupported_fragment_symbols"]:
            values[symbol] = "n"
        for package in (preservation.OMITTED_APPLICATION_PACKAGES + names
                        + preservation.EXCLUDED_APPLICATION_PACKAGES
                        + preservation.B_OPTIONAL_PACKAGE_NAMES):
            values["CONFIG_PACKAGE_" + package] = "n"
        text = "".join(key + "=" + value + "\n" for key, value in sorted(values.items()))
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "config"
            path.write_text(text)
            gate = preservation.Gate("config", CI / "baseline", application_selection=True,
                                     application_policy="minimal-20261031")
            gate.config(path, source_lock=CI / "sources.lock.json")
            self.assertEqual(gate.errors, [])
            self.assertEqual(gate.checks["application_selection"]["policy_id"], "minimal-20261031")
            for package in names:
                path.write_text(text.replace("CONFIG_PACKAGE_" + package + "=n",
                                             "CONFIG_PACKAGE_" + package + "=y"))
                bad = preservation.Gate("config", CI / "baseline", application_selection=True,
                                        application_policy="minimal-20261031")
                bad.config(path, source_lock=CI / "sources.lock.json")
                self.assertTrue(any(error["subject"] == "CONFIG_PACKAGE_" + package
                                    for error in bad.errors))
            path.write_text(text.replace("CONFIG_PACKAGE_kmod-tun=y", "CONFIG_PACKAGE_kmod-tun=n"))
            bad = preservation.Gate("config", CI / "baseline", application_selection=True,
                                    application_policy="minimal-20261031")
            bad.config(path, source_lock=CI / "sources.lock.json")
            self.assertTrue(any(error["subject"] == "CONFIG_PACKAGE_kmod-tun" for error in bad.errors))

    def test_honk_omission_keeps_original_stat_and_sqlite_tools(self):
        source = CI.parent / "wrt_core/deconfig/S20P-WIFI-YES.config"
        values = preservation.parse_config(source)
        for package in ("coreutils-stat", "sqlite3-cli", "libsqlite3"):
            with self.subTest(package=package):
                self.assertEqual(values.get("CONFIG_PACKAGE_" + package), "y",
                                 "Baseline tools must remain installed without Honk")

    def test_review_binds_exact_baseline_packages_and_files(self):
        review = preservation.load_application_selection(CI / "baseline")
        self.assertEqual(review["package_names"], list(preservation.OMITTED_APPLICATION_PACKAGES))
        self.assertEqual(review["required_packages"], list(preservation.REQUIRED_APPLICATION_PACKAGES))
        self.assertEqual(len(review["removed_records"]), 415)
        paths = {row["path"] for row in review["removed_records"]}
        self.assertTrue('/usr/bin/honk-core' in paths)
        self.assertIn('/etc/init.d/tingreader', paths)
        self.assertNotIn('/etc/init.d/dnsmasq', paths)
        self.assertNotIn('/usr/bin/ip', paths)

    def test_adguard_is_excluded_until_upstream_chinese_is_available(self):
        source = CI.parent / "wrt_core/deconfig/S20P-WIFI-YES.config"
        values = preservation.parse_config(source)
        names = ('adguardhome', 'luci-app-adguardhome', 'luci-i18n-adguardhome-zh-cn')
        for name in names:
            self.assertEqual(values.get('CONFIG_PACKAGE_' + name), 'n')
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'config'
            path.write_text(source.read_text().replace('CONFIG_PACKAGE_adguardhome=n',
                                                       'CONFIG_PACKAGE_adguardhome=y'))
            gate = preservation.Gate('config', CI / 'baseline', application_selection=True)
            gate.config(path, source_lock=CI / 'sources.lock.json')
            self.assertTrue(any(e['subject'] == 'CONFIG_PACKAGE_adguardhome' for e in gate.errors))
        self.assertEqual(list(preservation.REQUIRED_APPLICATION_PACKAGES), [])

    def test_tingreader_launcher_and_empty_license_parent_may_be_removed(self):
        review = preservation.load_application_selection(CI / "baseline")
        baseline = preservation.file_index(json.loads(
            (CI / "baseline/full-rootfs-manifest.json").read_text())["files"])
        removed = {row["path"] for row in review["removed_records"]}
        self.assertTrue("/usr/bin/ting-reader" in removed)
        self.assertTrue("/usr/share/licenses" in removed)
        # This shared parent's original contents all belonged to omitted Honk.
        self.assertTrue(all(path in removed for path in baseline
                            if path.startswith("/usr/share/licenses/")))
        actual = {path: row for path, row in baseline.items() if path not in removed}
        self.assertEqual(preservation.application_file_absences(review, baseline, actual), removed)

    def test_reviewed_shared_directory_can_remain_for_another_package(self):
        review = preservation.load_application_selection(CI / "baseline")
        baseline = preservation.file_index(json.loads(
            (CI / "baseline/full-rootfs-manifest.json").read_text())["files"])
        removed = {row["path"] for row in review["removed_records"]}
        actual = {path: row for path, row in baseline.items() if path not in removed}
        actual["/usr/share/licenses"] = baseline["/usr/share/licenses"]
        absent = preservation.application_file_absences(review, baseline, actual)
        self.assertNotIn("/usr/share/licenses", absent)
        self.assertTrue("/usr/bin/ting-reader" in absent)

    def test_unrelated_missing_package_is_still_rejected(self):
        lucky = preservation.load_b_optional_package_policy(CI / "baseline", CI / "sources.lock.json")
        original = preservation.parse_packages(CI / "baseline/original-package-manifest.json")
        for policy_id in ("legacy", "minimal-20261031"):
            review = preservation.load_application_selection(CI / "baseline", policy_id)
            removed = set(review["package_names"])
            actual = {name: row for name, row in original.items()
                      if name not in removed | set(preservation.B_OPTIONAL_PACKAGE_NAMES)}
            preservation.reviewed_b_optional_package_absences(lucky, original, actual,
                                                             preapproved_absences=removed)
            actual.pop("curl")
            with self.subTest(policy_id=policy_id), self.assertRaisesRegex(
                    ValueError, "unreviewed missing baseline package"):
                preservation.reviewed_b_optional_package_absences(lucky, original, actual,
                                                                 preapproved_absences=removed)

    def test_removed_recipe_does_not_require_package_metadata_but_cannot_be_enabled(self):
        gate = preservation.Gate("config", CI / "baseline", application_selection=True)
        source = CI.parent / "wrt_core/deconfig/S20P-WIFI-YES.config"
        gate.config(source, source_lock=CI / "sources.lock.json")
        symbols = {"CONFIG_PACKAGE_" + n for n in preservation.OMITTED_APPLICATION_PACKAGES}
        self.assertFalse(any(e["category"] == "config_symbol" and e["subject"] in symbols
                             for e in gate.errors))
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "config"
            path.write_text(source.read_text().replace("CONFIG_PACKAGE_luci-app-tingreader=n",
                                                      "CONFIG_PACKAGE_luci-app-tingreader=y"))
            bad = preservation.Gate("config", CI / "baseline", application_selection=True)
            bad.config(path, source_lock=CI / "sources.lock.json")
            self.assertTrue(any(e["category"] == "config_symbol" and e["subject"] in symbols
                                for e in bad.errors))

    def test_retained_payload_or_unrelated_absence_is_rejected(self):
        review = preservation.load_application_selection(CI / "baseline")
        baseline = preservation.file_index(json.loads((CI / "baseline/full-rootfs-manifest.json").read_text())["files"])
        lucky = preservation.load_b_optional_package_policy(CI / "baseline", CI / "sources.lock.json")
        allowed = {r["path"] for r in review["removed_records"]}
        lucky_paths = {r["path"] for r in lucky["removed_records"]}
        actual = {p: row for p, row in baseline.items() if p not in allowed | lucky_paths}
        self.assertEqual(preservation.application_file_absences(review, baseline, actual), allowed)
        self.assertEqual(preservation.reviewed_b_optional_file_absences(
            lucky, baseline, actual, preapproved_absences=allowed), lucky_paths)
        actual["/etc/init.d/tingreader"] = baseline["/etc/init.d/tingreader"]
        with self.assertRaisesRegex(ValueError, "must be absent"):
            preservation.application_file_absences(review, baseline, actual)
        actual.pop("/etc/init.d/tingreader")
        actual.pop("/etc/init.d/dnsmasq")
        with self.assertRaisesRegex(ValueError, "unreviewed missing rootfs path"):
            preservation.reviewed_b_optional_file_absences(lucky, baseline, actual,
                                                         preapproved_absences=allowed)

    def test_reviewed_record_drift_is_rejected(self):
        review = preservation.load_application_selection(CI / "baseline")
        baseline = {row["path"]: copy.deepcopy(row) for row in review["removed_records"]}
        baseline["/etc/config/tingreader"]["sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "baseline"):
            preservation.application_file_absences(review, baseline, {})

    def test_world_review_binds_roots_and_actual_kernel_not_one_old_abi(self):
        review = {"normalized_world_sha256": [preservation.digest(
            b"curl\nkernel=@SAME_BUILD_KERNEL@\nluci\n")]}
        kernel = "6.12.112~" + "b" * 32 + "-r1"
        data = "curl\nkernel=" + kernel + "\nluci\n"
        self.assertTrue(preservation.application_world_matches(review, data, kernel))
        for changed in (data + "unknown-package\n", data.replace("curl\n", ""),
                        data.replace(kernel, "6.12.112~wrong-r1"), data + "curl\n"):
            self.assertFalse(preservation.application_world_matches(review, changed, kernel))


if __name__ == "__main__":
    unittest.main()
