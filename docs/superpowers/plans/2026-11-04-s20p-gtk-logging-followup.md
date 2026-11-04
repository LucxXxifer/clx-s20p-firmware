# CLX-S20P GTK Logging Follow-up

## Scope

The 20261103 r2 profile lowered only the `cmm_wpa.c` `WPAGroupRekey` start
message, while natural rekeys from `GroupRekeyExec` and the AP rekey-trigger
path still emitted the same routine message at `DBG_LVL_ERROR`. This follow-up
adds an immutable 20261104 profile and package r3 with only those two
`DBG_LVL_ERROR` to `DBG_LVL_INFO` changes. The prior profile, fixed manifests,
baselines and approved differences stay immutable.

The three routine start callsites and two successful AP SETKEYS messages are
classified INFO. With `DBG_ENHANCE`, INFO maps to `pr_info`, but the driver's
INFO category mask defaults to disabled; category masks or logread filtering
may also hide the messages. This change does not prove M2 reception or
`REKEY_ESTABLISHED` and does not repair a real GTK handshake timeout.

## Verification

- [x] Lock the archive SHA256 and replay all 41 upstream patches, patch 100,
  then patch 101 using `patch --batch --fuzz=0`.
- [x] Pin post-upstream AP source snapshots and both resulting AP compiler
  source hashes in the dated profile.
- [x] Require exactly three routine GTK start callsites and two successful AP
  SETKEYS callsites, with INFO classification reaching the vendor logger's
  INFO-to-`pr_info` dispatcher.
- [x] Confirm AP source differences contain only the intended severity tokens;
  keep timers, retries, MIC/replay, deauth and timeout handling byte-preserved.
- [x] Capture and reject mutation of either AP compiler input; accept only the
  source-bound r1-to-r3 package transition.
- [x] Run full Python regression and focused release-candidate checks. All 414
  repository tests, 56 release-candidate tests and 23 Wi-Fi logging tests pass;
  private logs are indexed under
  `/Users/lucxxxifer/Documents/Codex/2026-11-02/cha/work/s20p-gtk-logging-followup-20261104/implementation/`.
- [ ] Receive root review before any later commit, push or build dispatch.
- [ ] Any cloud build and device acceptance require separate authorization.

No firmware compilation, device action, network change, commit, push or build
dispatch is part of this follow-up implementation.
