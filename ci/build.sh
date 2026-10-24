#!/usr/bin/env bash
# S20P-only orchestration. It preserves the existing wrapper's docker/honk path.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export S20P_AUTOBUILD=1
export S20P_CI_DIR="$repo/ci"
export S20P_CAPTURE_DIR="$repo/output"
export S20P_BASELINE_DIR="$repo/ci/baseline"
export S20P_LOCK_TOOL="$repo/ci/source_lock.py"
export S20P_SOURCE_LOCK="$repo/ci/sources.lock.json"
export S20P_GIT_LOG="$S20P_CAPTURE_DIR/provenance/git-clones.jsonl"
export S20P_HTTP_LOG="$S20P_CAPTURE_DIR/provenance/http-inputs.jsonl"
export S20P_BUILD_LOG="$S20P_CAPTURE_DIR/logs/build.log"
export S20P_MOBILE_PACKAGES_FILE="$repo/ci/mobile-usb-packages.txt"
unset ADD_CONFIG_FRAGMENTS REMOVE_CONFIG_FRAGMENTS
[[ ! -e action_build && ! -e output/reports/capture.json && ! -e output/logs/build.log ]] || {
    echo 'Use a fresh checkout/output for each build; previous evidence will not be overwritten.' >&2
    exit 1
}
for required in "$S20P_LOCK_TOOL" "$S20P_SOURCE_LOCK" "$S20P_MOBILE_PACKAGES_FILE" ci/preservation.py ci/provenance.py ci/baseline/policy.json; do
    [[ -f $required ]] || { echo "Required build policy missing: $required" >&2; exit 1; }
done
mkdir -p "$S20P_CAPTURE_DIR/logs" "$S20P_CAPTURE_DIR/provenance" "$S20P_CAPTURE_DIR/reports"
cp "$S20P_SOURCE_LOCK" "$S20P_CAPTURE_DIR/provenance/requested-sources.lock.json"
# The parent wrapper's pre-clone and build share these exported lock variables.
set +e
(
    set -euo pipefail
    bash ./wrt_core/pre_clone_action.sh S20P-WIFI-YES
    bash ./build.sh S20P-WIFI-YES ''
) 2>&1 | tee "$S20P_BUILD_LOG"
pipeline_status=("${PIPESTATUS[@]}")
build_status=${pipeline_status[0]}
log_status=${pipeline_status[1]}
set -e
if (( build_status != 0 || log_status != 0 )); then
    printf '{"status":"failed","build_exit":%d,"log_exit":%d,"live_acceptance":"not-run"}\n' "$build_status" "$log_status" > output/reports/build-status.json
    bash ci/capture.sh --audit-output "$S20P_CAPTURE_DIR"
    if (( build_status != 0 )); then exit "$build_status"; fi
    exit "$log_status"
fi
[[ -f output/reports/capture.json && -f output/reports/signed-feeds.json ]] || {
    echo 'Build did not produce the required pre-clean capture.' >&2; exit 1;
}
set +e
gate_command=(python3)
if [[ $(id -u) != 0 && -z ${FAKEROOTKEY:-} ]]; then
    if [[ ! -s output/.fakeroot.state ]]; then
        echo 'Required rootfs fakeroot metadata state is missing.' >&2
        exit 1
    fi
    gate_command=(fakeroot -i "$S20P_CAPTURE_DIR/.fakeroot.state" -- python3)
fi
gate_command+=(ci/preservation.py image)
if [[ -f $repo/ci/approved-differences.json ]]; then
    gate_command+=(--approve-differences "$repo/ci/approved-differences.json")
fi
"${gate_command[@]}" \
    --rootfs output/inspection/rootfs \
    --kernel-config output/metadata/kernel/.config \
    --packages output/inspection/rootfs/lib/apk/db/installed \
    --dtb output/inspection/board.dtb \
    --source-lock output/provenance/source-lock.json \
    --baseline-dir "$S20P_BASELINE_DIR" \
    --output output/reports/preservation-image.json
gate_status=$?
set -e
# Exit 2 is an unapproved difference: retain evidence, do not publish a release.
if (( gate_status != 0 && gate_status != 2 )); then
    bash ci/capture.sh --audit-output "$S20P_CAPTURE_DIR"
    exit "$gate_status"
fi
GATE_STATUS=$gate_status python3 - <<'PY'
import json, os
from pathlib import Path
gate = json.loads(Path('output/reports/preservation-image.json').read_text())
status = 'candidate' if os.environ['GATE_STATUS'] == '0' else 'review_required'
Path('output/reports/build-status.json').write_text(json.dumps({
    'status': status, 'preservation': gate['status'], 'live_acceptance': 'not-run',
    'stable_eligible': False, 'bit_identical_claim': False,
}, indent=2) + '\n')
PY
bash ci/capture.sh --audit-output "$S20P_CAPTURE_DIR"
if (( gate_status == 2 )); then
    echo 'Build retained for difference review; no candidate release will be published.'
    exit 2
fi
# Exclude the extracted inspection tree: its exact raw squashfs/FIT are already
# preserved, while rootfs absolute symlinks should not enter a publication bundle.
mkdir -p output/release
archive_name="s20p-samebuild-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}.tar.gz"
tar -C output --exclude='./release' --exclude='./inspection/rootfs' --exclude='./.fakeroot.state' \
    -czf "output/release/$archive_name" .
(cd output/release && sha256sum "$archive_name" > SHA256SUMS)
if [[ -n ${GITHUB_OUTPUT:-} ]]; then
    echo 'publish_candidate=true' >> "$GITHUB_OUTPUT"
    echo "archive=$archive_name" >> "$GITHUB_OUTPUT"
fi
