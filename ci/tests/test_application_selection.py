"""Requested application removal must not relax unrelated preservation gates."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
import preservation


class ApplicationSelectionTests(unittest.TestCase):
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
        self.assertEqual(len(review["removed_records"]), 413)
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

    def test_unrelated_missing_package_is_still_rejected(self):
        lucky = preservation.load_b_optional_package_policy(CI / "baseline", CI / "sources.lock.json")
        original = preservation.parse_packages(CI / "baseline/original-package-manifest.json")
        removed = set(preservation.OMITTED_APPLICATION_PACKAGES)
        actual = {name: row for name, row in original.items()
                  if name not in removed | set(preservation.B_OPTIONAL_PACKAGE_NAMES)}
        preservation.reviewed_b_optional_package_absences(lucky, original, actual,
                                                         preapproved_absences=removed)
        actual.pop("curl")
        with self.assertRaisesRegex(ValueError, "unreviewed missing baseline package"):
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


if __name__ == "__main__":
    unittest.main()
