#!/usr/bin/env python3
"""Bind daily components to prepared sources and reproduce CA outputs offline."""
import argparse
import base64
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

import ca_security
import components
import plugin_sources
import upstream_sync


def regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("regular component evidence required: " + str(path))
    return path.read_bytes()


def read(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate component evidence key: " + key)
            value[key] = item
        return value
    return json.loads(regular(path), object_pairs_hook=unique)


def manifest(path):
    rows = read(path)
    if not isinstance(rows, list) or not rows:
        raise ValueError("nonempty source manifest required")
    seen = set()
    for row in rows:
        name = row.get("path")
        if (not isinstance(name, str) or not name or "\\" in name or "\0" in name or
                name.startswith("/") or ".." in PurePosixPath(name).parts or name == "." or
                str(PurePosixPath(name)) != name or name in seen):
            raise ValueError("unsafe/duplicate source manifest path")
        seen.add(name)
        mode = row.get("mode")
        if (not isinstance(mode, str) or not re.fullmatch(r"0o[0-7]{1,4}", mode) or
                oct(int(mode, 8)) != mode):
            raise ValueError("invalid source manifest mode")
        if "target" in row:
            if (set(row) != {"path", "mode", "target"} or
                    not isinstance(row["target"], str) or not row["target"] or "\0" in row["target"]):
                raise ValueError("invalid source symlink")
        elif (set(row) != {"path", "mode", "size", "sha256"} or
              type(row["size"]) is not int or row["size"] < 0 or
              not components.SHA.fullmatch(str(row["sha256"]))):
            raise ValueError("invalid regular source manifest row")
    if any(str(parent) in seen for name in seen for parent in PurePosixPath(name).parents):
        raise ValueError("source file cannot be another input's ancestor")
    return rows


def bound_sources(directory):
    directory = Path(directory)
    source = read(directory / "source-lock.json")
    requested_bytes = regular(directory / "requested-sources.lock.json")
    requested = read(directory / "requested-sources.lock.json")
    if requested != source.get("source_lock") or components.digest(requested_bytes) != source.get("source_lock_sha256"):
        raise ValueError("requested/embedded component source lock mismatch")
    value = components.load(directory / "component-resolution.json")
    resolution = read(directory / "upstream-resolution.json")
    upstream_sync.validate_build_evidence(resolution, source["upstream_commit"], directory / "component-resolution.json")
    if resolution.get("schema_version") != 2 or source.get("upstream_resolution") != resolution:
        raise ValueError("daily component resolution is not embedded in provenance")
    decision = upstream_sync.Resolution(
        source["upstream_commit"], True, resolution["compare_status"], resolution["merge_base"],
        resolution["forced"], resolution["previous_attempted"], resolution.get("previous_comparison"),
        value["fingerprint"], value["plugin_sources"])
    committed = read(Path(__file__).with_name("sources.lock.json"))
    if requested != upstream_sync.update_lock(committed, decision):
        raise ValueError("daily component requested lock differs from resolved sources")
    resolved = {}
    for row in source["sources"]:
        repo = plugin_sources.repository(row["url"])
        if repo in resolved:
            raise ValueError("duplicate resolved component repository")
        resolved[repo] = row["commit"]
    pins = {plugin_sources.repository(row["url"]): row["commit"] for row in requested["sources"]}
    if any(pins.get(repo) != commit for repo, commit in resolved.items()):
        raise ValueError("actual component checkout differs from requested lock")
    prepared = manifest(directory / "prepared-source-files.json")
    wrapper = manifest(directory / "wrapper-source-files.json")
    encoded = {"prepared": prepared, "wrapper": wrapper, "lock": requested}
    for key, rows in (("prepared_tree_hash", prepared), ("wrapper_tree_hash", wrapper),
                      ("build_fingerprint", encoded)):
        if components.digest(json.dumps(rows, sort_keys=True).encode()) != source.get(key):
            raise ValueError("component source/build fingerprint mismatch: " + key)
    return value, prepared


def reproduce_preparation(directory):
    directory = Path(directory)
    value, prepared = bound_sources(directory)
    evidence = read(directory / "component-preparation.json")
    if (set(evidence) != {"schema_version", "status", "component_fingerprint", "ca", "ca_source_files",
                         "ca_archive_base64", "adguard_version"} or evidence["schema_version"] != 1 or
            evidence["status"] != "pass" or evidence["component_fingerprint"] != value["fingerprint"] or
            evidence["adguard_version"] != value["adguard"]["version"]):
        raise ValueError("component preparation schema/identity mismatch")
    with tempfile.TemporaryDirectory() as name:
        package = Path(name)
        # Reuse the input validator for path, SHA256 and Git-blob checks.
        candidate = json.loads(json.dumps(value))
        candidate["ca"]["files"] = evidence["ca_source_files"]
        candidate["fingerprint"] = components.fingerprint(candidate)
        components.validate(candidate)
        components.write_package(package, evidence["ca_source_files"])
        if components.ca_version((package / "Makefile").read_text()) < components.ca_version(
                components.file_bytes(value["ca"]["files"], "Makefile").decode()):
            raise ValueError("prepared CA is older than resolved official source")
        archive = base64.b64decode(evidence["ca_archive_base64"], validate=True)
        reproduction = ca_security.reproduce(package, archive)
    if reproduction != evidence["ca"]:
        raise ValueError("CA independent reproduction differs from preparation")
    expected = {row["path"]: row for row in reproduction["prepared_files"]}
    actual = {row["path"].removeprefix("package/system/ca-certificates/"): row for row in prepared
              if row["path"].startswith("package/system/ca-certificates/")}
    if (set(expected) != set(actual) or not expected or any(
            row["sha256"] != actual[name].get("sha256") for name, row in expected.items())):
        raise ValueError("reproduced CA source differs from prepared manifest")
    return value, prepared, reproduction


def verify_adguard(root, packages, selected):
    version = packages.get("adguardhome", {}).get("version", "")
    if not re.fullmatch(re.escape(selected) + r"-r\d+", version):
        raise ValueError("AdGuard image version differs from official stable source")
    for name in ("luci-app-adguardhome", "luci-i18n-adguardhome-zh-cn"):
        if name not in packages:
            raise ValueError("matching AdGuard LuCI package missing")
    config = regular(Path(root) / "etc/config/adguardhome").decode()
    init = regular(Path(root) / "etc/init.d/adguardhome").decode()
    keep = regular(Path(root) / "lib/upgrade/keep.d/adguardhome").decode()
    if (not re.search(r"option\s+enabled\s+['\"]?0['\"]?(?:\s|$)", config) or
            "/etc/adguardhome/work" not in config or "work_dir:string:/etc/adguardhome/work" not in init or
            keep != "/etc/adguardhome/\n/etc/config/adguardhome\n"):
        raise ValueError("AdGuard disabled/persistent-data/backup defaults changed")
    return {"version": version, "enabled": False, "work_dir": "/etc/adguardhome/work"}


def inspect(root, provenance_dir, payload, packages):
    value, prepared, ca = reproduce_preparation(provenance_dir)
    ca_security.verify_image(root, ca, packages, prepared)
    adguard = verify_adguard(root, packages, value["adguard"]["version"])
    return {"schema_version": 1, "status": "pass", "rootfs_sha256": components.digest(regular(payload)),
            "component_fingerprint": value["fingerprint"], "ca": ca, "adguard": adguard,
            "plugin_sources": plugin_sources.identity(value["plugin_sources"])}


def verify_release(directory, report, payload_sha256, packages):
    value, _, ca = reproduce_preparation(directory)
    if (report.get("schema_version") != 1 or report.get("status") != "pass" or
            report.get("rootfs_sha256") != payload_sha256 or report.get("ca") != ca or
            report.get("component_fingerprint") != value["fingerprint"] or
            report.get("plugin_sources") != plugin_sources.identity(value["plugin_sources"])):
        raise ValueError("component report differs from independent source reproduction")
    for name in ("ca-bundle", "ca-certificates"):
        if packages.get(name, {}).get("version") != ca["version"]:
            raise ValueError("release CA package version differs from reproduced source")
    expected_adg = {"version": packages.get("adguardhome", {}).get("version"), "enabled": False,
                    "work_dir": "/etc/adguardhome/work"}
    if (report.get("adguard") != expected_adg or not re.fullmatch(
            re.escape(value["adguard"]["version"]) + r"-r\d+", str(expected_adg["version"]))):
        raise ValueError("release AdGuard version/default evidence mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from preservation import parse_packages
    try:
        report = inspect(args.rootfs, args.provenance, args.payload,
                         parse_packages(args.rootfs / "lib/apk/db/installed"))
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, "Component image gate: " + str(error) + "\n")


if __name__ == "__main__":
    main()
