# S20P run 40996021264 image rejection and repair

The [failed run](https://github.com/LucxXxifer/clx-s20p-firmware/actions/runs/40996021264) completed compilation at wrapper commit `aba717fd23f005ad1144f94be1e7b6d959e050eb`. CA/Go security and dashboard checks passed, but the final preservation image gate rejected the result, so no candidate was published.

The evidence artifact is `12962653412`, SHA256 `1d7bfcd9f838f9dc27babf1cd2005c1bf5b66479d43f4c76c286ff5d7cabeac6`. Its rootfs SHA256 is `b7576a2e634e1ba5c1f5865a1e6fc8ca83420c8dccfd9c158d56dce2d5982f8a`.

| Cause | Repair |
| --- | --- |
| The Honk/TingReader omission review missed `/usr/bin/ting-reader` and the now-empty `/usr/share/licenses` parent. | Add their exact baseline records after checking native package ownership. Files and symlinks must be absent; shared directories may remain if another package still needs them. |
| Refreshing the official QuickStart recipe ran after the wrapper's storage dependency adjustment and undid it. This installed `mdadm`, three RAID kmods, `smartmontools` and `smartmontools-drivedb`. | Reapply the existing five-token dependency policy after recipe selection. Preserve other dependencies and reject unrecognized layouts. These six unintended packages and their files are not approved. |
| The reviewed `/etc/apk/world` hash embedded one older kernel ABI. | Normalize only the actual kernel constraint, already checked against the same-build package and kmod evidence. Match one of two exact reviewed root lists; the second includes four library roots already installed in the baseline. Unknown, missing or duplicate roots remain rejected. |
| Daily feed updates produced new package versions and payloads. | Independently verify five signed feeds, all 537 APKs, 156 kmods and 494 installed versions offline. Review only 75 exact canonical deltas, with package ownership and prepared source recipes recorded in `ci/reviewed-differences/daily-components-40996021264.json`. |

The daily review contains 39 version deltas, two dependency deltas and 34 payload deltas. It does not grant a general exemption for future changes. Replaying the actual failed image under the repaired policy still rejects its 21 regression deltas: the six unintended packages, QuickStart dependencies, package roots and 13 added payload entries. A fresh build must remove that regression before publication.

Local validation passed all 334 Python tests, dashboard JavaScript/Lua checks, network failure-propagation checks and shell syntax checks. Local Lua was 5.5.1; the workflow's Lua 5.1 checks remain required. GNU Make also parsed the actual selected official QuickStart recipe before and after adjustment: only the five intended dependency tokens changed, and repeating preparation was idempotent. The existing CA/Go floors, exact QuickStart binary exception, source tracking, signature/TLS checks, dashboard repair and application exclusions remain enforced.

A new Actions run is required to validate the repaired build and publish its candidate prerelease. Offline evidence and local tests do not establish hardware boot or service compatibility; the current router was not modified.
