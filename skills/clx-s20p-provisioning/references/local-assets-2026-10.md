# CLX-S20P reviewed image examples (2026-10)

These are filenames and image digests reviewed during initial development. The FIP and both primary GPT examples were checked on 2026-10-27. Recheck each selected file and its source release before a device operation: a digest identifies bytes, not compatibility or a recommended current version. No firmware, bootloader, GPT binary, per-device backup or private operation record is distributed with this skill.

## FIP selection

A previously consulted guide used `mt7986_clx_s20p-2g-emmc-fip.bin`, 674,201 bytes, MD5 `52653da34bd4ad3c6e914591b9ff7cf0`. The separately reviewed FIP below is a different build, 729,896 bytes, with U-Boot string `2025.07.1-Mediatek` and build tag `384fdd8a`. Do not use the guide's size/digest for that file. BL2 is a separate component; verify the exact source/board/BL2/FIP compatibility, not merely matching dates.

Do not clear `/permanent_config` or store backups on the eMMC being captured. Preserve that unit's files and small recovery regions first, as described in [recovery backup](recovery-backup.md).

| Example filename | Size | SHA-256 | Role |
|---|---:|---|---|
| `bl2-mt7986_clx_s20_SP2_md5-9cbc4985fdb683184d2d94a86050d950_20260831-143720.img` | 155,600 bytes | `59639f1560e5f343936e244cf03689930689237638f59e86ee29ee878b9964cf` | Reviewed separately; BL2 writing is outside the default operation. |
| `fip-mt7986_clx_s20_SP2-Yuzhii-dhcpd_md5-165678f024494f908b833507e2b4e41d_20260831-143720.bin` | 729,896 bytes | `2ab6bd44cb7a782953ea97330d0b9d04e5d62b2a3872e96a8d786354b3220752` | Contains `384fdd8a`; inspect GPT behavior for this source revision. |
| `gpt_2048mb-s20p-241696768-sectors-primary.bin` | 17,408 bytes | `f1c285e112c4399219a90b91fbae2d703b2c08d6799af1a3c19adcc6df691d7a` | Primary GPT for exactly 241,696,768 user-area sectors. |
| `gpt_2048mb.bin` | 17,408 bytes | `6e9af0c1fe5c2a1ae0612833d608b4cf2f5c2845ea7c3eb55a23250ff7871099` | Primary GPT for exactly 244,277,248 user-area sectors. |

## GPT capacity and known secondary-header defect

The 241,696,768-sector example has primary alternate LBA 241,696,767; its reviewed layout gives rootfs 2 GiB and p6 `nas_storage` through sector 239,682,559. The 244,277,248-sector example has primary alternate LBA 244,277,247 and last usable LBA 244,277,214. Both cards are marketed as 128 GB; filenames and marketed capacity cannot select the correct GPT.

Pinned source [Yuzhii0718/bl-mt798x-dhcpd at 384fdd8a743506a9c3ea73289d419f9d6519d61f](https://github.com/Yuzhii0718/bl-mt798x-dhcpd/tree/384fdd8a743506a9c3ea73289d419f9d6519d61f), `backup_secondary_gpt`, omits the secondary `alternate_lba=1` assignment. Header/entries CRCs can pass despite that self-reference. An earlier device operation at 244,277,248 sectors validated a geometry-derived, one-sector correction with complete GPT readback and protected-region checks. This is separate from the new automation's offline tests.

Always inspect both GPT copies after upload. A valid pair needs no repair. Use [the conditional repair procedure](gpt-repair.md) only for the recognized defect; do not reuse another device's final sector or RAM addresses. A future fixed U-Boot can avoid repair after readback passes, but upgrading U-Boot alone does not rewrite an existing bad GPT.

## Firmware and factory recovery

The companion [firmware repository](https://github.com/LucxXxifer/clx-s20p-firmware) provides fixed releases and daily upstream candidates with their own release notes/checksums. Review the exact model, image format, layout and release status before selection. The skill does not auto-select the newest candidate or upload firmware.

A 2-GiB rootfs GPT changes allocation. Factory recovery must first restore/reconstruct that unit's original map, then restore its corresponding boot/system/factory regions and rebuild p6 with reviewed filesystem/mount/UUID requirements. A similarly named third-party backup is not evidence of those regions: previous comparisons found files named `p1_bl2.bin` and `p3_factory.bin` actually contained stock `u-boot-env` and `fip` data. Resolve roles from GPT labels, ranges and provenance, never filenames alone. Each physical unit still needs its own factory/boot recovery artifacts; this does not require a default 100+ GB raw p6 clone.
