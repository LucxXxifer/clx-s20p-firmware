# Authorized FIP-only installation

This step writes the verified p3 `fip` partition while stock SSH is still available. It never writes BL2/boot0/boot1, GPT or firmware and never reboots. A user request to automate through U-Boot already authorizes this FIP step once all concrete prerequisites pass; do not repeat a permission question for that same scope.

## Review before running

1. Select the exact FIP, verified SHA-256 and source release asset evidence (saved GitHub release JSON with `html_url`, matching asset `name`, `size`, `digest`). Review source revision, board/eMMC variant and build compatibility separately; digest matching proves bytes, not compatibility. `0.4 Uboot` also contains BL2; never select it as FIP. See [local asset snapshot](local-assets-2026-10.md).
2. Use the new necessary-capture manifest with all required artifacts verified and a matching CID. A legacy capture can be resumed/imported with `s20p_recovery.py`; preserve its failed history. Review boot/system/factory restoration and p6 reconstruction/UUID dependencies. Capture success is not a tested factory restore.
3. Re-read health and inspect required diagnostics. Do not assert seven ports passed without exercising them. If full physical acceptance is required, unresolved ports block the write; if the user explicitly allows provisioning with limited acceptance, preserve the untested status.
4. Review the chosen U-Boot's GPT behavior and [repair/readback procedure](gpt-repair.md) for the live geometry, even if GPT upload will be later/manual. The known `384fdd8` defect has a validated conditional repair; it does not require reflashing FIP merely to repair the header.

Save a reviewed operation JSON **outside** the backup root. Populate `unit` from the exact manifest, not from a sample unit. Each `true` must describe an actually completed review/check:

```json
{
  "unit": {"cid": "LIVE_CID", "physical_serial_suffix": "A1B2", "sector_size": 512,
           "sectors": 0, "boot0_sectors": 0, "boot1_sectors": 0},
  "authorization_scope": "fip_only_no_reboot",
  "recovery_plan_reviewed": true,
  "recovery_plan": "Absolute path to the reviewed per-unit recovery plan",
  "recovery_plan_sha256": "SHA-256 of that exact reviewed plan file",
  "fip_board_compatibility_reviewed": true,
  "release_provenance_reviewed": true,
  "source_revision": "Verified repository and pinned revision",
  "gpt_repair_and_readback_procedure_reviewed": true,
  "required_diagnostics_passed": true,
  "seven_physical_ports_tested": false
}
```

This is a record of the agent's review and the user's existing authorization, not a request for another approval or a substitute for evidence. Do not set placeholder geometry/CID or fabricate a successful review. Keep diagnostics details and outstanding limits alongside it.

## Tool use

`scripts/s20p_fip.py --help` lists required target, manifest, review, release, FIP, digest and record-directory arguments. Without `--write` it performs preflight only; add `--write` when that FIP operation is already authorized. It revalidates local backup hashes, live identity/layout/health, source release digest/TOC, RAM staging, original FIP and protected raw regions. Operation records must be in an exclusive per-unit run directory outside `0.1 原厂备份`.

The write journal is created before staging, the exact payload is staged in verified `/tmp` tmpfs, and only those bytes are written to verified `/dev/mmcblk0p3`. The unchanged partition suffix is part of expected whole-partition SHA-256. Before/after images, independent remote/local hashes and protected-region comparisons are recorded. Rootfs remains a live mutable filesystem and is not a protected-region invariant.

Do not rerun an operation with an existing `fip-write-journal.json`. A timeout/lost acknowledgement can mean the write executed. Read the device state and journal first; no automatic retry or reboot. A new directory must not be used to evade that review. Failed/uncertain readback means stop and preserve powered state.

Only after successful readback, hand over the manual power/Reset action: power off, hold Reset, power on, keep held for 6 seconds, release. Confirm the actual U-Boot page separately. `fip_written_readback_verified_uboot_boot_untested` does not claim successful U-Boot boot, GPT validity, firmware boot, seven ports, or factory restoration.
