# S20P daily component policy implementation plan

> Execute inline in the current checkout. The user has approved the four requirements; do not dispatch duplicate firmware builds.

**Goal:** Omit Honk and TingReader, defer AdGuard Home per the latest user instruction, preserve the dashboard repair, and enforce CA/Go security floors while accepting newer official releases.

**Architecture:** Keep existing source and preservation gates. Resolve selected official component inputs before building, save exact source hashes as run-local evidence, and include that evidence in the daily change fingerprint. Check the actual image and release bundle independently.

**Tech Stack:** Python unittest, Bash, GitHub Actions, Lua, Node.js, OpenWrt APK feeds.

## 1. Application selection (complete)

- [x] Defer AdGuard core, LuCI and translation packages: the selected official LuCI source does not provide the requested Chinese package. Do not invent a replacement translation.
- [x] Omit only the four reviewed Honk/TingReader packages and their owned files. Preserve shared kernel features.
- [x] Require all three AdGuard packages to be absent in config, image inventory, and release validation. Keep baseline stat/SQLite tools explicitly selected after removing Honk's indirect dependencies.
- [x] Run `python3 -m unittest discover -s ci/tests -p 'test_application_selection.py' -v` and release tests.

## 2. Official component resolution (complete)

- [x] Compare upstream CA/Go versions against the existing security floors; keep newer sources and backport only when older.
- [x] Resolve upstream QuickStart updates with exact hashes. New unpatched QuickStart bytes must fail the existing Go gate. Excluded AdGuard releases do not enter the daily resolver.
- [x] Include selected component inputs in daily state; unchanged or previously failed identical inputs skip compilation.
- [x] Add regression cases for version regressions, changed-only dependencies, invalid hashes, and unchanged inputs.
- [x] Track all 31 selected plugin/feed repositories, including iStore, OpenClash, Docker and Dockerman. Prove source ancestry and test plugin-only rebuilds and identical-attempt suppression.

## 3. Dashboard and integration (complete)

- [x] Bind the exact dashboard payload to preservation and release checks, preserve authentication, and stop on incompatible upstream changes.
- [x] Add runner Lua/Node regression checks and payload provenance/license documentation.
- [x] Run the Python suite, shell checks, and Lua/Node tests locally; review the complete diff for private data and unrelated edits.

## 4. Delivery (active)

- [x] Update README with selected packages, update policy, security exception, backup limits, and prerelease limits.
- [ ] Publish the reviewed changes under the existing GitHub authorization, then start no more than one validated Actions build.
- [ ] Verify exact source/run/artifact checksums before reporting a usable firmware; do not flash the live router.
