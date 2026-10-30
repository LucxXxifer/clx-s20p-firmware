#!/usr/bin/env python3
"""Resolve the configured plugin/feed branches; never silently rewind a source."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

import source_lock

LOCK = Path(__file__).with_name("sources.lock.json")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
# These pins remain available to other wrapper profiles, but cannot trigger an
# S20P image rebuild. CA and Go have their own official-source resolvers.
EXCLUDED = {
    "gdy666/luci-app-lucky": "Lucky is omitted from the S20P image",
    "dqsq2e2/luci-app-tingreader": "TingReader is omitted from the S20P image",
    "kenzok8/openwrt-honk": "Honk is omitted from the S20P image",
    "kenzok8/openwrt-daede": "the daed fragment is not selected for S20P",
    "zqinking/luci-app-adguardhome": "AdGuard is deferred from the S20P image",
    "sbwml/packages_lang_golang": "Go is resolved from the official stable catalog",
    "immortalwrt/immortalwrt": "CA is resolved separately; this fallback tree is not a plugin feed",
}


def repository(url):
    canonical = source_lock.canonical_url(url)
    if not re.fullmatch(r"https://github.com/[a-z0-9_.-]+/[a-z0-9_.-]+", canonical):
        raise ValueError("only locked public GitHub plugin sources are supported")
    return canonical.removeprefix("https://github.com/")


def selected(lock):
    # lookup also checks duplicate repositories and full commit identities.
    rows = []
    for row in lock["sources"]:
        source_lock.lookup(lock, row["url"], row["ref"])
        if row["role"] != "main-source" and repository(row["url"]) not in EXCLUDED:
            rows.append(row)
    return sorted(rows, key=lambda row: repository(row["url"]))


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", "--cache", "10m", path], timeout=60))


def comparison(repo, base, head, fetch=api):
    if base == head:
        return {"base_commit": base, "compare_status": "identical", "merge_base": base}
    value = fetch(f"repos/{repo}/compare/{base}...{head}")
    result = {"base_commit": base, "compare_status": value.get("status"),
              "merge_base": value.get("merge_base_commit", {}).get("sha")}
    validate_comparison(result, head)
    return result


def validate_comparison(value, head):
    if (not isinstance(value, dict) or
            set(value) != {"base_commit", "compare_status", "merge_base"} or
            not COMMIT.fullmatch(str(value.get("base_commit"))) or
            value.get("merge_base") != value["base_commit"] or
            value.get("compare_status") != ("identical" if value["base_commit"] == head else "ahead")):
        raise ValueError("plugin comparison does not prove a forward source update")


def resolve(lock, previous_heads=None, fetch=api):
    previous_heads = previous_heads or {}
    def one(row):
        repo = repository(row["url"])
        ref = row["ref"]
        if ref == "HEAD":
            ref = fetch(f"repos/{repo}")["default_branch"]
        value = fetch(f"repos/{repo}/git/ref/heads/{quote(ref, safe='')}")
        head = value.get("object", {}).get("sha")
        if (not COMMIT.fullmatch(str(head)) or value.get("object", {}).get("type") != "commit"
                or value.get("ref") != "refs/heads/" + ref):
            raise ValueError("plugin branch did not resolve to one exact commit: " + repo)
        prior = previous_heads.get(repo)
        return {"repository": repo, "ref": row["ref"], "resolved_ref": ref,
                "base_commit": row["commit"], "head_commit": head,
                "comparison": comparison(repo, row["commit"], head, fetch),
                "previous_comparison": comparison(repo, prior, head, fetch) if prior else None}
    with ThreadPoolExecutor(max_workers=6) as pool:
        result = list(pool.map(one, selected(lock)))
    return validate(result, lock)


def validate(rows, lock=None):
    if lock is None:
        lock = json.loads(LOCK.read_text())
    expected = {repository(row["url"]): row for row in selected(lock)}
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("daily plugin source inventory is incomplete")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"repository", "ref", "resolved_ref", "base_commit",
                                                   "head_commit", "comparison", "previous_comparison"}):
            raise ValueError("invalid daily plugin source record")
        repo = row["repository"]
        original = expected.get(repo)
        if (not original or repo in seen or row["ref"] != original["ref"] or
                row["base_commit"] != original["commit"] or
                not COMMIT.fullmatch(str(row["head_commit"])) or
                not re.fullmatch(r"[A-Za-z0-9_./-]+", str(row["resolved_ref"])) or
                (row["ref"] != "HEAD" and row["resolved_ref"] != row["ref"])):
            raise ValueError("plugin source differs from the approved repository/ref/base")
        seen.add(repo)
        validate_comparison(row["comparison"], row["head_commit"])
        if row["comparison"]["base_commit"] != row["base_commit"]:
            raise ValueError("plugin comparison base differs from the reviewed source pin")
        if row["previous_comparison"] is not None:
            validate_comparison(row["previous_comparison"], row["head_commit"])
    if [row["repository"] for row in rows] != sorted(expected):
        raise ValueError("daily plugin sources must use canonical ordering")
    return rows


def identity(rows):
    # Previous-run comparisons are evidence, not a reason to rebuild again.
    return [{key: row[key] for key in ("repository", "ref", "resolved_ref", "head_commit")}
            for row in rows]


def update_lock(lock, rows):
    validate(rows, lock)
    updated = json.loads(json.dumps(lock))
    heads = {row["repository"]: row for row in rows}
    for row in updated["sources"]:
        value = heads.get(repository(row["url"]))
        if value is not None and value["head_commit"] != row["commit"]:
            row["commit"] = value["head_commit"]
            row["provenance"] = "daily-plugin-head-verified-by-github-compare"
            row["resolution"] = ("Exact " + value["resolved_ref"] + " head; forward from reviewed pin " +
                                 value["base_commit"] + "; source/package/ABI/image gates still required")
            # This tree described the old revision, not the newly selected one.
            row.pop("source_tree", None)
    return updated
