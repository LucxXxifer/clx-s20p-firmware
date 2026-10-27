# Read-only eMMC health

Use the existing, identified S20P SSH session. For provisioning this is immediately after identity/SSH and before a long backup or any flash. A health query does not require a backup, reboot, Dropbear restart, software installation, debugfs mount, or storage stress test. Keep commands short in GUI Terminal and inspect output before the next step.

`scripts/s20p_recovery.py probe --help` provides reusable SSH capture; it saves a new timestamped record in the selected operation directory outside the backup root. `scripts/s20p_health.py` interprets saved raw values and a saved kernel log offline. The backup runner calls the same health check first and embeds it in per-unit recovery metadata.

## Locate the card

Read the card associated with the target block device, not the controller directory `mmc0`:

```sh
readlink -f /sys/class/block/mmcblk0/device
```

On the observed stock unit this resolves to a platform path ending in `mmc_host/mmc0/mmc0:0001`. `/sys/bus/mmc/devices/mmc0:0001` is a symlink to that card. The platform prefix and card number can change; do not hardcode them. A search that does not follow symlinks can miss readable attributes.

If `readlink -f` is unavailable, resolve with `cd -P`/`pwd -P` in a subshell. If the block-device link is absent, inspect only cards owning `block/mmcblk0`:

```sh
for card in /sys/bus/mmc/devices/mmc*:*; do
  if test -d "$card/block/mmcblk0"; then printf '%s\n' "$card"; fi
done
```

Stop resolution if multiple cards match; never pick the first card by name. After checking the resolved path, assign it to a task-specific variable and read individual fields. The following assumes the verified block-device link exists:

```sh
s20p_mmc_path=$(readlink -f /sys/class/block/mmcblk0/device)
```

```sh
cat "$s20p_mmc_path/cid"
cat "$s20p_mmc_path/name"
cat "$s20p_mmc_path/serial"
```

```sh
cat "$s20p_mmc_path/life_time"
cat "$s20p_mmc_path/pre_eol_info"
```

Record raw values, resolved path, target IP, physical SN suffix, CID, and observation time. Distinguish missing node, absent attribute, read failure, empty value, and an actual `0x00` reading. The preflight tool emits uppercase keys such as `MMC_LIFE_TIME` and explicit `UNAVAILABLE:<reason>` values. If lifetime is unavailable and `mmc` is absent, report that limitation; do not install it automatically. Already available `mmc extcsd read` may be used only as a read operation on the verified card.

## Interpret controller estimates

`life_time` has two independently reported estimates, Type A and Type B. Do not assume which NAND type each represents without this part's documentation.

| Raw code | Meaning for each A/B value |
|---|---|
| `0x00` | Not defined / no estimate |
| `0x01` | Estimated endurance used: 0–10% |
| `0x02` … `0x09` | Successive 10% used-endurance bands |
| `0x0A` | Estimated endurance used: 90–100% |
| `0x0B` | Exceeded estimated endurance |
| Other values | Reserved / cannot interpret |

| `pre_eol_info` | Spare-block condition |
|---|---|
| `0x00` | Not defined |
| `0x01` | Normal |
| `0x02` | Warning |
| `0x03` | Urgent |
| Other values | Reserved / cannot interpret |

Example: `life_time=0x01 0x01`, `pre_eol_info=0x01` means both estimates are in the 0–10% used band and spare blocks are normal. It does not establish precise remaining percentage, remaining years, or that the chip is new. Show both raw fields even if one is unknown or disagrees with the other.

Kernel MMC/I/O errors provide separate evidence. Keep source-command errors and stderr; no matching log lines is not a complete hardware test. Partition size, long clone time, hashes, and a successful read do not measure endurance. A hash mismatch on a live filesystem can reflect changed data and is not by itself evidence of wear. Pure reads do not correspond to NAND program/erase cycles.

## Flash gate

The default tool policy requires both A/B estimates defined in `0x01`–`0x09`, spare blocks `0x01`, and a successfully read kernel log without recognized storage errors. `0x0A` (90–100% used) is a conservative stop for flashing, as are exceeded/unknown/reserved estimates, spare warnings/urgent status or storage errors. An estimate such as `0x02` is not automatically a fault. Passing this gate is not a complete hardware test and cannot rule out unreported faults.

A stopped flash gate still permits read-only rescue backup. Do not install tools, stress storage, repeatedly reread huge partitions or delete evidence to force a pass. Save new timestamped observations; do not alter an old backup's verification status or claim a physical restore test from these readings.
