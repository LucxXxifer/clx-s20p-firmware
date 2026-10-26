"""Fail-closed preparation of the B direct-download release assets."""

import hashlib
import importlib.util
import io
import json
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
        return release.prepare_release_assets(
            self.artifact, self.output, run_id="1234", run_attempt="2",
            build_commit=TEST_WRAPPER_COMMIT, **options,
        )

    def test_prepares_a_direct_bin_archive_checksums_and_install_guide(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        result = self.prepare()
        image = self.output / "s20p-B-25.12-9f397975-sysupgrade.bin"
        self.assertEqual(result["image"], image.name)
        self.assertEqual(image.read_bytes(), test_sysupgrade_image())
        self.assertEqual(result["archive"], archive.name)
        self.assertTrue((self.output / "SHA256SUMS").is_file())
        self.assertTrue((self.output / "archive.SHA256SUMS").is_file())
        guide = (self.output / "INSTALLATION.md").read_text()
        self.assertIn("1234", guide)
        self.assertIn(TEST_WRAPPER_COMMIT, guide)
        self.assertIn("not a stable release", guide.lower())
        self.assertTrue(release.verify_release_assets(
            self.output, run_id="1234", run_attempt="2", build_commit=TEST_WRAPPER_COMMIT,
        )["pass"])

    def test_rejects_a_bundle_from_another_wrapper_commit(self):
        release_fixture(self.artifact, commit="a" * 40)
        with self.assertRaisesRegex(ValueError, "wrapper commit"):
            self.prepare()

    def test_rejects_a_bundle_with_invalid_internal_checksum(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, tamper_checksum=True)
        with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
            self.prepare()

    def test_rejects_a_bundle_with_wrong_upstream_commit(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT, upstream_commit="a" * 40)
        with self.assertRaisesRegex(ValueError, "fixed B source"):
            self.prepare()

    def test_rejects_a_bundle_with_forged_resolved_main_source(self):
        archive = release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        rewrite_archive_json(archive, "provenance/source-lock.json", lambda data: data["sources"][0].update(commit="a" * 40))
        with self.assertRaisesRegex(ValueError, "resolved main-source"):
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
        image = self.output / "s20p-B-25.12-9f397975-sysupgrade.bin"
        image.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
            release.verify_release_assets(
                self.output, run_id="1234", run_attempt="2", build_commit=TEST_WRAPPER_COMMIT,
            )

    def test_verifier_rejects_an_archive_whose_rom_differs_from_the_direct_asset(self):
        release_fixture(self.artifact, commit=TEST_WRAPPER_COMMIT)
        self.prepare()
        image = self.output / "s20p-B-25.12-9f397975-sysupgrade.bin"
        image.write_bytes(b"a different but checksummed file")
        write_checksums(self.output, exclude={"SHA256SUMS"})
        with self.assertRaisesRegex(ValueError, "does not match the same-build archive"):
            release.verify_release_assets(
                self.output, run_id="1234", run_attempt="2", build_commit=TEST_WRAPPER_COMMIT,
            )


def release_fixture(artifact, commit, tamper_checksum=False, extra_image=False,
                    upstream_commit=None, preservation_status="pass", signed_feed_status="pass",
                    package_count_delta=0, include_lucky=False, unsafe_member=None,
                    unsafe_member_type=None, payload_mismatch=False, wrong_public_key_hash=False):
    """Build a same-shape archive with integrity and acceptance records."""
    packages = [
        {"name": "kernel", "version": TEST_KERNEL_ABI},
        {"name": "base-files", "version": "1~b25.12"},
    ]
    if include_lucky:
        packages.extend([
            {"name": "lucky", "version": "3.4.0-r3"},
            {"name": "luci-app-lucky", "version": "3.0.0-r26"},
            {"name": "luci-i18n-lucky-zh-cn", "version": "0"},
        ])
    actual_count = len(packages)
    key_hash = hashlib.sha256(TEST_PUBLIC_KEY).hexdigest()
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
            "checks": {"packages": {
                "original_count": 483, "actual_count": actual_count, "kernel": TEST_KERNEL_ABI,
                "kmods": 0, "luci_apps": 0,
                "reviewed_optional_absent": [] if include_lucky else sorted(release.B_OPTIONAL_PACKAGE_NAMES),
            }},
        }),
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
            "upstream_commit": upstream_commit or release.B_SOURCE_COMMIT,
            "wrapper_commit": release.WRAPPER_COMMIT,
            "wrapper_checkout_commit": commit,
            "sources": [{
                "role": "main-source", "url": release.B_SOURCE_URL,
                "ref": "25.12", "commit": upstream_commit or release.B_SOURCE_COMMIT,
                "tree_hash": "c" * 64,
            }],
            "source_lock_sha256": hashlib.sha256(json_bytes({"sources": [{
                "role": "main-source", "url": release.B_SOURCE_URL,
                "ref": "25.12", "commit": upstream_commit or release.B_SOURCE_COMMIT,
            }]})).hexdigest(),
            "source_lock": {"sources": [{
                "role": "main-source", "url": release.B_SOURCE_URL,
                "ref": "25.12", "commit": upstream_commit or release.B_SOURCE_COMMIT,
            }]},
        }),
        "provenance/config-gate.json": json_bytes({
            "status": "pass", "errors": [], "checks": {"config": {
                "b_optional_packages": list(release.B_OPTIONAL_PACKAGE_NAMES),
                "b_optional_package_omission": {
                    "upstream_commit": release.B_SOURCE_COMMIT,
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
            "checks": {"packages": {
                "original_count": 483, "actual_count": actual_count + package_count_delta,
                "kernel": TEST_KERNEL_ABI,
                "kmods": 0, "luci_apps": 0,
                "reviewed_optional_absent": sorted(release.B_OPTIONAL_PACKAGE_NAMES),
            }},
        })

    if extra_image:
        files["bin/targets/mediatek/filogic/clx_s20p-extra-sysupgrade.bin"] = b"second-image"
    build = artifact / "fixture"
    for name, data in files.items():
        path = build / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    requested_lock = {"sources": [{
        "role": "main-source", "url": release.B_SOURCE_URL,
        "ref": "25.12", "commit": upstream_commit or release.B_SOURCE_COMMIT,
    }]}
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
    temporary = archive.with_suffix(".rewritten.tar.gz")
    with tarfile.open(archive, "r:gz") as source, tarfile.open(temporary, "w:gz") as target:
        rewritten = {}
        for member in source.getmembers():
            member_name = Path(member.name).as_posix().removeprefix("./")
            if member_name == "SHA256SUMS":
                continue
            if member_name == name:
                data = json.load(source.extractfile(member))
                mutate(data)
                payload = json_bytes(data)
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
