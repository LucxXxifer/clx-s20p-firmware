# Recovered S20P source inputs

Run `40081936381` compiled successfully through `make checksum`, but the
preservation gate blocked publication. In addition to Lucky's frontend drift,
lock-time feed revisions had updated LuCI, AxonHub and geosite without being
needed for the requested Python/ipset/USB additions. The rebuild restores the
following independently identified original inputs instead of approving those
functional changes.

## LuCI

The original ROM package suffix `36e3f0d` resolves in
[immortalwrt/luci](https://github.com/immortalwrt/luci/commit/36e3f0d13622f5be6d59d9ba257811914d76e34d)
to full commit `36e3f0d13622f5be6d59d9ba257811914d76e34d`, tree
`ac28787e4b215bdec988ac0570101e29155c7a4e`, committed on
`2026-10-16T02:46:56Z`. Its shallow-checkout version is
`26.289.10016~36e3f0d`, matching the original ROM.

All 15 changed non-generated LuCI payloads were reproduced byte for byte from
that fixed public source: three zh-cn translations with its `po2lmo`, eight
JavaScript files with its `jsmin`, two templates with `luci.mk`'s
`SubstituteVersion`, and two timezone source files copied without modification.
The same transforms reproduce all 15 candidate files from the prior lock's
`61e876e98f91446fd8968dfede721b98ddf80a32`; this establishes actual source drift.
The two generated version files continue to report their real Git checkout
state. No original branch name is manufactured for a detached checkout.

Both source revisions contain identical files for `luci-proto-3g`,
`luci-proto-ncm`, `luci-proto-qmi`, and `luci-proto-mbim`. Both have the ucode
theme tree used by the wrapper. The packages feed, kernel source and optional
USB selections are unchanged by the LuCI pin.

## Geosite

The original ROM's `/usr/share/v2ray/geosite.dat` is exactly the public
[20261015074526 dlc.dat release](https://github.com/v2fly/domain-list-community/releases/tag/20261015074526):
2,325,634 bytes, SHA256
`405ad108a016ae20c4dfa94cf97384c93ed1bd688121acdc2c5a09c77cca2a4f`.
GitHub asset ID `643110519` reports the same size and digest; an independent
complete download also matched. Only `GEOSITE_VER` and that download's `HASH`
are restored. Existing geoip-only-cn-private and APK `-r` recipe adjustments
remain part of the exact recipe hash.

## AxonHub

The original binary embeds source commit
`a946fca495c52344ed073abb6c3562b9ff728b12`. This matches
[the 20261016-r1 recipe](https://github.com/VIKINGYFY/packages/blob/3c07374818b4fc69b1cec0d9747144c8285d7c6c/axonhub/Makefile).
Its fixed source archive is SHA256
`d767de4e917e3f62de6c71d2b37a1e9865ac835b89fa24aeeb714a5282d65524`;
the complete downloaded archive matched that hash. All eight original recipe
patches have identical bytes to the current locked source, and all eight
applied successfully, in order, to that archive. The restored recipe differs
only in `PKG_VERSION`, `PKG_SOURCE_VERSION`, and `PKG_HASH`; its LuCI app and
patches remain intact. The recovered source requires Go 1.26.0; the preserved
locked Go 1.27.1 host compiler satisfies that requirement.

`ci/original_recipe_pins.py` runs after the existing source preparation and
before configuration/package metadata generation. It accepts only exact
reviewed before/after recipe hashes and the exact AxonHub patch filename/hash
set. It checks both output hashes before writing either file. Source drift,
missing/changed/additional patches, symlinked patch directories, or missing
locks fail the build. Fixed URL/hash downloads retain the normal build-system
hash verification. This does not claim a rebuilt binary is byte-identical;
the next cloud output must still pass the complete preservation, signature,
kernel ABI and same-build feed gates.
