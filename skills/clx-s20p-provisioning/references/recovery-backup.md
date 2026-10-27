# Necessary factory recovery backup and resume

Preserve this unit's original boot/system/identity and current p6 files. This is capture verification, not a guarantee of restoration. Do not clone 100+ GB of raw p6 by default or hash it before preserving small boot/factory evidence.

## Preflight

- Match physical SN suffix, board identity, CID, RAM and actual user/boot-region sector counts. CID/card serial is not the case SN. New names are `clx-s20-<UPPERCASE suffix>`, e.g. `clx-s20-A1B2`; existing legacy names stay. A same-suffix/different-CID collision requires a unique naming choice based on the full physical serial; preserve the first directory.
- Check the external volume, destination and free space. Only per-unit backups and recovery metadata belong under `0.1 原厂备份`; tools, ER01 backups and operation logs stay outside it. Directory identity is immutable; do not delete a directory to bypass a refusal.
- Check health immediately after access. The runner observes health before capture but allows rescue backup when the flash gate fails.
- Read GPT/sysfs/mounts, confirm actual starts/sizes/labels and `/dev/mmcblk0p6` mounted at `/permanent_config`. The supported stock layout is p1 `u-boot-env`, p2 `factory`, p3 `fip`, p4 `kernel`, p5 `rootfs`, p6 `permanent_config`; ranges are derived dynamically. Unfamiliar layouts require review.
- Original stock primary GPT can have CRC-valid `alternate_lba=1` and a lower last-usable LBA. Preserve and report it; do not apply strict postflash GPT rules or repair it during capture.

## Required scope

| Artifact | Scope |
|---|---|
| Identity/layout/health metadata | CID, label suffix, RAM, geometry, GPT fields, sysfs, mounts and available filesystem UUID/type evidence |
| boot0/boot1 | Entire addressable hardware boot partitions; leave `force_ro` unchanged |
| p1–p5 | Raw verified label/ranges; this unit's factory is mandatory |
| Head/reserved boot area | LBA 0 through the sector before p1, including original primary GPT |
| GPT evidence | First 34 and physical final 33 sectors for the reviewed 512-byte/128-entry GPT; bounded indicated alternate header/entry array and area after last usable LBA |
| p6 files | `/permanent_config`, including dotfiles; stream, never stage on source eMMC |
| Settings/overlay | `/etc/config`, `/etc/dropbear`, and `/overlay/upper` when present |

The runner deliberately supports the reviewed 512-byte, 128×128-byte GPT. It fails on an unfamiliar format or oversized boot/system region rather than guessing. Other discovered boot dependencies need a reviewed capture extension.

## Run and resume

Use a known authenticated OpenSSH control connection; see [access/network](access-network.md). Examples run on the Mac and contain illustrative target values:

```sh
python3 "$HOME/.agents/skills/clx-s20p-provisioning/scripts/s20p_recovery.py" backup \
  --host 192.168.8.1 --serial-suffix A1B2 \
  --control-path /tmp/s20p-A1B2.sock
```

The existing backup root must be present. A first run creates `clx-s20-A1B2/unit.json` and an exclusive timestamped `capture-*/manifest.json`; the same command resumes the latest new manifest. A local lock prevents concurrent captures in that directory. No device write, reboot, service stop, mount change, `force_ro` change or p6 deletion occurs.

For a reviewed legacy directory, explicitly select its manifest:

```sh
python3 "$HOME/.agents/skills/clx-s20p-provisioning/scripts/s20p_recovery.py" backup \
  --host 192.168.8.1 --serial-suffix A1B2 --control-path /tmp/s20p-A1B2.sock \
  --unit-dir "/Volumes/Storage/CLX-S20P/0.1 原厂备份/clx-s20a1b2" \
  --resume-manifest "/Volumes/Storage/CLX-S20P/0.1 原厂备份/clx-s20a1b2/backup-manifest.json"
```

The importer supports the prior necessary-backup/continuation schema with CID, suffix, geometry provenance and verified artifact source evidence. It does not guess unknown schemas or import a full/p6 raw image as a new artifact. Unsupported legacy evidence requires a small reviewed adapter, preserving originals. Valid existing p6 raw evidence may be considered separately in the recovery plan without rereading/hashing 100 GB just to resume.

On resume the runner checks unit, original primary GPT, artifact scope and saved small-artifact hashes. It checks immutable boot/factory/FIP against the live unit before mixing captures. It retains verified results and recaptures only missing/failed artifacts into a new timestamped directory. It never overwrites old manifests, failed `.partial` bytes or successful images. No automatic infinite retry: one failed artifact is recorded while remaining small recovery work proceeds. A changed GPT/FIP stops factory-state mixing.

Archives have explicit compressed and expanded limits (`--archive-limit-mib`, `--unpacked-limit-mib`); free-space planning reserves these bounds. If p6 files exceed them, inspect the actual size and choose an appropriate reviewed limit/method. Do not silently omit data to pass.

## Verification and limits

- Raw regions: independent source hashes before/after, exact streamed byte count, SSH/dd status, stream hash, fsync and local reread hash. Binary stdout never goes to Terminal; stderr stays separate. A mismatch remains a failed attempt. Live p5 can change; this does not by itself establish wear or transport failure.
- Archives: SSH/tar exit zero without warnings, local stream/reread hash, inventory of all members, read file contents to validate integrity, and read gzip through EOF. Contents and keys remain local/private; reports show counts/status. A fresh independently generated compressed tar need not hash identically.
- Matching live hashes still do not establish an offline consistent filesystem snapshot. BusyBox tar may omit ACLs/xattrs; file-level p6 does not preserve its UUID/superblock, exact filesystem metadata, deleted/free space or a full block image. RPMB/controller internals are excluded.

The manifest separates `all_required_artifacts_verified` from `recovery_plan_reviewed` and `restore_tested`. The recovery plan must cover boot/system/factory restoration, GPT reconstruction on exact geometry, p6 filesystem creation/mounting/UUID dependencies and restoring files. Record available UUID/type and inspect boot mount references privately. Never format or restore merely to validate capture. Keep `restore_tested: false` until a real restore test.

## Optional full raw image

First preserve necessary small artifacts. Only with explicit user opt-in, use `backup_restore_image.py --full-image`; it captures both boot regions first, then the entire user area and independent source hashes. It has no resume and refuses an existing child directory. Preserve failed captures rather than deleting them. Allow capacity/time for the whole eMMC.

For an additional full image under an already verified unit:

```sh
s20p_full_root="/Volumes/Storage/CLX-S20P/0.1 原厂备份/clx-s20-A1B2/full-image-$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$HOME/.agents/skills/clx-s20p-provisioning/scripts/backup_restore_image.py" \
  --host 192.168.8.1 --serial-suffix A1B2 --full-image \
  --destination-root "$s20p_full_root"
```

This opt-in tool creates its own `clx-s20-A1B2` child inside that timestamped root. Full-image mismatch remains a failure even when individual boot artifacts passed. It does not prove restoration or replace a reviewed recovery plan.
