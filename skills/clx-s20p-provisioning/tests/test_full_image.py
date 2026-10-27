import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_restore_image.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("backup_restore_image", SCRIPT)
BACKUP_TOOL = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(BACKUP_TOOL)


class BackupRestoreImageTests(unittest.TestCase):
    def run_sysfs_preflight(self, fixture, card_link=True, missing_health=False, ambiguous=False, expected_returncode=0):
        card = fixture / "sys/devices/platform/mmc_host/mmc0/mmc0:0001"
        (card / "block/mmcblk0").mkdir(parents=True)
        (card / "cid").write_text("00112233445566778899aabbccddeeff\n")
        if not missing_health:
            (card / "life_time").write_text("0x01 0x01\n")
            (card / "pre_eol_info").write_text("0x01\n")
        for block, size in (("mmcblk0", 241696768), ("mmcblk0boot0", 8192), ("mmcblk0boot1", 8192)):
            block_dir = fixture / "sys/class/block" / block
            block_dir.mkdir(parents=True)
            (block_dir / "size").write_text(str(size) + "\n")
        (fixture / "sys/class/block/mmcblk0/queue").mkdir()
        (fixture / "sys/class/block/mmcblk0/queue/logical_block_size").write_text("512\n")
        if card_link:
            (fixture / "sys/class/block/mmcblk0/device").symlink_to(card)
        bus = fixture / "sys/bus/mmc/devices"
        bus.mkdir(parents=True)
        (bus / "mmc0:0001").symlink_to(card)
        other_card = bus / "mmc0:0000"
        (other_card / "block/mmcblk1").mkdir(parents=True)
        (other_card / "cid").write_text("NOT_THE_TARGET_CARD\n")
        if ambiguous:
            (other_card / "block/mmcblk0").mkdir()
        tree = fixture / "sys/firmware/devicetree/base"
        tree.mkdir(parents=True)
        (tree / "model").write_bytes(b"CLX-S20P\0")
        (tree / "compatible").write_bytes(b"mediatek,mt7986a-clx-s20p\0")
        proc = fixture / "proc/sys/kernel"
        proc.mkdir(parents=True)
        (proc / "hostname").write_text("S20P\n")
        (fixture / "proc/partitions").write_text("")
        command = BACKUP_TOOL.PREFLIGHT_COMMAND.replace("/sys/", str(fixture / "sys") + "/")
        command = command.replace("/proc/", str(fixture / "proc") + "/")
        result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True)
        self.assertEqual(result.returncode, expected_returncode, result.stderr)
        return BACKUP_TOOL.parse_metadata(result.stdout), card

    def test_reads_health_from_the_mmcblk0_card_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, card = self.run_sysfs_preflight(Path(temporary))
        self.assertEqual(metadata.get("MMC_CID"), "00112233445566778899aabbccddeeff")
        self.assertEqual(metadata.get("MMC_LIFE_TIME"), "0x01 0x01")
        self.assertEqual(metadata.get("MMC_PRE_EOL_INFO"), "0x01")
        self.assertEqual(metadata.get("MMC_DEVICE_PATH"), str(card.resolve()))

    def test_fallback_selects_the_card_that_owns_mmcblk0(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, _ = self.run_sysfs_preflight(Path(temporary), card_link=False)
        self.assertEqual(metadata.get("MMC_CID"), "00112233445566778899aabbccddeeff")

    def test_missing_health_attributes_are_recorded_as_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, _ = self.run_sysfs_preflight(Path(temporary), missing_health=True)
        self.assertEqual(metadata.get("MMC_CID"), "00112233445566778899aabbccddeeff")
        self.assertEqual(metadata.get("MMC_LIFE_TIME"), "UNAVAILABLE:ATTR_ABSENT")
        self.assertEqual(metadata.get("MMC_PRE_EOL_INFO"), "UNAVAILABLE:ATTR_ABSENT")

    def test_ambiguous_card_fallback_stops_without_choosing_a_card(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, _ = self.run_sysfs_preflight(
                Path(temporary), card_link=False, ambiguous=True, expected_returncode=22
            )
        self.assertEqual(metadata.get("MMC_DEVICE_PATH"), "UNAVAILABLE:AMBIGUOUS_CARD")
        self.assertNotIn("MMC_CID", metadata)

    def test_full_clone_requires_explicit_opt_in_before_ssh_or_directory_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "backups"
            with patch.object(BACKUP_TOOL, "run_preflight", side_effect=RuntimeError("unexpected SSH preflight")) as preflight, redirect_stderr(io.StringIO()) as stderr:
                code = BACKUP_TOOL.main([
                    "--host", "192.168.8.1", "--serial-suffix", "A1B2",
                    "--destination-root", str(destination),
                ])
            self.assertEqual(code, 2)
            preflight.assert_not_called()
            self.assertFalse(destination.exists())
            self.assertIn("--full-image", stderr.getvalue())

    def test_preflight_reads_hostname_from_procfs(self):
        self.assertIn("cat /proc/sys/kernel/hostname", BACKUP_TOOL.PREFLIGHT_COMMAND)

    def test_preflight_formats_size_keys_for_metadata_parser(self):
        self.assertIn("mmcblk0) block_key=MMCBLK0", BACKUP_TOOL.PREFLIGHT_COMMAND)
        self.assertIn("mmcblk0boot0) block_key=MMCBLK0BOOT0", BACKUP_TOOL.PREFLIGHT_COMMAND)
        self.assertIn("mmcblk0boot1) block_key=MMCBLK0BOOT1", BACKUP_TOOL.PREFLIGHT_COMMAND)

    def test_ssh_reads_reuse_one_authenticated_control_connection(self):
        args = BACKUP_TOOL.ssh_base("192.168.8.1", "root")
        self.assertIn("ControlMaster=auto", args)
        self.assertIn("ControlPersist=600", args)
        self.assertTrue(any(arg.startswith("ControlPath=") for arg in args))

    def test_accepts_the_verified_generic_rfb_board_fingerprint(self):
        preflight = "\n".join(
            [
                "CAPTURE_PREFLIGHT_BEGIN",
                "UID=0",
                "HOSTNAME=S20P",
                "MODEL=MediaTek MT7986a RFB",
                "COMPATIBLE=mediatek,mt7986a-emmc-rfb",
                "SIZE_MMCBLK0_SECTORS=241696768",
                "SIZE_MMCBLK0BOOT0_SECTORS=8192",
                "SIZE_MMCBLK0BOOT1_SECTORS=8192",
                "CAPTURE_PREFLIGHT_END",
            ]
        )
        result = subprocess.CompletedProcess([], 0, stdout=preflight, stderr="")
        with patch.object(BACKUP_TOOL.subprocess, "run", return_value=result):
            metadata, raw = BACKUP_TOOL.run_preflight("192.168.8.1", "root")
        self.assertEqual(metadata["HOSTNAME"], "S20P")
        self.assertEqual(metadata["SIZE_MMCBLK0_SECTORS"], "241696768")
        self.assertIn("CAPTURE_PREFLIGHT_END", raw)

    def test_rejects_rfb_board_fingerprint_mismatches(self):
        base = {
            "UID": "0",
            "HOSTNAME": "S20P",
            "MODEL": "MediaTek MT7986a RFB",
            "COMPATIBLE": "mediatek,mt7986a-emmc-rfb",
            "SIZE_MMCBLK0_SECTORS": "241696768",
            "SIZE_MMCBLK0BOOT0_SECTORS": "8192",
            "SIZE_MMCBLK0BOOT1_SECTORS": "8192",
        }
        for key, value in (
            ("HOSTNAME", "other"),
            ("COMPATIBLE", "mediatek,mt7986a-rfb"),
            ("SIZE_MMCBLK0_SECTORS", "0"),
            ("SIZE_MMCBLK0BOOT0_SECTORS", "unavailable"),
            ("SIZE_MMCBLK0BOOT1_SECTORS", "-1"),
        ):
            with self.subTest(key=key, value=value):
                candidate = dict(base, **{key: value})
                preflight = "CAPTURE_PREFLIGHT_BEGIN\n" + "\n".join(
                    f"{name}={item}" for name, item in candidate.items()
                ) + "\nCAPTURE_PREFLIGHT_END\n"
                result = subprocess.CompletedProcess([], 0, stdout=preflight, stderr="")
                with patch.object(BACKUP_TOOL.subprocess, "run", return_value=result):
                    with self.assertRaisesRegex(RuntimeError, "did not identify"):
                        BACKUP_TOOL.run_preflight("192.168.8.1", "root")

    def test_help_exposes_required_target_and_unique_serial_suffix(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--host", result.stdout)
        self.assertIn("--serial-suffix", result.stdout)
        self.assertIn("read-only", result.stdout.lower())

    def test_captures_raw_user_area_and_both_boot_regions_with_hashes(self):
        user_area = b"S20P" * 2048
        boot0 = b"B0" * 512
        boot1 = b"B1" * 512
        metadata = "\n".join(
            [
                "CAPTURE_PREFLIGHT_BEGIN",
                "UID=0",
                "MODEL=CLX-S20P",
                "COMPATIBLE=mediatek,mt7986a-clx-s20p",
                "SIZE_MMCBLK0_SECTORS=16",
                "SIZE_MMCBLK0BOOT0_SECTORS=2",
                "SIZE_MMCBLK0BOOT1_SECTORS=2",
                "MMC_CID=0123456789abcdef",
                "CAPTURE_PREFLIGHT_END",
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_ssh = fake_bin / "ssh"
            fake_ssh.write_text(
                "#!" + sys.executable + "\n"
                + "import os, sys\n"
                + "command = sys.argv[-1]\n"
                + "with open(os.environ['FAKE_SSH_LOG'], 'a') as log: log.write(command + '\\n')\n"
                + "if 'CAPTURE_PREFLIGHT' in command:\n"
                + "    sys.stdout.write(os.environ['FAKE_SSH_METADATA'])\n"
                + "elif command.startswith('dd if=/dev/mmcblk0boot0 '):\n"
                + "    sys.stdout.buffer.write(bytes.fromhex(os.environ['FAKE_SSH_BOOT0']))\n"
                + "elif command.startswith('dd if=/dev/mmcblk0boot1 '):\n"
                + "    sys.stdout.buffer.write(bytes.fromhex(os.environ['FAKE_SSH_BOOT1']))\n"
                + "elif command.startswith('dd if=/dev/mmcblk0 '):\n"
                + "    sys.stdout.buffer.write(bytes.fromhex(os.environ['FAKE_SSH_USER_AREA']))\n"
                + "elif command.startswith('sha256sum /dev/'):\n"
                + "    source = command.rsplit('/', 1)[-1]\n"
                + "    digest = {'mmcblk0': os.environ['FAKE_SSH_USER_SHA256'], 'mmcblk0boot0': os.environ['FAKE_SSH_BOOT0_SHA256'], 'mmcblk0boot1': os.environ['FAKE_SSH_BOOT1_SHA256']}[source]\n"
                + "    sys.stdout.write(digest + '  /dev/' + source + '\\n')\n"
                + "else:\n"
                + "    sys.stderr.write('unexpected remote command: ' + command)\n"
                + "    sys.exit(90)\n",
                encoding="utf-8",
            )
            fake_ssh.chmod(0o755)
            destination = root / "backups"
            log_path = root / "ssh.log"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": str(fake_bin) + os.pathsep + env["PATH"],
                    "FAKE_SSH_LOG": str(log_path),
                    "FAKE_SSH_METADATA": metadata,
                    "FAKE_SSH_USER_AREA": user_area.hex(),
                    "FAKE_SSH_BOOT0": boot0.hex(),
                    "FAKE_SSH_BOOT1": boot1.hex(),
                    "FAKE_SSH_USER_SHA256": hashlib.sha256(user_area).hexdigest(),
                    "FAKE_SSH_BOOT0_SHA256": hashlib.sha256(boot0).hexdigest(),
                    "FAKE_SSH_BOOT1_SHA256": hashlib.sha256(boot1).hexdigest(),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--full-image",
                    "--host",
                    "192.168.8.1",
                    "--serial-suffix",
                    "A1B2",
                    "--destination-root",
                    str(destination),
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            backup = destination / "clx-s20-A1B2"
            expected = {
                "mmcblk0.img": user_area,
                "mmcblk0boot0.img": boot0,
                "mmcblk0boot1.img": boot1,
            }
            for filename, content in expected.items():
                self.assertEqual((backup / filename).read_bytes(), content)
            manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["capture_status"], "complete")
            for filename, content in expected.items():
                self.assertEqual(manifest["artifacts"][filename]["bytes"], len(content))
                self.assertEqual(
                    manifest["artifacts"][filename]["sha256"],
                    hashlib.sha256(content).hexdigest(),
                )
                self.assertEqual(
                    manifest["artifacts"][filename]["local_readback_sha256"],
                    hashlib.sha256(content).hexdigest(),
                )
                self.assertEqual(
                    manifest["artifacts"][filename]["remote_sha256"],
                    hashlib.sha256(content).hexdigest(),
                )
            remote_commands = log_path.read_text(encoding="utf-8")
            self.assertNotIn("of=/dev/", remote_commands)
            self.assertIn("dd if=/dev/mmcblk0 ", remote_commands)

    def test_refuses_to_overwrite_an_existing_unit_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "backups"
            target = destination / "clx-s20-1234"
            target.mkdir(parents=True)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--full-image",
                    "--host",
                    "192.168.8.1",
                    "--serial-suffix",
                    "1234",
                    "--destination-root",
                    str(destination),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(target.is_dir())
            self.assertIn("already exists", (result.stdout + result.stderr).lower())

    def test_boot_backups_survive_failed_large_user_area_verification(self):
        payloads = {"mmcblk0": b"U" * 8192, "mmcblk0boot0": b"B0" * 512, "mmcblk0boot1": b"B1" * 512}
        metadata = {
            "SIZE_MMCBLK0_SECTORS": "16",
            "SIZE_MMCBLK0BOOT0_SECTORS": "2",
            "SIZE_MMCBLK0BOOT1_SECTORS": "2",
        }

        def capture(host, user, device, path, expected_bytes):
            content = payloads[device]
            path.write_bytes(content)
            return {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

        def remote_hash(host, user, device):
            return "0" * 64 if device == "mmcblk0" else hashlib.sha256(payloads[device]).hexdigest()

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "backups"
            with patch.object(BACKUP_TOOL, "run_preflight", return_value=(metadata, "fixture")), \
                 patch.object(BACKUP_TOOL, "capture_image", side_effect=capture), \
                 patch.object(BACKUP_TOOL, "hash_remote_block_device", side_effect=remote_hash), \
                 patch("sys.stdout", new=io.StringIO()), redirect_stderr(io.StringIO()):
                code = BACKUP_TOOL.main([
                    "--host", "192.168.8.1", "--serial-suffix", "A1B2", "--full-image",
                    "--destination-root", str(destination),
                ])
            self.assertEqual(code, 1)
            backup = destination / "clx-s20-A1B2"
            manifest = json.loads((backup / "manifest.json").read_text())
            self.assertEqual(manifest["capture_status"], "failed")
            for device in ("mmcblk0boot0", "mmcblk0boot1"):
                self.assertTrue((backup / f"{device}.img").is_file(), "Boot region must survive a later whole-user-area hash failure")
                self.assertEqual((backup / f"{device}.img").read_bytes(), payloads[device])
                artifact = manifest["artifacts"][f"{device}.img"]
                self.assertEqual(artifact["remote_sha256"], artifact["sha256"])


if __name__ == "__main__":
    unittest.main()
