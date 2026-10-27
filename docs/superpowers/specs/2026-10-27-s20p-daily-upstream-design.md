# S20P daily upstream candidate design

**Status:** Design for review. No workflow or source-validation behavior is changed by this document.

## Goal

Use the reviewed B build line as the new rolling S20P A candidate line. GitHub Actions should check the fixed upstream `chasey-dev/immortalwrt-mt798x-rebase` `25.12` branch daily, compile when its commit advances, and publish only a fully verified prerelease candidate. Preserve the original 2026-10-17 A firmware as a fixed, downloadable recovery candidate.

## Current project state

- Canonical GitHub repository: `LucxXxifer/clx-s20p-firmware`; default branch `main` is at A wrapper commit `2e6371c4a4801961cf88f2094224e1bb82ef4825`.
- The B implementation branch `codex/s20p-b-25-12` is at `51503b444a9ba0981831a23c0c7f11177b40b0d6`, locked to upstream commit `9f397975c7b47ce6986a6e39529d2f72fd5c729d`.
- The A release `s20p-candidate-40095658212-1` and B candidate release `s20p-b-25.12-candidate-40420056051-1` are already published and must remain available with their existing assets and checksums.
- The current workflow has a daily schedule, but it is disabled, runs only from the default branch, and uses a fixed source lock. The B prerelease job is restricted to manual runs on the B branch.

## Branch and release layout

- Create `s20p-a-10-17` at the existing A commit `2e6371c4a4801961cf88f2094224e1bb82ef4825` before moving the default branch.
- Promote the reviewed B line to `main`; `main` becomes the rolling A candidate line. Keep `codex/s20p-b-25-12` as the historical B implementation branch and keep the B release tag and assets unchanged.
- Keep the existing A release tag and downloadable `.bin` as the fixed 10/17 artifact. Do not rewrite its commit, tag, release assets, or checksums.
- Update the repository description and README documentation to explain that `main` tracks upstream candidates, `s20p-a-10-17` and the A release preserve the fixed image, and all rolling builds remain prereleases until hardware acceptance.

## Daily workflow behavior

- Run on GitHub-hosted `ubuntu-24.04` from default `main` at the existing daily UTC schedule, shown to the user as 04:20 Asia/Shanghai.
- Resolve the current upstream `25.12` head by exact 40-character commit SHA. Require it to be identical to or a descendant of the reviewed B starting point; reject missing, malformed, diverged, or regressed upstream identities.
- If scheduled and the head equals the committed source pin, finish successfully without building or uploading large artifacts. A manual dispatch builds the current head so the workflow can be tested or intentionally rerun.
- For an advanced head, create a run-local source lock that changes only the main-source SHA. Keep the other 38 Git pins and all private local configuration out of the update. Record the resolved SHA and comparison evidence in the run provenance; do not push generated lock changes back to the repository.
- Compile the exact resolved SHA on GitHub Actions. Preserve the existing package, source, signing, ABI, security, image-safety, and preservation gates. Keep the narrowly approved Lucky omission limited to `lucky`, `luci-app-lucky`, and `luci-i18n-lucky-zh-cn`.
- Publish a run- and upstream-SHA-labeled rolling A prerelease only when every existing gate passes and all source-specific reviews still match. Any new or changed difference, security-removal review mismatch, package-world change, or provenance mismatch retains diagnostics and blocks release; do not auto-approve it.
- Never publish a stable release, overwrite the fixed A release, or flash a router from the workflow.

## Required implementation and verification

1. Add a tested upstream-head resolver and run-local source-lock preparation path, including tests for unchanged, fast-forward, malformed, diverged, and regressed upstream states.
2. Make provenance record the exact resolved source SHA while keeping baseline invariants and source-specific review gates fail-closed.
3. Update workflow conditions so scheduled runs on `main` can publish a verified prerelease, while unchanged scheduled runs skip the build.
4. Update release asset names, metadata, installation instructions, repository README files, and the repository description to identify rolling A candidates and preserve the fixed A 10/17 download.
5. Run the complete Python and shell test suites, workflow lint, package/release verification tests, then perform one GitHub Actions manual run against the current upstream head before enabling the daily schedule.
6. Confirm the A branch/ref and existing A release are still intact, the new default branch is `main`, the workflow is enabled, and the first candidate has exact commit provenance and complete checksums.

## Explicit limits

Build success proves source capture and static artifact gates only. It does not prove live boot, device migration, long-term stability, or Lucky runtime compatibility. New upstream changes can compile but remain unpublished until their exact differences receive review.
