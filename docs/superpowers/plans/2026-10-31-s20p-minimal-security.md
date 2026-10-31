# CLX-S20P Minimal Security Fixed Build Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the isolated source and selection tasks; the primary agent owns pipeline wiring and final review. Track completion with the checkboxes below.

**Goal:** Build a new frozen-source S20P candidate containing only the official uhttpd list-type fix, scoped LuCI mounts ACL removal and EasyTier three-package exclusion.

**Architecture:** Preserve the 20261030 snapshot and legacy application policy unchanged. A separately hashed 20261031 manifest selects the reviewed security profile and application policy. Preparation and final-image inspection bind patches, source manifests, package versions and actual payloads; publication independently rechecks the evidence.

**Tech Stack:** Python unittest, Bash, OpenWrt/APK, GitHub Actions, existing Node/Lua dashboard contracts.

## Scope and limits

- Base system commit: `9f397975c7b47ce6986a6e39529d2f72fd5c729d`; frozen feeds and CA/Go inputs remain unchanged.
- uhttpd patch: official `64dcf47aa7f84ae60db9a3950e5c4b8fbe5ed6f1`, package revision distinguishes the hotfix.
- Delete only mounts.write.file[`/etc/crontabs/root`]; preserve the cron module and every other grant.
- Exclude `easytier`, `luci-app-easytier`, `luci-i18n-easytier-zh-cn`; retain shared dependencies.
- QuickStart three packages and its exact disclosed legacy Go exception remain; its rebuild is deferred.
- No kernel, ubus ABI, Docker, Samba, BusyBox or Dropbear migration. No router changes or flashing.
- Latest no-flash request produces a private backup/apply/verify/rollback guide; no live application.

## Task 1: Source patch and final-payload guard

Files: `ci/minimal_security.py`, `ci/security/minimal-20261031/`, `ci/tests/test_minimal_security.py`.

- [x] Add tests for clean-source preparation, source drift, duplicate application, precise ACL deletion and report/payload tampering.
- [x] Pin original recipe/ACL and official patch bytes. Prepare only the exact fixed tree; fail on drift.
- [x] Inspect actual image packages, ACL bytes and bound prepared-source records.

Run: `python3 -m unittest discover -s ci/tests -p 'test_minimal_security.py' -v`.
Expected: all positive and tamper-rejection tests pass.

## Task 2: Versioned application selection

Files: `ci/preservation.py`, `ci/reviewed-differences/application-image-selection-20261031.json`, `ci/tests/test_application_selection.py`.

- [x] Keep default legacy review and evidence unchanged.
- [x] Add `minimal-20261031` review with only the exact three EasyTier package/file/world omissions.
- [x] Test legacy replay, missing required packages, shared dependency retention and unexpected-file rejection.

Run: `python3 -m unittest discover -s ci/tests -p 'test_application_selection.py' -v`.
Expected: both policy versions pass their intended fixtures and reject cross-policy/drift cases.

## Task 3: Frozen manifest and build/release wiring

Files: `ci/fixed_inputs.py`, new `ci/fixed/20261031/`, `ci/build.sh`, `build.sh`, `wrt_core/update.sh`, `ci/release_candidate.py`, `.github/workflows/s20p-daily.yml`, corresponding existing tests.

- [x] Test schema-3 exact profile binding and rejection of unknown or tampered profile identifiers/hashes; schema 2 stays valid.
- [x] Copy the old snapshot bytes into a new dated immutable profile. Derive build policy from verified manifest, never inherited environment alone.
- [x] Apply the three config exclusions before defconfig; run prepare before provenance capture and final inspect before preservation/publication.
- [x] Independently require the selected application/security evidence at publication. Add only exact reviewed difference approvals.
- [x] Select the new fixed snapshot for manual fixed builds; retain the daily rolling schedule and historical releases.

Commands:

```sh
python3 -m unittest discover -s ci/tests -p 'test_*.py'
node --test ci/dashboard/frontend-test.cjs
bash -n ci/build.sh build.sh wrt_core/update.sh
git diff --check
```

Also run the existing 25 Lua contracts and actionlint in the available host/container tool environment. Recheck the old fixed artifact through its native offline preservation replay.

## Task 4: Review, single cloud build, independent acceptance

- [x] Review source, selection, wiring, diff, tests and private-data boundaries.
- [ ] Recheck remote main and active runs immediately before commit/push/dispatch.
- [ ] Commit and push the authorized minimal change; trigger one `fixed=true,force=true` workflow dispatch.
- [ ] Record exact commit/run privately and monitor quietly. No duplicate dispatch or blind rerun.
- [ ] After real compilation, verify all existing gates, exact artifact digest/checksums, signed feeds, package/ABI/image/security evidence and release assets.
- [ ] Preserve hardware boot/migration as untested; do not call cloud acceptance hardware stability.

## Task 5: Current-router no-flash guide

Private output: `work/s20p-security-plan-review-20261031/current-s20p-no-flash-guide.md` beside the repository.

- [x] Document scoped ACL patch with original hash, independent SSH, backup, session loss and rollback.
- [x] Document EasyTier stop/disable without deleting configs or shared packages.
- [x] Document uhttpd signed same-ABI APK transaction simulation, original rollback APKs and trust checks. Mark patched APKs unavailable until built and validated.
- [x] Distinguish guidance validation from live router testing.
