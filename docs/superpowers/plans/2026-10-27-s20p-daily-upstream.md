# S20P Daily Upstream Candidate Implementation Plan

> **For agentic workers:** Use inline execution task-by-task. Each step is independently verifiable before moving on.

**Goal:** Turn the reviewed B build line into the rolling default `main` that checks upstream `25.12` daily and builds only an exact, validated new head, while preserving the A 10/17 fixed download.

**Architecture:** Resolve the public upstream branch through GitHub compare against the reviewed B starting SHA. Generate a run-local source lock and integrity-checked runtime baseline; the existing fail-closed artifact checks continue to decide whether a candidate may publish. A scheduled unchanged head skips; a manual dispatch may force a retry. New source or image differences are never auto-approved.

**Tech Stack:** GitHub Actions, GitHub REST API/CLI, Python `unittest`, Bash, `actionlint`, existing `ci/source_lock.py` and `ci/preservation.py` gates.

---

## File map

- `ci/upstream_sync.py`: validate upstream compare results; make exact run-local lock/baseline inputs.
- `ci/tests/test_upstream_sync.py`: resolver, lock, and baseline integrity contracts.
- `ci/provenance.py`, `ci/preservation.py`, `ci/build.sh`, `build.sh`: carry the resolved SHA without weakening existing checks.
- `ci/release_candidate.py`: validate candidate provenance against the exact run-selected SHA, rather than a single hard-coded B SHA.
- `.github/workflows/s20p-daily.yml`: daily check, no-change skip, exact build, evidence and candidate retention, candidate prerelease.
- `README.md`, `README_AUTOBUILD.md`, `docs/SOURCE-TRANSITION-B.md`: rolling-main and fixed-A descriptions.
- `ci/tests/test_release_candidate.py`, `ci/tests/test_provenance.py`, `ci/tests/test_source_transition.py`: dynamic exact-SHA and fail-closed regression coverage.

## Task 1: Validate upstream resolution and run-local inputs

Files: create `ci/upstream_sync.py`; test `ci/tests/test_upstream_sync.py`.

- [x] Add first the tests for identical/scheduled skip, identical/manual force, valid descendant, malformed SHA, diverged/behind branch, changed base, preservation of the other 38 pins, and unchanged baseline files.
- [x] Verify the test fails because the resolver module does not exist.
- [x] Implement `resolve_head`, `update_lock`, integrity-checked `prepare_baseline`, and the `prepare` CLI using the exact `9f397975...` base and `25.12` repository identity.
- [x] Run `python3 -m unittest ci.tests.test_upstream_sync -v`; expect all resolver tests to pass.

## Task 2: Thread the selected SHA through build provenance and gates

Files: modify `build.sh`, `ci/build.sh`, `ci/provenance.py`, `ci/preservation.py`; test `ci/tests/test_source_transition.py`, `ci/tests/test_optional_packages.py`, `ci/tests/test_security_removals.py`.

- [x] Make the build use only the run-local lock and baseline paths supplied by the resolver; keep repository defaults for manual local inspection.
- [x] Derive provenance's expected main-source SHA from the validated run-local lock and verify checkout, cloned source, repository URL, and `25.12` ref all match it.
- [x] Keep the CA-removal review exact to its reviewed source SHA and recipes; a new SHA must produce a review failure until separately approved.
- [x] Keep Lucky omission restricted to the three exact package names. Apply its known source-bound approval only at its reviewed SHA; for later heads, let any changed world/package difference fail closed.
- [x] Run focused provenance, optional-package, source-transition, and CA removal tests; preserve the current `9f397975...` behavior byte-for-byte.

## Task 3: Verify release provenance for any resolved head

Files: modify `ci/release_candidate.py`; test `ci/tests/test_release_candidate.py`.

- [x] Read expected upstream SHA from the workflow's validated build record and require exact equality across requested lock, resolved lock, source capture, image metadata, and build commit.
- [x] Use a run/upstream-SHA-derived ROM asset name; keep `BUILD-VERIFICATION.json`, installation guide, archive and checksum verification tied to run id, attempt, wrapper SHA, and upstream SHA.
- [x] Add regression tests proving mismatch, missing SHA, malformed SHA, and altered archive metadata prevent release preparation.
- [x] Run `python3 -m unittest ci.tests.test_release_candidate -v` and verify the current fixed `9f397975...` release fixture remains valid.

## Task 4: Add daily exact-head workflow behavior

Files: modify `.github/workflows/s20p-daily.yml`; test workflow shell and static YAML.

- [x] Add workflow-dispatch input `force`; on schedule resolve latest `25.12` using GitHub compare against `9f397975...`; reject diverged, behind, or malformed results.
- [x] Skip the build when the scheduled head equals the reviewed base or equals the last attempted head from an intact tiny state artifact for the unchanged wrapper commit. Allow `force=true` to retry the exact current head after a policy update.
- [x] Build only the SHA from the compare result using a generated lock; upload only compact run-state metadata on unchanged/blocked attempts, full evidence on builds, and candidate artifacts only after every gate passes.
- [x] Publish a labeled prerelease from either schedule or manual run only when all gates pass. Never publish stable, overwrite A, or flash hardware.
- [x] Set temporary artifact retention to seven days; retain release assets independently.
- [x] Run `actionlint`, `bash -n`, and a local workflow-expression/source audit.

## Task 5: Update public project documentation

Files: modify `README.md`, `README_AUTOBUILD.md`, `docs/SOURCE-TRANSITION-B.md`.

- [x] Document `main` as rolling upstream candidate source, `s20p-a-10-17` and existing A release as the fixed download, prerelease-only behavior, and hard stop on new unreviewed differences.
- [x] Add exact A and latest B release URLs plus a short description of how daily check/skip/build works.
- [x] State that successful Actions/static gates are not hardware acceptance and that Lucky runtime compatibility remains unverified.
- [x] Run Markdown link and `git diff --check` review.

## Task 6: Full validation and handoff for GitHub promotion

- [x] Run `python3 -m unittest discover -s ci/tests -v`, `bash ci/tests/test_network.sh`, shell syntax checks, `actionlint`, and all existing deterministic public-artifact verification commands.
- [x] Review the complete diff for unrelated user files; do not stage `docs/superpowers/` user content or `work/` evidence except the named plan/spec files.
- [x] Confirm A fixed release assets and checksums remain untouched, and B's published candidate release remains unchanged.
- [ ] Execute the reviewed branch operations: create `s20p-a-10-17` at A commit `2e6371c4...`, fast-forward `main` to the rolling workflow implementation, update the public repo description, enable the schedule, and run one manual cloud validation.
- [x] External promotion, repository description, schedule activation, and one manual cloud validation are authorized by the user’s explicit approval of the design and implementation.

## Local verification record (2026-10-27)

- 274 Python tests passed; network failure-propagation test passed; shell syntax, actionlint v1.7.7, and diff whitespace checks passed.
- Two independent read-only reviews passed after regressions for rollback across rejected runs, old Actions rerun identities, and pre/post-build runtime-policy integrity.
- A and B release IDs, assets, API digests, and tag objects recorded under local `work/daily-promotion-20261027/`; these evidence files are not staged.
- Full firmware compilation and first rolling release verification remain GitHub Actions work. No router operation is included.
