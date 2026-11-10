# Sources, application and evidence boundary

Reviewed on 2026-11-10. Third-party material is technical evidence, not permission to perform its commands. Directory contents can change; this skill bundles no firmware, private backup or third-party board image.

## Reference sources

- [Godsun S20 flashing article](https://godsun.pro/blog/s20-update-3.html): stock access, partition roles, FIP/U-Boot, GPT and firmware workflow.
- [Companion S20P/S20L/S20M directory](https://p.godsun.pro/route&%E8%B7%AF%E7%94%B1&%E7%9B%92%E5%AD%90iptv&%E7%A7%BB%E5%8A%A8%E8%AE%BE%E5%A4%87/MTK-%E8%81%94%E7%A7%91%E5%8F%91/%E8%B6%85%E7%BA%A7%E8%B7%AF%E7%94%B1S20P&S20L&S20M): its rendered directory notes and board photo were reviewed using existing authenticated browser access. A fetched HTML app shell alone does not establish the files were read.
- Within that directory, `说明.txt`/rendered instructions discuss backup, FIP, different kernel/rootfs/production layouts and `dual_boot.current_slot`; `楚留香S20P调整天线顺序+TTL顺序+修改无线功率.jpg` shows board UART silkscreen and cross-wiring. The photo also proposes calibration edits, which are not part of rescue.

The photo, with silkscreen upright, shows `VCC RX TX GND` left to right and recommends a 3.3V adapter, TX→RX, RX→TX, GND→GND, VCC open. Use the actual matching board revision/orientation to corroborate this; wire colors and another model's layout are not pin identification. [Receive-only wiring](rescue.md#uart-wiring-and-capture) needs only board TX and GND.

## How the skill applies these sources

| Source advice or limitation | Applied behavior |
|---|---|
| Access → backup → FIP → U-Boot → GPT/firmware | Retain the staged workflow and independently re-identify/read back at transitions. |
| Some passages say S20 variants are universal, others require model-specific FIP | Require actual board/DRAM/storage/layout compatibility. S20P tests do not certify S20L/S20M. |
| Store backups on p6 or stage large backups in `/tmp`; clear caches first | Stream to an external host, preserve original p6, and avoid RAM exhaustion. Small bounded tmpfs staging is reserved for the reviewed FIP write. |
| `dd` completion, UI `complete` or a partition MD5 is treated as success | Require exact byte counts and independent full readback; account for the preserved partition suffix. CRC32 and MD5 are not new SHA-256 captures. |
| Example GPT geometry, `sgdisk -e`, rebuilding/formatting a data partition | Derive current geometry, validate both GPT copies, and repair only the supported defect. Partition creation/formatting requires its own reviewed scope. |
| Legacy `.bin`, FIT `.itb`, production slot and boot-environment commands | Match the installed bootloader and actual layout. Do not apply `dual_boot.current_slot` commands by firmware extension or historical tutorial alone. |
| TTL photo also edits factory Wi-Fi power | Use pin evidence only; preserve the per-unit factory/calibration bytes unchanged. |
| Published default passwords | Keep credentials interactive and out of the skill, test fixtures and release notes. |

Directory listings and filenames establish availability, not digest, binary format, board compatibility or a successful BROM rescue. Obtain and inspect a chosen image separately before any write. No automatic bulk download or execution of the site's files is implied.

The reviewed directory separates `BL2`, `uboot-fip-不死` and `gpt分区表`: BL2 includes SP2 and 2666-labelled images; FIP includes both a legacy/FIT image and a newer SP2 DHCP build; GPT includes rootfs/production 512M and 1024M pairs. These are different roles and layouts, not interchangeable rescue files. This review inspected their listings and instructions, not every binary's contents or suitability for a particular board.

## Physical evidence distilled into this revision

On one S20P, fourteen UART captures showed the same missing `/permanent_config/unique_id` error followed by `Kernel panic - not syncing: very bad firmware`, with an approximately 20.6-second cycle. Receive-only 115200 8N1 and short Reset events during preinit enabled stock failsafe access. Runtime hostname was `(none)` while stock ROM/config identified `S20P`; physical jacks did not follow the assumed Linux naming.

That operation completed the required factory backup, exact FIP write/readback, a GPT matching 241,696,768 user sectors, firmware readback and a normal new-OS boot. Its GPT pair was already valid, so no repair was applied. The working WAN negotiated 2.5G/full and passed router-originated network checks despite a dark LED. The original missing identity file was not reconstructed.

These observations inform the rescue choices; they are not an end-to-end hardware validation of the new helper scripts. Automated tests use synthetic identities/bytes and a local pseudoterminal; no physical UART, BROM upload, BL2 restore or factory restoration is tested by them. Seven-port traffic, Wi-Fi client throughput, M.2 and long-term stability require separate physical acceptance. Per-unit logs, SN/CID, SSH keys and backups stay outside the public package.
