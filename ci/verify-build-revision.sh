#!/usr/bin/env bash
# Reject a prepared upstream tree whose revision cannot become a valid APK version.
#
# package/base-files builds its version as $(COMMITCOUNT)~$(lastword of REVISION),
# where REVISION comes from the upstream scripts/getver.sh as r<count>-<commit>.
# apk-tools only accepts hexadecimal digits after '~' (src/version.c,
# TOKEN_COMMIT_HASH), so a revision that lost its commit hash packages as
# "1~r0" and aborts the build at the very end of the packaging stage.
#
# A depth-one checkout without a tracking branch leaves getver.sh's internal
# UPSTREAM_BASE unassigned, so any exported variable of that name is inherited;
# the resulting "git log <foreign sha>" failure is swallowed and the commit hash
# silently disappears. Detecting that here costs seconds instead of hours.
set -euo pipefail

build_tree=${1:?usage: verify-build-revision.sh <build-tree>}
probe="$build_tree/scripts/getver.sh"

if [[ ! -f $probe ]]; then
    echo "Upstream revision probe is missing: $probe" >&2
    exit 1
fi

revision=$(cd "$build_tree" && bash scripts/getver.sh) || revision=''

if [[ ! $revision =~ ^r[0-9]+-[0-9a-f]{7,40}$ ]]; then
    cat >&2 <<MSG
Upstream revision '${revision}' is not usable as an APK package version.
Expected r<commit-count>-<hex commit>; the commit hash is missing or not
hexadecimal, which makes base-files package as "${revision}" and makes apk
mkpkg reject the version field. Check that no exported environment variable
shadows a variable owned by the upstream build system (scripts/getver.sh
reads UPSTREAM_BASE, include/version.mk reads REVISION and
SOURCE_DATE_EPOCH) and that the checkout is a git tree.
MSG
    exit 1
fi

printf 'build revision: %s\n' "$revision"
