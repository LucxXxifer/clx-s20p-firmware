#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
source "$ROOT/wrt_core/modules/network.sh"
# A failed Git request must retain its status after the retry loop.
git() { return 23; }
export NETWORK_RETRY_MAX=1
set +e
git_retry ls-remote https://example.invalid/missing >/dev/null 2>&1
status=$?
set -e
[[ $status == 23 ]] || { echo "Git failure returned $status" >&2; exit 1; }
printf '%s\n' 'network retry failure propagation: PASS'
