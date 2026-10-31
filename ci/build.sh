#!/usr/bin/env bash
# S20P-only orchestration. It preserves the existing wrapper's docker/honk path.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export S20P_AUTOBUILD=1
export S20P_CI_DIR="$repo/ci"
export S20P_CAPTURE_DIR="$repo/output"
export S20P_BASELINE_DIR="${S20P_BASELINE_DIR:-$repo/ci/baseline}"
export S20P_LOCK_TOOL="$repo/ci/source_lock.py"
export S20P_SOURCE_LOCK="${S20P_SOURCE_LOCK:-$repo/ci/sources.lock.json}"
export S20P_UPSTREAM_EVIDENCE="${S20P_UPSTREAM_EVIDENCE:-}"
export S20P_UPSTREAM_COMMIT="${S20P_UPSTREAM_COMMIT:-}"
export S20P_COMPONENT_INPUTS="${S20P_COMPONENT_INPUTS:-}"
export S20P_FIXED_INPUTS="${S20P_FIXED_INPUTS:-}"
# Only a verified fixed manifest may select the new image/security policy.
export S20P_APPLICATION_POLICY=legacy
export S20P_GIT_LOG="$S20P_CAPTURE_DIR/provenance/git-clones.jsonl"
export S20P_HTTP_LOG="$S20P_CAPTURE_DIR/provenance/http-inputs.jsonl"
export S20P_BUILD_LOG="$S20P_CAPTURE_DIR/logs/build.log"
export S20P_MOBILE_PACKAGES_FILE="$repo/ci/mobile-usb-packages.txt"
unset ADD_CONFIG_FRAGMENTS REMOVE_CONFIG_FRAGMENTS
[[ ! -e action_build && ! -e output/reports/capture.json && ! -e output/logs/build.log ]] || {
    echo 'Use a fresh checkout/output for each build; previous evidence will not be overwritten.' >&2
    exit 1
}
for required in "$S20P_LOCK_TOOL" "$S20P_SOURCE_LOCK" "$S20P_MOBILE_PACKAGES_FILE" ci/preservation.py ci/provenance.py "$S20P_BASELINE_DIR/policy.json"; do
    [[ -f $required ]] || { echo "Required build policy missing: $required" >&2; exit 1; }
done
verify_runtime_inputs() {
    if [[ -n $S20P_UPSTREAM_EVIDENCE ]]; then
        python3 "$repo/ci/upstream_sync.py" verify-prepared \
            --committed-lock "$repo/ci/sources.lock.json" --committed-baseline "$repo/ci/baseline" \
            --lock "$S20P_SOURCE_LOCK" --baseline "$S20P_BASELINE_DIR" \
            --evidence "$S20P_UPSTREAM_EVIDENCE"
    elif [[ $S20P_SOURCE_LOCK != "$repo/ci/sources.lock.json" || $S20P_BASELINE_DIR != "$repo/ci/baseline" ]]; then
        echo 'Custom source locks and baselines require verified upstream resolution evidence.' >&2
        return 1
    fi
}
verify_runtime_inputs
mkdir -p "$S20P_CAPTURE_DIR/logs" "$S20P_CAPTURE_DIR/provenance" "$S20P_CAPTURE_DIR/reports"
TZ=Asia/Shanghai date +%Y%m%d > "$S20P_CAPTURE_DIR/provenance/build-date.txt"
if [[ -z $S20P_COMPONENT_INPUTS ]]; then
    export S20P_COMPONENT_INPUTS="$S20P_CAPTURE_DIR/provenance/component-resolution.json"
    python3 ci/components.py resolve --output "$S20P_COMPONENT_INPUTS"
elif [[ $S20P_COMPONENT_INPUTS != "$S20P_CAPTURE_DIR/provenance/component-resolution.json" ]]; then
    cp "$S20P_COMPONENT_INPUTS" "$S20P_CAPTURE_DIR/provenance/component-resolution.json"
fi
cp "$S20P_SOURCE_LOCK" "$S20P_CAPTURE_DIR/provenance/requested-sources.lock.json"
if [[ -n $S20P_UPSTREAM_EVIDENCE ]]; then
    [[ -f $S20P_UPSTREAM_EVIDENCE && ! -L $S20P_UPSTREAM_EVIDENCE ]] || {
        echo 'Regular verified upstream resolution evidence is required.' >&2; exit 1;
    }
    cp "$S20P_UPSTREAM_EVIDENCE" "$S20P_CAPTURE_DIR/provenance/upstream-resolution.json"
fi
locked_upstream=$(python3 "$S20P_LOCK_TOOL" --lock "$S20P_SOURCE_LOCK" get \
    https://github.com/chasey-dev/immortalwrt-mt798x-rebase.git --ref 25.12)
[[ $locked_upstream =~ ^[0-9a-f]{40}$ ]] || { echo 'Exact locked upstream commit required.' >&2; exit 1; }
if [[ -n $S20P_UPSTREAM_COMMIT && $S20P_UPSTREAM_COMMIT != "$locked_upstream" ]]; then
    echo 'Workflow-selected upstream SHA differs from the generated source lock.' >&2
    exit 1
fi
if [[ -n $S20P_UPSTREAM_EVIDENCE ]]; then
    evidence_upstream=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["head_commit"])' "$S20P_UPSTREAM_EVIDENCE")
    [[ $evidence_upstream == "$locked_upstream" ]] || {
        echo 'Upstream resolution evidence differs from the generated source lock.' >&2; exit 1;
    }
fi
export S20P_UPSTREAM_COMMIT="$locked_upstream"
if [[ -n $S20P_FIXED_INPUTS ]]; then
    python3 ci/fixed_inputs.py verify "$S20P_FIXED_INPUTS" \
        --head "$locked_upstream" --components "$S20P_COMPONENT_INPUTS" \
        --capture "$S20P_CAPTURE_DIR/provenance/fixed"
    S20P_APPLICATION_POLICY=$(python3 ci/fixed_inputs.py policy "$S20P_FIXED_INPUTS")
fi
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
verify_runtime_inputs
if [[ -n $S20P_FIXED_INPUTS ]]; then
    python3 ci/fixed_inputs.py verify "$S20P_FIXED_INPUTS" \
        --head "$locked_upstream" --components "$S20P_COMPONENT_INPUTS"
    python3 ci/fixed_inputs.py verify-http "$S20P_FIXED_INPUTS" --log "$S20P_HTTP_LOG"
fi
python3 ci/dashboard.py inspect \
    --rootfs output/inspection/rootfs --payload output/inspection/rootfs.squashfs \
    --output output/reports/dashboard.json
if [[ $S20P_APPLICATION_POLICY == minimal-20261031 ]]; then
    python3 ci/minimal_security.py inspect \
        --rootfs output/inspection/rootfs --payload output/inspection/rootfs.squashfs \
        --provenance output/provenance --output output/reports/minimal-security.json
fi
if [[ -f output/provenance/component-preparation.json ]]; then
    python3 ci/component_gate.py --rootfs output/inspection/rootfs \
        --payload output/inspection/rootfs.squashfs --provenance output/provenance \
        --output output/reports/components.json
fi
python3 ci/go_security.py inspect \
    --rootfs output/inspection/rootfs --payload output/inspection/rootfs.squashfs \
    --output output/reports/go-x509-security.json
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
gate_command+=(
    --application-selection \
    --application-policy "$S20P_APPLICATION_POLICY" \
    --rootfs output/inspection/rootfs \
    --kernel-config output/metadata/kernel/.config \
    --packages output/inspection/rootfs/lib/apk/db/installed \
    --dtb output/inspection/board.dtb \
    --source-lock output/provenance/source-lock.json \
    --public-key output/public-keys/public-key.pem \
    --signed-feeds output/reports/signed-feeds.json \
    --baseline-dir "$S20P_BASELINE_DIR" \
    --output output/reports/preservation-image.json
)
if [[ -n $S20P_UPSTREAM_EVIDENCE ]]; then
    gate_command+=(--upstream-evidence "$S20P_UPSTREAM_EVIDENCE")
fi
"${gate_command[@]}"
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
