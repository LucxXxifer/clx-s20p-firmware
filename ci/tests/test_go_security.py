"""Reject unpatched Go build metadata and missing linked x509 distrust data."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ci import go_security


def elf(version="go1.26.10", verify=True, hashes=True):
    header = b"\xff Go buildinf:" + bytes([8, 2]) + bytes(16)
    info = version.encode()
    data = b"\x7fELF" + bytes(252) + header + bytes([len(info)]) + info + b"\x00"
    if verify:
        data += b"crypto/x509.(*Certificate).Verify\x00"
    if hashes:
        data += b"\x00".join(h.encode() for h in go_security.DISTRUSTED_SPKI)
    return data


class GoSecurityTests(unittest.TestCase):
    def test_patched_both_series_and_later_patch_versions_pass(self):
        for version in ("go1.26.10", "go1.26.11", "go1.27.3", "go1.27.4"):
            with self.subTest(version=version):
                row = go_security.inspect_elf(elf(version), "/usr/bin/test")
                self.assertEqual(row["go_version"], version)
                self.assertEqual(row["distrusted_spki_count"], 8)

    def test_older_and_development_go_versions_fail_even_with_hash_strings(self):
        for version in ("go1.26.9", "go1.27.1", "go1.27.2", "go1.25.9", "devel go1.28", "go1.27rc1"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "unpatched|release"):
                go_security.inspect_elf(elf(version), "/usr/bin/test")

    def test_x509_verify_requires_all_eight_hashes(self):
        data = elf().replace(go_security.DISTRUSTED_SPKI[0].encode(), b"0" * 64)
        with self.assertRaisesRegex(ValueError, "distrust"):
            go_security.inspect_elf(data, "/usr/bin/test")

    def test_no_linked_verifier_does_not_require_dead_code_constants(self):
        row = go_security.inspect_elf(elf(verify=False, hashes=False), "/usr/sbin/runc")
        self.assertFalse(row["x509_verify_linked"])
        self.assertEqual(row["distrusted_spki_count"], 0)

    def test_plain_version_text_cannot_substitute_for_build_info(self):
        self.assertIsNone(go_security.inspect_elf(b"\x7fELFgo1.26.10", "/usr/bin/test"))
        with self.assertRaisesRegex(ValueError, "build info"):
            go_security.inspect_elf(b"\x7fELF" + bytes(252) + b"\xff Go buildinf:", "/usr/bin/test")

    def test_rootfs_requires_all_four_docker_binaries_and_binds_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "rootfs"
            payload = Path(temp) / "rootfs.squashfs"
            payload.write_bytes(b"test squashfs bytes")
            for name in go_security.REQUIRED_BINARIES:
                path = root / name.lstrip("/")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(elf(verify=not name.endswith("runc")))
            report = go_security.inspect_rootfs(root, payload)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["rootfs_sha256"], hashlib.sha256(payload.read_bytes()).hexdigest())
            go_security.verify_report(report, report["rootfs_sha256"])
            (root / "usr/bin/dockerd").unlink()
            with self.assertRaisesRegex(ValueError, "required"):
                go_security.inspect_rootfs(root, payload)

    def test_release_rejects_tampered_policy_payload_and_old_binary_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in go_security.REQUIRED_BINARIES:
                path = root / name.lstrip("/")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(elf())
            payload = root / "rootfs.squashfs"
            payload.write_bytes(b"rootfs")
            valid = go_security.inspect_rootfs(root, payload)
            for edit in (lambda d: d.update(policy_sha256="0" * 64),
                         lambda d: d.update(rootfs_sha256="0" * 64),
                         lambda d: d["binaries"][0].update(go_version="go1.26.9"),
                         lambda d: d["binaries"][0].update(distrusted_spki_count=7)):
                report = json.loads(json.dumps(valid))
                edit(report)
                with self.assertRaises(ValueError):
                    go_security.verify_report(report, valid["rootfs_sha256"])


if __name__ == "__main__":
    unittest.main()
