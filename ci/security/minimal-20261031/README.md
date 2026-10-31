# Frozen management security backports

This profile retains all other fixed inputs, including the explicitly disclosed
legacy QuickStart exception. It applies only these two source-level changes:

- uhttpd: unchanged official patch
  [`64dcf47aa7f84ae60db9a3950e5c4b8fbe5ed6f1`](https://github.com/openwrt/uhttpd/commit/64dcf47aa7f84ae60db9a3950e5c4b8fbe5ed6f1),
  a direct child of the existing `373145f7` source. The recipe release rises from
  `1` to `2`; source commit, ABI and normal authentication semantics stay pinned.
- LuCI mounts ACL: exact one-line removal recommended by the official
  [`GHSA-v5f9-62c7-cw29`](https://github.com/openwrt/luci/security/advisories/GHSA-v5f9-62c7-cw29).
  This is an advisory-based local backport, not a claimed upstream merged commit.
  Cron-management permissions and every other ACL entry remain byte-identical.
  The LuCI package version is unchanged; its actual ACL payload hash identifies
  the backport. Source is ImmortalWrt LuCI `c84adb975e207ae8b14982191838ec6218d632cd`.

`manifest.json` pins original inputs, official patch, expected post-patch C source
and exact ACL output. Preparation rejects altered originals and pre-existing
patches. Image/release gates bind the profile, wrapper and prepared-source
manifests, installed package versions, and exact ACL bytes. The complete package
and signature gates still apply. No claim of complete vulnerability remediation,
HTTP authentication regression or hardware acceptance follows from these checks.

For an isolated native source regression, obtain official libubox
`7dd127841e82eb1cfb61185da37dde7b9bd9ba6d` (the frozen system recipe's source)
and run from the repository root:

```sh
python3 ci/security/minimal-20261031/native-regression.py \
  --libubox /absolute/path/to/libubox-7dd127841e82eb1cfb61185da37dde7b9bd9ba6d \
  --output /absolute/path/to/native-regression.json
```

The runner verifies all used libubox sources/headers against manifest hashes,
applies the actual official uhttpd patch, extracts its unchanged validator,
compiles it with AddressSanitizer and UndefinedBehaviorSanitizer, and executes
11 real blobmsg cases: null/non-array legacy semantics, empty array, valid strings,
integer, boolean, nested object/array, unterminated string, and mixed entries.
This tests the upstream validator on the host, not a fake implementation, HTTP
endpoint or live router. No network or persistent host installation is performed
by the runner. Full target compilation and same-build verification remain required.
