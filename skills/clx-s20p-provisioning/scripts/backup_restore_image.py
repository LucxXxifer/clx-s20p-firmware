#!/usr/bin/env python3
"""Capture read-only CLX-S20P eMMC images over SSH."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


DEFAULT_DESTINATION = Path("/Volumes/Storage/CLX-S20P/0.1 原厂备份")
STREAM_CHUNK = 4 * 1024 * 1024
SPACE_MARGIN = 64 * 1024 * 1024
SSH_CONTROL_PATH = str(Path.home() / ".ssh" / "clx-s20p-%C")

PREFLIGHT_COMMAND = r"""set -eu
printf 'CAPTURE_PREFLIGHT_BEGIN\n'
printf 'UID='; id -u
printf 'HOSTNAME='; (cat /proc/sys/kernel/hostname 2>/dev/null || true); printf '\n'
printf 'MODEL='; (tr '\000\n' '  ' < /sys/firmware/devicetree/base/model 2>/dev/null || true); printf '\n'
printf 'COMPATIBLE='; (tr '\000\n' '  ' < /sys/firmware/devicetree/base/compatible 2>/dev/null || true); printf '\n'
for block in mmcblk0 mmcblk0boot0 mmcblk0boot1; do
  test -r "/sys/class/block/$block/size" || { echo "MISSING_BLOCK=$block"; exit 20; }
  case "$block" in
    mmcblk0) block_key=MMCBLK0 ;;
    mmcblk0boot0) block_key=MMCBLK0BOOT0 ;;
    mmcblk0boot1) block_key=MMCBLK0BOOT1 ;;
    *) echo "UNEXPECTED_BLOCK=$block"; exit 21 ;;
  esac
  printf 'SIZE_%s_SECTORS=' "$block_key"; tr -d '\n' < "/sys/class/block/$block/size"; printf '\n'
done
printf 'MMC_LOGICAL_BLOCK_BYTES='; cat /sys/class/block/mmcblk0/queue/logical_block_size
mmc_card=
if test -d "/sys/class/block/mmcblk0/device"; then
  mmc_card=$(CDPATH= cd -P "/sys/class/block/mmcblk0/device" 2>/dev/null && pwd -P) || mmc_card=
fi
if test -z "$mmc_card"; then
  for candidate in /sys/bus/mmc/devices/mmc*:*; do
    if test -d "$candidate/block/mmcblk0"; then
      candidate_path=$(CDPATH= cd -P "$candidate" 2>/dev/null && pwd -P) || candidate_path=
      if test -n "$candidate_path"; then
        if test -n "$mmc_card" && test "$mmc_card" != "$candidate_path"; then
          printf 'MMC_DEVICE_PATH=UNAVAILABLE:AMBIGUOUS_CARD\n'
          exit 22
        fi
        mmc_card=$candidate_path
      fi
    fi
  done
fi
printf 'MMC_DEVICE_PATH=%s\n' "${mmc_card:-UNAVAILABLE:NO_CARD_NODE}"
for attr in cid name type manfid oemid serial date hwrev fwrev life_time pre_eol_info; do
  attr_key=$(printf '%s' "$attr" | tr 'a-z' 'A-Z')
  printf 'MMC_%s=' "$attr_key"
  if test -z "$mmc_card"; then
    printf 'UNAVAILABLE:NO_CARD_NODE\n'
  elif ! test -e "$mmc_card/$attr"; then
    printf 'UNAVAILABLE:ATTR_ABSENT\n'
  elif attr_value=$(cat "$mmc_card/$attr" 2>/dev/null); then
    if test -n "$attr_value"; then
      printf '%s' "$attr_value" | tr '\n' ' '; printf '\n'
    else
      printf 'UNAVAILABLE:EMPTY\n'
    fi
  else
    printf 'UNAVAILABLE:READ_FAILED\n'
  fi
done
for part in 1 2 3 4 5 6; do
  path="/sys/class/block/mmcblk0p$part"
  if test -r "$path/size"; then
    printf 'PART_%s_SIZE_SECTORS=' "$part"; tr -d '\n' < "$path/size"; printf '\n'
    printf 'PART_%s_START_SECTORS=' "$part"; tr -d '\n' < "$path/start" 2>/dev/null || true; printf '\n'
    if test -r "$path/uevent"; then
      printf 'PART_%s_UEVENT=' "$part"; tr '\n' ';' < "$path/uevent"; printf '\n'
    fi
  fi
done
printf 'PROC_PARTITIONS_BEGIN\n'; cat /proc/partitions
printf 'PROC_PARTITIONS_END\n'
printf 'BLKID_BEGIN\n'; (blkid 2>/dev/null || true)
printf 'BLKID_END\n'
printf 'CAPTURE_PREFLIGHT_END\n'
"""


def validate_host(value: str) -> str:
    if value.startswith("-") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", value):
        raise argparse.ArgumentTypeError("Use a device IP address or DNS hostname, without SSH options.")
    return value


def validate_serial_suffix(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9]{4}", normalized):
        raise argparse.ArgumentTypeError("Enter exactly the final four alphanumeric characters of the physical serial number.")
    return normalized.upper()


def parse_metadata(text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if re.fullmatch(r"[A-Z0-9_]+", key):
                metadata[key] = value.strip()
    return metadata


def ssh_base(host: str, user: str) -> list[str]:
    return [
        "ssh",
        "-T",
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPersist=600",
        "-o",
        f"ControlPath={SSH_CONTROL_PATH}",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "--",
        f"{user}@{host}",
    ]


def run_preflight(host: str, user: str) -> tuple[dict[str, str], str]:
    command = ssh_base(host, user) + [PREFLIGHT_COMMAND]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"ssh exited with status {result.returncode}"
        raise RuntimeError(f"Read-only device preflight failed: {detail}")
    if "CAPTURE_PREFLIGHT_BEGIN" not in result.stdout or "CAPTURE_PREFLIGHT_END" not in result.stdout:
        raise RuntimeError("The remote response did not contain a complete preflight record.")
    metadata = parse_metadata(result.stdout)
    if metadata.get("UID") != "0":
        raise RuntimeError("SSH must reach the device as root to read all eMMC areas.")
    identity = (metadata.get("MODEL", "") + " " + metadata.get("COMPATIBLE", "")).lower()
    known_s20p_identity = "s20p" in identity
    verified_rfb_fingerprint = (
        metadata.get("HOSTNAME", "").strip().lower() == "s20p"
        and "mediatek,mt7986a-emmc-rfb" in metadata.get("COMPATIBLE", "").lower().split()
        and all(metadata.get(key, "").isdigit() and int(metadata[key]) > 0 for key in (
            "SIZE_MMCBLK0_SECTORS", "SIZE_MMCBLK0BOOT0_SECTORS", "SIZE_MMCBLK0BOOT1_SECTORS"))
    )
    if not (known_s20p_identity or verified_rfb_fingerprint):
        raise RuntimeError("The device did not identify itself as CLX-S20P; no backup was started.")
    for block in ("MMCBLK0", "MMCBLK0BOOT0", "MMCBLK0BOOT1"):
        key = f"SIZE_{block}_SECTORS"
        if key not in metadata or not metadata[key].isdigit() or int(metadata[key]) <= 0:
            raise RuntimeError(f"Missing or invalid eMMC size for {block.lower()}.")
    return metadata, result.stdout


def artifact_sizes(metadata: dict[str, str]) -> dict[str, int]:
    return {
        "mmcblk0.img": int(metadata["SIZE_MMCBLK0_SECTORS"]) * 512,
        "mmcblk0boot0.img": int(metadata["SIZE_MMCBLK0BOOT0_SECTORS"]) * 512,
        "mmcblk0boot1.img": int(metadata["SIZE_MMCBLK0BOOT1_SECTORS"]) * 512,
    }


def create_unit_directory(root: Path, suffix: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise RuntimeError(f"Backup destination is not a directory: {root}")
    target = root / f"clx-s20-{suffix.upper()}"
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise RuntimeError(f"Backup directory already exists; refusing to overwrite it: {target}") from exc
    return target


def ensure_backup_volume(root: Path) -> None:
    volume = Path("/Volumes/Storage").resolve()
    resolved_root = root.resolve()
    if resolved_root != volume and not resolved_root.is_relative_to(volume):
        return
    if not os.path.ismount(volume):
        raise RuntimeError(f"Required external backup volume is not mounted: {volume}")
    existing = resolved_root
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if os.stat(existing).st_dev != os.stat(volume).st_dev:
        raise RuntimeError(f"Backup path is not on the mounted Storage volume: {resolved_root}")


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def capture_image(host: str, user: str, device: str, path: Path, expected_bytes: int) -> dict[str, int | str]:
    command = ssh_base(host, user) + [f"dd if=/dev/{device} bs={STREAM_CHUNK}"]
    partial = path.with_name(path.name + ".partial")
    digest = hashlib.sha256()
    captured = 0
    stderr_file = tempfile.TemporaryFile()
    try:
        with partial.open("xb") as output:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_file)
            assert process.stdout is not None
            try:
                while True:
                    chunk = process.stdout.read(STREAM_CHUNK)
                    if not chunk:
                        break
                    captured += len(chunk)
                    if captured > expected_bytes:
                        process.terminate()
                        raise RuntimeError(f"{device} returned more data than its recorded eMMC size.")
                    output.write(chunk)
                    digest.update(chunk)
                return_code = process.wait()
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                process.wait()
                raise
            output.flush()
            os.fsync(output.fileno())
        stderr_file.seek(0)
        stderr_text = stderr_file.read().decode("utf-8", errors="replace").strip()
        if return_code != 0:
            raise RuntimeError(f"Remote read of {device} failed (ssh/dd exit {return_code}): {stderr_text}")
        if captured != expected_bytes:
            raise RuntimeError(f"{device} image is incomplete: captured {captured} of {expected_bytes} bytes. {stderr_text}")
        local_readback_sha256 = hash_file(partial)[1]
        if local_readback_sha256 != digest.hexdigest():
            raise RuntimeError(f"Local read-back verification failed for the saved {device} image.")
        os.replace(partial, path)
        return {
            "bytes": captured,
            "sha256": digest.hexdigest(),
            "local_readback_sha256": local_readback_sha256,
            "source": f"/dev/{device}",
        }
    except BaseException:
        # Failed bytes are evidence; a later run must use a new destination.
        raise
    finally:
        if not stderr_file.closed:
            stderr_file.close()


def hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            chunk = source.read(STREAM_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def hash_remote_block_device(host: str, user: str, device: str) -> str:
    command = ssh_base(host, user) + [f"sha256sum /dev/{device}"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"ssh exited with status {result.returncode}"
        raise RuntimeError(f"Could not independently hash remote {device}: {detail}")
    match = re.search(r"\b([0-9a-fA-F]{64})\b", result.stdout)
    if not match:
        raise RuntimeError(f"Remote {device} hash response was missing a SHA-256 digest.")
    return match.group(1).lower()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only capture of the full CLX-S20P eMMC user area and both eMMC boot regions over SSH. "
        "The script never writes to the device; it does not test restoration or flash firmware."
    )
    parser.add_argument("--host", required=True, type=validate_host, help="Device IP address or DNS hostname")
    parser.add_argument("--serial-suffix", required=True, type=validate_serial_suffix, help="Last four characters read from the physical serial label")
    parser.add_argument("--full-image", action="store_true", help="Explicitly opt in to cloning and independently hashing the full user area (including p6); see references/recovery-backup.md for the default recovery backup")
    parser.add_argument("--user", default="root", help="SSH user (default: root)")
    parser.add_argument("--destination-root", type=Path, default=DEFAULT_DESTINATION, help=f"Backup root (default: {DEFAULT_DESTINATION})")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.full_image:
        print("Full raw capture requires explicit --full-image; it reads and hashes the entire eMMC, including p6. "
              "Use references/recovery-backup.md for the default per-device recovery backup.", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", args.user):
        print("SSH user must be a simple account name.", file=sys.stderr)
        return 2
    if shutil.which("ssh") is None:
        print("OpenSSH client was not found on this computer.", file=sys.stderr)
        return 2
    try:
        intended_directory = args.destination_root / f"clx-s20-{args.serial_suffix.upper()}"
        if intended_directory.exists() or intended_directory.is_symlink():
            raise RuntimeError(f"Backup directory already exists; refusing to overwrite it: {intended_directory}")
        ensure_backup_volume(args.destination_root)
        metadata, raw_preflight = run_preflight(args.host, args.user)
        sizes = artifact_sizes(metadata)
        required = sum(sizes.values()) + SPACE_MARGIN
        space_probe = args.destination_root
        while not space_probe.exists() and space_probe != space_probe.parent:
            space_probe = space_probe.parent
        available = shutil.disk_usage(space_probe).free
        if available < required:
            raise RuntimeError(f"Insufficient local space: need at least {required} bytes; available {available} bytes.")
        directory = create_unit_directory(args.destination_root, args.serial_suffix)
        captured_at = datetime.now(timezone.utc).isoformat()
        (directory / "device-preflight.txt").write_text(raw_preflight, encoding="utf-8")
        manifest: dict = {
            "format_version": 1,
            "capture_status": "running",
            "captured_at_utc": captured_at,
            "host": args.host,
            "ssh_user": args.user,
            "physical_serial_suffix": args.serial_suffix,
            "device_metadata": metadata,
            "artifacts": {},
            "verification": {
                "checks": [
                    "remote SSH/dd exit status was zero",
                    "captured byte count matched the eMMC sysfs sector count",
                    "saved-file read-back SHA-256 matched the streaming capture SHA-256",
                    "independent SHA-256 of the source device matched the capture SHA-256",
                ],
                "restore_tested": False,
            },
        }
        manifest_path = directory / "manifest.json"
        write_json(manifest_path, manifest)
        devices = ("mmcblk0boot0", "mmcblk0boot1", "mmcblk0")
        filenames = ("mmcblk0boot0.img", "mmcblk0boot1.img", "mmcblk0.img")
        try:
            for device, filename in zip(devices, filenames):
                print(f"Capturing {device} ({sizes[filename]} bytes) ...", flush=True)
                manifest["artifacts"][filename] = capture_image(
                    args.host, args.user, device, directory / filename, sizes[filename]
                )
                print(f"Verifying remote read of {device} ...", flush=True)
                remote_sha256 = hash_remote_block_device(args.host, args.user, device)
                manifest["artifacts"][filename]["remote_sha256"] = remote_sha256
                if remote_sha256 != manifest["artifacts"][filename]["sha256"]:
                    raise RuntimeError(f"SHA-256 mismatch for {device}: the source device and saved image differ.")
                write_json(manifest_path, manifest)
            manifest["capture_status"] = "complete"
            write_json(manifest_path, manifest)
        except BaseException as exc:
            manifest["capture_status"] = "failed"
            manifest["failure"] = str(exc)
            write_json(manifest_path, manifest)
            raise
        print(f"Backup captured and locally checked: {directory}")
        print("This verifies capture size and saved-file hashes; it does not prove a restoration has been tested.")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Backup stopped: {exc}", file=sys.stderr)
        print("Do not flash this device unless the backup is complete and independently reviewed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
