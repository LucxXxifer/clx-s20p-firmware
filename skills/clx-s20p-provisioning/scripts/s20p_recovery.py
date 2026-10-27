#!/usr/bin/env python3
"""Read-only S20P health preflight and necessary factory recovery capture/resume."""
import argparse
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import tarfile

from s20p_common import (CHUNK, DEFAULT_DESTINATION, add_connection_arguments, assert_same_unit,
                         check_live_cid, connection, ensure_backup_volume, hash_file, now,
                         probe, region_command, require, safe_within, sha, stamp, write_json)
from s20p_gpt import header, inspect_primary

LABELS = ["u-boot-env", "factory", "fip", "kernel", "rootfs", "permanent_config"]
LIMITS = [16, 16, 16, 128, 2048]  # MiB; unfamiliar oversized boot/system layouts need review.
LEGACY_NAMES = {
    "boot0": "boot0_bl2.bin", "boot1": "boot1.bin", "factory": "mmcblk0p2_factory.bin",
    "env": "mmcblk0p1_u-boot-env.bin", "fip": "mmcblk0p3_fip.bin",
    "head_reserved": "mmcblk0_first4MiB.bin", "gpt_primary": "mmcblk0_GPT.bin",
    "gpt_physical_tail": "mmcblk0_disk_tail33.bin",
    "gpt_after_last_usable": "mmcblk0_after_last_usable33.bin",
    "gpt_indicated_alternate": "mmcblk0_GPT_alternate_header.bin",
    "kernel": "mmcblk0p4_kernel.bin", "rootfs": "mmcblk0p5_rootfs.bin",
    "permanent_config_files": "permanent_config_files.tar.gz",
    "settings_files": "stock_config_files.tar.gz", "overlay_files": "overlay_files.tar.gz",
}


def raw_spec(device, start, sectors):
    return dict(kind="raw", source=dict(device=device, start=start, sectors=sectors), expected_bytes=sectors * 512)


def build_plan(observation, first34, alternate_sector=None, overlay=False):
    unit, metadata = observation["unit"], observation["metadata"]
    gpt, _ = inspect_primary(first34, unit["sectors"])
    parts = gpt["partitions"]
    require([(p["index"], p["label"]) for p in parts] == list(enumerate(LABELS, 1)),
            "Unfamiliar stock partition labels/order; review before capture")
    for p in parts:
        key = "PART_" + str(p["index"])
        require(metadata.get(key + "_START_SECTORS") == str(p["start"]) and
                metadata.get(key + "_SIZE_SECTORS") == str(p["sectors"]), "GPT/sysfs range mismatch")
        require("PARTNAME=" + p["label"] in metadata.get(key + "_UEVENT", "").split(";"),
                "GPT/sysfs label mismatch")
        if p["index"] <= 5:
            require(p["sectors"] * 512 <= LIMITS[p["index"] - 1] * CHUNK, "Partition exceeds reviewed backup limit")
    require(34 <= parts[0]["start"] <= 32768, "Unfamiliar reserved boot head size")
    p6_mounts = [x for x in observation["mounts"] if x["target"] == "/permanent_config"]
    require(len(p6_mounts) == 1 and p6_mounts[0]["source"] == "/dev/mmcblk0p6",
            "File-level p6 capture requires its verified /permanent_config mount")
    plan = {
        "boot0": raw_spec("mmcblk0boot0", 0, unit["boot0_sectors"]),
        "boot1": raw_spec("mmcblk0boot1", 0, unit["boot1_sectors"]),
    }
    for key, index in (("factory", 2), ("env", 1), ("fip", 3)):
        plan[key] = raw_spec("mmcblk0p" + str(index), 0, parts[index - 1]["sectors"])
    plan.update(head_reserved=raw_spec("mmcblk0", 0, parts[0]["start"]),
                gpt_primary=raw_spec("mmcblk0", 0, 34),
                gpt_physical_tail=raw_spec("mmcblk0", unit["sectors"] - 33, 33),
                gpt_after_last_usable=raw_spec("mmcblk0", gpt["primary"]["last"] + 1, 33),
                gpt_indicated_alternate=raw_spec("mmcblk0", gpt["primary"]["alternate"], 1))
    if alternate_sector is not None:
        try:
            alternate = header(alternate_sector)
        except ValueError as error:
            gpt["alternate_header_inspection_error"] = str(error)
        else:
            require(alternate["entries_lba"] + 32 <= unit["sectors"], "Indicated GPT entries exceed device")
            plan["gpt_indicated_entries"] = raw_spec("mmcblk0", alternate["entries_lba"], 32)
            gpt["indicated_header"] = alternate
    for key, index in (("kernel", 4), ("rootfs", 5)):
        plan[key] = raw_spec("mmcblk0p" + str(index), 0, parts[index - 1]["sectors"])
    plan["permanent_config_files"] = dict(kind="archive", source_command="tar -C /permanent_config -czf - .")
    plan["settings_files"] = dict(kind="archive", source_command="tar -C / -czf - etc/config etc/dropbear")
    if overlay:
        plan["overlay_files"] = dict(kind="archive", source_command="tar -C /overlay -czf - upper")
    for item in plan.values():
        if item["kind"] == "raw":
            source = item["source"]
            maximum = unit["sectors"] if source["device"] == "mmcblk0" else (
                unit["boot" + source["device"][-1] + "_sectors"] if "boot" in source["device"] else
                parts[int(source["device"][-1]) - 1]["sectors"])
            require(source["start"] + source["sectors"] <= maximum, "Capture range exceeds source")
    return plan, gpt


def artifact_path(item, manifest_path, unit_dir):
    value = Path(item["path"])
    if not value.is_absolute():
        value = manifest_path.parent / value
    require(not value.is_symlink(), "Saved artifact must not be a symlink")
    return safe_within(value, unit_dir)


def legacy_source(command, spec):
    """Recognize only the reviewed legacy dd syntax; never execute a manifest command."""
    try:
        fields = shlex.split(command)
        if fields[0] != "dd" or any("=" not in x for x in fields[1:]):
            return False
        options = dict(x.split("=", 1) for x in fields[1:])
        if set(options) - {"if", "bs", "skip", "count"}:
            return False
        block = int(options.get("bs", "512"))
        source = spec["source"]
        return (options["if"] == "/dev/" + source["device"] and
                int(options.get("skip", "0")) * block == source["start"] * 512 and
                ("count" not in options or int(options["count"]) * block == spec["expected_bytes"]) and
                ("count" in options or (source["device"] != "mmcblk0" and source["start"] == 0)))
    except (IndexError, KeyError, ValueError):
        return False


def load_previous(path, unit, unit_dir, plan, first_sha):
    path = safe_within(path, unit_dir)
    old = json.loads(path.read_text())
    require("schema_version" not in old or old["schema_version"] == 2, "Unsupported backup manifest schema")
    if old.get("schema_version") == 2:
        assert_same_unit(old["unit"], unit)
        require(old["original_primary_sha256"] == first_sha, "GPT changed since original capture; do not mix factory states")
        require(old["plan"] == plan, "Capture scope/layout changed; review rather than merge silently")
        artifacts = old["artifacts"]
    else:
        # Legacy manifests retain their schema/history. Import only explicitly supplied evidence.
        base = old
        for _ in range(4):
            if "user_area_sectors" in base:
                break
            parent = base.get("source_manifest")
            require(parent, "Legacy manifest lacks geometry/provenance; review it before importing")
            base = json.loads(safe_within(parent, unit_dir).read_text())
        require(base.get("format_version") == 1 and base.get("backup_mode") == "factory_recovery_regions_and_p6_files",
                "Unsupported legacy backup format/scope")
        require(base.get("cid") == old.get("cid") and
                base.get("physical_serial_suffix", "").upper() == old.get("physical_serial_suffix", "").upper(),
                "Legacy geometry provenance belongs to a different unit")
        legacy_unit = dict(cid=old.get("cid"), physical_serial_suffix=old.get("physical_serial_suffix", "").upper(),
                           sector_size=base.get("sector_size"), sectors=base.get("user_area_sectors"),
                           boot0_sectors=base.get("boot_region_sectors"), boot1_sectors=base.get("boot_region_sectors"))
        assert_same_unit(legacy_unit, unit)
        artifacts = {}
        for key, name in LEGACY_NAMES.items():
            item = old.get("artifacts", {}).get(name)
            if not item or not item.get("verified") or key not in plan:
                continue
            spec = plan[key]
            if spec["kind"] == "raw":
                require(legacy_source(item.get("source_command", ""), spec), "Legacy source scope is ambiguous: " + key)
                require(item.get("source_sha256_before") == item.get("sha256") == item.get("source_sha256_after"),
                        "Legacy raw capture lacks stable source hashes: " + key)
            else:
                require(item.get("archive_members", 0) > 0, "Legacy archive lacks inventory evidence")
                allowed = {spec["source_command"]}
                if key == "settings_files":
                    allowed.add("tar -C / -czf - etc/config etc/dropbear etc/rc.local etc/init.d")
                require(item.get("source_command") in allowed, "Legacy archive scope is ambiguous")
            item = dict(item, spec=spec)
            item.setdefault("path", str(path.parent / name))
            artifacts[key] = item
        primary = artifacts.get("gpt_primary")
        require(primary and primary["sha256"] == first_sha, "Legacy original GPT does not match this stock state")
    reusable = {}
    for key, spec in plan.items():
        item = artifacts.get(key)
        if not item or item.get("verified") is not True:
            continue
        require(item.get("spec") == spec, "Saved artifact source scope changed: " + key)
        saved = artifact_path(item, path, unit_dir)
        require(saved.is_file(), "Saved verified artifact is absent/unsafe")
        # The plan contains only small raw regions and file archives, never a p6/full raw image.
        require(hash_file(saved) == (item["bytes"], item["sha256"]), "Saved verified artifact changed: " + key)
        if spec["kind"] == "raw":
            require(item["bytes"] == spec["expected_bytes"], "Saved region length mismatch")
            require(item.get("source_sha256_before") == item["sha256"] == item.get("source_sha256_after"),
                    "Saved raw capture lacks stable source hashes: " + key)
        reusable[key] = dict(item, path=str(saved), reused_from_manifest=str(path))
    return reusable, dict(manifest=str(path), prior_status=old.get("status"), failure=old.get("failure"))


def archive_inventory(path, unpacked_limit):
    inventory, total = [], 0
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            require(len(inventory) < 200000, "Archive member-count limit exceeded")
            parts = PurePosixPath(member.name)
            require(not parts.is_absolute() and ".." not in parts.parts, "Unsafe archive member path")
            total += member.size
            require(total <= unpacked_limit, "Archive unpacked-byte limit exceeded")
            entry = dict(path=member.name, type=member.type.decode(errors="replace"), bytes=member.size,
                         mode=member.mode, uid=member.uid, gid=member.gid, mtime=member.mtime, linkname=member.linkname)
            if member.isfile():
                import hashlib
                digest, count = hashlib.sha256(), 0
                with archive.extractfile(member) as source:
                    for block in iter(lambda: source.read(CHUNK), b""):
                        count += len(block)
                        digest.update(block)
                require(count == member.size, "Truncated archive member")
                entry["sha256"] = digest.hexdigest()
            inventory.append(entry)
    require(inventory, "Empty archive inventory")
    # tarfile can stop at the tar end marker before gzip's trailer. Read gzip to EOF too.
    import gzip
    expanded = 0
    with gzip.open(path, "rb") as source:
        for block in iter(lambda: source.read(CHUNK), b""):
            expanded += len(block)
            require(expanded <= unpacked_limit + 256 * CHUNK, "Expanded archive exceeds bound")
    return inventory


def capture_one(ssh, key, spec, directory, archive_limit, unpacked_limit):
    target = directory / (key + (".bin" if spec["kind"] == "raw" else ".tar.gz"))
    command = region_command(spec["source"]) if spec["kind"] == "raw" else spec["source_command"]
    item = dict(spec=spec, verified=False, path=str(target.with_name(target.name + ".partial")), started_at_utc=now())
    try:
        before = ssh.source_hash(command) if spec["kind"] == "raw" else None
        captured, partial = ssh.capture(command, target, spec.get("expected_bytes"),
                                        spec.get("expected_bytes", archive_limit), timeout=1800)
        item.update(captured)
        if spec["kind"] == "raw":
            after = ssh.source_hash(command)
            item.update(source_sha256_before=before, source_sha256_after=after)
            require(before == item["sha256"] == after, "Source changed/hash mismatch; preserve this attempt and review only " + key)
            item["verification_method"] = "stable before/after source hashes plus stream and saved-file SHA-256"
        else:
            require(not item["remote_stderr"].strip(), "Archive emitted warnings; consistency needs review")
            inventory = archive_inventory(partial, unpacked_limit)
            inventory_path = directory / (key + ".inventory.json")
            write_json(inventory_path, inventory)
            item.update(archive_members=len(inventory), inventory_path=str(inventory_path),
                        verification_method="SSH/tar exit zero without warnings; full archive/gzip read; stream/local SHA-256")
        # Destination belongs to this exclusive attempt directory; never overwrite prior evidence.
        require(not target.exists(), "Final artifact already exists")
        partial.rename(target)
        item.update(path=str(target), verified=True, completed_at_utc=now())
    except Exception as error:
        item.update(failure=str(error), completed_at_utc=now())
    return item


def backup(args, ssh, observation):
    root = args.destination_root.resolve()
    ensure_backup_volume(root)
    require(root.is_dir(), "Backup root must already exist")
    unit = observation["unit"]
    unit_dir = (args.unit_dir or root / ("clx-s20-" + args.serial_suffix)).absolute()
    require(not unit_dir.is_symlink() and unit_dir.parent.resolve() == root, "Unit directory must be a direct child of backup root")
    if not unit_dir.exists():
        require(not args.resume_manifest, "Cannot resume an absent unit directory")
        unit_dir.mkdir(mode=0o700)
    identity_path = unit_dir / "unit.json"
    require(not identity_path.is_symlink(), "Unit identity file must not be a symlink")
    if identity_path.exists():
        assert_same_unit(json.loads(identity_path.read_text()), unit)
    else:
        require(not any(p.name != ".capture.lock" for p in unit_dir.iterdir()) or args.resume_manifest,
                "Existing backup needs an explicit legacy manifest for identity review")
    with (unit_dir / ".capture.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        first = ssh.read("dd if=/dev/mmcblk0 bs=512 count=34", expected_bytes=34 * 512)
        gpt, _ = inspect_primary(first, unit["sectors"])
        alternate = ssh.read("dd if=/dev/mmcblk0 bs=512 skip=" + str(gpt["primary"]["alternate"]) + " count=1", expected_bytes=512)
        overlay = ssh.read("if test -d /overlay/upper; then printf PRESENT; else printf ABSENT; fi") == b"PRESENT"
        require(ssh.read("test -d /etc/config && test -d /etc/dropbear && printf PRESENT") == b"PRESENT", "Settings directories missing")
        plan, gpt = build_plan(observation, first, alternate, overlay)
        previous = args.resume_manifest
        if previous is None and identity_path.exists():
            existing = sorted(unit_dir.glob("capture-*/manifest.json"))
            if existing:
                previous = existing[-1]
            else:
                require(not any(p.name not in (".capture.lock", "unit.json") for p in unit_dir.iterdir()),
                        "No resume manifest found; review existing unit directory")
        reusable, history = ({}, None) if previous is None else load_previous(previous, unit, unit_dir, plan, sha(first))
        # Before mixing resumed artifacts, check immutable identity/boot regions against saved stock captures.
        for key in ("factory", "fip", "boot0", "boot1"):
            if key in reusable:
                require(ssh.source_hash(region_command(plan[key]["source"])) == reusable[key]["sha256"],
                        "Current " + key + " differs from original capture; stop resuming factory backup")
        missing = [key for key in plan if key not in reusable]
        needed = sum(plan[k].get("expected_bytes", args.archive_limit_mib * CHUNK) for k in missing) + 64 * CHUNK
        require(shutil.disk_usage(root).free >= needed, "Insufficient free space for bounded capture plan")
        if not identity_path.exists():
            write_json(identity_path, unit)
        directory = unit_dir / ("capture-" + stamp())
        directory.mkdir(mode=0o700)
        manifest = dict(schema_version=2, status="capturing", unit=unit, created_at_utc=now(),
                        original_primary_sha256=sha(first), original_gpt=gpt, plan=plan, artifacts=dict(reusable),
                        history=[history] if history else [], observation=observation, restore_tested=False,
                        whole_emmc_cloned=False, p6_raw_cloned=False, all_required_artifacts_verified=False,
                        recovery_plan_reviewed=False, seven_physical_ports_tested=False,
                        known_limits=["Live mounted rootfs/overlay is not an offline filesystem-consistent snapshot",
                                      "Tar does not guarantee ACL/xattr preservation or p6 UUID/superblock/free/deleted-space preservation",
                                      "P6 rebuild/mount/UUID dependencies require a reviewed recovery plan",
                                      "RPMB/controller internal state excluded; physical factory restoration untested"])
        target = directory / "manifest.json"
        write_json(target, manifest)
        try:
            ssh.read("set -o pipefail; true")
            for key in missing:
                check_live_cid(ssh, unit)
                print("Capturing " + key, flush=True)
                item = capture_one(ssh, key, plan[key], directory, args.archive_limit_mib * CHUNK,
                                   args.unpacked_limit_mib * CHUNK)
                manifest["artifacts"][key] = item
                write_json(target, manifest)
                # Preserve small evidence even if one mutable partition failed; never loop/retry automatically.
            manifest["all_required_artifacts_verified"] = all(manifest["artifacts"].get(k, {}).get("verified") is True for k in plan)
            manifest["status"] = "capture_verified_restore_untested" if manifest["all_required_artifacts_verified"] else "incomplete_review_failed_artifacts"
        except BaseException as error:
            manifest.update(status="interrupted_incomplete", failure=str(error))
            raise
        finally:
            manifest["completed_at_utc"] = now()
            write_json(target, manifest)
        print(json.dumps(dict(status=manifest["status"], manifest=str(target), reused_artifacts=len(reusable),
                              captured_artifacts=len(missing), restore_tested=False), indent=2))
        return 0 if manifest["all_required_artifacts_verified"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    inspect_parser = sub.add_parser("probe", help="Identity, RAM, eMMC health and mount evidence only")
    add_connection_arguments(inspect_parser)
    inspect_parser.add_argument("--record-dir", required=True, type=Path)
    backup_parser = sub.add_parser("backup", help="Capture/resume necessary recovery artifacts; never clone raw p6")
    add_connection_arguments(backup_parser)
    backup_parser.add_argument("--destination-root", default=DEFAULT_DESTINATION, type=Path)
    backup_parser.add_argument("--unit-dir", type=Path, help="Explicit existing legacy unit directory")
    backup_parser.add_argument("--resume-manifest", type=Path, help="Explicit prior new/legacy manifest; otherwise latest new capture")
    backup_parser.add_argument("--archive-limit-mib", type=int, default=2048)
    backup_parser.add_argument("--unpacked-limit-mib", type=int, default=16384)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        ssh = connection(args)
        if args.mode == "probe":
            require(args.record_dir.is_dir(), "Prepare an operation record directory outside the backup root")
            require(not args.record_dir.resolve().is_relative_to(DEFAULT_DESTINATION.resolve()), "Operation logs belong outside backup root")
            result = probe(ssh, args.serial_suffix)
            path = args.record_dir / ("health-" + stamp() + ".json")
            write_json(path, result)
            print(json.dumps(dict(record=str(path), unit=result["unit"], health=result["health"]), indent=2))
            return 0
        require(args.archive_limit_mib > 0 and args.unpacked_limit_mib > 0, "Archive bounds must be positive")
        result = probe(ssh, args.serial_suffix)  # Health is observed before the first long backup.
        return backup(args, ssh, result)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, "Read-only recovery stopped: " + str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
