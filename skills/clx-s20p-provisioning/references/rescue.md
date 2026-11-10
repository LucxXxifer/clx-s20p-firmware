# Rescue, receive-only UART and stock failsafe

Use when stock Web/Wi-Fi is absent, the host loses Ethernet repeatedly, or normal access cannot survive long enough to back up. Keep original logs and failed captures private. Source material and the tested hardware boundary are in [sources and evidence](sources-and-evidence.md).

## Choose the next access path from evidence

| Observation | Next action |
|---|---|
| macOS changes DHCP/DNS, but UART uptime continues | Inspect the selected network service and configuration owner; follow [host networking](access-network.md). Do not infer a router reboot. |
| Link drops or an LED is dark, without boot evidence | Map the physical connector, carrier, peer and counters; use [component checks](component-checks.md). |
| Repeated kernel starts followed by the same panic | Preserve a few complete cycles; try stock preinit failsafe before rewriting boot firmware. |
| A failsafe prompt appears | This is an invitation, not proof of entry. Verify the active mode, stable uptime and the target over the available network. |
| Existing U-Boot works | Revalidate the board, geometry and images; follow the existing FIP/GPT/firmware gates. Do not rewrite a working boot chain just to diagnose Linux. |
| No legible UART output | Check adapter enumeration, receive path, ground, pins, baud and power first. Silence alone does not prove dead BL2/eMMC. |
| Boot chain cannot load and no usable U-Boot exists | Preserve logs and any readable same-unit data. BROM/`mtk_uartboot` or eMMC programmer recovery needs separately reviewed board/DRAM/BL2/FIP compatibility and actual tool instructions. No generic BL2-writing recipe is validated here. |

## UART wiring and capture

1. Read the router's DC input label and the adapter's actual logic-level documentation. `220V` on a charger is its AC input, not proof of the router's required DC voltage/current. Use matching output voltage, polarity and sufficient current; do not use the UART adapter to power the board.
2. With router power disconnected, identify GND/TX/RX/VCC from the **same board revision and photo orientation**, silkscreen or measurement. Do not infer pins from wire colors, a square pad, another S20 variant, or an unlabelled four-hole row. A board-specific photo is corroboration, not a universal left-to-right pinout.
3. Start with **router GND → adapter GND, router TX → adapter RX** only. Leave router VCC, adapter VCC and adapter TX disconnected. RX/TX cross when bidirectional access is later justified. A CH340 chip name or `3.3V/5V` power pin does not establish TX logic voltage. Without a meter/verified specification, keep adapter TX off the board.
4. Unsoldered loose Dupont wires can short or lose contact. Use fitted headers, a suitable fixed pogo/PCB fixture, or qualified soldering; do not hold bare contacts together while powered. If contact cannot be secured, stop physical hookup until it can.
5. Identify the serial device after plugging in only the adapter. On macOS compare `ioreg -p IOUSB -l -w 0` and the actual `/dev/cu.*` inventory before/after. Use the newly identified device, never a guessed glob or the Bluetooth port. Only one terminal/capture may own it.
6. Start receive-only 115200 baud, 8 data bits, no parity, 1 stop bit, no hardware/software flow control. Prepare capture before the human powers the router. This tool disables echo and never writes serial bytes:

```sh
s20p_skill_dir="/absolute/path/to/clx-s20p-provisioning"
python3 "${s20p_skill_dir:?}/scripts/s20p_uart.py" capture \
  --device /dev/cu.REPLACE_WITH_VERIFIED_ADAPTER \
  --output-prefix /absolute/private-run/boot-01 --seconds 60
python3 "${s20p_skill_dir:?}/scripts/s20p_uart.py" analyze \
  --log /absolute/private-run/boot-01.bin --output /absolute/private-run/boot-01-analysis.json
```

The CLI creates exclusive private `.bin` and `.jsonl` files; `.bin` retains received bytes and `.jsonl` records chunk offsets/time. It rejects an existing output, keeps partial evidence on disconnect and can stop with Ctrl-C. Adapter open may toggle modem-control signals: keep DTR/RTS disconnected too. Other listed baud rates are diagnostic alternatives only when board evidence supports them. This receiver does not offer serial login, boot-menu selection or BROM upload.

For silence/garbling, confirm serial enumeration, common ground and that **board TX reaches adapter RX** before changing baud. An adapter-only loopback may test the adapter with the board completely disconnected, using a reviewed separate terminal; it proves neither board pins nor board-safe voltage. Repeated intact boot logs distinguish a real reboot from loose serial contact. Save the panic stack, earlier filesystem errors and boot timing; do not identify the cause from one final line.

## Stock failsafe

Observe the actual preinit prompt and timing in the boot log. On the validated stock unit, short press/release events on Reset during that window entered failsafe while UART remained receive-only. Ask the human to perform those short presses; long hold/factory-reset semantics are different. The observed time after power-on is not universal. If timing is missed, first preserve the failed cycle, then make one deliberate next attempt guided by the log.

Verify a `FAILSAFE MODE` marker or equivalent current mode evidence and stable uptime. Determine which physical jack reaches the rescue network: stock logical `lan1` may be a differently numbered case connector. Move one known cable at a time and compare carrier/port events; never publish one unit's mapping as universal. The rescue subnet must be observed; preserve Wi-Fi Internet and keep the wired default gateway/DNS empty.

Reuse trusted SSH if available. Otherwise use the identified isolated stock Telnet/console, enable Dropbear as in [access](access-network.md), and verify fresh SSH. Do not blindly run `firstboot`, `jffs2reset`, `mount_root`, cache deletion, formatting or identity-file creation. If a write/mount is necessary to obtain access, inspect the actual filesystem and command behavior first within the user's recovery scope.

## Failsafe identity and SSH

Stock failsafe can report runtime hostname `(none)` while both ROM and configured hostname are `S20P`. The ordinary identity gate remains strict. `--stock-failsafe-evidence` is a narrow supported exception for this case, **not** a flag to skip identification or eMMC health.

Before creating the private evidence JSON, independently confirm physical label/board revision and isolation to the intended router, then collect on that same boot:

- Actual runtime hostname `(none)`, compatible `mediatek,mt7986a-emmc-rfb`, reviewed 2-GiB RAM and `/tmp/failsafe` present.
- `uci -q get system.@system[0].hostname` and `uci -c /rom/etc/config -q get system.@system[0].hostname` both return `S20P`.
- `cat /proc/sys/kernel/random/boot_id`, CID of the card owning `mmcblk0`, logical sector size, user-area and both hardware boot-region sector counts.
- SHA-256 SSH host-key fingerprint verified through an already trusted console/isolated authenticated access path, compared with the SSH key, and present under the exact target host in a dedicated `known_hosts` file. A new `ssh-keyscan` response or an unreviewed acceptance prompt is not independent verification.

JSON shape (illustrative placeholders; replace with verified values, do not copy the `true` without the physical check):

```json
{
  "schema_version": 1,
  "mode": "stock_failsafe",
  "host": "192.0.2.1",
  "physical_serial_suffix": "A1B2",
  "physical_identity_confirmed": true,
  "console_host_key_sha256": "SHA256:REPLACE_WITH_VERIFIED_FINGERPRINT",
  "ssh_host_key_sha256": "SHA256:REPLACE_WITH_SAME_FINGERPRINT",
  "boot_id": "REPLACE_WITH_CURRENT_BOOT_ID",
  "unit": {
    "cid": "REPLACE_WITH_THIS_CARD_CID",
    "sector_size": 512,
    "sectors": 0,
    "boot0_sectors": 0,
    "boot1_sectors": 0
  }
}
```

Run `python3 "${s20p_skill_dir:?}/scripts/s20p_recovery.py" probe` with the actual `--host`, `--serial-suffix`, authenticated `--control-path`, `--known-hosts`, `--stock-failsafe-evidence` and an existing private `--record-dir` directory outside the backup root. Use the same connection arguments for `backup`, `rescue-small` or FIP. The tool rechecks the live marker, ROM/config, trusted-key record, boot ID, CID/geometry and RAM, retaining the real hostname in its output. Reboot, changed host/key/CID or missing mode evidence requires a fresh review; never rename the router or weaken SSH checking to pass.

## Save small regions before attempting repair

Use ordinary `backup` when the verified original p6 mount and settings are readable. If they are not, `rescue-small` preserves boot0/boot1, factory, env, FIP, reserved head and bounded GPT evidence without mounting/writing p6 or archiving settings:

```sh
python3 "${s20p_skill_dir:?}/scripts/s20p_recovery.py" rescue-small \
  --host 192.0.2.1 --serial-suffix A1B2 \
  --control-path /absolute/private-run/ssh.sock \
  --known-hosts /absolute/private-run/known_hosts \
  --stock-failsafe-evidence /absolute/private-run/failsafe.json \
  --destination-root /absolute/external-backup-root
```

It still requires a parseable stock GPT matching sysfs and the identified card. It captures only bounded raw regions and verifies byte counts, independent source hashes and local reread. Each invocation creates a new `rescue-*` set; it is not a resumable full backup. `selected_artifacts_verified` may be true, but `all_required_artifacts_verified` stays false and FIP preflight rejects it. After stabilizing access, run normal `backup`: it can import the latest matching small set, recheck saved hashes and current immutable regions, then capture the missing system/settings/p6 artifacts into a new `capture-*` manifest without rewriting the rescue record. Changed identity, GPT, scope or source bytes stop the transition. Complete this full backup and its recovery plan before flashing. For an unparseable GPT, preserve raw reads through a separately reviewed bounded plan; do not relax range checks or repair the table to make backup pass.

## Data correction means identifying the faulty layer

| Evidence | Permitted conclusion / next step |
|---|---|
| `Error 2 opening /permanent_config/unique_id` then `very bad firmware` on repeated boots | A missing-file startup failure is observed. Preserve p6 files/mount evidence and this unit's factory/boot regions. Do not fabricate `unique_id`, copy another router's identity or clear p6. A compatible replacement OS may boot while the original fault remains. |
| Copy length/hash mismatch | The transfer is unverified. Keep partial bytes; inspect source stability, transport and storage errors. Re-read immutable bytes independently; do not call this hardware ECC repair. |
| Known self-referential secondary GPT, all other checks valid | Follow the existing [one-sector conditional repair](gpt-repair.md) using current readback. A valid pair needs no repair. |
| CRC, arrays, partition bounds or geometry unexpectedly wrong | Stop writes, preserve both GPT copies and investigate. No automatic `sgdisk -e` or transplantation of another unit's tail. |
| MMC timeouts, I/O errors, failed health or changing immutable bytes | Preserve small reads first; stop flashing and follow [eMMC health](emmc-health.md). SHA-256/GPT CRC do not repair NAND, and no controller-ECC reprogramming is provided. |

Do not claim factory recovery from a successful new-OS boot. The validated rescue preserved the original missing-file fault rather than rebuilding it; restoring that original image may reproduce its panic.
