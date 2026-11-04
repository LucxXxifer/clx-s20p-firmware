# Frozen S20P Wi-Fi diagnostics

This profile targets only upstream `9f397975c7b47ce6986a6e39529d2f72fd5c729d`,
`mt_wifi` 7.6.7.3 and source archive `mt79xx_20250408-705eb4.tar.xz`
(SHA256 `b029d7b43c498193092dc6a3c857361c8835ce34bb306561c550ac0539784a78`).
The 41 public `patches-7673` files were checked against their Git blob IDs and
applied in order with zero fuzz before taking `source-before/` and generating
`100-s20p-bound-pmf-action-and-gtk-diagnostics.patch`. `manifest.json` pins every
profile file, the recipe/tree Git identities and the resulting compiler inputs.
The only recipe change adds explicit `PKG_RELEASE:=2`; no baseline is rewritten.

Only the AP `NOT_ROBUST_UNICAST_FRAME` diagnostic for SubType 13 is rate limited.
The original rejection and return, other SubType ERROR diagnostics and all PMF
policy are preserved. One global spinlock-protected bucket across radios/peers
prints the first event, then at most one event per five seconds. A printed
`suppressed` value counts events suppressed since the previous printed event;
`4294967295+` means a saturated unsigned-int lower bound. It is not a per-peer
count, and a pending tail count requires another eligible event to be printed.
No timer, peer table or asynchronous work is added.

The compact line `PMF NR_UCAST global suppressed=... SubType=13 WCID=...` uses
`NR_UCAST` for the unchanged `NOT_ROBUST_UNICAST_FRAME` rejection reason. It fits
the actual 160-byte `mtwf_dbg_prt` buffer with all prefix options enabled,
15-character interface/thread names, maximum WCID and saturated count. `logger.c`
is the exact vendor logger function, independently compared during archive replay;
`logger-test.c` compiles that function and the actual patched diagnostic call.
`logging-test.c` checks the effective `DBG_ENHANCE` WARN/INFO kernel severity
mapping. `helper-test.c` exercises the actual helper for 32/64-bit rollover,
bursts, saturation and concurrent calls.

The only GTK changes move one normal rekey-start and two AP SETKEYS DONE messages
to INFO. Timeout, retry, timer, MIC/replay, deauthentication and reason16 behavior
are byte-preserved. This logging patch does not repair the observed GTK handshake
timeout. No hardware boot or driver runtime result is claimed.

Preparation fails before writes on source, inventory, mode, path or checkout
drift. Build capture checks the actual archive and compiler source hashes before
cleanup. Image inspection binds r2, selected driver/kernel config, kernel ABI,
module ownership/dependencies and installed module bytes to the rootfs hash and
source fingerprints. Release verification uses trusted host `unsquashfs` to read
the APK database and both modules again from the actual SquashFS. Every new or
unknown wrapper requires this evidence. Only three frozen historical wrapper
commits can replay the original policy with their exact bound pipeline bytes.

Run the focused suite with the public archive available:

```sh
S20P_WIFI_TEST_ARCHIVE=/absolute/path/mt79xx_20250408-705eb4.tar.xz \
  python3 -m unittest discover -s ci/tests -p test_wifi_logging.py -v
```

The small userspace C harnesses are local checks. Full firmware compilation runs
only on GitHub; compilation, hardware boot, migration and real rekey behavior
still need verification on the newly built candidate.
