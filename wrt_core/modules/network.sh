#!/usr/bin/env bash

network_retry() {
    local max_attempts="${NETWORK_RETRY_MAX:-5}"
    local delay_seconds="${NETWORK_RETRY_DELAY:-5}"
    local attempt=1
    local exit_code

    while true; do
        "$@" && return 0
        exit_code=$?
        if ((attempt >= max_attempts)); then
            return "$exit_code"
        fi

        echo "网络命令失败，${delay_seconds}s 后重试 ($attempt/$max_attempts): $*" >&2
        sleep "$delay_seconds"
        attempt=$((attempt + 1))
        delay_seconds=$((delay_seconds * 2))
    done
}

git_retry() {
    local max_attempts="${NETWORK_RETRY_MAX:-5}"
    local delay_seconds="${NETWORK_RETRY_DELAY:-5}"
    local attempt=1
    local exit_code
    local clone_target=""
    local clone_url=""
    local argument
    local locked_commit

    if [[ ${1:-} == "clone" ]]; then
        clone_target="${!#}"
        for argument in "$@"; do
            case "$argument" in
                https://*|http://*|file://*) clone_url=$argument ;;
            esac
        done
        if [[ ${S20P_AUTOBUILD:-0} == 1 ]]; then
            [[ -n $clone_url && -n ${S20P_LOCK_TOOL:-} && -n ${S20P_SOURCE_LOCK:-} ]] || return 1
            locked_commit=$(python3 "$S20P_LOCK_TOOL" --lock "$S20P_SOURCE_LOCK" get "$clone_url") || return
        fi
    fi

    while true; do
        if git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 "$@"; then
            if [[ -n $clone_target && ${S20P_AUTOBUILD:-0} == 1 ]]; then
                if [[ $(git -C "$clone_target" rev-parse --is-shallow-repository) == true ]]; then
                    git -C "$clone_target" fetch --depth 1 origin "$locked_commit" || return
                else
                    git -C "$clone_target" fetch origin "$locked_commit" || return
                fi
                git -C "$clone_target" checkout --detach "$locked_commit" || return
                python3 "$S20P_LOCK_TOOL" --lock "$S20P_SOURCE_LOCK" verify "$clone_target" --expected "$clone_url" || return
                python3 "${S20P_CI_DIR:?}/provenance.py" record-clone \
                    "$clone_target" --output "${S20P_GIT_LOG:?}" || return
            fi
            return 0
        else
            exit_code=$?
        fi
        if ((attempt >= max_attempts)); then
            return "$exit_code"
        fi

        if [[ -n "$clone_target" && -e "$clone_target" ]]; then
            rm -rf "$clone_target"
        fi

        echo "Git 网络操作失败，${delay_seconds}s 后重试 ($attempt/$max_attempts): git $*" >&2
        sleep "$delay_seconds"
        attempt=$((attempt + 1))
        delay_seconds=$((delay_seconds * 2))
    done
}

curl_retry() {
    if [[ ${S20P_AUTOBUILD:-0} == 1 ]]; then
        python3 "${S20P_CI_DIR:?}/http_record.py" "${S20P_HTTP_LOG:?}" \
            curl --retry 3 --retry-delay 2 --retry-all-errors "$@"
        return
    fi
    network_retry curl --retry 3 --retry-delay 2 --retry-all-errors "$@"
}

wget_retry() {
    if [[ ${S20P_AUTOBUILD:-0} == 1 ]]; then
        python3 "${S20P_CI_DIR:?}/http_record.py" "${S20P_HTTP_LOG:?}" \
            wget --tries=3 --waitretry=2 "$@"
        return
    fi
    network_retry wget --tries=3 --waitretry=2 "$@"
}
