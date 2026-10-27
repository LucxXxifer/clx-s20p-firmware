"""Exact B-only optional package omission policy regression tests."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch
import subprocess


CI = Path(__file__).resolve().parents[1]
REPO = CI.parent
SPEC = importlib.util.spec_from_file_location("optional_package_preservation", CI / "preservation.py")
preservation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preservation)


class OptionalPackagePolicyTests(unittest.TestCase):
    def setUp(self):
        self.baseline = CI / "baseline"
        self.review = CI / "reviewed-differences/lucky-image-omission-b.json"
        self.source_lock = CI / "sources.lock.json"
        self.config = REPO / "wrt_core/deconfig/S20P-WIFI-YES.config"

    def temporary_path(self, name):
        temporary = tempfile.TemporaryDirectory(dir=CI)
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / name

    def load(self, source_lock=None, review=None, upstream_evidence=None):
        return preservation.load_b_optional_package_policy(
            self.baseline,
            source_lock or self.source_lock,
            review_path=review or self.review,
            upstream_evidence_path=upstream_evidence,
        )

    def test_review_is_bound_to_fixed_b_source_and_exact_baseline_records(self):
        review = self.load()
        self.assertEqual(review["upstream_commit"], preservation.B_SOURCE_COMMIT)
        self.assertEqual(
            {item["name"] for item in review["baseline_packages"]},
            set(preservation.B_OPTIONAL_PACKAGE_NAMES),
        )
        self.assertGreater(len(review["removed_records"]), 0)

    def test_fixed_b_approvals_are_not_reused_for_a_moving_upstream_head(self):
        with tempfile.TemporaryDirectory() as temp:
            baseline = Path(temp) / "baseline"
            shutil.copytree(self.baseline, baseline)
            policy_path = baseline / "policy.json"
            policy = json.loads(policy_path.read_text())
            policy["upstream_commit"] = "a" * 40
            policy_path.write_text(json.dumps(policy, indent=2) + "\n")
            checksums_path = baseline / "baseline-sha256.json"
            checksums = json.loads(checksums_path.read_text())
            checksums["policy.json"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
            checksums_path.write_text(json.dumps(checksums, indent=2) + "\n")
            approval = Path(temp) / "approved-differences.json"
            approval.write_text(json.dumps({"approved": [{"id": "b" * 64, "reason": "fixed-B review"}]}))
            gate = preservation.Gate("config", baseline, approval)
            self.assertEqual(gate.approvals, {})

    def test_a_source_does_not_receive_the_b_omission(self):
        request = json.loads(self.source_lock.read_text())
        main = [item for item in request["sources"] if item.get("role") == "main-source"]
        self.assertEqual(len(main), 1)
        main[0]["commit"] = "03eb30617955519e35845838c6255ebe687eb717"
        path = self.temporary_path("source-lock.json")
        path.write_text(json.dumps(request))
        self.assertIsNone(self.load(source_lock=path))

    def test_resolved_source_lock_must_bind_the_b_main_source_commit(self):
        request = json.loads(self.source_lock.read_text())
        resolved = {
            "upstream_commit": preservation.B_SOURCE_COMMIT,
            "source_lock": request,
            "sources": [{
                "role": "main-source",
                "url": "https://github.com/chasey-dev/immortalwrt-mt798x-rebase.git",
                "ref": "25.12",
                "commit": "a" * 40,
                "tree_hash": "b" * 64,
            }],
        }
        path = self.temporary_path("resolved-source-lock.json")
        path.write_text(json.dumps(resolved))
        with self.assertRaisesRegex(ValueError, "resolved main-source"):
            self.load(source_lock=path)

    def test_native_resolved_source_needs_exact_checkout_not_a_branch_field(self):
        request = json.loads(self.source_lock.read_text())
        resolved = {
            "upstream_commit": preservation.B_SOURCE_COMMIT,
            "source_lock": request,
            "sources": [{
                "url": "https://github.com/chasey-dev/immortalwrt-mt798x-rebase",
                "commit": preservation.B_SOURCE_COMMIT,
                "tree_hash": "b" * 64,
            }],
        }
        path = self.temporary_path("native-resolved-source-lock.json")
        path.write_text(json.dumps(resolved))
        self.assertEqual(self.load(source_lock=path)["upstream_commit"], preservation.B_SOURCE_COMMIT)

    def test_descendant_upstream_uses_only_the_pinned_lucky_omission(self):
        head = "a" * 40
        request = json.loads(self.source_lock.read_text())
        main = next(row for row in request["sources"] if row.get("role") == "main-source")
        main["commit"] = head
        path = self.temporary_path("daily-source-lock.json")
        path.write_text(json.dumps(request))
        evidence = {
            "schema_version": 1,
            "repository": "https://github.com/chasey-dev/immortalwrt-mt798x-rebase",
            "ref": "25.12",
            "base_commit": preservation.B_SOURCE_COMMIT,
            "head_commit": head,
            "compare_status": "ahead",
            "merge_base": preservation.B_SOURCE_COMMIT,
            "should_build": True,
            "forced": False,
            "previous_attempted": False,
        }
        evidence_path = self.temporary_path("upstream-resolution.json")
        evidence_path.write_text(json.dumps(evidence))
        policy = self.load(source_lock=path, upstream_evidence=evidence_path)
        self.assertEqual(policy["upstream_commit"], head)
        self.assertEqual(policy["package_names"], list(preservation.B_OPTIONAL_PACKAGE_NAMES))

    def test_descendant_upstream_requires_matching_compare_evidence(self):
        head = "a" * 40
        request = json.loads(self.source_lock.read_text())
        main = next(row for row in request["sources"] if row.get("role") == "main-source")
        main["commit"] = head
        path = self.temporary_path("daily-source-lock.json")
        path.write_text(json.dumps(request))
        evidence = {
            "schema_version": 1,
            "repository": "https://github.com/chasey-dev/immortalwrt-mt798x-rebase",
            "ref": "25.12",
            "base_commit": preservation.B_SOURCE_COMMIT,
            "head_commit": head,
            "compare_status": "ahead",
            "merge_base": "b" * 40,
            "should_build": True,
            "forced": False,
            "previous_attempted": False,
        }
        evidence_path = self.temporary_path("upstream-resolution.json")
        evidence_path.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(ValueError, "upstream compare evidence"):
            self.load(source_lock=path, upstream_evidence=evidence_path)

    def test_image_source_gate_rejects_a_b_claim_without_resolved_b_source(self):
        policy = preservation.read_json(self.baseline / "policy.json")
        lock = {
            "wrapper_commit": policy["wrapper_commit"],
            "upstream_commit": preservation.B_SOURCE_COMMIT,
            "wrapper_tree_hash": "a" * 64,
            "build_fingerprint": "b" * 64,
            "sources": [{
                "name": "unrelated/source",
                "url": "https://github.com/unrelated/source.git",
                "commit": "c" * 40,
                "tree_hash": "d" * 64,
            }],
        }
        path = self.temporary_path("resolved-source-lock.json")
        path.write_text(json.dumps(lock))
        gate = preservation.Gate("image", self.baseline)
        self.assertFalse(gate.source_lock(path))
        self.assertTrue(any(error["category"] == "source_lock" for error in gate.errors))

    def test_review_bytes_are_hash_pinned(self):
        data = json.loads(self.review.read_text())
        data["title"] += " altered"
        path = self.temporary_path("review.json")
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "review digest"):
            self.load(review=path)

    def test_lucky_world_omission_approval_is_bound_to_exact_root_delta(self):
        prior = json.loads((CI / "reviewed-differences/40118211453-world.json").read_text())
        old_world = prior["candidate_world"]
        removed_roots = {"lucky", "luci-app-lucky", "luci-i18n-lucky-zh-cn"}
        current_world = "".join(
            line for line in old_world.splitlines(keepends=True)
            if line.strip().split("=", 1)[0] not in removed_roots
        )

        self.assertEqual(hashlib.sha256(old_world.encode()).hexdigest(),
                         "a08d0b1eae1da43dc707dddc3635a228200c51e153aa54002bebae4712b80e64")
        self.assertEqual(hashlib.sha256(current_world.encode()).hexdigest(),
                         "2bbd7462e0a5bdc03f606afbbf0475f6694f9569e87dad7eed1efe85dc398b88")
        self.assertEqual(set(old_world.splitlines()) - set(current_world.splitlines()), removed_roots)
        self.assertEqual(set(current_world.splitlines()) - set(old_world.splitlines()), set())

        original_world = next(
            item for item in json.loads((self.baseline / "full-rootfs-manifest.json").read_text())["files"]
            if item["path"] == "/etc/apk/world"
        )
        difference = {
            "category": "file_sha256",
            "subject": "/etc/apk/world",
            "expected": original_world["sha256"],
            "actual": hashlib.sha256(current_world.encode()).hexdigest(),
            "reason": "world roots changed; package closure checked but root selection needs explicit review",
        }
        identifier = preservation.digest(preservation.canonical(difference))
        approvals = json.loads((CI / "approved-differences.json").read_text())["approved"]
        matching = [item for item in approvals if item["id"] == identifier]
        self.assertEqual(identifier, "da06c767088a9e921960526cd699392e28b23f47046f8728a4903fd522e06ac6")
        self.assertEqual(len(matching), 1)
        self.assertIn("40347118325", matching[0]["reason"])
        evidence = json.loads((CI / "reviewed-differences/40347118325-world.json").read_text())
        self.assertEqual(evidence["world_delta"]["difference_id"], identifier)
        self.assertEqual(sorted(evidence["world_delta"]["removed_roots"]), sorted(removed_roots))
        self.assertEqual(evidence["world_delta"]["added_roots"], [])
        self.assertEqual(evidence["source_run"]["evidence_artifact_digest"],
                         "sha256:6c0ec07b006d646f2579d75cd53e98c0e3a6f85c6f49163f1a58dbf1b930b7b1")

    def test_package_omission_allows_only_the_exact_absent_set(self):
        review = self.load()
        manifest = json.loads((self.baseline / "original-package-manifest.json").read_text())
        original = {item["name"]: item for item in manifest}
        actual = dict(original)
        for item in review["baseline_packages"]:
            actual.pop(item["name"])

        absent = preservation.reviewed_b_optional_package_absences(review, original, actual)
        self.assertEqual(absent, set(preservation.B_OPTIONAL_PACKAGE_NAMES))
        self.assertNotIn("luci-app-openclash", absent)

        actual.pop("luci-app-openclash")
        with self.assertRaisesRegex(ValueError, "unreviewed missing baseline package"):
            preservation.reviewed_b_optional_package_absences(review, original, actual)
        actual["luci-app-openclash"] = original["luci-app-openclash"]

        actual[review["baseline_packages"][0]["name"]] = original[review["baseline_packages"][0]["name"]]
        with self.assertRaisesRegex(ValueError, "must not be installed"):
            preservation.reviewed_b_optional_package_absences(review, original, actual)

    def test_package_versions_and_dependencies_must_match_baseline(self):
        review = self.load()
        manifest = json.loads((self.baseline / "original-package-manifest.json").read_text())
        original = {item["name"]: item for item in manifest}
        actual = dict(original)
        for item in review["baseline_packages"]:
            actual.pop(item["name"])
        original[review["baseline_packages"][0]["name"]]["version"] += ".tampered"
        with self.assertRaisesRegex(ValueError, "baseline package identity"):
            preservation.reviewed_b_optional_package_absences(review, original, actual)

    def test_file_omission_allows_only_exact_absent_paths(self):
        review = self.load()
        records = json.loads((self.baseline / "full-rootfs-manifest.json").read_text())["files"]
        baseline = {item["path"]: item for item in records}
        actual = dict(baseline)
        for item in review["removed_records"]:
            actual.pop(item["path"])

        absent = preservation.reviewed_b_optional_file_absences(review, baseline, actual)
        self.assertEqual(absent, {item["path"] for item in review["removed_records"]})

        path = review["removed_records"][0]["path"]
        actual[path] = baseline[path]
        with self.assertRaisesRegex(ValueError, "must be absent"):
            preservation.reviewed_b_optional_file_absences(review, baseline, actual)

    def test_non_reviewed_missing_file_is_never_added_to_the_omission_set(self):
        review = self.load()
        records = json.loads((self.baseline / "full-rootfs-manifest.json").read_text())["files"]
        baseline = {item["path"]: item for item in records}
        actual = dict(baseline)
        for item in review["removed_records"]:
            actual.pop(item["path"])
        actual.pop("/etc/passwd")

        with self.assertRaisesRegex(ValueError, "unreviewed missing rootfs path"):
            preservation.reviewed_b_optional_file_absences(review, baseline, actual)

    def test_store_feed_is_signed_trusted_and_compatible_with_s20p_arch(self):
        review = self.load()
        store = review["trusted_store_feed"]
        self.assertEqual(store["trusted_key_sha256"], preservation.B_LUCKY_STORE_KEY_SHA256)
        self.assertIn("aarch64_cortex-a53", store["archlist"])
        self.assertEqual(
            {item["name"] for item in store["packages"]},
            set(preservation.B_OPTIONAL_PACKAGE_NAMES),
        )
        self.assertTrue(all(item["signature"] == "verified" for item in store["indexes"]))
        self.assertIn("older", store["version_note"])

    def test_store_application_metadata_is_present_and_hash_bound(self):
        store = self.load()["trusted_store_feed"]
        application = store["application_metadata"]
        self.assertEqual(application["name"], "app-meta-lucky")
        self.assertEqual(application["version"], "2.19.5-r1")
        self.assertEqual(application["arch"], "noarch")
        self.assertEqual(application["file_size"], 14463)
        self.assertEqual(application["index_identity"],
                         "65b4212956bc38924b2e6d0674a885c0250479e5e8277f626290e84efd94c783")
        self.assertEqual(application["fetched_apk_sha256"],
                         "cceaaa304443f3ec1f15876d6ddc8e6f9eea78caa5edb14fea7349813fe4a0e0")
        self.assertEqual(set(application["depends"]), {
            "libc", "luci-app-lucky", "luci-i18n-lucky-zh-cn", "lucky",
        })

    def test_store_application_metadata_rejects_tampered_index_identity_or_size(self):
        for key, value in (("index_identity", "not-an-index-id"), ("file_size", 0)):
            with self.subTest(key=key):
                data = json.loads(self.review.read_text())
                data["trusted_store_feed"]["application_metadata"][key] = value
                raw = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
                path = self.temporary_path("review.json")
                path.write_text(raw)
                with patch.object(preservation, "B_OPTIONAL_PACKAGE_REVIEW_SHA256",
                                  hashlib.sha256(raw.encode()).hexdigest()):
                    with self.assertRaisesRegex(ValueError, "application metadata"):
                        self.load(review=path)

    def test_critical_lucky_paths_are_exactly_reviewed_but_other_critical_paths_are_not(self):
        review = self.load()
        critical = json.loads((self.baseline / "critical-files-manifest.json").read_text())["files"]
        critical_paths = {item["path"] for item in critical}
        reviewed_critical = {item["path"] for item in review["removed_records"]} & critical_paths
        self.assertEqual(len(reviewed_critical), 9)
        self.assertTrue(reviewed_critical)
        self.assertTrue(all(
            set(review["package_payload_path_owners"][path]) <= set(preservation.B_OPTIONAL_PACKAGE_NAMES) |
            {"lucky (generated service enable links)"}
            for path in reviewed_critical
        ))
        self.assertNotIn("/etc/passwd", {item["path"] for item in review["removed_records"]})

    def test_image_file_gate_accepts_exact_lucky_absence_including_critical_paths(self):
        review = self.load()
        baseline = json.loads((self.baseline / "full-rootfs-manifest.json").read_text())["files"]
        actual = {item["path"]: item for item in baseline}
        for item in review["removed_records"]:
            actual.pop(item["path"])
        root = self.temporary_path("rootfs")
        root.mkdir()
        gate = preservation.Gate("image", self.baseline)
        with patch.object(preservation, "walk_files", return_value=actual), \
             patch.object(gate, "security_removals", return_value={}):
            gate.files(root, True, {}, set(), optional_package_policy=review)
        report = gate.result()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["pending_review_count"], 0)
        self.assertEqual(
            {item["subject"] for item in report["differences"]
             if item["category"] == "removed_optional_file"},
            {item["path"] for item in review["removed_records"]},
        )

    def test_image_file_gate_rejects_an_unreviewed_missing_critical_path(self):
        review = self.load()
        baseline = json.loads((self.baseline / "full-rootfs-manifest.json").read_text())["files"]
        actual = {item["path"]: item for item in baseline}
        for item in review["removed_records"]:
            actual.pop(item["path"])
        critical = json.loads((self.baseline / "critical-files-manifest.json").read_text())["files"]
        # Pick a present critical file that is not in the reviewed Lucky set.
        other_critical = next(item["path"] for item in critical
                              if item["path"] in actual and
                              item["path"] not in {row["path"] for row in review["removed_records"]})
        actual.pop(other_critical)
        root = self.temporary_path("rootfs")
        root.mkdir()
        gate = preservation.Gate("image", self.baseline)
        with patch.object(preservation, "walk_files", return_value=actual), \
             patch.object(gate, "security_removals", return_value={}):
            with self.assertRaisesRegex(ValueError, "unreviewed missing rootfs path"):
                gate.files(root, True, {}, set(), optional_package_policy=review)

    def test_image_package_gate_allows_only_the_three_reviewed_absences(self):
        review = self.load()
        baseline = preservation.parse_packages(self.baseline / "original-package-manifest.json")
        selected = {name: baseline[name] for name in ("kernel", *preservation.B_OPTIONAL_PACKAGE_NAMES,
                                                       "base-files")}
        gate = preservation.Gate("image", self.baseline)
        gate.original = selected
        gate.policy["required_packages"] = []
        kernel = dict(selected["kernel"])
        kernel["version"] = "6.12.112~" + "b" * 32 + "-r1"
        actual = {"kernel": kernel, "base-files": selected["base-files"]}
        inventory = self.temporary_path("packages.json")
        inventory.write_text(json.dumps(list(actual.values())))
        root = self.temporary_path("rootfs")
        root.mkdir()
        with patch.object(preservation, "parse_packages", side_effect=[actual, actual]):
            gate.packages(root, inventory, optional_package_policy=review)
        report = gate.result()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["checks"]["packages"]["reviewed_optional_absent"],
                         sorted(preservation.B_OPTIONAL_PACKAGE_NAMES))
        self.assertNotIn("base-files", report["checks"]["packages"]["reviewed_optional_absent"])

    def test_image_package_gate_rejects_an_unreviewed_missing_package(self):
        review = self.load()
        baseline = preservation.parse_packages(self.baseline / "original-package-manifest.json")
        selected = {name: baseline[name] for name in ("kernel", *preservation.B_OPTIONAL_PACKAGE_NAMES,
                                                       "base-files")}
        gate = preservation.Gate("image", self.baseline)
        gate.original = selected
        gate.policy["required_packages"] = []
        kernel = dict(selected["kernel"])
        kernel["version"] = "6.12.112~" + "b" * 32 + "-r1"
        actual = {"kernel": kernel}
        inventory = self.temporary_path("packages.json")
        inventory.write_text(json.dumps(list(actual.values())))
        root = self.temporary_path("rootfs")
        root.mkdir()
        with patch.object(preservation, "parse_packages", side_effect=[actual, actual]):
            gate.packages(root, inventory, optional_package_policy=review)
        self.assertTrue(any(error["category"] == "optional_package_omission"
                            for error in gate.errors))

    def test_store_feed_must_not_require_unavailable_dependencies(self):
        data = json.loads(self.review.read_text())
        data["trusted_store_feed"]["packages"][0]["depends"] = ["unknown-kernel-module"]
        path = self.temporary_path("review.json")
        raw = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        path.write_text(raw)
        expected = hashlib.sha256(raw.encode()).hexdigest()
        with patch.object(preservation, "B_OPTIONAL_PACKAGE_REVIEW_SHA256", expected):
            with self.assertRaisesRegex(ValueError, "store package dependency is not in B baseline"):
                self.load(review=path)

    def test_b_config_requires_lucky_selectors_disabled(self):
        gate = preservation.Gate("config", self.baseline)
        gate.config(self.config, source_lock=self.source_lock)
        symbols = {"CONFIG_PACKAGE_" + name for name in preservation.B_OPTIONAL_PACKAGE_NAMES}
        self.assertEqual(gate.checks["config"]["b_optional_packages"],
                         list(preservation.B_OPTIONAL_PACKAGE_NAMES))
        self.assertFalse(any(
            error["category"] == "config_symbol" and error["subject"] in symbols
            for error in gate.errors
        ))

    def test_b_config_rejects_any_lucky_selector_enabled(self):
        original = self.config.read_text()
        modified = re.sub(r"(?m)^CONFIG_PACKAGE_luci-app-lucky=n$",
                          "CONFIG_PACKAGE_luci-app-lucky=y", original)
        self.assertNotEqual(modified, original)
        config = self.temporary_path(".config")
        config.write_text(modified)
        gate = preservation.Gate("config", self.baseline)
        gate.config(config, source_lock=self.source_lock)
        self.assertTrue(any(
            error["category"] == "config_symbol" and
            error["subject"] == "CONFIG_PACKAGE_luci-app-lucky" and
            error["expected"] == "n"
            for error in gate.errors
        ))

    def test_b_download_uses_normal_selected_packages_without_check_all(self):
        values = preservation.parse_config(self.config)
        for name in preservation.B_OPTIONAL_PACKAGE_NAMES:
            self.assertEqual(values.get("CONFIG_PACKAGE_" + name), "n")
        build = (REPO / "build.sh").read_text()
        start = build.index("preflight_download_sources()")
        end = build.index("# 读取设备元信息", start)
        download_function = build[start:end]
        self.assertIn("unset CHECK_ALL", download_function)
        self.assertRegex(download_function, r"make download -j")
        self.assertNotRegex(download_function, r"make\s+download\s+CHECK_ALL")

    def test_b_download_rejects_all_check_all_makeflag_assignment_forms(self):
        build = (REPO / "build.sh").read_text()
        function = build[build.index("has_check_all_makeflag()"):build.index("preflight_download_sources()")]
        script = function + "\nfor value in 'CHECK_ALL=y' 'CHECK_ALL:=y' 'CHECK_ALL+=y' 'CHECK_ALL?=y' 'CHECK_ALL'; do MAKEFLAGS=\"$value\"; has_check_all_makeflag || exit 1; done\n"
        subprocess.run(["bash", "-c", script], check=True)

    def test_a_config_does_not_receive_b_lucky_omission(self):
        request = json.loads(self.source_lock.read_text())
        main = [item for item in request["sources"] if item.get("role") == "main-source"]
        main[0]["commit"] = "03eb30617955519e35845838c6255ebe687eb717"
        source = self.temporary_path("source-lock.json")
        source.write_text(json.dumps(request))
        gate = preservation.Gate("config", self.baseline)
        gate.config(self.config, source_lock=source)
        self.assertIsNone(gate.checks["config"].get("b_optional_packages"))
        self.assertTrue(any(
            error["category"] == "config_symbol" and
            error["subject"] == "CONFIG_PACKAGE_luci-app-lucky"
            for error in gate.errors
        ))


if __name__ == "__main__":
    unittest.main()
