#!/usr/bin/env bash
# 上游源码拉取、清理和复位。

clone_repo() {
    if [[ ! -d $BUILD_DIR ]]; then
        echo "克隆仓库: $REPO_URL 分支: $REPO_BRANCH"
        if ! git_retry clone --depth 1 -b "$REPO_BRANCH" "$REPO_URL" "$BUILD_DIR"; then
            echo "错误：克隆仓库 $REPO_URL 失败" >&2
            exit 1
        fi
    fi
}


clean_up() {
    if [[ ! -d "$BUILD_DIR" ]]; then
        echo "Build directory $BUILD_DIR does not exist"
        return
    fi
    cd "$BUILD_DIR" || return
    if [[ -f ".config" ]]; then
        \rm -f ".config"
    fi
    if [[ -d "tmp" ]]; then
        \rm -rf "tmp"
    fi
    if [[ -d "logs" ]]; then
        \rm -rf "logs/*"
    fi
    if [[ -d "feeds" ]]; then
        ./scripts/feeds clean
    fi
    mkdir -p "tmp"
    echo "1" >"tmp/.build"
}


reset_feeds_conf() {
    if [[ ${S20P_AUTOBUILD:-0} == 1 ]]; then
        [[ $COMMIT_HASH =~ ^[0-9a-f]{40}$ ]] || return 1
        # Fetch the actual historical object; depth-one branch HEAD is insufficient.
        git_retry fetch --depth 1 origin "$COMMIT_HASH"
        # Dedicated CI checkout: discard the pre-clone mirror rewrite before patching.
        git reset --hard "$COMMIT_HASH"
        git checkout --detach "$COMMIT_HASH"
        [[ $(git rev-parse HEAD) == "$COMMIT_HASH" ]] || return 1
        [[ -n ${S20P_LOCK_TOOL:-} && -n ${S20P_SOURCE_LOCK:-} ]] || return 1
        python3 "$S20P_LOCK_TOOL" --lock "$S20P_SOURCE_LOCK" verify "$PWD" --expected "$REPO_URL"
        return
    fi
    # 所有源码修正都基于远端分支或指定提交的干净状态。
    git_retry reset --hard "origin/$REPO_BRANCH"
    git_retry clean -f -d
    git_retry pull
    if [[ $COMMIT_HASH != "none" ]]; then
        git_retry checkout "$COMMIT_HASH"
    fi
}
