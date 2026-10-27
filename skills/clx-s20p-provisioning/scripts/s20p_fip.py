#!/usr/bin/env python3
"""Guarded FIP-only install over SSH. Defaults to preflight; no reboot/BL2/GPT writes."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import uuid

from s20p_common import (DEFAULT_DESTINATION, add_connection_arguments, assert_same_unit,
                         check_live_cid, connection, hash_file, now, probe, region_command,
                         require, safe_within, save_new, sha, stamp, write_json)
from s20p_recovery import artifact_path, build_plan

COMPONENT_UUIDS = {"47d4086d4cfe98469b952950cbbd5a00", "d6d0eea7fcead54b97829934f234b6e4",
                   "a2cceab7f8254b279704633a6fd69ad8"}


def inspect_fip(payload):
    require(176 < len(payload) <= 16 * 1024 * 1024 and payload[:4] == bytes.fromhex("010064aa"),
            "Not a reviewed TF-A FIP format/size")
    entries, ranges = [], []
    offset = 16
    for _ in range(4):
        uid = payload[offset:offset + 16].hex()
        start, size, flags = struct.unpack_from("<QQQ", payload, offset + 16)
        offset += 40
        if uid == "0" * 32:
            break
        require(start >= 176 and size > 0 and start + size <= len(payload), "FIP component out of bounds")
        require(not any(start < end and begin < start + size for begin, end in ranges), "Overlapping FIP components")
        ranges.append((start, start + size))
        entries.append(dict(uuid=uid, offset=start, bytes=size, flags=flags, sha256=sha(payload[start:start + size])))
    require(offset == 176 and len(entries) == 3 and {x["uuid"] for x in entries} == COMPONENT_UUIDS,
            "Unfamiliar FIP component table; review compatibility separately")
    return entries


def validate_release(payload, fip_path, release, expected_sha):
    require(re.fullmatch(r"[0-9a-f]{64}", expected_sha), "Supply a verified SHA-256")
    require(sha(payload) == expected_sha, "Selected FIP SHA-256 mismatch")
    assets = [x for x in release.get("assets", []) if x.get("name") == fip_path.name]
    require(len(assets) == 1 and assets[0].get("digest") == "sha256:" + expected_sha and
            assets[0].get("size") == len(payload), "Saved release asset evidence does not match name/size/digest")
    require(str(release.get("html_url", "")).startswith("https://"), "Release source URL missing")
    return inspect_fip(payload)


def preflight(args, ssh):
    manifest_path = args.backup_manifest.resolve()
    backup = json.loads(manifest_path.read_text())
    require(backup.get("schema_version") == 2, "Normalize a reviewed legacy backup through s20p_recovery.py first")
    require(backup.get("all_required_artifacts_verified") is True and
            backup.get("status") == "capture_verified_restore_untested", "Recovery capture is incomplete")
    unit_dir = manifest_path.parent.parent
    require(manifest_path.parent.name.startswith("capture-"), "Unexpected manifest location")
    assert_same_unit(json.loads((unit_dir / "unit.json").read_text()), backup["unit"])
    review = json.loads(args.review_record.read_text())
    assert_same_unit(review["unit"], backup["unit"])
    for key in ("recovery_plan_reviewed", "fip_board_compatibility_reviewed", "release_provenance_reviewed",
                "gpt_repair_and_readback_procedure_reviewed", "required_diagnostics_passed"):
        require(review.get(key) is True, "Missing reviewed write prerequisite: " + key)
    require(review.get("authorization_scope") == "fip_only_no_reboot", "FIP write scope must match the user request")
    require(bool(review.get("source_revision")) and bool(review.get("recovery_plan")), "Source revision/recovery plan evidence missing")
    recovery_plan = Path(review["recovery_plan"])
    require(recovery_plan.is_absolute() and recovery_plan.is_file(), "Reviewed recovery plan file is absent")
    require(hash_file(recovery_plan)[1] == review.get("recovery_plan_sha256"), "Reviewed recovery plan digest mismatch")
    artifacts = backup["artifacts"]
    for key, spec in backup["plan"].items():
        item = artifacts.get(key, {})
        require(item.get("verified") is True and item.get("spec") == spec, "Unverified backup: " + key)
        path = artifact_path(item, manifest_path, unit_dir)
        require(hash_file(path) == (item["bytes"], item["sha256"]), "Saved backup mismatch: " + key)
        if spec["kind"] == "raw":
            require(item["bytes"] == spec["expected_bytes"] and
                    item.get("source_sha256_before") == item["sha256"] == item.get("source_sha256_after"),
                    "Saved raw backup lacks stable source/length verification: " + key)
    observation = probe(ssh, args.serial_suffix)
    assert_same_unit(backup["unit"], observation["unit"])
    require(observation["health"]["flash_gate_passed"], "Fresh eMMC health/storage-log gate did not pass")
    require(any(x == dict(source="tmpfs", target="/tmp", filesystem="tmpfs") or
                (x["target"] == "/tmp" and x["filesystem"] == "tmpfs") for x in observation["mounts"]),
            "Payload staging must be RAM-backed /tmp")
    first = ssh.read("dd if=/dev/mmcblk0 bs=512 count=34", expected_bytes=34 * 512)
    require(sha(first) == backup["original_primary_sha256"], "GPT changed since original backup")
    # Cross-check the current kernel's ranges and labels against this unit's original GPT.
    alternate = ssh.read("dd if=/dev/mmcblk0 bs=512 skip=" + str(backup["original_gpt"]["primary"]["alternate"]) +
                         " count=1", expected_bytes=512)
    live_plan, _ = build_plan(observation, first, alternate, overlay="overlay_files" in backup["plan"])
    require(backup["plan"] == live_plan, "Live source map changed")
    payload = args.fip.read_bytes()
    release = json.loads(args.release_json.read_text())
    components = validate_release(payload, args.fip, release, args.sha256.lower())
    fip_spec = backup["plan"]["fip"]
    require(fip_spec["source"]["device"] == "mmcblk0p3" and fip_spec["source"]["start"] == 0,
            "Only the verified p3 FIP partition is a write target")
    require(len(payload) <= fip_spec["expected_bytes"], "FIP exceeds partition")
    before = ssh.read(region_command(fip_spec["source"]), expected_bytes=fip_spec["expected_bytes"])
    require(sha(before) == artifacts["fip"]["sha256"], "Live FIP differs from saved original; inspect prior writes")
    protected = {}
    for key, spec in backup["plan"].items():
        if spec["kind"] == "raw" and key not in ("fip", "rootfs"):
            protected[key] = ssh.source_hash(region_command(spec["source"]))
            require(protected[key] == artifacts[key]["sha256"], "Protected original region changed: " + key)
    result = dict(status="preflight_passed_no_flash_write", observed_at_utc=now(), unit=backup["unit"],
                  boot_id=observation["boot_id"], health=observation["health"], backup_manifest=str(manifest_path),
                  review_record=str(args.review_record.resolve()), review_record_sha256=hash_file(args.review_record)[1],
                  recovery_plan=str(recovery_plan), recovery_plan_sha256=review["recovery_plan_sha256"],
                  release_url=release["html_url"], release_evidence_sha256=hash_file(args.release_json)[1],
                  fip_path=str(args.fip.resolve()), fip_bytes=len(payload), fip_sha256=sha(payload), components=components,
                  before_sha256=sha(before), expected_after_sha256=sha(payload + before[len(payload):]),
                  protected_before=protected, protected_specs={k: backup["plan"][k] for k in protected},
                  fip_spec=fip_spec, seven_physical_ports_tested=review.get("seven_physical_ports_tested") is True,
                  restore_tested=False, rebooted=False, uboot_boot_verified=False)
    return result, payload, before


def install(args, ssh, report, payload, before):
    journal = args.record_dir / "fip-write-journal.json"
    require(not journal.exists(), "Existing write journal: read device state; never blindly retry")
    # Exclusive journal is durable before the first device-side change, including RAM staging.
    save_new(journal, (json.dumps(report, indent=2) + "\n").encode())
    stage = "/tmp/s20p-" + uuid.uuid4().hex + ".fip"
    try:
        save_new(args.record_dir / "fip-before.bin", before)
        save_new(args.record_dir / "selected-fip.bin", payload)
        report.update(status="staging_in_ram_no_flash_yet", stage=stage)
        write_json(journal, report)
        staged = subprocess.run(ssh.base() + ["umask 077; set -C; cat > " + shlex.quote(stage)], input=payload,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        require(staged.returncode == 0 and not staged.stdout, "RAM staging failed")
        require(ssh.source_hash("cat " + shlex.quote(stage)) == report["fip_sha256"], "RAM staging digest mismatch")
        check_live_cid(ssh, report["unit"])
        require(ssh.read("cat /sys/class/block/mmcblk0/size").decode().strip() == str(report["unit"]["sectors"]),
                "Geometry changed before write")
        require(ssh.read("cat /proc/sys/kernel/random/boot_id").decode().strip() == report["boot_id"], "Device rebooted during preflight")
        require(ssh.source_hash(region_command(report["fip_spec"]["source"])) == report["before_sha256"], "FIP changed before write")
        report["status"] = "write_started_outcome_unknown_until_readback_do_not_reboot"
        write_json(journal, report)
        # No padding: preserve bytes beyond the exact FIP payload, including the final partial sector.
        try:
            written = subprocess.run(ssh.base() + ["dd if=" + shlex.quote(stage) + " of=/dev/mmcblk0p3 bs=512 conv=fsync"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            report.update(write_returncode=written.returncode,
                          write_stderr=written.stderr.decode(errors="replace")[:16384])
        except subprocess.TimeoutExpired:
            report["write_transport_timeout"] = True
        write_json(journal, report)
        # A lost response is never a reason to repeat the write. Inspect once, then stop on uncertainty.
        after = ssh.read(region_command(report["fip_spec"]["source"]), expected_bytes=len(before))
        save_new(args.record_dir / "fip-after-readback.bin", after)
        protected_after = {k: ssh.source_hash(region_command(v["source"])) for k, v in report["protected_specs"].items()}
        checks = dict(payload_matches=after[:len(payload)] == payload,
                      suffix_preserved=after[len(payload):] == before[len(payload):],
                      partition_size_preserved=len(after) == len(before),
                      expected_whole_partition_digest=sha(after) == report["expected_after_sha256"],
                      protected_regions_unchanged=protected_after == report["protected_before"],
                      independent_source_digest=ssh.source_hash(region_command(report["fip_spec"]["source"])) == sha(after),
                      saved_readback_digest=hash_file(args.record_dir / "fip-after-readback.bin") == (len(after), sha(after)),
                      same_boot=ssh.read("cat /proc/sys/kernel/random/boot_id").decode().strip() == report["boot_id"])
        check_live_cid(ssh, report["unit"])
        report.update(checks=checks, protected_after=protected_after, after_sha256=sha(after), completed_at_utc=now())
        require(all(checks.values()), "Readback/protection verification failed; do not reboot")
        report["status"] = "fip_written_readback_verified_uboot_boot_untested"
    except BaseException as error:
        report.update(status="stopped_do_not_reboot_inspect_journal_and_device", failure=str(error))
        raise
    finally:
        write_json(journal, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_arguments(parser)
    parser.add_argument("--backup-manifest", required=True, type=Path)
    parser.add_argument("--review-record", required=True, type=Path)
    parser.add_argument("--release-json", required=True, type=Path)
    parser.add_argument("--fip", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--record-dir", required=True, type=Path)
    parser.add_argument("--write", action="store_true", help="Only with the user's existing FIP-write authorization; never reboots")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        require(args.record_dir.is_dir(), "Prepare an exclusive operation directory")
        require(not args.record_dir.resolve().is_relative_to(DEFAULT_DESTINATION.resolve()), "Flash logs belong outside backup root")
        require(not (args.record_dir / "fip-write-journal.json").exists(), "Write journal exists; inspect outcome instead of retrying")
        with (args.record_dir / ".fip.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ssh = connection(args)
            report, payload, before = preflight(args, ssh)
            write_json(args.record_dir / ("fip-preflight-" + stamp() + ".json"), report)
            if args.write:
                report = install(args, ssh, report, payload, before)
        print(json.dumps(dict(status=report["status"], fip_sha256=report["fip_sha256"],
                              rebooted=False, uboot_boot_verified=False, restore_tested=False), indent=2))
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, "FIP operation stopped: " + str(error) + "\n")


if __name__ == "__main__":
    main()
