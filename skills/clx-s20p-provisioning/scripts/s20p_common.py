"""Shared read-only SSH transport, unit identity, and local evidence helpers."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import threading

from backup_restore_image import (DEFAULT_DESTINATION, PREFLIGHT_COMMAND, SSH_CONTROL_PATH,
                                  ensure_backup_volume, hash_file, parse_metadata,
                                  validate_host, validate_serial_suffix, write_json)
from s20p_health import interpret

CHUNK = 1024 * 1024


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_new(path, data):
    with Path(path).open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def safe_within(path, directory):
    path, directory = Path(path).resolve(), Path(directory).resolve()
    require(path.is_relative_to(directory), "Artifact must stay inside its unit directory")
    return path


class SSH:
    def __init__(self, host, control_path=SSH_CONTROL_PATH, known_hosts=None):
        self.host = validate_host(host)
        self.control_path = str(control_path)
        self.known_hosts = known_hosts

    def base(self):
        command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                   "-o", "ControlMaster=auto", "-o", "ControlPersist=600",
                   "-o", "ControlPath=" + self.control_path, "-o", "ConnectTimeout=8",
                   "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
        if self.known_hosts:
            command += ["-o", "UserKnownHostsFile=" + str(self.known_hosts)]
        return command + ["--", "root@" + self.host]

    def stream(self, command, output, expected_bytes=None, limit_bytes=16 * CHUNK, timeout=300):
        """No retry; separate stderr, hard byte/time bounds; caller owns partial file."""
        digest, count = hashlib.sha256(), 0
        expired = threading.Event()
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(self.base() + [command], stdout=subprocess.PIPE, stderr=errors)

            def expire():
                expired.set()
                process.kill()

            timer = threading.Timer(timeout, expire)
            timer.daemon = True
            timer.start()
            try:
                for block in iter(lambda: process.stdout.read(CHUNK), b""):
                    count += len(block)
                    require(count <= limit_bytes and (expected_bytes is None or count <= expected_bytes),
                            "Transfer exceeded its reviewed byte limit")
                    output.write(block)
                    digest.update(block)
                code = process.wait(timeout=10)
                errors.seek(0)
                stderr = errors.read(16384).decode(errors="replace")
                require(not expired.is_set(), "SSH read timed out; partial evidence retained")
                require(code == 0, "SSH/remote command failed: " + stderr)
                require(expected_bytes is None or count == expected_bytes, "Incomplete SSH transfer")
                return dict(bytes=count, sha256=digest.hexdigest(), source_command=command,
                            ssh_remote_exit_status=code, remote_stderr=stderr)
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()

    def read(self, command, limit_bytes=16 * CHUNK, expected_bytes=None, timeout=300):
        import io
        output = io.BytesIO()
        self.stream(command, output, expected_bytes, limit_bytes, timeout)
        return output.getvalue()

    def capture(self, command, target, expected_bytes=None, limit_bytes=16 * CHUNK, timeout=900):
        target = Path(target)
        require(not target.exists() and not target.is_symlink(), "Refusing to overwrite capture")
        partial = target.with_name(target.name + ".partial")
        with partial.open("xb") as output:
            report = self.stream(command, output, expected_bytes, limit_bytes, timeout)
            output.flush()
            os.fsync(output.fileno())
        size, digest = hash_file(partial)
        require((size, digest) == (report["bytes"], report["sha256"]), "Local reread mismatch")
        report["local_readback_sha256"] = digest
        return report, partial

    def source_hash(self, command):
        # BusyBox ash pipefail is checked before any pipeline is used.
        result = self.read("set -o pipefail; " + command + " | sha256sum", timeout=900).decode().strip()
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+-", result)
        require(match is not None, "Invalid independent source hash response")
        return match.group(1).lower()


def add_connection_arguments(parser):
    parser.add_argument("--host", required=True, type=validate_host)
    parser.add_argument("--serial-suffix", required=True, type=validate_serial_suffix)
    parser.add_argument("--control-path", default=SSH_CONTROL_PATH)
    parser.add_argument("--known-hosts", type=Path)


def connection(args):
    return SSH(args.host, args.control_path, args.known_hosts)


def unit_identity(metadata, suffix):
    require(metadata.get("UID") == "0", "Root access is required")
    require(metadata.get("MMC_TYPE") == "MMC", "Expected the card owning mmcblk0 to be eMMC")
    require(metadata.get("MMC_LOGICAL_BLOCK_BYTES") == "512", "Only the reviewed 512-byte logical sector size is supported")
    compatible = metadata.get("COMPATIBLE", "").lower().split()
    name = metadata.get("MODEL", "").lower() + " " + " ".join(compatible)
    known = ("s20p" in name or "clx-s20" in name) and any("mediatek,mt7986a" in x for x in compatible)
    rfb = metadata.get("HOSTNAME", "").upper() == "S20P" and "mediatek,mt7986a-emmc-rfb" in compatible
    require(known or rfb, "Board did not identify as S20P; capacity alone is insufficient")
    cid = metadata.get("MMC_CID", "").lower()
    require(re.fullmatch(r"[0-9a-f]{32}", cid), "Missing/invalid CID")
    sizes = {}
    for block in ("mmcblk0", "mmcblk0boot0", "mmcblk0boot1"):
        value = metadata.get("SIZE_" + block.upper() + "_SECTORS", "")
        require(value.isdigit() and 0 < int(value) < 2**63, "Missing/invalid block geometry: " + block)
        sizes[block] = int(value)
    require(sizes["mmcblk0"] > 17408, "User area is unexpectedly small")
    require(all(sizes[x] <= 65536 for x in ("mmcblk0boot0", "mmcblk0boot1")), "Boot region exceeds reviewed size limit")
    return dict(cid=cid, physical_serial_suffix=validate_serial_suffix(suffix),
                sector_size=512, sectors=sizes["mmcblk0"],
                boot0_sectors=sizes["mmcblk0boot0"], boot1_sectors=sizes["mmcblk0boot1"],
                model=metadata.get("MODEL"), compatible=compatible,
                card_name=metadata.get("MMC_NAME"), card_serial=metadata.get("MMC_SERIAL"))


def assert_same_unit(old, live):
    for key in ("cid", "physical_serial_suffix", "sector_size", "sectors", "boot0_sectors", "boot1_sectors"):
        require(old.get(key) == live.get(key), "Unit identity/geometry conflict: " + key)


def probe(ssh, suffix):
    raw = ssh.read(PREFLIGHT_COMMAND).decode()
    require("CAPTURE_PREFLIGHT_BEGIN" in raw and "CAPTURE_PREFLIGHT_END" in raw, "Incomplete identity response")
    metadata = parse_metadata(raw)
    unit = unit_identity(metadata, suffix)
    filesystems = []
    block_section = re.search(r"^BLKID_BEGIN\n(.*?)^BLKID_END$", raw, re.M | re.S)
    if block_section:
        for line in block_section.group(1).splitlines():
            match = re.fullmatch(r"(/dev/mmcblk0p[1-6]):\s*(.*)", line)
            if match:
                values = dict(re.findall(r'([A-Z0-9_]+)="([^"\n]*)"', match.group(2)))
                filesystems.append(dict(device=match.group(1), **{k: values[k] for k in ("UUID", "TYPE", "PARTUUID") if k in values}))
    meminfo = ssh.read("cat /proc/meminfo").decode()
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB$", meminfo, re.M)
    require(match is not None, "RAM size unavailable")
    unit["mem_total_kib"] = int(match.group(1))
    require(1536 * 1024 <= unit["mem_total_kib"] <= 2200 * 1024, "Expected the reviewed 2-GiB S20P variant")
    try:
        kernel_log = ssh.read("dmesg").decode(errors="replace")
    except RuntimeError:
        kernel_log = None
    health = interpret(metadata.get("MMC_LIFE_TIME", "UNAVAILABLE"),
                       metadata.get("MMC_PRE_EOL_INFO", "UNAVAILABLE"), kernel_log)
    health["kernel_log_read_successfully"] = kernel_log is not None
    health["kernel_log_sha256"] = sha(kernel_log.encode()) if kernel_log is not None else None
    mounts = []
    for line in ssh.read("cat /proc/mounts").decode().splitlines():
        fields = line.split()
        require(len(fields) >= 3, "Incomplete mount table")
        mounts.append(dict(source=fields[0], target=fields[1], filesystem=fields[2]))
    boot_id = ssh.read("cat /proc/sys/kernel/random/boot_id").decode().strip()
    require(re.fullmatch(r"[0-9a-f-]{36}", boot_id), "Boot ID unavailable")
    return dict(observed_at_utc=now(), host=ssh.host, unit=unit, metadata=metadata,
                health=health, filesystems=filesystems, mounts=mounts, boot_id=boot_id)


def region_command(source):
    device, start, sectors = source["device"], source["start"], source["sectors"]
    require(re.fullmatch(r"mmcblk0(?:boot[01]|p[1-6])?", device), "Unreviewed source device")
    require(isinstance(start, int) and start >= 0 and isinstance(sectors, int) and sectors > 0,
            "Invalid source range")
    block_sectors = 2048 if start % 2048 == 0 and sectors % 2048 == 0 else 1
    return ("dd if=/dev/" + device + " bs=" + str(block_sectors * 512) + " skip=" +
            str(start // block_sectors) + " count=" + str(sectors // block_sectors))


def check_live_cid(ssh, unit):
    actual = ssh.read("cat /sys/class/block/mmcblk0/device/cid").decode().strip().lower()
    require(actual == unit["cid"], "Live CID changed; stop")
