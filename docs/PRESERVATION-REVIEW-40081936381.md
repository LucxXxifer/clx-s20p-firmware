# Preservation review for run 40081936381

This run reached `make checksum` and passed signed-feed capture and publication
content audit. Publication correctly failed at the image preservation gate:
four original Lucky r26 frontend paths were absent after a lock-time r28 update.
The failed evidence is not a validated candidate.

The repair recovers the original Lucky r26 and LuCI source revisions, geosite
release and AxonHub source/patch set. See `ORIGINAL-SOURCE-RECOVERY.md` and
`ci/sources.lock.json`. Fixed source bytes reproduce the missing frontend and
the changed original scripts, translations, templates and data; the build does
not inject files extracted from the original ROM.

## Baseline parser correction

The original SquashFS reader reset an out-of-block metadata offset to zero
instead of subtracting the preceding block length. The `/usr/bin/head` target
starts across the 8192-byte boundary. Independent `unsquashfs 4.6.1` confirms
`../../bin/busybox`; the old baseline recorded binary metadata as part of the
target. All 4334 records were checked against the original ROM again: only
this target/hash record and parser provenance change. Original image bytes,
package inventory, required paths and safety constraints are unchanged.
`ci/baseline/provenance-corrections.json` records the exact reader patch,
before/after hashes and independent readback evidence.

## Exact reviewed differences

`ci/approved-differences.json` contains only canonical SHA256 difference IDs.
The public evidence in `ci/reviewed-differences/` binds each reviewed record:

- 485 added Python/ipset/USB dependency files: native signed-index verification,
  offline fetch, exact APK payload identity and actual SquashFS content,
  type/mode/UID/GID/target plus installed DB ownership. The four r28 Lucky
  assets are excluded; they do not excuse missing original paths.
- One exact `/etc/apk/world` delta: every original explicit root remains after
  the checked same-build kernel identity substitution; all 14 new roots are
  in the requested increment dependency closure.
- Two exact generated LuCI version-file contents: the recovered original
  revision is unchanged, and the upstream tools truthfully label its pinned
  detached checkout. No original branch name is fabricated.
- The exact Lucky core `3.2.4-r3` to `3.4.0-r3` candidate version delta: the old
  public archive is unavailable. The fixed full `wanji_docker` arm64 release
  has an official SHA256; upstream `sstrip -z` reproduces the audited cloud
  binary exactly. Real Linux `-info`/`-h` execution confirms the r26 helper's
  required CLI surface in both versions. Original UI/config/service code is
  recovered. Live service behavior and private configuration migration remain
  unverified and require attended rollout acceptance.

Different future contents or metadata generate different IDs and remain
blocked for review. Missing paths, signatures, ABI, provenance and publication
checks retain their existing failure behavior.

The fresh public signing key is handled separately because each build creates
a different valid key. It is expected only when image bytes exactly match the
preserved public key and the same capture report records verified signatures,
native payload identity, exact installed versions and kernel ABI. Missing
evidence leaves the key pending; invalid evidence fails. Key metadata changes
are still reviewed independently. The gate consumes capture's verification
report; it does not claim to be a second independent signature verifier.

## Verification scope

Policy regression tests and source/recipe integration checks run before a
new cloud build. The actual failed image still fails on its missing paths
after exact approvals. A separate planning projection of source-reproduced
files is used only to exercise all known deterministic gate decisions; it is
not a newly compiled, signed or flashable artifact. A new cloud run must pass
the complete real capture, preservation and publication flow, and its outputs
still need local download checks and live preflash preparation.
