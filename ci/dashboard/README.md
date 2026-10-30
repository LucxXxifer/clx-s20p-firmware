# Reviewed QuickStart dashboard adapters

These files package the dashboard repair separately from private router settings.
They change only authenticated successful GET responses for the three reviewed
status/statistics/ports endpoints. Other requests retain the upstream behavior.
The adapters read the actual default route, interface RX/TX counters and netifd
USB carrier state; missing measurements return an explicit error.

## Sources and preparation stage

- Frontend and template: `kenzok8/small-package`, package
  `luci-app-quickstart`, reviewed at
  `7230e80a3daa9524b38ebd8c05f41dcd09219183`. Its Makefile declares
  Apache-2.0; the repository license is retained in `LICENSE-quickstart`.
  The minified upstream bundle is downloaded by the feed process and is not
  vendored here. The small frontend test fixture contains only transformation
  anchors and synthetic expressions.
- Controller: xiaobao's MIT-licensed `istore_backend.lua`, obtained by the
  wrapper from
  `https://gist.github.com/puteulanus/1c180fae6bccd25e57eb6d30b7aa28aa`, followed
  by the existing `wrt_core/patches/istore_backend-lua.patch`. Its copyright
  header is retained; see `LICENSE-controller`.
- Local modifications: `s20p_dashboard.lua`, `s20p_ports.lua`, the bounded
  controller response adapter, metadata-gated frontend labels and the JS cache
  version suffix. Authentication and network configuration are preserved.

`ci/dashboard.py prepare` runs **after** `apply_custom_feed_patches()`. Therefore
the JS input already includes the existing appfilter link, DSA link-state and
disk-manager link corrections. The template input is still feed source, with
`PKG_VERSION` placeholders, rather than the version-substituted installed file.
The reviewed raw template SHA256 is
`806d968acb47afadc44f2bcb2c482f7149763a03f83934dc7323a9b95ae85fc2`;
the reviewed JS input SHA256 is
`6d3accdd96d0183ad2d3b2770c43fb214147b545a5a5db30e5accdbafa74d8ab`.

Source drift stops preparation before any adapter file is written. A new
frontend/template requires reviewing and rebasing the adapter; replacing a hash
alone is not an acceptable update. The image and release gates bind all five
installed files to exact reviewed bytes and the same SquashFS digest.

## Checks

```sh
python3 -m unittest discover -s ci/tests -p 'test_dashboard.py' -v
node --test ci/dashboard/frontend-test.cjs
for test in ci/dashboard/tests/test_*.lua; do lua5.1 "$test"; done
```

These checks cover source drift, authentication boundaries, truthful statistics,
DNS read-only behavior, USB carrier evidence and payload integrity. They do not
replace target-device/browser acceptance after flashing a candidate.
