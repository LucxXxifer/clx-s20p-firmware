# CLX-S20P Wi-Fi Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development. The implementation agent uses `gpt-6.1-sol` with `max`; independent spec review precedes quality review. Track execution below.

**Goal:** Produce a new source-bound S20P candidate with bounded PMF rejection diagnostics and correctly classified routine GTK messages, preserving all protection and real timeout behavior.

**Architecture:** Start from wrapper `59e093e6c5f33591eebe9e332898bf111ed64b0a` and fixed upstream `9f397975c7b47ce6986a6e39529d2f72fd5c729d`. Apply one reviewed patch to the exact `mt_wifi` 7.6.7.3 source/recipe/patch inventory, then bind preparation, configuration, installed module bytes and package revision to same-build provenance and independent release verification. Keep historical manifests and baselines immutable.

**Tech Stack:** Python unittest, Bash, C/kernel logging primitives, OpenWrt/APK, GitHub Actions.

## Scope and acceptance

- PMF: retain the `NOT_ROBUST_UNICAST_FRAME` rejection and `return FALSE`; limit only the confirmed AP receive diagnostic. Preserve the first diagnostic, SubType/WCID context and an explicit suppression count; do not key new state solely by reusable WCID. Use concurrency-safe, bounded state and existing kernel/vendor primitives where possible.
- GTK: change only the routine rekey-start and successful key-install diagnostics. Keep both timeout/deauth paths, retry limits, timers, PMF policy, MIC/replay checks and management frame classification unchanged.
- Do not claim this logging patch repairs the unexplained client GTK handshake timeout. Client/radio attribution and packet evidence are a separate private read-only investigation.
- Preserve ArkBridge, proxy selections, cron SYSLOG_INFO fix, network/firewall/WPA settings, source/security/preservation gates and historical releases.
- Firmware compilation runs only on GitHub. Do not modify live driver modules, reload Wi-Fi, reboot or flash hardware in this preparation phase.

## Task 1: Frozen driver patch and focused regression

Files: new `ci/wifi_logging.py`, `ci/wifi-logging/20261103/`, `ci/tests/test_wifi_logging.py`.

- [x] Obtain the exact upstream `package/mtk/drivers/mt_wifi/Makefile` and complete `patches-7673` inventory. Verify the archive SHA256 `b029d7b43c498193092dc6a3c857361c8835ce34bb306561c550ac0539784a78`. Apply the complete existing patch stack in scratch before generating the smallest additional patch.
- [x] Add failing regression cases for clean exact-source application, source/recipe drift, unreviewed or missing patches, duplicate application, symlink input, tampered source binding, and forbidden changes to PMF rejection/GTK timeout behavior.
- [x] Implement PMF bounded diagnostic accounting with first-event/context retention. Exercise real helper C code for burst suppression, counter bounds, time rollover and concurrent calls; document any global aggregation and avoid per-peer attribution claims.
- [x] Move only the normal rekey-start and successful key-install messages to INFO/diagnostic severity. Verify effective `DBG_ENHANCE` logging mapping and preserve all real ERROR messages.
- [x] Pin the reviewed original recipe and patch inventory plus the new patch bytes in an immutable profile. Distinguish patched package revision through a minimal recipe delta and fail before writes on mismatches.

Run: `python3 -m unittest discover -s ci/tests -p 'test_wifi_logging.py' -v`.
Expected: red first, then all positive and drift/tamper rejection tests pass; changed driver code consists solely of the agreed diagnostics and bounded accounting.

## Task 2: Build, capture and independent publication binding

Files: `wrt_core/update.sh`, `ci/build.sh`, `ci/capture.sh` or existing provenance collection when sufficient, `ci/preservation.py`, `ci/release_candidate.py`, focused existing test modules.

- [x] Invoke preparation for the exact supported S20P fixed source before provenance capture. Require evidence for this wrapper while retaining verification of historical unpatched artifacts.
- [x] Bind archive hash, original recipe/patch inventory, prepared recipe/new patch, selected driver configuration, actual installed `.ko` digests and exact package/kernel ABI to the final rootfs payload.
- [x] Independently recompute/recheck this evidence during release preparation/verification, rejecting omitted, cross-build or tampered reports and module bytes.
- [x] If a package revision difference needs review, use a new precise source-bound rule for only `kmod-mt_wifi`; retain historical package/file gates, baselines, dependency checks and unknown-change rejection.
- [x] Run relevant tests plus full repository Python regression, frontend contracts, shell syntax, actionlint/Lua checks when provided by this repository. Review the actual diff independently.

Commands:

```sh
python3 -m unittest discover -s ci/tests -p 'test_*.py'
node --test ci/dashboard/frontend-test.cjs
for script in ci/build.sh ci/capture.sh wrt_core/update.sh; do
  bash -n "$script" || exit
done
git diff --check -- .github/workflows/s20p-daily.yml ci/build.sh ci/capture.sh \
  ci/preservation.py ci/release_candidate.py ci/wifi_logging.py ci/tests \
  wrt_core/update.sh docs/superpowers/plans/2026-11-03-s20p-wifi-logging.md
```

Check shell scripts individually: `bash -n` accepts one script, with later names treated as its arguments. Whitespace checks apply to authored source and documentation. Frozen public source fixtures and `.patch` context retain their exact upstream whitespace because their bytes are pinned; do not reformat them to satisfy a whitespace check. A pre-stage `git diff --check` does not cover untracked fixtures. Before committing newly added authored files, inspect their staged diff and run the corresponding `git diff --cached --check` over the authored paths.

## Task 3: Reviewed cloud build and safe handoff

- [x] Complete independent spec and quality review; resolve substantive findings with the implementation agent.
- [ ] Recheck branch/remote SHA and active runs. Commit/push the authorized change and create a draft PR; dispatch exactly one `fixed=true` run for the exact new commit, attempt 1. If temporary enablement is needed, restore `disabled_manually` immediately after dispatch.
- [ ] Record exact run/commit, expected build duration and provenance privately. Monitor at 45-minute intervals; stay quiet while unchanged. Never blindly rerun an unchanged commit.
- [ ] On a build failure, preserve logs/artifacts and use the authorized `gpt-6-astra/max` read-only red-team review before a `gpt-6.1-sol/max` corrective change.
- [ ] On success, verify exact artifact API/ZIP/inner/outer digests, same-build sources, signed feeds, preservation/security gates, installed driver provenance and kernel ABI. Prepare rollback and a wired maintenance deployment procedure before any live action.

Done criteria: reviewed source patch and regressions, exact cloud build provenance, independently verified candidate or an explicitly evidenced blocker. Hardware GTK repair remains unproven until client/radio evidence and at least two natural rekey cycles are validated after a separately scheduled deployment.

## Execution status

Active step: Task 3 commit and cloud build. Tasks 1 and 2 implementation and final verification are complete. Independent spec, Task 1 quality and final Task 2/overall quality reviews have passed with no remaining findings; dispatch and monitoring have not started.

The exact public archive and all 41 existing patches replay with zero fuzz. The focused Wi-Fi suite passes 21 tests, including the actual helper for 32/64-bit rollover/concurrency and the complete vendor logger at its real 160-byte buffer limit. Review regressions first reproduced output-path mutation and logger truncation, then passed with preflight validation and a compact diagnostic. The latter preserves global suppression, UINT_MAX `+`, SubType and WCID with default and maximum logger prefixes.

The release suite passes 56 tests and preservation passes 48. Against the frozen 69-file snapshot, the coordinating agent independently passed the full Python suite 412/412 with the public archive replay enabled, Node frontend contracts 9/9, all 25 Lua 5.1 package/contracts checks, network retry failure handling, actionlint and individual syntax checks for all five shell scripts selected for final validation. All 69 file modes, sizes and SHA256 values matched the frozen snapshot. Authored-source diff whitespace checks passed; frozen public fixtures and patch context preserve their original pinned bytes. Task 1/2 spec review, Task 1 quality review and final Task 2/overall quality review passed with no remaining findings.

No firmware compilation, hardware runtime test, commit, push, build dispatch or live router change has occurred in this implementation phase. The real GTK handshake timeout remains unexplained and is not repaired by this logging change; cloud compilation, independent candidate verification and separately authorized hardware acceptance remain required.
