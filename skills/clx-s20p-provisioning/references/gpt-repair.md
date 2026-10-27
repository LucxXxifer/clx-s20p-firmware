# GPT validation and conditional secondary-header repair

Use after an authorized GPT upload; GPT/firmware uploads remain manual when reserved by the user. All repair addresses/data must derive from **this unit's live readback and actual geometry**. Never reuse another unit's final sector or invent CRCs/RAM addresses.

## Select and read back

1. Revalidate target identity, observed user-area sectors and selected exact primary GPT asset. Marketed 128 GB does not imply identical sectors. Validate its header/entries CRCs, PMBR capacity, partition ranges and no overlap using `scripts/s20p_gpt.py --first34 <asset> --sectors <live-count>`; this does not validate the secondary copy that U-Boot will generate.
2. After upload, capture actual first 34 and physical final 33 sectors for the reviewed 512-byte/128×128-byte format. In the observed HTTP build, read-only `/backup/main` range form fields are `mode=range`, `storage=mmc`, `target=raw`, `start=<byte>`, `end=<exclusive byte>`. Bind the discovered Ethernet interface, bypass proxies, save binary directly and check exact lengths; never print raw bytes. Revalidate endpoint semantics against the selected U-Boot source before use.
3. Run strict pair validation:

   ```sh
   python3 "$HOME/.agents/skills/clx-s20p-provisioning/scripts/s20p_gpt.py" \
     --first34 /absolute/run/first34.bin --final33 /absolute/run/final33.bin \
     --sectors LIVE_SECTOR_COUNT
   ```

   Replace the illustrative paths/count before execution. Check reciprocal pointers, current locations, shared disk GUID/usable bounds, equal entry arrays/CRCs, partition bounds/no overlap and PMBR geometry. **Already valid → no repair.** A fixed U-Boot needs no agent repair once this verification passes. Upgrading U-Boot alone does not modify an already defective GPT.

## The only supported repair case

Observed source `Yuzhii0718/bl-mt798x-dhcpd@384fdd8a743506a9c3ea73289d419f9d6519d61f`, `backup_secondary_gpt`, fails to set the secondary header's `alternate_lba` to 1. On the observed build, secondary `current_lba=last` and `alternate_lba=last`; both header/entry CRCs can still pass. CRC-only checking misses the defect.

`s20p_gpt.py --prepare-secondary-header <new-local-path>` allows **only** that known secondary self-reference while requiring every other strict pair check to pass. It derives a 512-byte candidate by changing alternate LBA (bytes 32–39) to 1 and recomputing header CRC (bytes 16–19), then validates the resulting whole pair. Unexpected pointers/CRC errors/mismatched arrays/ranges are rejected. A valid pair generates no candidate. The tool is offline and never writes a device.

Original stock backup evidence is a different mode: its primary may itself have `alternate_lba=1`. `--stock-evidence` records that nonstandard state without repairing it; do not apply the postupload repair to original capture.

## Authorized one-sector application

Only after the user has authorized repair, use the reviewed U-Boot console protocol and preserve an exclusive transcript. No unattended generic disk repair command is provided: console support and safe RAM must be established on the live build.

1. Ensure one console consumer. An owned browser on `/console.html` can auto-poll and consume agent output; move it to a safe homepage before CLI calls. Never reload `/flashing.html`, which can retrigger a write. Do not alter unrelated tabs. `scripts/s20p_uboot_console.py` takes explicit origin/interface and unique transcript names; commands are never retried automatically.
2. Review supported commands once. Derive unused RAM and separate readback space from `bdinfo`/reservations; check `base=0`. Do not inherit earlier RAM addresses. Explicitly select the user area with `mmc dev 0 0` before every user-area write; leave it selected when finished. The observed build lacks `mmc reg read`, `hash` and `md5sum`; do not repeat unsupported commands. `help` can return nonzero despite useful text; inspect the transcript instead of treating it as a write failure.
3. Re-read the physical final sector into reviewed safe RAM and dump exactly 512 bytes. Parse the dump locally and compare it byte for byte with this unit's saved original secondary header. If changed, stop and obtain fresh GPT captures. Keep primary/secondary arrays and protected boot/factory/reserved-region baselines.
4. Prepare **RAM only**: modify only differing candidate bytes/words in the permitted alternate-LBA and CRC fields using supported memory commands. Derive offsets/values from local original/candidate bytes. Dump all 512 bytes, parse locally, require an exact candidate match, and validate the complete proposed GPT pair again. No MMC write until these checks pass.
5. Journal the single final-LBA, one-sector write before issuing it. Recheck selected MMC device/user area and geometry. Issue exactly one write of the verified RAM candidate to `live_sectors - 1`, count 1. A missing/nonzero HTTP response leaves an unknown outcome; **read back, never blindly repeat**.
6. Independently read the sector into separate safe RAM and compare all 512 bytes. Download complete first34/final33 again; run strict pair validation. Require unchanged primary/entry arrays, factory, reserved boot/FIP regions and other protected areas. Compare boot-region evidence using the actual method available; CRC32 comparisons must not be called new SHA-256 captures.
7. Record before/candidate/after hashes, derived LBA/geometry, exact commands, write count/outcome, complete pair verification and protection results. Keep all originals. This establishes GPT repair, not firmware boot or factory restoration. Preserve state on any incomplete check.

An observed device operation on 2026-10-27 validated this flow at 244,277,248 sectors: one final-sector write, complete GPT pair readback and unchanged protected evidence. The reusable automation has separate offline tests; that earlier operation is not an end-to-end test of the new tools. Units with 241,696,768 sectors need their own geometry-derived candidate. Per-unit identities, binary repair fixtures and operation transcripts are not bundled.
