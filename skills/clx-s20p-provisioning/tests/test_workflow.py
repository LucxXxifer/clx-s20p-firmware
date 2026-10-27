"""Offline regression tests; synthetic data only, no network or device access."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import s20p_common as common
import s20p_fip as fip
import s20p_gpt as gpt
import s20p_health as health
import s20p_recovery as recovery
import s20p_uboot_console as console


def make_gpt(sectors=241696768, stock=False, secondary_alternate=1, guid=b"T" * 16):
    entries = bytearray(16384)
    start = 64
    for index, (label, size) in enumerate(zip(recovery.LABELS, (8, 8, 8, 32, 64, 128))):
        offset = index * 128
        entries[offset:offset + 16] = b"P" * 16
        entries[offset + 16:offset + 32] = bytes([index + 1]) * 16
        struct.pack_into("<QQ", entries, offset + 32, start, start + size - 1)
        encoded = label.encode("utf-16-le")
        entries[offset + 56:offset + 56 + len(encoded)] = encoded
        start += size
    crc = zlib.crc32(entries)

    def make_header(current, alternate, entries_lba):
        raw = bytearray(512)
        raw[:8] = b"EFI PART"
        struct.pack_into("<IIIIQQQQ", raw, 8, 0x10000, 92, 0, 0, current, alternate, 34, sectors - 34)
        raw[56:72] = guid
        struct.pack_into("<QIII", raw, 72, entries_lba, 128, 128, crc)
        struct.pack_into("<I", raw, 16, zlib.crc32(raw[:92]))
        return bytes(raw)

    mbr = bytearray(512)
    mbr[450] = 0xee
    struct.pack_into("<II", mbr, 454, 1, sectors - 1)
    mbr[510:] = b"\x55\xaa"
    first = bytes(mbr) + make_header(1, 1 if stock else sectors - 1, 2) + bytes(entries)
    tail = bytes(entries) + make_header(sectors - 1, secondary_alternate, sectors - 33)
    return first, tail


def make_archive():
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        for name in (".", ".synthetic-fixture"):
            item = tarfile.TarInfo(name)
            if name == ".":
                item.type = tarfile.DIRTYPE
                archive.addfile(item)
            else:
                item.size = 4
                archive.addfile(item, io.BytesIO(b"test"))
    return raw.getvalue()


class FakeSSH:
    """Fail closed on unknown commands; never delegates to a real transport."""
    host = "192.0.2.1"
    cid = "00112233445566778899aabbccddeeff"
    boot_id = "00000000-0000-0000-0000-000000000001"

    def __init__(self, sectors=241696768):
        self.sectors = sectors
        self.first, self.tail = make_gpt(sectors, stock=True)
        table, _ = gpt.inspect_primary(self.first, sectors)
        self.parts = table["partitions"]
        self.regions = {"mmcblk0boot0": b"B" * 4096, "mmcblk0boot1": b"C" * 4096}
        for part in self.parts:
            self.regions["mmcblk0p" + str(part["index"])] = bytes([part["index"]]) * part["sectors"] * 512
        self.archive = make_archive()
        self.commands, self.captured = [], []
        self.changed_source = None
        self.hash_calls = {}
        self.warning = ""

    def metadata(self):
        data = dict(UID="0", HOSTNAME="S20P", MODEL="MediaTek MT7986a RFB",
                    COMPATIBLE="mediatek,mt7986a-emmc-rfb", MMC_CID=self.cid, MMC_TYPE="MMC",
                    MMC_LOGICAL_BLOCK_BYTES="512", MMC_LIFE_TIME="0x01 0x01", MMC_PRE_EOL_INFO="0x01",
                    SIZE_MMCBLK0_SECTORS=str(self.sectors), SIZE_MMCBLK0BOOT0_SECTORS="8",
                    SIZE_MMCBLK0BOOT1_SECTORS="8")
        for part in self.parts:
            prefix = "PART_" + str(part["index"])
            data[prefix + "_START_SECTORS"] = str(part["start"])
            data[prefix + "_SIZE_SECTORS"] = str(part["sectors"])
            data[prefix + "_UEVENT"] = "PARTNAME=" + part["label"] + ";"
        return data

    def base(self):
        raise AssertionError("Real SSH is forbidden in offline tests")

    def read(self, command, expected_bytes=None, **kwargs):
        self.commands.append(command)
        if command == common.PREFLIGHT_COMMAND:
            raw = "CAPTURE_PREFLIGHT_BEGIN\n" + "\n".join(k + "=" + v for k, v in self.metadata().items())
            data = (raw + '\nBLKID_BEGIN\n/dev/mmcblk0p6: UUID="synthetic-fs-uuid" TYPE="ext4"\n'
                    'BLKID_END\nCAPTURE_PREFLIGHT_END\n').encode()
        elif command == "cat /proc/meminfo":
            data = b"MemTotal:        2097152 kB\n"
        elif command == "dmesg":
            data = b"mmc0: new MMC card\n"
        elif command == "cat /proc/mounts":
            data = b"/dev/mmcblk0p6 /permanent_config ext4 rw 0 0\ntmpfs /tmp tmpfs rw 0 0\n"
        elif command == "cat /proc/sys/kernel/random/boot_id":
            data = self.boot_id.encode()
        elif command == "cat /sys/class/block/mmcblk0/device/cid":
            data = self.cid.encode()
        elif command == "cat /sys/class/block/mmcblk0/size":
            data = str(self.sectors).encode()
        elif command.startswith("if test -d /overlay/upper;"):
            data = b"PRESENT"
        elif command.startswith("test -d /etc/config"):
            data = b"PRESENT"
        elif command == "set -o pipefail; true":
            data = b""
        elif command.startswith("tar "):
            data = self.archive
        elif command.startswith("dd if=/dev/"):
            options = dict(x.split("=", 1) for x in shlex.split(command)[1:])
            device = options["if"].removeprefix("/dev/")
            block = int(options["bs"])
            begin = int(options.get("skip", 0)) * block
            size = int(options["count"]) * block
            if device == "mmcblk0":
                if begin + size <= self.parts[0]["start"] * 512:
                    data = (self.first + b"\0" * (self.parts[0]["start"] * 512 - len(self.first)))[begin:begin + size]
                elif begin == (self.sectors - 33) * 512 and size == len(self.tail):
                    data = self.tail
                else:
                    raise AssertionError("Unreviewed raw range, including any full/p6 clone")
            else:
                if device == "mmcblk0p6":
                    raise AssertionError("Raw p6 capture is forbidden")
                data = self.regions[device][begin:begin + size]
        else:
            raise AssertionError("Unexpected command: " + command)
        if expected_bytes is not None:
            assert len(data) == expected_bytes, command
        return data

    def source_hash(self, command):
        self.hash_calls[command] = self.hash_calls.get(command, 0) + 1
        value = common.sha(self.read(command))
        if self.changed_source and self.changed_source in command and self.hash_calls[command] % 2 == 0:
            return "0" * 64
        return value

    def capture(self, command, target, expected_bytes=None, limit_bytes=None, **kwargs):
        data = self.read(command, expected_bytes)
        assert len(data) <= limit_bytes
        self.captured.append(target.name)
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(data)
        return dict(bytes=len(data), sha256=common.sha(data), local_readback_sha256=common.sha(data),
                    ssh_remote_exit_status=0, remote_stderr=self.warning if command.startswith("tar ") else "",
                    source_command=command), partial


def run_backup(root, ssh, **options):
    args = argparse.Namespace(destination_root=root, serial_suffix="T123", unit_dir=None,
                              resume_manifest=None, archive_limit_mib=1, unpacked_limit_mib=2)
    for key, value in options.items():
        setattr(args, key, value)
    with redirect_stdout(io.StringIO()):
        code = recovery.backup(args, ssh, common.probe(ssh, args.serial_suffix))
    paths = sorted((root / "clx-s20-T123").glob("capture-*/manifest.json"))
    return code, paths[-1], json.loads(paths[-1].read_text())


def make_fip():
    payload = bytearray(176)
    payload[:4] = bytes.fromhex("010064aa")
    for index, uid in enumerate(sorted(fip.COMPONENT_UUIDS)):
        data = bytes([65 + index]) * (17 + index * 5)
        offset = 16 + index * 40
        payload[offset:offset + 16] = bytes.fromhex(uid)
        struct.pack_into("<QQQ", payload, offset + 16, len(payload), len(data), 0)
        payload.extend(data)
    return bytes(payload)


class GPTTests(unittest.TestCase):
    def test_two_observed_geometries_have_valid_pairs(self):
        for sectors in (241696768, 244277248):
            with self.subTest(sectors=sectors):
                first, tail = make_gpt(sectors)
                self.assertFalse(gpt.pair(first, tail, sectors)["repair_needed"])

    def test_wrong_geometry_is_rejected_in_both_directions(self):
        for source, target in ((241696768, 244277248), (244277248, 241696768)):
            with self.subTest(source=source):
                first, tail = make_gpt(source)
                with self.assertRaises(ValueError):
                    gpt.pair(first, tail, target)

    def test_only_known_secondary_defect_can_be_patched(self):
        for sectors in (241696768, 244277248):
            first, bad = make_gpt(sectors, secondary_alternate=sectors - 1)
            with self.assertRaisesRegex(ValueError, "alternate"):
                gpt.pair(first, bad, sectors)
            candidate = gpt.corrected_header(first, bad, sectors)
            valid_first, valid_tail = make_gpt(sectors)
            self.assertEqual(candidate, valid_tail[-512:])
            self.assertEqual(first, valid_first)
            self.assertFalse(gpt.pair(first, bad[:-512] + candidate, sectors)["repair_needed"])
            with self.assertRaisesRegex(ValueError, "already valid"):
                gpt.corrected_header(first, valid_tail, sectors)

    def test_crc_entries_and_unknown_pointer_corruption_are_rejected(self):
        first, tail = make_gpt(secondary_alternate=99)
        with self.assertRaisesRegex(ValueError, "alternate"):
            gpt.corrected_header(first, tail, 241696768)
        first, tail = make_gpt()
        for pos in (512 + 24, 1024 + 10):
            bad = bytearray(first)
            bad[pos] ^= 1
            with self.assertRaises(ValueError):
                gpt.pair(bytes(bad), tail, 241696768)
        bad_tail = bytearray(tail)
        bad_tail[12] ^= 1
        with self.assertRaises(ValueError):
            gpt.corrected_header(first, bytes(bad_tail), 241696768)

    def test_stock_nonstandard_primary_is_preserved_but_not_treated_as_upload_valid(self):
        first, _ = make_gpt(stock=True)
        original = bytes(first)
        report, _ = gpt.inspect_primary(first, 241696768)
        self.assertEqual(report["primary"]["alternate"], 1)
        self.assertTrue(report["warnings"])
        self.assertEqual(first, original)
        with self.assertRaises(ValueError):
            gpt.inspect_primary(first, 241696768, strict=True)

    def test_valid_pair_cli_creates_no_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, tail = make_gpt()
            (root / "first").write_bytes(first)
            (root / "tail").write_bytes(tail)
            args = ["s20p_gpt", "--first34", str(root / "first"), "--final33", str(root / "tail"),
                    "--sectors", "241696768", "--prepare-secondary-header", str(root / "candidate")]
            with patch.object(sys, "argv", args), redirect_stdout(io.StringIO()) as output:
                gpt.main()
            self.assertFalse((root / "candidate").exists())
            self.assertFalse(json.loads(output.getvalue())["candidate_created"])


class HealthAndIdentityTests(unittest.TestCase):
    def test_endurance_bands_are_separate_and_not_exact_remaining_life(self):
        for code in range(1, 10):
            item = health.interpret(f"0x{code:02x} 0x01", "0x01", "")
            self.assertTrue(item["flash_gate_passed"])
            self.assertEqual(item["estimates"][0]["estimated_used_endurance_percent_band"], [(code - 1) * 10, code * 10])
            self.assertFalse(item["precise_remaining_lifetime_known"])

    def test_unknown_worn_spare_warnings_and_unread_logs_block_flash_not_rescue(self):
        for life, eol, log in [("0x0a 0x01", "0x01", ""), ("0x01 0x0b", "0x01", ""),
                               ("0x00 0x01", "0x01", ""), ("0x0c 0x01", "0x01", ""),
                               ("UNAVAILABLE", "0x01", ""), ("0x01 0x01", "0x02", ""),
                               ("0x01 0x01", "0x03", ""), ("0x01 0x01", "0x00", ""),
                               ("0x01 0x01", "0x01", None), ("0x01 0x01", "0x01", "mmc0: timeout"),
                               ("0x01 0x01", "0x01", "EXT4-fs (mmcblk0p6): error")]:
            with self.subTest(life=life, eol=eol, log=log):
                report = health.interpret(life, eol, log)
                self.assertFalse(report["flash_gate_passed"])
                self.assertTrue(report["rescue_backup_allowed"])

    def test_probe_records_filesystem_and_measured_geometry_without_capacity_identity(self):
        for sectors in (241696768, 244277248):
            report = common.probe(FakeSSH(sectors), "t123")
            self.assertEqual(report["unit"]["sectors"], sectors)
            self.assertEqual(report["unit"]["physical_serial_suffix"], "T123")
            self.assertEqual(report["filesystems"][0]["UUID"], "synthetic-fs-uuid")
            self.assertTrue(report["health"]["kernel_log_read_successfully"])
        for key, value in (("HOSTNAME", "OTHER"), ("MMC_TYPE", "SD"),
                           ("MMC_LOGICAL_BLOCK_BYTES", "4096"), ("MMC_CID", "bad")):
            candidate = FakeSSH().metadata()
            candidate[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                common.unit_identity(candidate, "T123")


class TransportTests(unittest.TestCase):
    def local_transport(self, program):
        ssh = common.SSH("192.0.2.1")
        ssh.base = lambda: [sys.executable, "-c", program]
        return ssh

    def test_binary_stdout_and_stderr_are_separate(self):
        ssh = self.local_transport("import sys; sys.stdout.buffer.write(b'\\0\\xffOK'); sys.stderr.write('diagnostic')")
        output = io.BytesIO()
        report = ssh.stream("fixture", output, expected_bytes=4, limit_bytes=4)
        self.assertEqual(output.getvalue(), b"\0\xffOK")
        self.assertEqual(report["remote_stderr"], "diagnostic")
        self.assertIn("BatchMode=yes", common.SSH("192.0.2.1").base())
        self.assertIn("StrictHostKeyChecking=yes", common.SSH("192.0.2.1").base())

    def test_truncation_and_overrun_retain_partial_without_retry(self):
        for output, expected, message in (("abc", 4, "Incomplete"), ("abcde", 4, "exceeded")):
            ssh = self.local_transport("import sys; sys.stdout.write('" + output + "')")
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "capture.bin"
                with self.assertRaisesRegex(RuntimeError, message):
                    ssh.capture("fixture", target, expected_bytes=expected, limit_bytes=expected)
                self.assertTrue(target.with_name("capture.bin.partial").exists())
                self.assertFalse(target.exists())

    def test_remote_nonzero_and_timeout_are_failures(self):
        ssh = self.local_transport("import sys; sys.stdout.write('abc'); sys.exit(7)")
        with self.assertRaisesRegex(RuntimeError, "failed"):
            ssh.read("fixture")
        ssh = self.local_transport("import time; time.sleep(2)")
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            ssh.read("fixture", timeout=0.1)


class RecoveryTests(unittest.TestCase):
    def test_complete_necessary_capture_and_resume_do_not_clone_raw_p6(self):
        with tempfile.TemporaryDirectory() as directory:
            root, ssh = Path(directory), FakeSSH()
            code, first_path, manifest = run_backup(root, ssh)
            self.assertEqual(code, 0)
            self.assertTrue(manifest["all_required_artifacts_verified"])
            self.assertFalse(manifest["restore_tested"])
            self.assertFalse(manifest["p6_raw_cloned"])
            self.assertFalse(manifest["whole_emmc_cloned"])
            before = first_path.read_bytes()
            ssh.captured.clear()
            code, second_path, resumed = run_backup(root, ssh)
            self.assertEqual(code, 0)
            self.assertNotEqual(first_path, second_path)
            self.assertEqual(before, first_path.read_bytes())
            self.assertFalse(ssh.captured)
            self.assertTrue(all("reused_from_manifest" in x for x in resumed["artifacts"].values()))
            self.assertFalse(any("if=/dev/mmcblk0p6" in x or "of=/dev/" in x for x in ssh.commands))

    def test_failed_partition_is_retained_and_only_that_artifact_is_recaptured(self):
        with tempfile.TemporaryDirectory() as directory:
            root, ssh = Path(directory), FakeSSH()
            ssh.changed_source = "if=/dev/mmcblk0p5 "
            code, path, failed = run_backup(root, ssh)
            self.assertEqual(code, 1)
            self.assertTrue(failed["artifacts"]["boot0"]["verified"])
            self.assertFalse(failed["artifacts"]["rootfs"]["verified"])
            partial = Path(failed["artifacts"]["rootfs"]["path"])
            self.assertTrue(partial.exists())
            original_manifest = path.read_bytes()
            ssh.changed_source = None
            ssh.captured.clear()
            code, _, resumed = run_backup(root, ssh)
            self.assertEqual(code, 0)
            self.assertEqual(ssh.captured, ["rootfs.bin"])
            self.assertTrue(partial.exists())
            self.assertEqual(path.read_bytes(), original_manifest)
            self.assertEqual(resumed["history"][0]["prior_status"], "incomplete_review_failed_artifacts")

    def test_identity_gpt_and_postflash_fip_conflicts_stop_resume(self):
        for conflict in ("cid", "gpt", "fip"):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as directory:
                root, ssh = Path(directory), FakeSSH()
                run_backup(root, ssh)
                ssh.captured.clear()
                if conflict == "cid":
                    ssh.cid = "1" * 32
                elif conflict == "gpt":
                    ssh.first, ssh.tail = make_gpt(stock=True, guid=b"X" * 16)
                else:
                    ssh.regions["mmcblk0p3"] = b"X" * len(ssh.regions["mmcblk0p3"])
                with self.assertRaises(RuntimeError):
                    run_backup(root, ssh)
                self.assertFalse(ssh.captured)

    def test_partial_initialization_can_resume_without_deleting_identity_evidence(self):
        for save_identity in (False, True):
            with self.subTest(save_identity=save_identity), tempfile.TemporaryDirectory() as directory:
                root, ssh = Path(directory), FakeSSH()
                unit_dir = root / "clx-s20-T123"
                unit_dir.mkdir()
                (unit_dir / ".capture.lock").touch()
                if save_identity:
                    common.write_json(unit_dir / "unit.json", common.probe(ssh, "T123")["unit"])
                code, _, _ = run_backup(root, ssh)
                self.assertEqual(code, 0)

    def test_modified_saved_file_or_forged_raw_hash_evidence_stops_resume(self):
        for conflict in ("file", "evidence"):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as directory:
                root, ssh = Path(directory), FakeSSH()
                _, path, manifest = run_backup(root, ssh)
                if conflict == "file":
                    Path(manifest["artifacts"]["factory"]["path"]).write_bytes(b"changed")
                else:
                    manifest["artifacts"]["factory"]["source_sha256_after"] = "0" * 64
                    path.write_text(json.dumps(manifest))
                with self.assertRaises(RuntimeError):
                    run_backup(root, ssh)

    def test_relative_symlink_is_not_imported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "original").write_bytes(b"fixture")
            (root / "link").symlink_to(root / "original")
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                recovery.artifact_path({"path": "link"}, root / "manifest.json", root)

    def test_archives_are_fully_read_and_truncated_gzip_or_limits_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.tar.gz"
            archive = make_archive()
            path.write_bytes(archive)
            entries = recovery.archive_inventory(path, 1024 * 1024)
            self.assertTrue(any(x["path"] == ".synthetic-fixture" and x["sha256"] == common.sha(b"test") for x in entries))
            with self.assertRaises(RuntimeError):
                recovery.archive_inventory(path, 1)
            path.write_bytes(archive[:-4])
            with self.assertRaises((EOFError, OSError, tarfile.TarError)):
                recovery.archive_inventory(path, 1024 * 1024)

    def test_tar_warning_preserves_raw_backups_and_marks_archives_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            ssh = FakeSSH()
            ssh.warning = "tar: file changed as we read it"
            code, _, manifest = run_backup(Path(directory), ssh)
            self.assertEqual(code, 1)
            self.assertTrue(manifest["artifacts"]["factory"]["verified"])
            self.assertFalse(manifest["artifacts"]["permanent_config_files"]["verified"])

    def test_legacy_continuation_import_uses_provenance_and_rejects_ambiguous_scope(self):
        for scenario in ("valid", "wrong_archive", "unknown_schema", "wrong_parent"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root, ssh = Path(directory), FakeSSH()
                _, current, manifest = run_backup(root, ssh)
                unit_dir = current.parent.parent
                legacy = dict(format_version=1, backup_mode="factory_recovery_regions_and_p6_files",
                              cid=ssh.cid, physical_serial_suffix="T123", sector_size=512,
                              user_area_sectors=ssh.sectors, boot_region_sectors=8, artifacts={})
                for key, name in recovery.LEGACY_NAMES.items():
                    if key not in manifest["artifacts"]:
                        continue
                    item = dict(manifest["artifacts"][key])
                    item.pop("spec")
                    if key == "settings_files":
                        item["source_command"] = "tar -C / -czf - etc/config etc/dropbear etc/rc.local etc/init.d"
                    legacy["artifacts"][name] = item
                parent = unit_dir / "legacy.json"
                continuation = unit_dir / "legacy-continuation.json"
                common.write_json(parent, legacy)
                completed = dict(cid=ssh.cid, physical_serial_suffix="T123", source_manifest=str(parent),
                                 artifacts=legacy["artifacts"], status="complete")
                if scenario == "wrong_archive":
                    completed["artifacts"]["permanent_config_files.tar.gz"]["source_command"] = "tar -C / -czf - etc/config etc/dropbear etc/rc.local etc/init.d"
                elif scenario == "unknown_schema":
                    completed["schema_version"] = 999
                elif scenario == "wrong_parent":
                    legacy["cid"] = "1" * 32
                    parent.write_text(json.dumps(legacy))
                common.write_json(continuation, completed)
                arguments = (continuation, manifest["unit"], unit_dir, manifest["plan"], manifest["original_primary_sha256"])
                if scenario == "valid":
                    reusable, history = recovery.load_previous(*arguments)
                    self.assertEqual(set(reusable), set(manifest["plan"]) - {"gpt_indicated_entries"})
                    self.assertIn("settings_files", reusable)
                    self.assertEqual(history["manifest"], str(continuation.resolve()))
                else:
                    with self.assertRaises(RuntimeError):
                        recovery.load_previous(*arguments)

    def test_unhealthy_emmc_can_still_receive_rescue_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            ssh = FakeSSH()
            metadata = ssh.metadata()
            metadata["MMC_LIFE_TIME"] = "0x0b 0x0b"
            ssh.metadata = lambda: metadata
            code, _, manifest = run_backup(Path(directory), ssh)
            self.assertEqual(code, 0)
            self.assertFalse(manifest["observation"]["health"]["flash_gate_passed"])
            self.assertTrue(manifest["all_required_artifacts_verified"])


class FIPTests(unittest.TestCase):
    def setup_operation(self, directory):
        root, ssh = Path(directory), FakeSSH()
        backups = root / "backups"
        backups.mkdir()
        _, manifest_path, backup = run_backup(backups, ssh)
        record = root / "operation"
        record.mkdir()
        plan = record / "recovery-plan.md"
        plan.write_text("Synthetic fixture: reviewed boot/system/GPT/p6 reconstruction plan.")
        review = dict(unit=backup["unit"], recovery_plan_reviewed=True, fip_board_compatibility_reviewed=True,
                      release_provenance_reviewed=True, gpt_repair_and_readback_procedure_reviewed=True,
                      required_diagnostics_passed=True, source_revision="synthetic-repository@synthetic-revision",
                      recovery_plan=str(plan), recovery_plan_sha256=common.sha(plan.read_bytes()),
                      authorization_scope="fip_only_no_reboot", seven_physical_ports_tested=False)
        common.write_json(record / "review.json", review)
        payload = make_fip()
        selected = record / "synthetic.fip"
        selected.write_bytes(payload)
        release = dict(html_url="https://example.invalid/releases/test", assets=[dict(name=selected.name,
                       digest="sha256:" + common.sha(payload), size=len(payload))])
        common.write_json(record / "release.json", release)
        args = argparse.Namespace(backup_manifest=manifest_path, review_record=record / "review.json",
                                  release_json=record / "release.json", fip=selected, sha256=common.sha(payload),
                                  serial_suffix="T123", record_dir=record, write=False,
                                  host=ssh.host, control_path="synthetic", known_hosts=None)
        return args, ssh

    def test_default_cli_preflight_never_stages_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            args, ssh = self.setup_operation(directory)
            argv = ["s20p_fip", "--host", ssh.host, "--serial-suffix", "T123", "--backup-manifest", str(args.backup_manifest),
                    "--review-record", str(args.review_record), "--release-json", str(args.release_json),
                    "--fip", str(args.fip), "--sha256", args.sha256, "--record-dir", str(args.record_dir)]
            with patch.object(sys, "argv", argv), patch.object(fip, "connection", return_value=ssh), \
                    patch.object(fip.subprocess, "run", side_effect=AssertionError("Unexpected staging/write")), \
                    redirect_stdout(io.StringIO()) as output:
                fip.main()
            self.assertEqual(json.loads(output.getvalue())["status"], "preflight_passed_no_flash_write")
            self.assertFalse((args.record_dir / "fip-write-journal.json").exists())

    def test_missing_health_or_review_or_release_evidence_stops_preflight(self):
        for conflict in ("health", "review", "plan", "release", "backup"):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as directory:
                args, ssh = self.setup_operation(directory)
                if conflict == "health":
                    metadata = ssh.metadata()
                    metadata["MMC_PRE_EOL_INFO"] = "0x02"
                    ssh.metadata = lambda: metadata
                elif conflict == "plan":
                    (args.record_dir / "recovery-plan.md").write_text("changed")
                else:
                    path = {"review": args.review_record, "release": args.release_json, "backup": args.backup_manifest}[conflict]
                    value = json.loads(path.read_text())
                    if conflict == "review":
                        value["recovery_plan_reviewed"] = False
                    elif conflict == "release":
                        value["assets"][0]["digest"] = "sha256:" + "0" * 64
                    else:
                        value["plan"].pop("factory")
                    path.write_text(json.dumps(value))
                with self.assertRaises(RuntimeError):
                    fip.preflight(args, ssh)
                self.assertFalse((args.record_dir / "fip-write-journal.json").exists())

    def test_write_once_preserves_suffix_even_with_lost_response(self):
        for outcome in ("normal", "timeout", "bad_readback"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                args, ssh = self.setup_operation(directory)
                report, payload, before = fip.preflight(args, ssh)
                commands = []
                original_hash = ssh.source_hash

                def fake_hash(command):
                    if command.startswith("cat /tmp/s20p-"):
                        return common.sha(payload)
                    return original_hash(command)

                def execute(command, **kwargs):
                    line = command[-1]
                    commands.append(line)
                    if line.startswith("umask 077;"):
                        self.assertEqual(kwargs["input"], payload)
                    elif line.startswith("dd if=/tmp/s20p-"):
                        self.assertTrue((args.record_dir / "fip-write-journal.json").exists())
                        self.assertIn("of=/dev/mmcblk0p3 bs=512 conv=fsync", line)
                        ssh.regions["mmcblk0p3"] = payload + before[len(payload):]
                        if outcome == "bad_readback":
                            ssh.regions["mmcblk0p3"] = b"X" + ssh.regions["mmcblk0p3"][1:]
                        if outcome == "timeout":
                            raise subprocess.TimeoutExpired(command, 120)
                    else:
                        raise AssertionError("Unexpected device change: " + line)
                    return subprocess.CompletedProcess(command, 0, b"", b"")

                ssh.source_hash = fake_hash
                ssh.base = lambda: ["synthetic-ssh"]
                with patch.object(fip.subprocess, "run", side_effect=execute):
                    if outcome == "bad_readback":
                        with self.assertRaisesRegex(RuntimeError, "do not reboot"):
                            fip.install(args, ssh, report, payload, before)
                    else:
                        result = fip.install(args, ssh, report, payload, before)
                        self.assertEqual(result["status"], "fip_written_readback_verified_uboot_boot_untested")
                        self.assertTrue(all(result["checks"].values()))
                    with self.assertRaisesRegex(RuntimeError, "journal"):
                        fip.install(args, ssh, report, payload, before)
                self.assertEqual(sum(x.startswith("dd ") for x in commands), 1)
                self.assertFalse(any("reboot" in x for x in commands))
                self.assertEqual(ssh.regions["mmcblk0p3"][len(payload):], before[len(payload):])
                journal = json.loads((args.record_dir / "fip-write-journal.json").read_text())
                self.assertFalse(journal["uboot_boot_verified"])
                self.assertFalse(journal["rebooted"])
                if outcome == "timeout":
                    self.assertTrue(journal["write_transport_timeout"])
                if outcome == "bad_readback":
                    self.assertIn("do_not_reboot", journal["status"])

    def test_invalid_fip_format_or_component_overlap_rejected(self):
        with self.assertRaises(RuntimeError):
            fip.inspect_fip(b"not an fip")
        payload = bytearray(make_fip())
        struct.pack_into("<Q", payload, 16 + 40 + 16, 176)
        with self.assertRaisesRegex(RuntimeError, "Overlapping"):
            fip.inspect_fip(bytes(payload))


class ConsoleTests(unittest.TestCase):
    def test_echo_return_overflow_and_exclusive_transcript(self):
        for scenario in ("success", "echo", "return", "overflow", "transport"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                instance = console.Console(directory, "http://192.0.2.1", "fixture0")
                executed = []

                def request(endpoint, fields):
                    if endpoint == "/console/exec":
                        executed.append(fields["cmd"])
                        if scenario == "transport":
                            raise RuntimeError("Lost response")
                        return dict(ok=True, cmd=fields["cmd"], ret=1 if scenario == "return" else 0)
                    return dict(data="wrong consumer" if scenario == "echo" else "MT7986> version\nfixture\nMT7986> ",
                                avail=False, overflow=scenario == "overflow")

                instance.request = request
                if scenario == "success":
                    self.assertIn("fixture", instance.run("version", "once"))
                else:
                    with self.assertRaises(RuntimeError):
                        instance.run("version", "once")
                with self.assertRaises(FileExistsError):
                    instance.run("version", "once")
                self.assertEqual(executed, ["version"])
                self.assertTrue((Path(directory) / "once.json").is_file())

    def test_pending_output_is_not_accepted_as_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = console.Console(directory, "http://192.0.2.1", "fixture0")
            responses = [dict(ok=True, cmd="version", ret=0)] + [dict(data="MT7986> version", avail=True)] * 12
            with patch.object(instance, "request", side_effect=responses), patch.object(console.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "Incomplete"):
                    instance.run("version", "pending")


if __name__ == "__main__":
    unittest.main()
