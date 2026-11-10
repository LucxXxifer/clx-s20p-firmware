"""Synthetic rescue tests; no serial hardware, router, or network is accessed."""
import base64
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import s20p_common as common
import s20p_fip as fip
import s20p_recovery as recovery
from test_workflow import FakeSSH, run_backup
import backup_restore_image as full_image


class RescueSSH(FakeSSH):
    def metadata(self):
        result = super().metadata()
        result["HOSTNAME"] = "(none)"
        return result

    def read(self, command, **kwargs):
        responses = {
            "uci -q get system.@system[0].hostname": b"S20P\n",
            "uci -c /rom/etc/config -q get system.@system[0].hostname": b"S20P\n",
            "test -f /tmp/failsafe && printf PRESENT": b"PRESENT",
        }
        if command in responses:
            return getattr(self, "overrides", {}).get(command, responses[command])
        return super().read(command, **kwargs)


class RescueIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ssh = RescueSSH()
        wire = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + b"T" * 32
        key = base64.b64encode(wire).decode()
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(wire).digest()).decode().rstrip("=")
        self.ssh.known_hosts = Path(self.temp.name) / "known_hosts"
        self.ssh.known_hosts.write_text("192.0.2.1 ssh-ed25519 " + key + "\n")
        self.ssh.stock_failsafe_evidence = {
            "schema_version": 1, "mode": "stock_failsafe", "host": "192.0.2.1",
            "physical_serial_suffix": "T123", "physical_identity_confirmed": True,
            "console_host_key_sha256": fingerprint, "ssh_host_key_sha256": fingerprint,
            "boot_id": self.ssh.boot_id,
            "unit": {"cid": self.ssh.cid, "sector_size": 512, "sectors": 241696768,
                     "boot0_sectors": 8, "boot1_sectors": 8},
        }

    def test_explicit_verified_failsafe_preserves_actual_runtime_hostname(self):
        report = common.probe(self.ssh, "T123")
        self.assertEqual(report["metadata"]["HOSTNAME"], "(none)")
        self.assertEqual(report["unit"]["cid"], "00112233445566778899aabbccddeeff")
        self.assertTrue(report["rescue_identity"]["verified"])

    def test_no_evidence_keeps_default_identity_rejection(self):
        del self.ssh.stock_failsafe_evidence
        with self.assertRaises(RuntimeError):
            common.probe(self.ssh, "T123")

    def test_named_model_requires_exact_s20p_and_reviewed_soc(self):
        for model, compatible in [("CLX-S20L", "clx,clx-s20l mediatek,mt7986a"),
                                  ("CLX-S20M", "clx,clx-s20m mediatek,mt7986a"),
                                  ("CLX-S20Pro", "clx,clx-s20pro mediatek,mt7986a"),
                                  ("CLX-S20P", "other,unknown-soc")]:
            metadata = dict(FakeSSH().metadata(), MODEL=model, COMPATIBLE=compatible, HOSTNAME="other")
            with self.subTest(model=model, compatible=compatible):
                with self.assertRaises(RuntimeError):
                    common.unit_identity(metadata, "T123")
                text = "CAPTURE_PREFLIGHT_BEGIN\n" + "\n".join(k + "=" + v for k, v in metadata.items()) + "\nCAPTURE_PREFLIGHT_END"
                response = subprocess.CompletedProcess([], 0, stdout=text, stderr="")
                with mock.patch.object(full_image.subprocess, "run", return_value=response):
                    with self.assertRaises(RuntimeError):
                        full_image.run_preflight("192.0.2.1", "root")
        for model, compatible in [("LUNZN_S20P V2.0", "clx,s20p mediatek,mt7986a"),
                                  ("CLX-S20P", "mediatek,mt7986a-clx-s20p")]:
            metadata = dict(FakeSSH().metadata(), MODEL=model, COMPATIBLE=compatible, HOSTNAME="other")
            self.assertEqual(common.unit_identity(metadata, "T123")["model"], model)

    def test_evidence_conflicts_reject_same_capacity_wrong_target(self):
        for key, value in [("host", "192.0.2.2"), ("physical_serial_suffix", "X999"),
                           ("physical_identity_confirmed", False), ("boot_id", "different"),
                           ("ssh_host_key_sha256", "SHA256:wrong")]:
            with self.subTest(key=key):
                original = self.ssh.stock_failsafe_evidence[key]
                self.ssh.stock_failsafe_evidence[key] = value
                with self.assertRaises(RuntimeError):
                    common.probe(self.ssh, "T123")
                self.ssh.stock_failsafe_evidence[key] = original
        self.ssh.stock_failsafe_evidence["unit"]["cid"] = "f" * 32
        with self.assertRaises(RuntimeError):
            common.probe(self.ssh, "T123")

    def test_rom_marker_and_trusted_key_are_all_required(self):
        for command in ["uci -q get system.@system[0].hostname",
                        "uci -c /rom/etc/config -q get system.@system[0].hostname",
                        "test -f /tmp/failsafe && printf PRESENT"]:
            self.ssh.overrides = {command: b"OTHER"}
            with self.subTest(command=command), self.assertRaises(RuntimeError):
                common.probe(self.ssh, "T123")
        self.ssh.overrides = {}
        self.ssh.known_hosts.write_text("192.0.2.9 ssh-ed25519 AAAA\n")
        with self.assertRaises(RuntimeError):
            common.probe(self.ssh, "T123")

    def test_failsafe_does_not_bypass_emmc_health(self):
        self.ssh.overrides = {}
        metadata = self.ssh.metadata()
        metadata["MMC_LIFE_TIME"] = "0x0b 0x01"
        with mock.patch.object(self.ssh, "metadata", return_value=metadata):
            result = common.probe(self.ssh, "T123")
        self.assertFalse(result["health"]["flash_gate_passed"])
        self.assertTrue(result["health"]["rescue_backup_allowed"])


class PartialRescueTests(unittest.TestCase):
    def create_rescue(self, root, ssh):
        observation = common.probe(ssh, "T123")
        observation["mounts"] = []
        args = SimpleNamespace(destination_root=root, serial_suffix="T123")
        self.assertEqual(recovery.rescue_small(args, ssh, observation), 0)
        return next(root.glob("clx-s20-T123/rescue-*/manifest.json"))

    def test_small_rescue_can_continue_to_full_backup_without_rewriting_history(self):
        ssh = FakeSSH()
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(recovery, "ensure_backup_volume"):
            root = Path(directory)
            rescued = self.create_rescue(root, ssh)
            original = rescued.read_bytes()
            code, _, manifest = run_backup(root, ssh)
            self.assertEqual(code, 0)
            self.assertTrue(manifest["all_required_artifacts_verified"])
            self.assertEqual(rescued.read_bytes(), original)
            self.assertEqual(Path(manifest["history"][0]["manifest"]).resolve(), rescued.resolve())
            self.assertEqual(len(ssh.captured), len(manifest["plan"]))
            self.assertIn("permanent_config_files", manifest["artifacts"])

    def test_small_rescue_transition_rejects_modified_factory_and_unknown_content(self):
        for corruption in ("factory", "live_factory", "claimed_complete", "unknown_directory"):
            ssh = FakeSSH()
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory, mock.patch.object(recovery, "ensure_backup_volume"):
                root = Path(directory)
                rescued = self.create_rescue(root, ssh)
                if corruption == "factory":
                    manifest = json.loads(rescued.read_text())
                    Path(manifest["artifacts"]["factory"]["path"]).write_bytes(b"changed")
                elif corruption == "live_factory":
                    ssh.regions["mmcblk0p2"] = b"changed"
                elif corruption == "claimed_complete":
                    manifest = json.loads(rescued.read_text())
                    manifest["all_required_artifacts_verified"] = True
                    rescued.write_text(json.dumps(manifest))
                else:
                    (root / "clx-s20-T123" / "unreviewed").mkdir()
                with self.assertRaises(RuntimeError):
                    run_backup(root, ssh)

    def test_unmounted_p6_allows_small_capture_but_never_fip_preflight(self):
        self.assertTrue(hasattr(recovery, "rescue_small"), "Small-region rescue entrypoint is missing")
        ssh = FakeSSH()
        observation = common.probe(ssh, "T123")
        observation["mounts"] = [{"source": "tmpfs", "target": "/tmp", "filesystem": "tmpfs"}]
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(destination_root=Path(directory), serial_suffix="T123")
            with mock.patch.object(recovery, "ensure_backup_volume"):
                self.assertEqual(recovery.rescue_small(args, ssh, observation), 0)
            manifest_path = next(Path(directory).glob("clx-s20-T123/rescue-*/manifest.json"))
            manifest = json.loads(manifest_path.read_text())
            self.assertTrue(manifest["selected_artifacts_verified"])
            self.assertFalse(manifest["all_required_artifacts_verified"])
            self.assertIn("factory", manifest["artifacts"])
            self.assertNotIn("rootfs", manifest["artifacts"])
            self.assertFalse(any("tar " in x or "if=/dev/mmcblk0p6" in x for x in ssh.commands))
            with self.assertRaises(RuntimeError):
                fip.preflight(SimpleNamespace(backup_manifest=manifest_path), ssh)


class UARTTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("s20p_uart"), "Receive-only UART helper is missing")
        return importlib.import_module("s20p_uart")

    def test_analyzer_separates_prompt_missing_identity_and_repeated_panics(self):
        uart = self.module()
        boot = ("[ 0.000] Linux version 5.4.0\n"
                "Press the [f] key and hit [enter] to enter failsafe mode\n"
                "[ 12.0] Error 2 opening /permanent_config/unique_id\n"
                "[ 12.1] Kernel panic - not syncing: very bad firmware\n")
        result = uart.analyze(boot + boot)
        self.assertEqual(result["kernel_start_count"], 2)
        self.assertEqual(result["missing_identity_then_panic_count"], 2)
        self.assertTrue(result["repeated_panic_evidence"])
        self.assertTrue(result["failsafe_prompt_seen"])
        self.assertFalse(result["failsafe_active_marker_seen"])
        self.assertFalse(result["flash_authorized"])

    def test_link_changes_alone_do_not_become_reboot_or_storage_failure(self):
        uart = self.module()
        result = uart.analyze("eth1: Link is Down\neth1: Link is Up - 2.5Gbps/Full\n")
        self.assertFalse(result["repeated_panic_evidence"])
        self.assertEqual(result["kernel_start_count"], 0)
        self.assertEqual(result["storage_error_lines"], [])
        self.assertFalse(result["hardware_health_proven"])
        result = uart.analyze("mmc0: timeout waiting for hardware interrupt\n")
        self.assertTrue(result["storage_error_lines"])

    def test_capture_exact_binary_and_no_transmission_with_real_pty(self):
        uart = self.module()
        with tempfile.TemporaryDirectory() as directory:
            master, slave = pty.openpty()
            self.addCleanup(os.close, master)
            self.addCleanup(os.close, slave)
            prefix = Path(directory) / "uart"
            process = subprocess.Popen([sys.executable, str(Path(uart.__file__)), "capture",
                "--device", os.ttyname(slave), "--output-prefix", str(prefix), "--seconds", "10"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                readable, _, _ = select.select([process.stdout], [], [], 5)
                self.assertTrue(readable, "UART capture did not become ready")
                self.assertIn("READY", process.stdout.readline())
                payload = b"boot\x00\xff\r\n"
                os.write(master, payload)
                events = prefix.with_suffix(".jsonl")
                for _ in range(50):
                    if '"received"' in events.read_text():
                        break
                    import time
                    time.sleep(0.02)
                self.assertEqual(prefix.with_suffix(".bin").read_bytes(), payload)
                self.assertEqual(select.select([master], [], [], 0.1)[0], [], "Capture echoed/transmitted bytes")
                process.send_signal(signal.SIGINT)
                process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(prefix.with_suffix(".bin").stat().st_mode & 0o777, 0o600)
                retry = subprocess.run([sys.executable, str(Path(uart.__file__)), "capture",
                    "--device", os.ttyname(slave), "--output-prefix", str(prefix), "--seconds", "1"],
                    capture_output=True, timeout=5)
                self.assertNotEqual(retry.returncode, 0)
                self.assertEqual(prefix.with_suffix(".bin").read_bytes(), payload)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()


if __name__ == "__main__":
    unittest.main()
