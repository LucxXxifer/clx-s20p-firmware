#!/usr/bin/env python3
"""Prepare an exact, run-local source lock for the reviewed S20P upstream line."""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import zipfile


UPSTREAM_URL = "https://github.com/chasey-dev/immortalwrt-mt798x-rebase.git"
UPSTREAM_REF = "25.12"
BASE_COMMIT = "9f397975c7b47ce6986a6e39529d2f72fd5c729d"
ORIGINAL_COMMIT = "03eb30617955519e35845838c6255ebe687eb717"
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")


class UpstreamSyncError(ValueError):
    pass


@dataclass(frozen=True)
class Resolution:
    commit: str
    should_build: bool
    status: str
    merge_base: str
    forced: bool
    previous_attempted: bool = False
    previous_comparison: dict | None = None
    component_fingerprint: str | None = None
    plugin_sources: list | None = None


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise UpstreamSyncError("duplicate JSON key: " + key)
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpstreamSyncError("invalid JSON evidence") from error


def validate_state(state, wrapper_commit):
    if (not isinstance(state, dict) or state.get("schema_version") not in (1, 2) or
            state.get("wrapper_commit") != wrapper_commit or
            not COMMIT_RE.fullmatch(str(state.get("upstream_commit", ""))) or
            type(state.get("build_started")) is not bool or
            type(state.get("previous_attempted", False)) is not bool or
            state.get("outcome") not in {"skipped", "success", "failure"}):
        raise UpstreamSyncError("state is invalid or belongs to another wrapper commit")
    if state["schema_version"] == 2:
        if (not re.fullmatch(r"[0-9a-f]{64}", str(state.get("component_fingerprint"))) or
                not isinstance(state.get("plugin_heads"), dict) or not state["plugin_heads"] or
                any(not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", key) or
                    not COMMIT_RE.fullmatch(str(value)) for key, value in state["plugin_heads"].items())):
            raise UpstreamSyncError("invalid component identity in previous state")
    return state


def read_state(path, wrapper_commit):
    state_path = Path(path)
    if state_path.is_symlink() or not state_path.is_file():
        raise UpstreamSyncError("regular previous state file required")
    return validate_state(strict_json(state_path.read_bytes()), wrapper_commit)


def verify_state_zip(zip_path, api_digest, wrapper_commit):
    archive_path = Path(zip_path)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(api_digest or "")):
        raise UpstreamSyncError("GitHub artifact API SHA256 digest is required")
    if sha256_file(archive_path) != api_digest.split(":", 1)[1]:
        raise UpstreamSyncError("GitHub artifact API digest differs from state ZIP")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != "upstream-state.json":
                raise UpstreamSyncError("state artifact must contain only upstream-state.json")
            item = entries[0]
            kind = stat.S_IFMT(item.external_attr >> 16)
            if kind not in (0, stat.S_IFREG) or item.file_size > 16384:
                raise UpstreamSyncError("state artifact member is not a small regular file")
            if archive.testzip() is not None:
                raise UpstreamSyncError("state artifact ZIP CRC check failed")
            state = strict_json(archive.read(item))
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise UpstreamSyncError("invalid state artifact ZIP") from error
    return validate_state(state, wrapper_commit)


def validate_previous_comparison(head_commit, comparison):
    if comparison is None:
        return
    if (not isinstance(comparison, dict) or
            set(comparison) != {"base_commit", "compare_status", "merge_base"} or
            not COMMIT_RE.fullmatch(str(comparison.get("base_commit", ""))) or
            comparison["base_commit"] == head_commit or
            comparison.get("compare_status") != "ahead" or
            comparison.get("merge_base") != comparison["base_commit"]):
        raise UpstreamSyncError("upstream compare does not prove advancement from the previous verified head")


def resolve_head(base_commit, head_commit, status, merge_base, *, force,
                 previous_state=None, wrapper_commit=None,
                 previous_status=None, previous_merge_base=None, component_inputs=None):
    if base_commit != BASE_COMMIT:
        raise UpstreamSyncError("committed S20P upstream base changed; review the new base before tracking")
    if not COMMIT_RE.fullmatch(str(head_commit)):
        raise UpstreamSyncError("upstream head must be a full lowercase Git commit SHA")
    if status not in {"identical", "ahead"}:
        raise UpstreamSyncError("upstream branch is not identical to or ahead of the reviewed base")
    if merge_base != base_commit:
        raise UpstreamSyncError("upstream compare does not prove the reviewed base is an ancestor")
    if (head_commit == base_commit) != (status == "identical"):
        raise UpstreamSyncError("upstream compare status disagrees with the resolved head")
    if wrapper_commit is not None and not COMMIT_RE.fullmatch(str(wrapper_commit)):
        raise UpstreamSyncError("wrapper commit must be a full lowercase Git commit SHA")
    previous_comparison = None
    if (isinstance(previous_state, dict) and wrapper_commit is not None and
            previous_state.get("wrapper_commit") == wrapper_commit):
        validate_state(previous_state, wrapper_commit)
        if previous_state["upstream_commit"] != head_commit:
            previous_comparison = {
                "base_commit": previous_state["upstream_commit"],
                "compare_status": previous_status,
                "merge_base": previous_merge_base,
            }
            validate_previous_comparison(head_commit, previous_comparison)
    component_fingerprint, plugins = None, None
    if component_inputs is not None:
        import components
        components.validate(component_inputs)
        component_fingerprint = component_inputs["fingerprint"]
        plugins = component_inputs["plugin_sources"]
    already_attempted = (
        isinstance(previous_state, dict) and wrapper_commit is not None and
        previous_state.get("wrapper_commit") == wrapper_commit and
        previous_state.get("upstream_commit") == head_commit and
        (component_fingerprint is None or previous_state.get("component_fingerprint") == component_fingerprint) and
        (previous_state.get("build_started") is True or
         previous_state.get("previous_attempted") is True)
    )
    return Resolution(
        commit=head_commit,
        should_build=bool(force or ((component_fingerprint is not None or head_commit != base_commit) and not already_attempted)),
        status=status,
        merge_base=merge_base,
        forced=bool(force),
        previous_attempted=already_attempted,
        previous_comparison=previous_comparison,
        component_fingerprint=component_fingerprint,
        plugin_sources=plugins,
    )


def update_lock(lock, resolution):
    if not isinstance(lock, dict) or lock.get("schema_version") != 1:
        raise UpstreamSyncError("unsupported source lock schema")
    updated = json.loads(json.dumps(lock))
    sources = updated.get("sources")
    if not isinstance(sources, list):
        raise UpstreamSyncError("source lock has no source inventory")
    matches = [row for row in sources if isinstance(row, dict) and row.get("role") == "main-source"]
    if len(matches) != 1:
        raise UpstreamSyncError("source lock must have one main-source entry")
    main = matches[0]
    if (main.get("url") != UPSTREAM_URL or main.get("ref") != UPSTREAM_REF or
            main.get("commit") != BASE_COMMIT):
        raise UpstreamSyncError("committed main-source entry differs from the reviewed starting pin")
    if resolution.commit != BASE_COMMIT:
        main["commit"] = resolution.commit
        main["provenance"] = "daily-upstream-head-verified-by-github-compare"
        main["resolution"] = (
            "Resolved exact 25.12 head " + resolution.commit + "; GitHub compare status=" +
            resolution.status + ", merge-base=" + resolution.merge_base +
            ", reviewed starting pin=" + BASE_COMMIT
        )
    if resolution.plugin_sources is not None:
        import plugin_sources
        updated = plugin_sources.update_lock(updated, resolution.plugin_sources)
    return updated


def verify_baseline(directory):
    root = Path(directory)
    manifest_path = root / "baseline-sha256.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise UpstreamSyncError("baseline integrity manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise UpstreamSyncError("baseline integrity manifest is invalid") from error
    if not isinstance(manifest, dict) or not manifest:
        raise UpstreamSyncError("baseline integrity manifest must be a non-empty object")
    expected_names = set(manifest) | {"baseline-sha256.json"}
    actual_names = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise UpstreamSyncError("symlink in baseline input: " + path.relative_to(root).as_posix())
        if path.is_file():
            actual_names.add(path.relative_to(root).as_posix())
    if actual_names != expected_names:
        raise UpstreamSyncError("baseline file inventory differs from its integrity manifest")
    for relative, expected in manifest.items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
            raise UpstreamSyncError("invalid baseline file record: " + str(relative))
        if sha256_file(path) != expected:
            raise UpstreamSyncError("baseline integrity mismatch: " + str(relative))


def prepare_baseline(source, destination, commit):
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise UpstreamSyncError("runtime baseline destination already exists")
    verify_baseline(source)
    policy_path = source / "policy.json"
    policy = json.loads(policy_path.read_text())
    if (policy.get("upstream_commit") != BASE_COMMIT or
            policy.get("original_upstream_commit") != ORIGINAL_COMMIT):
        raise UpstreamSyncError("committed baseline policy differs from the reviewed starting identity")
    shutil.copytree(source, destination)
    if commit == BASE_COMMIT:
        return destination
    runtime_policy_path = destination / "policy.json"
    policy["upstream_commit"] = commit
    runtime_policy_path.write_text(json.dumps(policy, indent=2) + "\n")
    manifest_path = destination / "baseline-sha256.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["policy.json"] = sha256_file(runtime_policy_path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    verify_baseline(destination)
    return destination


def prepare(lock_path, baseline_path, output_dir, resolution, component_inputs=None):
    lock_path, baseline_path, output_dir = Path(lock_path), Path(baseline_path), Path(output_dir)
    if output_dir.exists():
        raise UpstreamSyncError("runtime input destination already exists")
    try:
        lock = strict_json(lock_path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise UpstreamSyncError("committed source lock is invalid") from error
    updated = update_lock(lock, resolution)
    output_dir.mkdir(parents=True)
    if resolution.component_fingerprint is not None:
        import components
        if components.validate(component_inputs)["fingerprint"] != resolution.component_fingerprint:
            raise UpstreamSyncError("prepared component inputs differ from the selected identity")
        (output_dir / "component-resolution.json").write_text(json.dumps(component_inputs, indent=2, sort_keys=True) + "\n")
    runtime_lock = output_dir / "sources.lock.json"
    runtime_lock.write_text(json.dumps(updated, indent=2) + "\n")
    runtime_baseline = output_dir / "baseline"
    prepare_baseline(baseline_path, runtime_baseline, resolution.commit)
    evidence = {
        "schema_version": 2 if resolution.component_fingerprint is not None else 1,
        "repository": UPSTREAM_URL.removesuffix(".git"),
        "ref": UPSTREAM_REF,
        "base_commit": BASE_COMMIT,
        "head_commit": resolution.commit,
        "compare_status": resolution.status,
        "merge_base": resolution.merge_base,
        "should_build": resolution.should_build,
        "forced": resolution.forced,
        "previous_attempted": resolution.previous_attempted,
        "previous_comparison": resolution.previous_comparison,
    }
    if resolution.component_fingerprint is not None:
        evidence.update(component_fingerprint=resolution.component_fingerprint,
                        plugin_sources=resolution.plugin_sources)
    (output_dir / "upstream-resolution.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    verify_prepared(lock_path, baseline_path, runtime_lock, runtime_baseline,
                    output_dir / "upstream-resolution.json")
    return runtime_lock, runtime_baseline, output_dir / "upstream-resolution.json"


def verify_prepared(committed_lock_path, baseline_path, runtime_lock_path,
                    runtime_baseline_path, evidence_path):
    for path in (committed_lock_path, runtime_lock_path, evidence_path):
        if Path(path).is_symlink() or not Path(path).is_file():
            raise UpstreamSyncError("regular prepared input required: " + str(path))
    committed = strict_json(Path(committed_lock_path).read_bytes())
    runtime = strict_json(Path(runtime_lock_path).read_bytes())
    evidence = strict_json(Path(evidence_path).read_bytes())
    expected = {
        "schema_version": evidence.get("schema_version"),
        "repository": UPSTREAM_URL.removesuffix(".git"),
        "ref": UPSTREAM_REF,
        "base_commit": BASE_COMMIT,
        "head_commit": evidence.get("head_commit"),
        "compare_status": evidence.get("compare_status"),
        "merge_base": BASE_COMMIT,
    }
    if (evidence.get("schema_version") not in (1, 2) or
            any(evidence.get(key) != value for key, value in expected.items()) or
            not COMMIT_RE.fullmatch(str(evidence.get("head_commit", ""))) or
            evidence.get("compare_status") not in {"identical", "ahead"} or
            type(evidence.get("should_build")) is not bool or
            type(evidence.get("forced")) is not bool or
            type(evidence.get("previous_attempted")) is not bool):
        raise UpstreamSyncError("upstream resolution evidence is not a valid descendant comparison")
    expected_build = evidence["forced"] or (
        (evidence["schema_version"] == 2 or evidence["head_commit"] != BASE_COMMIT) and not evidence["previous_attempted"]
    )
    if evidence["should_build"] != expected_build:
        raise UpstreamSyncError("upstream resolution build decision does not match verified state")
    validate_previous_comparison(evidence["head_commit"], evidence.get("previous_comparison"))
    resolution = Resolution(evidence["head_commit"], evidence["should_build"],
                            evidence["compare_status"], evidence["merge_base"], evidence["forced"],
                            evidence["previous_attempted"], evidence.get("previous_comparison"),
                            evidence.get("component_fingerprint"), evidence.get("plugin_sources"))
    validate_component_evidence(evidence, Path(evidence_path).with_name("component-resolution.json"))
    if runtime != update_lock(committed, resolution):
        raise UpstreamSyncError("prepared source lock differs from reviewed pins and resolved head")
    verify_baseline(baseline_path)
    verify_baseline(runtime_baseline_path)
    baseline_root, runtime_root = Path(baseline_path), Path(runtime_baseline_path)
    original_names = {path.relative_to(baseline_root).as_posix() for path in baseline_root.rglob("*") if path.is_file()}
    runtime_names = {path.relative_to(runtime_root).as_posix() for path in runtime_root.rglob("*") if path.is_file()}
    if runtime_names != original_names:
        raise UpstreamSyncError("runtime baseline file inventory differs from reviewed baseline")
    original_policy = strict_json((baseline_root / "policy.json").read_bytes())
    runtime_policy = strict_json((runtime_root / "policy.json").read_bytes())
    original_policy.pop("upstream_commit", None)
    runtime_policy_without_sha = dict(runtime_policy)
    if runtime_policy_without_sha.pop("upstream_commit", None) != resolution.commit or runtime_policy_without_sha != original_policy:
        raise UpstreamSyncError("runtime baseline policy differs beyond the resolved upstream SHA")
    for relative in original_names - {"policy.json", "baseline-sha256.json"}:
        if sha256_file(baseline_root / relative) != sha256_file(runtime_root / relative):
            raise UpstreamSyncError("runtime baseline changed reviewed file: " + relative)
    original_manifest = strict_json((baseline_root / "baseline-sha256.json").read_bytes())
    runtime_manifest = strict_json((runtime_root / "baseline-sha256.json").read_bytes())
    expected_manifest = dict(original_manifest)
    expected_manifest["policy.json"] = sha256_file(runtime_root / "policy.json")
    if runtime_manifest != expected_manifest:
        raise UpstreamSyncError("runtime baseline manifest differs beyond the upstream-bound policy hash")
    return resolution


def validate_component_evidence(evidence, path):
    if evidence.get("schema_version") == 2:
        import components
        value = components.load(path)
        if (value["fingerprint"] != evidence.get("component_fingerprint") or
                value["plugin_sources"] != evidence.get("plugin_sources")):
            raise UpstreamSyncError("component resolution differs from the daily source evidence")
        return value
    if evidence.get("component_fingerprint") is not None or evidence.get("plugin_sources") is not None:
        raise UpstreamSyncError("legacy source evidence cannot authorize plugin pin changes")
    return None


def validate_build_evidence(evidence, commit, component_path):
    expected = {"repository": UPSTREAM_URL.removesuffix(".git"), "ref": UPSTREAM_REF,
                "base_commit": BASE_COMMIT, "head_commit": commit,
                "compare_status": "identical" if commit == BASE_COMMIT else "ahead",
                "merge_base": BASE_COMMIT, "should_build": True}
    if (not isinstance(evidence, dict) or evidence.get("schema_version") not in (1, 2) or
            not COMMIT_RE.fullmatch(str(commit)) or
            any(evidence.get(key) != value for key, value in expected.items()) or
            type(evidence.get("forced")) is not bool or
            type(evidence.get("previous_attempted")) is not bool or
            (evidence["previous_attempted"] and not evidence["forced"]) or
            (evidence["schema_version"] == 1 and commit == BASE_COMMIT and not evidence["forced"])):
        raise UpstreamSyncError("upstream resolution does not bind a buildable reviewed descendant SHA")
    validate_previous_comparison(commit, evidence.get("previous_comparison"))
    return validate_component_evidence(evidence, component_path)


def write_state(path, *, wrapper_commit, upstream_commit, build_started, outcome, run_id, run_attempt,
                previous_attempted=False, component_inputs=None):
    if not COMMIT_RE.fullmatch(str(wrapper_commit)) or not COMMIT_RE.fullmatch(str(upstream_commit)):
        raise UpstreamSyncError("state commits must be full lowercase Git commit SHAs")
    if (type(build_started) is not bool or type(previous_attempted) is not bool or
            outcome not in {"skipped", "success", "failure"}):
        raise UpstreamSyncError("invalid build state")
    state = {
        "schema_version": 1,
        "wrapper_commit": wrapper_commit,
        "upstream_commit": upstream_commit,
        "build_started": build_started,
        "previous_attempted": previous_attempted,
        "outcome": outcome,
        "run_id": str(run_id),
        "run_attempt": str(run_attempt),
    }
    if component_inputs is not None:
        import components
        components.validate(component_inputs)
        state.update(schema_version=2, component_fingerprint=component_inputs["fingerprint"],
                     plugin_heads={row["repository"]: row["head_commit"]
                                   for row in component_inputs["plugin_sources"]})
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists() or state_path.is_symlink():
        raise UpstreamSyncError("state output already exists")
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return state


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare")
    command.add_argument("--lock", type=Path, required=True)
    command.add_argument("--baseline", type=Path, required=True)
    command.add_argument("--head", required=True)
    command.add_argument("--status", required=True)
    command.add_argument("--merge-base", required=True)
    command.add_argument("--wrapper-commit", required=True)
    command.add_argument("--previous-state", type=Path)
    command.add_argument("--previous-status")
    command.add_argument("--previous-merge-base")
    command.add_argument("--components", type=Path)
    command.add_argument("--force", action="store_true")
    command.add_argument("--output-dir", type=Path, required=True)
    command.add_argument("--github-output", type=Path)
    prepared = sub.add_parser("verify-prepared")
    prepared.add_argument("--committed-lock", type=Path, required=True)
    prepared.add_argument("--committed-baseline", type=Path, required=True)
    prepared.add_argument("--lock", type=Path, required=True)
    prepared.add_argument("--baseline", type=Path, required=True)
    prepared.add_argument("--evidence", type=Path, required=True)
    verify = sub.add_parser("verify-state-zip")
    verify.add_argument("zip", type=Path)
    verify.add_argument("--api-digest", required=True)
    verify.add_argument("--wrapper-commit", required=True)
    verify.add_argument("--destination", type=Path, required=True)
    record = sub.add_parser("record-state")
    record.add_argument("--output", type=Path, required=True)
    record.add_argument("--wrapper-commit", required=True)
    record.add_argument("--upstream-commit", required=True)
    record.add_argument("--build-started", choices=("true", "false"), required=True)
    record.add_argument("--previous-attempted", choices=("true", "false"), default="false")
    record.add_argument("--outcome", choices=("skipped", "success", "failure"), required=True)
    record.add_argument("--run-id", required=True)
    record.add_argument("--run-attempt", required=True)
    record.add_argument("--components", type=Path)
    args = parser.parse_args()
    if args.command == "verify-prepared":
        try:
            resolution = verify_prepared(args.committed_lock, args.committed_baseline,
                                         args.lock, args.baseline, args.evidence)
        except (OSError, UpstreamSyncError) as error:
            parser.exit(1, "prepared input verification rejected: " + str(error) + "\n")
        print(json.dumps({"result": "verified", "upstream_commit": resolution.commit}, sort_keys=True))
        return
    if args.command == "verify-state-zip":
        try:
            state = verify_state_zip(args.zip, args.api_digest, args.wrapper_commit)
            if args.destination.exists() or args.destination.is_symlink():
                raise UpstreamSyncError("state destination already exists")
            args.destination.parent.mkdir(parents=True, exist_ok=True)
            args.destination.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        except (OSError, UpstreamSyncError) as error:
            parser.exit(1, "state verification rejected: " + str(error) + "\n")
        print(json.dumps({"result": "verified", "upstream_commit": state["upstream_commit"]}, sort_keys=True))
        return
    if args.command == "record-state":
        try:
            inputs = None
            if args.components:
                import components
                inputs = components.load(args.components)
            state = write_state(args.output, wrapper_commit=args.wrapper_commit,
                                upstream_commit=args.upstream_commit,
                                build_started=args.build_started == "true", outcome=args.outcome,
                                previous_attempted=args.previous_attempted == "true",
                                run_id=args.run_id, run_attempt=args.run_attempt, component_inputs=inputs)
        except (OSError, UpstreamSyncError) as error:
            parser.exit(1, "state write rejected: " + str(error) + "\n")
        print(json.dumps(state, sort_keys=True))
        return
    previous_state = None
    try:
        inputs = None
        if args.components:
            import components
            inputs = components.load(args.components)
        if args.previous_state:
            previous_state = read_state(args.previous_state, args.wrapper_commit)
        resolution = resolve_head(
            BASE_COMMIT, args.head, args.status, args.merge_base, force=args.force,
            previous_state=previous_state, wrapper_commit=args.wrapper_commit,
            previous_status=args.previous_status, previous_merge_base=args.previous_merge_base,
            component_inputs=inputs,
        )
        lock, baseline, evidence = prepare(args.lock, args.baseline, args.output_dir, resolution, inputs)
    except (OSError, UpstreamSyncError) as error:
        parser.exit(1, "upstream sync rejected: " + str(error) + "\n")
    result = {
        "should_build": str(resolution.should_build).lower(),
        "previous_attempted": str(resolution.previous_attempted).lower(),
        "upstream_commit": resolution.commit,
        "source_lock": str(lock),
        "baseline_dir": str(baseline),
        "resolution_evidence": str(evidence),
        "inputs_dir": str(args.output_dir),
    }
    if args.github_output:
        with args.github_output.open("a") as handle:
            for key, value in result.items():
                handle.write(key + "=" + value + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
