#!/usr/bin/env python3
"""Append evidence-based release notes after the existing candidate gates pass.

No device/configuration inputs, LLM dependency, artifact tools, or release writes.
Baseline identity/download failures block publication. Optional upstream commit
summaries may be unavailable and are explicitly labeled as such.
"""
import argparse
from datetime import datetime
import html
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote

import release_candidate as release
from release_inventory import build_inventory, validate_inventory, SHA

TAG = re.compile(r"s20p-a-25\.12-(?:fixed-)?candidate-(\d+)-(\d+)-([0-9a-f]{8})\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
WORKFLOW_ID = 394714552


class GitHub:
    def json(self, path):
        try:
            result = subprocess.run(["gh", "api", path], check=True, capture_output=True,
                                    timeout=60)
        except (subprocess.SubprocessError, OSError) as error:
            raise RuntimeError("GitHub changelog API lookup failed") from error
        if len(result.stdout) > 16 * 1024 * 1024:
            raise ValueError("GitHub changelog response exceeds limit")
        return json.loads(result.stdout)

    def download(self, asset, destination):
        # Only the canonical API asset id is used, never an asset-supplied URL.
        try:
            with destination.open("wb") as output:
                subprocess.run(["gh", "api", f"repos/{self.repository}/releases/assets/{asset['id']}",
                                "-H", "Accept: application/octet-stream"], stdout=output,
                               stderr=subprocess.PIPE, check=True, timeout=900)
        except (subprocess.SubprocessError, OSError) as error:
            raise RuntimeError("GitHub changelog asset download failed") from error

    def __init__(self, repository):
        self.repository = repository


def checked_asset(api, asset, cache):
    identity, size, digest = asset.get("id"), asset.get("size"), asset.get("digest", "")
    if (type(identity) is not int or identity <= 0 or type(size) is not int
            or not 0 < size <= 2 * 1024 * 1024 * 1024
            or not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)):
        raise ValueError("baseline asset lacks an exact API SHA256 identity")
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / str(identity)
    if path.is_symlink():
        raise ValueError("unsafe baseline cache")
    if not path.is_file() or path.stat().st_size != size or release.sha256_file(path) != digest[7:]:
        api.download(asset, path)
    if path.stat().st_size != size or release.sha256_file(path) != digest[7:]:
        raise ValueError("baseline asset API digest mismatch")
    return path


def tag_commit(api, repository, tag):
    obj = api.json(f"repos/{repository}/git/ref/tags/{quote(tag, safe='')}")["object"]
    for _ in range(5):
        if not isinstance(obj.get("sha"), str) or not SHA.fullmatch(obj["sha"]):
            break
        if obj.get("type") == "commit":
            return obj["sha"]
        if obj.get("type") != "tag":
            break
        obj = api.json(f"repos/{repository}/git/tags/{obj['sha']}")["object"]
    raise ValueError("baseline tag does not resolve to a commit")


def release_rows(api, repository):
    rows = []
    for page in range(1, 101):
        chunk = api.json(f"repos/{repository}/releases?per_page=100&page={page}")
        if not isinstance(chunk, list) or len(chunk) > 100:
            raise ValueError("invalid release history response")
        rows.extend(chunk)
        if len(chunk) < 100:
            return rows
    raise ValueError("release history exceeds bounded lookup; baseline is unknown")


def find_baseline(api, repository, current_run, cache, *, current_attempt="1"):
    rows = [row for row in release_rows(api, repository) if row.get("draft") is False
            and TAG.fullmatch(row.get("tag_name", ""))
            and TAG.fullmatch(row["tag_name"]).groups()[:2] != (str(current_run), str(current_attempt))]
    try:
        rows.sort(key=lambda r: (datetime.fromisoformat(r["published_at"].replace("Z", "+00:00")), r["id"]),
                  reverse=True)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("candidate publication time is not verifiable") from error
    for row in rows:
        match = TAG.fullmatch(row["tag_name"])
        run_id, attempt, upstream8 = match.groups()
        run = api.json(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
        if (str(run.get("id")) != run_id or str(run.get("run_attempt")) != attempt
                or run.get("workflow_id") != WORKFLOW_ID):
            raise ValueError("baseline CI identity mismatch")
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        assets = row.get("assets", [])
        names = [a.get("name") for a in assets]
        if len(names) != 6 or len(set(names)) != 6:
            raise ValueError("baseline does not have the verified six-asset contract")
        assets = dict(zip(names, assets))
        if not {"BUILD-VERIFICATION.json", "SHA256SUMS", "archive.SHA256SUMS", "INSTALLATION.md"} <= set(assets):
            raise ValueError("baseline release metadata is incomplete")
        meta_path = checked_asset(api, assets["BUILD-VERIFICATION.json"], cache)
        if meta_path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("baseline metadata exceeds limit")
        metadata = json.loads(meta_path.read_text())
        wrapper, upstream = metadata.get("wrapper_commit", ""), metadata.get("upstream_commit", "")
        if (not SHA.fullmatch(wrapper) or not SHA.fullmatch(upstream)
                or metadata.get("run_id") != run_id or metadata.get("run_attempt") != attempt
                or metadata.get("stable_eligible") is not False
                or metadata.get("live_acceptance") != "not-run"
                or wrapper != run.get("head_sha") or upstream[:8] != upstream8
                or tag_commit(api, repository, row["tag_name"]) != wrapper):
            raise ValueError("baseline tag/run/metadata identity mismatch")
        sums = release.parse_sha256sums(checked_asset(api, assets["SHA256SUMS"], cache))
        archive_name = f"s20p-samebuild-{run_id}-{attempt}.tar.gz"
        expected = {"BUILD-VERIFICATION.json", "INSTALLATION.md", "archive.SHA256SUMS",
                    archive_name, metadata.get("image")}
        if set(sums) != expected or set(assets) != expected | {"SHA256SUMS"}:
            raise ValueError("baseline checksum asset coverage mismatch")
        for name, digest in sums.items():
            if assets[name].get("digest") != "sha256:" + digest:
                raise ValueError("baseline API and release checksums disagree")
        if metadata.get("image_sha256") != sums[metadata["image"]]:
            raise ValueError("baseline image checksum identity mismatch")
        if "changelog_inventory" in metadata:
            snapshot = validate_inventory(metadata["changelog_inventory"], metadata)
            origin = "verified-release-inventory"
        else:
            archive_sum = release.parse_sha256sums(checked_asset(api, assets["archive.SHA256SUMS"], cache))
            if archive_sum != {archive_name: sums[archive_name]}:
                raise ValueError("baseline same-build checksum mismatch")
            archive = checked_asset(api, assets[archive_name], cache)
            with tempfile.TemporaryDirectory(prefix="s20p-changelog-") as temp:
                root = Path(temp) / "bundle"
                release.extract_verified_archive(archive, root)
                snapshot = validate_inventory(build_inventory(root), metadata)
            origin = "checksum-verified-archive"
        return {"tag": row["tag_name"], "published_at": row["published_at"],
                "metadata": metadata, "inventory": snapshot, "inventory_origin": origin}
    return None


def compare_source(api, repository, base, head):
    result = {"repository": repository, "base": base, "head": head,
              "status": "unavailable", "total_commits": None, "commits": [], "complete": False}
    try:
        data = api.json(f"repos/{repository}/compare/{base}...{head}?per_page=20&page=1")
    except (RuntimeError, ValueError):
        return result
    if not isinstance(data, dict):
        return result
    commits = data.get("commits", [])
    total = data.get("total_commits")
    status = data.get("status")
    if (type(total) is not int or total < 0 or not isinstance(commits, list)
            or len(commits) > 20 or not isinstance(status, str)
            or status not in {"ahead", "behind", "diverged", "identical"}):
        return result
    for row in commits:
        if (not isinstance(row, dict) or not isinstance(row.get("sha"), str)
                or not SHA.fullmatch(row["sha"]) or not isinstance(row.get("commit"), dict)
                or not isinstance(row["commit"].get("message"), str)):
            return result
    result.update(status=data["status"], total_commits=total,
                  commits=[{"sha": r["sha"], "subject": r["commit"]["message"].split("\n")[0][:200]}
                           for r in commits], complete=len(commits) == total)
    return result


def md(value):
    # Treat upstream text as text, including HTML, mentions and control characters.
    value = " ".join(str(value).split())
    value = "".join(c for c in value if c.isprintable() and c not in "\u202a\u202b\u202d\u202e\u2066\u2067\u2068\u2069")
    value = html.escape(value, quote=False).replace("@", "&#64;")
    return re.sub(r"([\\`*_{}\[\]()#+!|>~])", r"\\\1", value)


def delta(old, new):
    return [(k, old.get(k), new.get(k)) for k in sorted(old.keys() | new.keys()) if old.get(k) != new.get(k)]


def diff_table(title, old, new, *, limit=80):
    rows = delta(old, new)
    lines = ["", "### " + title, ""]
    if not rows:
        return lines + ["與比較基準相同。"]
    lines += [f"共 {len(rows)} 項變動。", "", "| 項目 | 前版 | 本版 | 類型 |", "| --- | --- | --- | --- |"]
    for name, before, after in rows[:limit]:
        kind = "新增" if before is None else "移除" if after is None else "變更"
        lines.append(f"| {md(name)} | {md(before) if before is not None else '—'} | {md(after) if after is not None else '—'} | {kind} |")
    if len(rows) > limit:
        lines += ["", f"此處列前 {limit} 項，另 {len(rows)-limit} 項請對照兩版同批封存／BUILD-VERIFICATION.json；不是零變更。"]
    return lines


def security_summary(snapshot):
    result = {}
    for name in ('go', 'ca'):
        for field, value in snapshot[name].items():
            result[name + '/' + field] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    for name, report in snapshot['reports'].items():
        result[name] = json.dumps(report, ensure_ascii=False, sort_keys=True)
    result['BusyBox cron INFO config'] = json.dumps(snapshot['cron_syslog_info'])
    return result


def render_notes(repository, metadata, current, baseline, upstream):
    old = baseline["inventory"] if baseline else None
    lines = ["## 本版更新日誌", ""]
    if baseline:
        tag = baseline["tag"]
        lines += [f"比較基準：[前一個成功發布候選 {md(tag)}](https://github.com/{repository}/releases/tag/{tag})。依發布時間選擇，已核對成功 CI attempt、tag commit 與資產 SHA256。"]
    else:
        lines += ["首次記錄：沒有較早成功發布的同系列候選，以下僅列本版證據，不推測歷史差異。"]
    lines += ["", f"本版 run `{metadata['run_id']}`／attempt `{metadata['run_attempt']}`；kernel ABI：{md(current['packages']['kernel'])}。",
              "", "證據層級：上游宣稱＝公開提交摘要；同批產物＝checksum 綁定的套件與報告，專項有效性依本版驗證器；硬體／冷開機／插件互通仍未驗收，候選不等於 stable。"]
    if old:
        lines += diff_table("系統與插件套件（同批映像清單）", old["packages"], current["packages"])
        lines += diff_table("公開來源 SHA", old["sources"], current["sources"], limit=40)
        if old["patch_inventory_available"] and current["patch_inventory_available"]:
            lines += diff_table("wrapper 補丁檔案（來源變化，不等於已套用）", old["patches"], current["patches"], limit=30)
        else:
            lines += ["", "### wrapper 補丁檔案", "", "至少一版未提供完整 wrapper 補丁清單，不能判定新增／移除。"]
        lines += diff_table("安全／logging 證據變動", security_summary(old), security_summary(current), limit=30)
    lines += ["", "### 安全與 logging（本版同批證據）", ""]
    go, ca = current["go"], current["ca"]
    if go:
        lines += [f"- Go：{md(', '.join(go['versions']))}；檢查狀態 {md(go['status'])}；保留未修補例外 {go['exception_count']} 項。{md(', '.join(go['advisories']))}"]
    else:
        lines += ["- Go：未提供可比較的同批報告。"]
    if ca:
        lines += [f"- CA：{md(ca.get('version', '版本未提供'))}，{ca.get('certificate_count', '未知')} 張；bundle SHA256 {md(ca.get('bundle_sha256', '未提供'))}。"]
    else:
        lines += ["- CA：未提供可比較的同批報告。"]
    cron = current["cron_syslog_info"]
    state = "已啟用" if cron is True else "未啟用" if cron is False else "未提供設定證據"
    previous_cron = ""
    if old and old["cron_syslog_info"] != cron:
        previous_cron = "（相對基準有變動）"
    lines += [f"- BusyBox cron INFO：{state}{previous_cron}；依 final.config，仍需以實機日誌驗收。"]
    for name in ("minimal-security", "kernel-security", "wifi-logging"):
        report = current["reports"].get(name)
        if not report:
            lines += [f"- {name}：未提供本版專項報告；不能由檔名或版本號聲稱修復。"]
            continue
        changed = ""
        if old and old["reports"].get(name) != report:
            changed = "（相對基準變動）"
        lines += [f"- {name}：同批報告 {md(report.get('status', '未知'))}，profile {md(report.get('profile_id', '未提供'))} {md(report.get('cve', ''))}{changed}；runtime 未驗收。"]
    removed = sorted(set(old["reports"]) - set(current["reports"])) if old else []
    if removed:
        lines += [f"- 基準有、本版未提供的專項報告：{md(', '.join(removed))}。不繼承前版的安全修復宣稱。"]
    lines += ["", "上游內建 CVE 覆蓋若無本版專項來源／編譯證據，維持未確認。Wi-Fi logging 報告不證明真實 Group rekey timeout 根因已修復。私人 DNS／啟動修補與安裝後下載的插件程式不在此公開映像證據內。",
              "", "### 上游宣稱（公開提交摘要，非成品修復驗證）", ""]
    if not upstream:
        lines += ["本次沒有可列出的來源提交比較；請以上述來源 SHA 及同批證據為準。"]
    for comparison in upstream:
        repo, base, head = (comparison[k] for k in ("repository", "base", "head"))
        url = f"https://github.com/{repo}/compare/{base}...{head}"
        total = comparison["total_commits"]
        status = comparison["status"]
        label = "API 無法提供摘要，變動內容未確認" if status == "unavailable" else f"{status}，{total} 個提交"
        if not comparison["complete"] and status != "unavailable":
            label += "；摘要已截斷，僅列目前取得的前 20 筆內提交"
        lines += [f"- [{md(repo)} 完整比較]({url})：{label}。"]
        for commit in comparison["commits"][:20]:
            lines += [f"  - [{commit['sha'][:10]}](https://github.com/{repo}/commit/{commit['sha']}) {md(commit['subject'])}"]
    lines += ["", "驗證範圍及已知例外以本版 INSTALLATION.md、BUILD-VERIFICATION.json 和同批封存為準；本日誌不放寬來源、安全、preservation 或發布關卡。", ""]
    notes = "\n".join(lines)
    if len(notes.encode()) > 100000:
        raise ValueError("release changelog exceeds bounded notes size")
    return notes


def generate(api, repository, assets, cache):
    release.verify_checksums(assets)
    metadata = release.read_json(assets, "BUILD-VERIFICATION.json")
    current = validate_inventory(metadata["changelog_inventory"], metadata)
    baseline = find_baseline(api, repository, metadata["run_id"], cache,
                             current_attempt=metadata["run_attempt"])
    comparisons = []
    if baseline:
        old = baseline["inventory"]
        sources = [(repository, old["wrapper_commit"], current["wrapper_commit"])]
        for url, before, after in delta(old["sources"], current["sources"]):
            if before and after and url.startswith("https://github.com/"):
                slug = url.removeprefix("https://github.com/")
                if REPOSITORY.fullmatch(slug):
                    sources.append((slug, before, after))
        for slug, before, after in sources:
            if before != after:
                comparisons.append(compare_source(api, slug, before, after))
            if len(comparisons) == 10:
                break
    notes = render_notes(repository, metadata, current, baseline, comparisons)
    if baseline:
        changed_sources = sum(1 for _, b, a in delta(baseline["inventory"]["sources"], current["sources"]) if b and a)
        if changed_sources + (baseline["inventory"]["wrapper_commit"] != current["wrapper_commit"]) > len(comparisons):
            notes += "\n上游提交摘要最多查詢 10 個 GitHub repository；其餘來源僅列精確 SHA，不代表沒有變更。\n"
    return notes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not REPOSITORY.fullmatch(args.repository):
        parser.error("invalid GitHub repository")
    args.output.write_text(generate(GitHub(args.repository), args.repository, args.assets_dir, args.cache_dir))


if __name__ == "__main__":
    main()
