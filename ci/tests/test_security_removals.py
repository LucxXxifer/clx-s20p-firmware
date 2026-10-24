"""Fixed-source CA removal guards, using the immutable real ROM baseline."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


CI = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("security_preservation", CI / "preservation.py")
preservation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preservation)
REVIEW = CI / "reviewed-security-removals/chasey-9f397975.json"
FIXTURES = Path(__file__).parent / "fixtures/security-removals"


class SecurityRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "root"
        self.trust = self.root / "etc/ssl/certs"
        self.trust.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "B-ca-bundle.crt", self.trust / "ca-certificates.crt")
        (self.root / "etc/ssl/cert.pem").symlink_to("/etc/ssl/certs/ca-certificates.crt")
        self.review = json.loads(REVIEW.read_text())
        self.prepared = [{k: v for k, v in row.items() if k != "evidence_file"}
                         for row in self.review["source"]["prepared_files"]]
        self.wrapper = [{"path": "ci/build.sh", "mode": "0o755", "size": 1,
                         "sha256": "f" * 64}]
        self.requested = json.loads((CI / "sources.lock.json").read_text())
        self.packages = {name: {"name": name, "version": version}
                         for name, version in self.review["required_packages"].items()}
        self.path = self.base / "source-lock.json"
        self.lock = {
            "wrapper_commit": json.loads((CI / "baseline/policy.json").read_text())["wrapper_commit"],
            "upstream_commit": self.review["source"]["commit"],
            "sources": [{"name": "github.com/chasey-dev/immortalwrt-mt798x-rebase",
                         "url": self.review["source"]["repository"],
                         "commit": self.review["source"]["commit"], "tree_hash": "a" * 64}],
            "source_lock": self.requested,
        }
        self.write_provenance()

    def write_provenance(self):
        # Match provenance.py's identities, including its default JSON spaces.
        for filename, data in (("prepared-source-files.json", self.prepared),
                               ("wrapper-source-files.json", self.wrapper),
                               ("requested-sources.lock.json", self.requested)):
            (self.base / filename).write_text(json.dumps(data, indent=2) + "\n")
        self.lock.update(
            source_lock_sha256=preservation.digest((self.base / "requested-sources.lock.json").read_bytes()),
            prepared_tree_hash=preservation.digest(json.dumps(self.prepared, sort_keys=True).encode()),
            wrapper_tree_hash=preservation.digest(json.dumps(self.wrapper, sort_keys=True).encode()),
            build_fingerprint=preservation.digest(json.dumps({"prepared": self.prepared,
                "wrapper": self.wrapper, "lock": self.requested}, sort_keys=True).encode()))
        self.path.write_text(json.dumps(self.lock, indent=2) + "\n")

    def gate(self, approval=None):
        return preservation.Gate("image", CI / "baseline", approval)

    def evaluate(self, gate=None):
        gate = gate or self.gate()
        self.assertTrue(callable(getattr(gate, "security_removals", None)),
                        "Missing fixed-source CA security-removal gate")
        provenance_ok = gate.source_lock(self.path)
        baseline = preservation.file_index(json.loads((CI / "baseline/full-rootfs-manifest.json").read_text())["files"])
        allowed = gate.security_removals(self.root, provenance_ok, self.packages, baseline)
        return gate, allowed

    def assert_rejected(self):
        gate, allowed = self.evaluate()
        self.assertFalse(allowed)
        self.assertEqual(gate.result()["status"], "fail", gate.result())
        self.assertFalse(any(d["category"] == "security_removal" for d in gate.differences))
        return gate

    def test_fixed_b_two_absent_records_require_exact_approval(self):
        gate, allowed = self.evaluate()
        self.assertEqual(gate.errors, [])
        self.assertEqual(set(allowed), {r["path"] for r in self.review["removed_records"]})
        differences = [d for d in gate.differences if d["category"] == "security_removal"]
        self.assertEqual(len(differences), 2)
        self.assertEqual(gate.result()["status"], "review_required")
        self.assertTrue(all(d["disposition"] == "review_required" for d in differences))
        approval = self.base / "approval.json"
        approval.write_text(json.dumps({"approved": [{"id": d["id"], "reason": "independent exact review"}
                                                    for d in differences]}))
        approved, _ = self.evaluate(self.gate(approval))
        self.assertEqual(approved.result()["status"], "pass", approved.result())

    def test_only_two_missing_paths_can_be_removed(self):
        gate, _ = self.evaluate()
        gate.files(self.root, True, self.packages, set())
        errors = {e["subject"] for e in gate.errors if e["category"] == "missing_file"}
        self.assertIn("/etc/ssl/certs/COMODO_ECC_Certification_Authority.crt", errors)
        self.assertIn("/etc/ssl/certs/COMODO_RSA_Certification_Authority.crt", errors)
        self.assertIn("/etc/ssl/certs/emSign_Root_CA_-_G1.crt", errors)
        self.assertTrue(errors.isdisjoint(r["path"] for r in self.review["removed_records"]))

    def test_all_three_source_identities_are_required(self):
        for location in ("top", "actual", "embedded", "repository", "embedded-repository", "duplicate-main", "duplicate-actual"):
            with self.subTest(location=location):
                original = copy.deepcopy((self.lock, self.requested))
                if location == "top":
                    self.lock["upstream_commit"] = "0" * 40
                elif location == "actual":
                    self.lock["sources"][0]["commit"] = "0" * 40
                elif location == "repository":
                    self.lock["sources"][0]["url"] += "-wrong"
                elif location == "duplicate-actual":
                    row = copy.deepcopy(self.lock["sources"][0])
                    row["url"] += ".git"
                    row["name"] += "-duplicate"
                    self.lock["sources"].append(row)
                else:
                    row = next(r for r in self.requested["sources"] if r.get("role") == "main-source")
                    if location == "embedded":
                        row["commit"] = "0" * 40
                    elif location == "embedded-repository":
                        row["url"] += "-wrong"
                    else:
                        self.requested["sources"].append(copy.deepcopy(row))
                self.write_provenance()
                self.assert_rejected()
                self.lock, self.requested = original
                self.lock["source_lock"] = self.requested

    def test_prepared_subtree_is_exact_not_a_path_whitelist(self):
        changes = ("missing-old", "missing-new", "changed-patch", "recipe", "extra", "symlink")
        for change in changes:
            with self.subTest(change=change):
                original = copy.deepcopy(self.prepared)
                if change == "missing-old":
                    del self.prepared[1]
                elif change == "missing-new":
                    del self.prepared[2]
                elif change == "extra":
                    self.prepared.append({"path": "package/system/ca-certificates/patches/0003-extra.patch",
                                          "mode": "0o644", "size": 1, "sha256": "c" * 64})
                elif change == "symlink":
                    self.prepared[2] = {"path": self.prepared[2]["path"], "mode": "0o777", "target": "elsewhere"}
                else:
                    self.prepared[0 if change == "recipe" else 2]["sha256"] = "c" * 64
                self.write_provenance()
                self.assert_rejected()
                self.prepared = original

    def test_impossible_source_ancestor_is_rejected(self):
        self.prepared.append({"path": "package/system", "mode": "0o777", "target": "other-tree"})
        self.write_provenance()
        self.assert_rejected()

    def test_intervening_sibling_cannot_hide_impossible_source_ancestor(self):
        self.prepared.extend([{"path": "package/system", "mode": "0o777", "target": "other-tree"},
                              {"path": "package/system-bridge", "mode": "0o644", "size": 1, "sha256": "a" * 64}])
        self.write_provenance()
        self.assert_rejected()

    def test_duplicate_json_keys_are_rejected(self):
        path = self.base / "prepared-source-files.json"
        path.write_text(path.read_text().replace('"mode": "0o644"', '"mode": "0o755", "mode": "0o644"', 1))
        self.assert_rejected()

    def test_provenance_integrity_rejects_unbound_or_malformed_manifests(self):
        for case in ("missing", "symlink-file", "wrong-tree", "compact-tree", "wrong-wrapper", "wrong-fingerprint",
                     "wrong-requested-hash", "wrong-requested", "duplicate-path", "dotdot", "absolute", "noncanonical"):
            with self.subTest(case=case):
                original = copy.deepcopy((self.lock, self.prepared))
                self.write_provenance()
                prepared_path = self.base / "prepared-source-files.json"
                if case == "missing":
                    prepared_path.unlink()
                elif case == "symlink-file":
                    prepared_path.rename(self.base / "elsewhere.json")
                    prepared_path.symlink_to("elsewhere.json")
                elif case == "wrong-tree":
                    self.lock["prepared_tree_hash"] = "0" * 64
                elif case == "compact-tree":
                    self.lock["prepared_tree_hash"] = preservation.digest(preservation.canonical(self.prepared))
                elif case == "wrong-wrapper":
                    self.lock["wrapper_tree_hash"] = "0" * 64
                elif case == "wrong-fingerprint":
                    self.lock["build_fingerprint"] = "0" * 64
                elif case == "wrong-requested-hash":
                    self.lock["source_lock_sha256"] = "0" * 64
                elif case == "wrong-requested":
                    (self.base / "requested-sources.lock.json").write_text("{}\n")
                else:
                    if case == "duplicate-path":
                        self.prepared.append(copy.deepcopy(self.prepared[0]))
                    else:
                        self.prepared[0]["path"] = {"dotdot": "a/../b", "absolute": "/a", "noncanonical": "a//b"}[case]
                    self.write_provenance()
                self.path.write_text(json.dumps(self.lock))
                self.assert_rejected()
                if prepared_path.is_symlink():
                    prepared_path.unlink()
                self.lock, self.prepared = original
                self.lock["source_lock"] = self.requested

    def test_review_and_original_baseline_cannot_expand_scope(self):
        review_path = self.base / "review.json"
        review = copy.deepcopy(self.review)
        review["removed_records"].append({"path": "/third-path"})
        review_path.write_text(json.dumps(review))
        with patch.object(preservation, "SECURITY_REMOVAL_REVIEW", review_path):
            self.assert_rejected()
        for field, value in (("sha256", "0" * 64), ("target", "elsewhere"), ("mode", "0777"),
                             ("uid", 1), ("gid", 1), ("size", 1)):
            with self.subTest(field=field):
                gate = self.gate()
                gate.source_lock(self.path)
                records = preservation.file_index(json.loads((CI / "baseline/full-rootfs-manifest.json").read_text())["files"])
                records[self.review["removed_records"][0]["path"]][field] = value
                allowed = gate.security_removals(self.root, True, self.packages, records)
                self.assertFalse(allowed)
                self.assertTrue(gate.errors)

    def test_rechecks_exact_manifest_digest_even_if_checksum_file_is_rewritten(self):
        baseline = self.base / "baseline"
        shutil.copytree(CI / "baseline", baseline)
        manifest = baseline / "full-rootfs-manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        hashes = baseline / "baseline-sha256.json"
        checksums = json.loads(hashes.read_text())
        checksums[manifest.name] = preservation.digest(manifest.read_bytes())
        hashes.write_text(json.dumps(checksums))
        gate = preservation.Gate("image", baseline)
        self.assertEqual(gate.errors, [])
        gate.source_lock(self.path)
        records = preservation.file_index(json.loads(manifest.read_text())["files"])
        self.assertFalse(gate.security_removals(self.root, True, self.packages, records))
        self.assertEqual(gate.result()["status"], "fail")

    def test_revoked_paths_must_remain_absent_for_every_inode_type(self):
        for record in self.review["removed_records"]:
            for kind in ("original", "changed", "directory", "dangling-link", "fifo"):
                with self.subTest(path=record["path"], kind=kind):
                    path = self.root / record["path"].lstrip("/")
                    if kind == "directory":
                        path.mkdir()
                    elif kind == "dangling-link":
                        path.symlink_to("absent")
                    elif kind == "fifo":
                        os.mkfifo(path)
                    else:
                        path.write_bytes((FIXTURES / "original-emSign-G3.crt").read_bytes()
                                         if kind == "original" else b"different")
                    self.assert_rejected()
                    path.rmdir() if kind == "directory" else path.unlink()

    def test_only_exact_b_bundle_alias_and_package_versions_are_eligible(self):
        bundle = self.trust / "ca-certificates.crt"
        original = bundle.read_bytes()
        for content in (original + b"\n", original[:-1], original + (FIXTURES / "original-emSign-G3.crt").read_bytes()):
            bundle.write_bytes(content)
            self.assert_rejected()
        bundle.write_bytes(original)
        bundle.rename(self.trust / "other.crt")
        bundle.symlink_to("other.crt")
        self.assert_rejected()
        bundle.unlink()
        (self.trust / "other.crt").rename(bundle)
        alias = self.root / "etc/ssl/cert.pem"
        alias.unlink()
        alias.symlink_to("certs/ca-certificates.crt")
        self.assert_rejected()
        alias.unlink()
        alias.symlink_to("/etc/ssl/certs/ca-certificates.crt")
        for name in self.packages:
            self.packages[name]["version"] = "20260816-r1"
            self.assert_rejected()
            self.packages[name]["version"] = "20260816-r2"

    def test_revoked_der_cannot_be_renamed_rewrapped_or_bundled(self):
        original = (FIXTURES / "original-emSign-G3.crt").read_bytes()
        import base64
        import re
        body = re.search(rb"-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----", original, re.S)[1]
        body = re.sub(rb"\s+", b"", body)
        rewrapped = b"-----BEGIN CERTIFICATE-----\n" + b"\n".join(body[i:i+17] for i in range(0, len(body), 17)) + b"\n-----END CERTIFICATE-----\n"
        for content in (original, rewrapped, b"other PEM bundle\n" + rewrapped, base64.b64decode(body)):
            path = self.trust / "renamed"
            path.write_bytes(content)
            self.assert_rejected()
            path.unlink()
        outside = self.root / "elsewhere.crt"
        outside.write_bytes(original)
        (self.trust / "external.0").symlink_to("/elsewhere.crt")
        self.assert_rejected()

    def test_pem_certificate_must_consume_exact_der_without_trailing_data(self):
        import base64
        import re
        pem = (FIXTURES / "original-emSign-G3.crt").read_bytes()
        body = re.search(rb"-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----", pem, re.S)[1]
        der = base64.b64decode(re.sub(rb"\s+", b"", body))
        for trailer in (b"\0", b"junk", b"\x30\0"):
            with self.subTest(trailer=trailer):
                path = self.trust / "renamed-with-trailer.crt"
                path.write_bytes(b"-----BEGIN CERTIFICATE-----\n" + base64.b64encode(der + trailer) +
                                 b"\n-----END CERTIFICATE-----\n")
                self.assert_rejected()
                path.unlink()

    def test_a_policy_never_receives_b_exception(self):
        gate = self.gate()
        gate.policy["upstream_commit"] = "03eb30617955519e35845838c6255ebe687eb717"
        gate.source_lock(self.path)
        baseline = preservation.file_index(json.loads((CI / "baseline/full-rootfs-manifest.json").read_text())["files"])
        self.assertEqual(gate.security_removals(self.root, False, self.packages, baseline), {})
        self.assertFalse(any(d["category"] == "security_removal" for d in gate.differences))


if __name__ == "__main__":
    unittest.main()
