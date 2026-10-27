#!/usr/bin/env python3
"""Fail-closed, offline preservation gates for the pinned dqsq S20P rebuild.

Exit 0: checks in this phase passed; 1: missing/invalid evidence or invariant
failure; 2: exact, hash-addressed differences still require review. This checks
build artifacts, not boot, hardware behavior, or bit-identical reproduction.
"""

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import struct
import sys


# This is a one-source security transition, not an extensible missing-path list.
# Updating the review requires a code review of this independent digest anchor.
SECURITY_REMOVAL_REVIEW = Path(__file__).with_name("reviewed-security-removals") / "chasey-9f397975.json"
SECURITY_REMOVAL_REVIEW_SHA256 = "0dc44aff92ac7662334e3a1dc9f562e2704149767b54b26cdb984626bbc1fc8b"
SECURITY_REMOVAL_COMMIT = "9f397975c7b47ce6986a6e39529d2f72fd5c729d"
B_SOURCE_COMMIT = "9f397975c7b47ce6986a6e39529d2f72fd5c729d"
B_OPTIONAL_PACKAGE_REVIEW = Path(__file__).with_name("reviewed-differences") / "lucky-image-omission-b.json"
B_OPTIONAL_PACKAGE_REVIEW_SHA256 = "03d11294764d575f08a14d0e260603ca89fbc7532f6c453c291e244415cbd8aa"
B_OPTIONAL_PACKAGE_NAMES = (
    "lucky", "luci-app-lucky", "luci-i18n-lucky-zh-cn",
)
B_LUCKY_STORE_KEY_SHA256 = "17d39c124a39a0f0bf54601073ddb3d42bad45b40be9ad28c17e805bee04db08"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def read_json(path):
    return json.loads(Path(path).read_text())


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def source_commit(lock):
    sources = lock.get("sources") if isinstance(lock, dict) else None
    if not isinstance(sources, list):
        return None
    main = [row for row in sources if isinstance(row, dict) and row.get("role") == "main-source"]
    if len(main) != 1:
        return None
    return main[0].get("commit")


def require_b_main_source(rows, description):
    if not isinstance(rows, list):
        raise ValueError(description + " main-source inventory is missing")
    main = [row for row in rows if isinstance(row, dict) and row.get("role") == "main-source"]
    if len(main) != 1:
        raise ValueError(description + " requires exactly one main-source")
    row = main[0]
    if (row.get("url", "").removesuffix(".git") !=
            "https://github.com/chasey-dev/immortalwrt-mt798x-rebase" or
            row.get("ref") != "25.12" or row.get("commit") != B_SOURCE_COMMIT):
        raise ValueError(description + " main-source does not match fixed B repository/ref/commit")


def require_b_resolved_main_source(rows):
    if not isinstance(rows, list):
        raise ValueError("resolved main-source inventory is missing")
    expected_url = "https://github.com/chasey-dev/immortalwrt-mt798x-rebase"
    matches = [row for row in rows if isinstance(row, dict) and
               row.get("url", "").removesuffix(".git") == expected_url]
    if len(matches) != 1 or matches[0].get("commit") != B_SOURCE_COMMIT:
        raise ValueError("resolved main-source does not match fixed B repository/commit")


def require_tracked_main_source(rows, expected_commit, description, *, require_ref=False):
    if not isinstance(rows, list):
        raise ValueError(description + " main-source inventory is missing")
    expected_url = "https://github.com/chasey-dev/immortalwrt-mt798x-rebase"
    matches = [row for row in rows if isinstance(row, dict) and
               row.get("url", "").removesuffix(".git") == expected_url]
    if (len(matches) != 1 or (require_ref and matches[0].get("ref") != "25.12") or
            matches[0].get("commit") != expected_commit):
        raise ValueError(description + " main-source does not match exact tracked repository/ref/commit")


def validate_upstream_evidence(path, commit):
    if commit == B_SOURCE_COMMIT:
        return None
    if not path:
        raise ValueError("upstream compare evidence is required for a moving source head")
    evidence_path = Path(path)
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise ValueError("regular upstream compare evidence is required")
    evidence = strict_json(evidence_path.read_bytes())
    expected = {
        "schema_version": 1,
        "repository": "https://github.com/chasey-dev/immortalwrt-mt798x-rebase",
        "ref": "25.12",
        "base_commit": B_SOURCE_COMMIT,
        "head_commit": commit,
        "compare_status": "ahead",
        "merge_base": B_SOURCE_COMMIT,
        "should_build": True,
    }
    if (not isinstance(evidence, dict) or
            any(evidence.get(key) != value for key, value in expected.items()) or
            type(evidence.get("forced")) is not bool or
            type(evidence.get("previous_attempted")) is not bool or
            (evidence.get("previous_attempted") is True and evidence.get("forced") is not True)):
        raise ValueError("upstream compare evidence does not prove a forward update from the reviewed base")
    return evidence


def load_b_optional_package_policy(baseline_dir, source_lock_path,
                                   review_path=B_OPTIONAL_PACKAGE_REVIEW,
                                   upstream_evidence_path=None):
    """Load the one hash-pinned Lucky omission for the reviewed upstream line."""
    source_path = Path(source_lock_path)
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError("regular source lock required for optional package policy")
    lock = strict_json(source_path.read_bytes())
    requested_lock = lock.get("source_lock", lock)
    commit = lock.get("upstream_commit") or source_commit(requested_lock)
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("exact upstream commit required for optional package policy")
    if commit == "03eb30617955519e35845838c6255ebe687eb717":
        return None
    if source_commit(requested_lock) != commit:
        raise ValueError("requested main-source must match the exact upstream commit")
    require_tracked_main_source(requested_lock.get("sources"), commit, "requested", require_ref=True)
    validate_upstream_evidence(upstream_evidence_path, commit)
    if "source_lock" in lock:
        if lock.get("upstream_commit") != commit:
            raise ValueError("resolved main-source upstream commit does not match requested lock")
        require_tracked_main_source(lock.get("sources"), commit, "resolved")

    review_file = Path(review_path)
    if review_file.is_symlink() or not review_file.is_file():
        raise ValueError("regular B optional package review required")
    raw = review_file.read_bytes()
    if digest(raw) != B_OPTIONAL_PACKAGE_REVIEW_SHA256:
        raise ValueError("B optional package review digest mismatch")
    review = strict_json(raw)
    if (review.get("schema_version") != 1 or review.get("source", {}).get("commit") != B_SOURCE_COMMIT or
            review.get("source", {}).get("repository") != "https://github.com/chasey-dev/immortalwrt-mt798x-rebase" or
            review.get("source", {}).get("ref") != "25.12"):
        raise ValueError("B optional package review source mismatch")
    if review.get("package_names") != list(B_OPTIONAL_PACKAGE_NAMES):
        raise ValueError("B optional package review must name the exact Lucky package set")

    baseline = Path(baseline_dir)
    package_path = baseline / "original-package-manifest.json"
    rootfs_path = baseline / "full-rootfs-manifest.json"
    package_bytes, rootfs_bytes = package_path.read_bytes(), rootfs_path.read_bytes()
    if digest(package_bytes) != review.get("baseline_package_manifest_sha256"):
        raise ValueError("B optional package review baseline package digest mismatch")
    if digest(rootfs_bytes) != review.get("rootfs_manifest_sha256"):
        raise ValueError("B optional package review baseline rootfs digest mismatch")
    integrity = read_json(baseline / "baseline-sha256.json")
    if (integrity.get(package_path.name) != digest(package_bytes) or
            integrity.get(rootfs_path.name) != digest(rootfs_bytes)):
        raise ValueError("B optional package review baseline integrity mismatch")

    package_rows = strict_json(package_bytes)
    expected_packages = [row for row in package_rows
                         if isinstance(row, dict) and row.get("name") in B_OPTIONAL_PACKAGE_NAMES]
    reviewed_packages = review.get("baseline_packages")
    expected_by_name = {row["name"]: row for row in expected_packages}
    if (len(expected_by_name) != len(B_OPTIONAL_PACKAGE_NAMES) or
            reviewed_packages != [expected_by_name.get(name) for name in B_OPTIONAL_PACKAGE_NAMES]):
        raise ValueError("B optional package review baseline package identity mismatch")

    manifest_rows = strict_json(rootfs_bytes).get("files")
    if not isinstance(manifest_rows, list):
        raise ValueError("B optional package review baseline rootfs inventory is invalid")
    baseline_files = {row.get("path"): row for row in manifest_rows if isinstance(row, dict)}
    removed_records = review.get("removed_records")
    if (not isinstance(removed_records, list) or len(removed_records) != review.get("removed_path_count") or
            not removed_records or any(baseline_files.get(row.get("path")) != row for row in removed_records)):
        raise ValueError("B optional package review must match exact baseline file records")
    removed_paths = [row.get("path") for row in removed_records]
    if len(removed_paths) != len(set(removed_paths)):
        raise ValueError("duplicate B optional package review file path")
    critical_rows = strict_json((baseline / "critical-files-manifest.json").read_bytes()).get("files", [])
    critical_paths = {row.get("path") for row in critical_rows}
    owners = review.get("package_payload_path_owners")
    if not isinstance(owners, dict):
        raise ValueError("B optional package review file ownership map is missing")
    service_links = {"/etc/rc.d/K15lucky", "/etc/rc.d/S99lucky"}
    for path in removed_paths:
        path_owners = owners.get(path)
        allowed_owners = ({"lucky (generated service enable links)"}
                          if path in service_links else set(B_OPTIONAL_PACKAGE_NAMES))
        if (not isinstance(path_owners, list) or not path_owners or
                not set(path_owners) <= allowed_owners):
            raise ValueError("B optional package path has unrelated or unknown owner: " + path)
        if path in critical_paths and not set(path_owners) <= allowed_owners:
            raise ValueError("B optional critical path has unrelated or unknown owner: " + path)
    if review.get("required_absent_service_links") != sorted(service_links):
        raise ValueError("B optional package review service links mismatch")
    if any(baseline_files.get(path, {}).get("type") != "symlink"
           for path in review["required_absent_service_links"]):
        raise ValueError("B optional package review service link baseline mismatch")

    store = review.get("trusted_store_feed")
    if (not isinstance(store, dict) or store.get("repository_root") != "https://istore.istoreos.com/repo-apk" or
            store.get("trusted_key_sha256") != B_LUCKY_STORE_KEY_SHA256):
        raise ValueError("trusted Lucky store feed key mismatch")
    key_records = [row for row in critical_rows if row.get("path") == "/etc/apk/keys/istore.pem"]
    if len(key_records) != 1 or key_records[0].get("sha256") != B_LUCKY_STORE_KEY_SHA256:
        raise ValueError("trusted Lucky store key is not in the critical baseline")
    if not {"aarch64_cortex-a53", "aarch64_generic"} <= set(store.get("archlist", [])):
        raise ValueError("trusted Lucky store feed does not list S20P architecture")
    indexes = store.get("indexes")
    expected_index_urls = {
        "https://istore.istoreos.com/repo-apk/aarch64_generic/nas/packages.adb",
        "https://istore.istoreos.com/repo-apk/all/nas_luci/packages.adb",
        "https://istore.istoreos.com/repo-apk/all/meta/packages.adb",
    }
    if not isinstance(indexes, list) or len(indexes) != 3 or any(
            not isinstance(row, dict) or row.get("signature") != "verified" or
            row.get("signing_key_sha256") != B_LUCKY_STORE_KEY_SHA256 or
            not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", "")))
            for row in indexes):
        raise ValueError("trusted Lucky store feed signature evidence is incomplete")
    if {row["url"] for row in indexes} != expected_index_urls:
        raise ValueError("trusted Lucky store feed index set mismatch")
    index_by_url = {row["url"]: row for row in indexes}
    packages = store.get("packages")
    if not isinstance(packages, list) or {row.get("name") for row in packages if isinstance(row, dict)} != set(B_OPTIONAL_PACKAGE_NAMES):
        raise ValueError("trusted Lucky store feed package set mismatch")
    baseline_names = {row["name"] for row in package_rows}
    for package in packages:
        if (package.get("arch") not in {"aarch64_generic", "noarch"} or
                package.get("index_url") not in index_by_url or
                package.get("index_sha256") != index_by_url[package["index_url"]]["sha256"] or
                not re.fullmatch(r"[0-9a-f]{64}", str(package.get("fetched_apk_sha256", ""))) or
                not re.fullmatch(r"[0-9a-f]{64}", str(package.get("index_identity", ""))) or
                type(package.get("file_size")) is not int or package["file_size"] <= 0):
            raise ValueError("trusted Lucky store feed package evidence is malformed")
        if not set(package.get("depends", [])) <= baseline_names | set(B_OPTIONAL_PACKAGE_NAMES):
            raise ValueError("store package dependency is not in B baseline: " + package["name"])
    application = store.get("application_metadata")
    meta_index = index_by_url["https://istore.istoreos.com/repo-apk/all/meta/packages.adb"]
    if (not isinstance(application, dict) or application.get("name") != "app-meta-lucky" or
            application.get("version") != "2.19.5-r1" or application.get("arch") != "noarch" or
            application.get("index_url") != meta_index["url"] or
            application.get("index_sha256") != meta_index["sha256"] or
            not re.fullmatch(r"[0-9a-f]{64}", str(application.get("index_identity", ""))) or
            type(application.get("file_size")) is not int or application["file_size"] <= 0 or
            not re.fullmatch(r"[0-9a-f]{64}", str(application.get("fetched_apk_sha256", ""))) or
            not set(application.get("depends", [])) <= baseline_names | set(B_OPTIONAL_PACKAGE_NAMES)):
        raise ValueError("iStore Lucky application metadata evidence is incomplete")
    if not store.get("version_note"):
        raise ValueError("trusted Lucky store feed version limitation is missing")
    normalized = dict(review)
    normalized.update({
        "upstream_commit": commit,
        "reviewed_base_commit": B_SOURCE_COMMIT,
        "packages": review["baseline_packages"],
        "absent_files": review["removed_records"],
        "review_sha256": B_OPTIONAL_PACKAGE_REVIEW_SHA256,
    })
    return normalized


def reviewed_b_optional_package_absences(review, original, actual):
    """Require exactly the reviewed B-only package set to be absent."""
    optional = set(B_OPTIONAL_PACKAGE_NAMES)
    present = optional & set(actual)
    if present:
        raise ValueError("reviewed optional Lucky package must not be installed: " + ", ".join(sorted(present)))
    missing = set(original) - set(actual)
    unexpected = missing - optional
    if unexpected:
        raise ValueError("unreviewed missing baseline package: " + ", ".join(sorted(unexpected)))
    if missing != optional:
        raise ValueError("reviewed optional Lucky packages must be absent")
    for name in optional:
        old = original[name]
        expected = next(row for row in review["baseline_packages"] if row["name"] == name)
        if (old.get("version") != expected.get("version") or
                old.get("depends", old.get("dependencies", "")) != expected.get("dependencies", "")):
            raise ValueError("baseline package identity differs from B optional package review: " + name)
    return missing


def reviewed_b_optional_file_absences(review, baseline, actual, preapproved_absences=None):
    """Return only exact reviewed baseline file records that are absent."""
    allowed = {row["path"]: row for row in review["removed_records"]}
    if any(baseline.get(path) != row for path, row in allowed.items()):
        raise ValueError("reviewed Lucky file record differs from baseline")
    missing = set(baseline) - set(actual)
    unreviewed = missing - set(allowed) - set(preapproved_absences or ())
    if unreviewed:
        raise ValueError("unreviewed missing rootfs path: " + ", ".join(sorted(unreviewed)))
    # Regular package payloads and service links must be gone. Directories may
    # remain when the rest of the image still needs them.
    required_absent = {path for path, record in allowed.items()
                       if record.get("type") != "directory"}
    present = required_absent & set(actual)
    if present:
        raise ValueError("reviewed Lucky files must be absent: " + ", ".join(sorted(present)))
    return missing & set(allowed)


def parse_config(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        match = re.fullmatch(r"(CONFIG_[A-Za-z0-9_+.-]+)=(.*)", line.strip())
        unset = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_+.-]+) is not set", line.strip())
        if not match and not unset:
            continue
        key, value = match.groups() if match else (unset.group(1), "n")
        if key in values and values[key] != value:
            raise ValueError("Conflicting config assignments: " + key)
        values[key] = value
    if not values:
        raise ValueError("Empty or unrecognized config: " + str(path))
    return values


def package_selectors(path):
    """Map emitted APK names to real recipe selectors from tmp/.packageinfo.

    Pinned upstream include/feeds.mk FormatABISuffix adds ABI-Version to
    non-kmods, with a separating '-' only when the recipe ends in a digit.
    Provides aliases do not establish that an original runtime package exists.
    """
    rows, current = [], None
    for line in Path(path).read_text().splitlines():
        if line.startswith("Package: "):
            current = {"Package": line[len("Package: "):].strip()}
            rows.append(current)
        elif line == "@@":
            current = None
        elif current is not None and line.startswith("ABI-Version: "):
            if "ABI-Version" in current:
                raise ValueError("Duplicate package metadata ABI-Version")
            current["ABI-Version"] = line[len("ABI-Version: "):].strip()
    if not rows:
        raise ValueError("Empty or unrecognized package metadata: " + str(path))
    result = {}
    for row in rows:
        name, abi = row["Package"], row.get("ABI-Version", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", name):
            raise ValueError("Invalid package metadata name: " + name)
        if abi and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", abi):
            raise ValueError("Invalid package metadata ABI-Version: " + name)
        suffix = ("-" if name[-1].isdigit() else "") + abi if abi else ""
        runtime = name if name.startswith("kmod-") else name + suffix
        if runtime in result and result[runtime] != name:
            raise ValueError("ambiguous package metadata runtime name: " + runtime)
        result[runtime] = name
    return result


def parse_packages(path):
    """Read native APK installed DB, release manifest, TSV, or explicit JSON."""
    raw = Path(path).read_text()
    if raw.lstrip().startswith(("[", "{")):
        rows = json.loads(raw)
        if isinstance(rows, dict):
            rows = rows["packages"]
        rows = [({"name": row[0], "version": row[1], "depends": row[2]}
                 if isinstance(row, list) else row) for row in rows]
    elif re.search(r"^P:", raw, re.M):
        rows = []
        for block in re.split(r"\n\s*\n", raw.strip()):
            fields = {}
            package_files, directory = [], ""
            for line in block.splitlines():
                if len(line) > 1 and line[1] == ":" and line[0] in "PVDp":
                    if line[0] in fields:
                        raise ValueError("Duplicate APK field: " + line[0])
                    fields[line[0]] = line[2:]
                elif line.startswith("F:"):
                    directory = line[2:]
                elif line.startswith("R:"):
                    package_files.append("/" + str(PurePosixPath(directory) / line[2:]).lstrip("/"))
            if fields:
                rows.append({"name": fields["P"], "version": fields["V"],
                             "depends": fields.get("D", ""), "provides": fields.get("p", ""),
                             "files": package_files})
    elif raw.startswith("name\t"):
        rows = list(csv.DictReader(io.StringIO(raw), delimiter="\t"))
    else:
        rows = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"([^\s]+)\s+-\s+([^\s]+)", line.strip())
            if not match:
                raise ValueError("Unrecognized package record: " + line[:120])
            rows.append({"name": match[1], "version": match[2]})
    packages = {}
    for row in rows:
        name, version = row["name"], row["version"]
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise ValueError("Package name and version must be nonempty strings")
        if name in packages:
            raise ValueError("Duplicate package: " + name)
        package = {"name": name, "version": version}
        depends = row.get("depends", row.get("dependencies"))
        if depends is not None:
            package["depends"] = " ".join(depends) if isinstance(depends, list) else depends
        package["provides"] = row.get("provides", "")
        if "files" in row:
            package["files"] = row["files"]
        packages[name] = package
    if not packages:
        raise ValueError("Empty package inventory")
    return packages


def dependency_names(depends):
    return {re.split(r"[<>=~]", item)[0]
            for token in depends.split() if not token.startswith("!")
            for item in token.split("|")}


def parse_dtb(path):
    """Decode real flattened DT nodes/properties without executing target tools."""
    data = Path(path).read_bytes()
    if len(data) < 40:
        raise ValueError("Truncated DTB header")
    magic, total, start, strings, _, version, _, _, string_size, tree_size = struct.unpack(">10I", data[:40])
    if magic != 0xD00DFEED or version < 16 or total > len(data):
        raise ValueError("Invalid DTB magic/version/size")
    if min(start, strings) < 40 or start + tree_size > total or strings + string_size > total:
        raise ValueError("Invalid DTB section bounds")
    table = data[strings:strings + string_size]
    nodes, stack = {}, []
    pos, limit, ended = start, start + tree_size, False
    while pos + 4 <= limit:
        token = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        if token == 1:
            end = data.find(b"\0", pos, limit)
            if end < 0:
                raise ValueError("Unterminated DTB node")
            name = data[pos:end].decode()
            if "/" in name or (not stack and name):
                raise ValueError("Invalid DTB node name")
            stack.append(name)
            node = "/" + "/".join(stack[1:])
            if node in nodes:
                raise ValueError("Duplicate DTB node: " + node)
            nodes[node] = {}
            pos = (end + 4) & ~3
        elif token == 2:
            if not stack:
                raise ValueError("Unbalanced DTB end-node")
            stack.pop()
        elif token == 3:
            if not stack or pos + 8 > limit:
                raise ValueError("Invalid DTB property")
            size, offset = struct.unpack_from(">II", data, pos)
            pos += 8
            if offset >= len(table) or pos + size > limit:
                raise ValueError("Invalid DTB property bounds")
            name_end = table.find(b"\0", offset)
            if name_end < 0:
                raise ValueError("Unterminated DTB property name")
            name = table[offset:name_end].decode()
            props = nodes["/" + "/".join(stack[1:])]
            if name in props:
                raise ValueError("Duplicate DTB property: " + name)
            props[name] = data[pos:pos + size]
            pos = (pos + size + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            if stack:
                raise ValueError("Unclosed DTB nodes")
            ended = True
            break
        else:
            raise ValueError("Unknown DTB token: " + str(token))
    if not ended or "/" not in nodes:
        raise ValueError("Incomplete DTB structure")
    return nodes


def semantic_dtb(nodes):
    """Normalize documented phandle references; unknown properties stay exact.

    Unknown reference formats fail conservatively on changed bytes rather than
    assuming arbitrary integers are references and hiding a hardware change.
    """
    handles = {}
    for path, props in nodes.items():
        for key in ("phandle", "linux,phandle"):
            if key in props:
                if len(props[key]) != 4:
                    raise ValueError("Invalid phandle size")
                handle = struct.unpack(">I", props[key])[0]
                if handle in handles and handles[handle] != path:
                    raise ValueError("Duplicate phandle")
                handles[handle] = path
    arrays = {"clocks": "#clock-cells", "assigned-clocks": "#clock-cells",
              "assigned-clock-parents": "#clock-cells", "resets": "#reset-cells",
              "phys": "#phy-cells", "dmas": "#dma-cells", "pwms": "#pwm-cells",
              "iommus": "#iommu-cells", "power-domains": "#power-domain-cells",
              "interrupts-extended": "#interrupt-cells"}
    single = {"phy-handle", "interrupt-parent", "memory-region", "nvmem-cells",
              "cpu", "ethernet", "mdio", "msi-parent", "mediatek,ethsys",
              "mediatek,infracfg", "mediatek,wed", "mediatek,wed2"}
    result = {}
    for path, props in nodes.items():
        result[path] = {}
        for key, value in props.items():
            if key in ("phandle", "linux,phandle"):
                continue
            gpio = key == "gpios" or key.endswith("-gpios")
            cells_key = "#gpio-cells" if gpio else arrays.get(key)
            reference = cells_key or key in single or key.endswith("-supply") or re.fullmatch(r"pinctrl-\d+", key)
            if path.startswith(("/__symbols__", "/__fixups__", "/__local_fixups__")):
                reference = False
            if not reference:
                result[path][key] = value.hex()
                continue
            if len(value) % 4:
                raise ValueError("Unaligned phandle reference: " + key)
            cells = list(struct.unpack(">" + "I" * (len(value) // 4), value))
            normalized, index = [], 0
            while index < len(cells):
                handle = cells[index]
                index += 1
                if handle == 0:
                    normalized.append(None)
                    continue
                if handle not in handles:
                    raise ValueError("Unresolved phandle in " + path + ":" + key)
                provider = handles[handle]
                count = 0
                if cells_key:
                    count_bytes = nodes[provider].get(cells_key)
                    if count_bytes is None or len(count_bytes) != 4:
                        raise ValueError("Missing provider " + cells_key + " at " + provider)
                    count = struct.unpack(">I", count_bytes)[0]
                if index + count > len(cells):
                    raise ValueError("Truncated phandle arguments")
                normalized.append({"node": provider, "args": cells[index:index + count]})
                index += count
            result[path][key] = normalized
    return result


class Gate:
    def __init__(self, phase, baseline, approval=None):
        self.phase = phase
        self.baseline = Path(baseline)
        self.errors, self.differences, self.checks = [], [], {}
        self._source_evidence = None
        self.b_optional_package_policy = None
        self.approvals = {}
        self.policy = read_json(self.baseline / "policy.json")
        if approval:
            review = read_json(approval)
            if not isinstance(review, dict) or not isinstance(review.get("approved"), list):
                raise ValueError("Approval manifest must contain an approved list")
            approval_scope_matches = (
                self.policy.get("upstream_commit") == B_SOURCE_COMMIT or
                review.get("upstream_commit") == self.policy.get("upstream_commit")
            )
            for item in (review["approved"] if approval_scope_matches else []):
                if (not isinstance(item, dict) or
                        not re.fullmatch(r"[0-9a-f]{64}", str(item.get("id", ""))) or
                        not item.get("reason", "").strip()):
                    raise ValueError("Approval requires an exact difference ID and review reason")
                self.approvals[item["id"]] = item["reason"]
        integrity = self.baseline / "baseline-sha256.json"
        if integrity.exists():
            for name, expected in read_json(integrity).items():
                if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise ValueError("Invalid baseline checksum entry")
                actual = digest((self.baseline / name).read_bytes())
                if expected != actual:
                    self.error("baseline_checksum", name, expected, actual)
        elif self.policy.get("integrity_required"):
            self.error("missing_baseline_checksum", "baseline-sha256.json")
        self.original = parse_packages(self.baseline / "original-package-manifest.json")
        self.mtk = parse_config(self.baseline / "baseline-mtk-config.txt")
        counts = {"packages": len(self.original),
                  "kmods": sum(name.startswith("kmod-") for name in self.original),
                  "luci_apps": sum(name.startswith("luci-app-") for name in self.original),
                  "mtk_symbols": len(self.mtk)}
        for key, value in counts.items():
            if value != self.policy["counts"][key]:
                self.error("baseline_count", key, self.policy["counts"][key], value)
        self.checks["baseline_counts"] = counts

    def error(self, category, subject, expected=None, actual=None):
        self.errors.append({"category": category, "subject": subject,
                            "expected": expected, "actual": actual})

    def difference(self, category, subject, expected, actual, disposition="review_required", reason=""):
        record = {"category": category, "subject": subject, "expected": expected,
                  "actual": actual, "reason": reason}
        identifier = digest(canonical(record))
        record["id"] = identifier
        record["disposition"] = disposition
        if disposition == "review_required" and identifier in self.approvals:
            record["disposition"] = "approved"
            record["approval_reason"] = self.approvals[identifier]
        self.differences.append(record)

    def config(self, path, optional_packages=None, package_info=None, source_lock=None,
               optional_package_review=B_OPTIONAL_PACKAGE_REVIEW, upstream_evidence=None):
        values = parse_config(path)
        required = dict(self.policy["required_config"])
        required.update(self.mtk)
        unsupported = self.policy.get("unsupported_fragment_symbols", {})
        for symbol, review in unsupported.items():
            if (not isinstance(review, dict) or not review.get("reason") or
                    required.get(symbol) != review.get("input_value")):
                raise ValueError("Invalid unsupported fragment symbol policy: " + symbol)
            del required[symbol]
            if values.get(symbol, "n") != "n":
                self.error("unsupported_fragment_symbol", symbol,
                           "absent/disabled after defconfig; not an enabled feature", values[symbol])
        metadata = package_selectors(package_info) if package_info else None
        selectors = {}
        # Multi-profile/per-device rootfs may build original extras as =m;
        # only the actual image gate can establish that they were installed.
        for name in self.original:
            selector = "CONFIG_PACKAGE_" + name
            if metadata is not None:
                if name == "kernel":
                    # kernel is implicitly emitted, not a selectable recipe.
                    release = self.policy["kernel_version"].split(".")[:2]
                    selector = "CONFIG_LINUX_" + "_".join(release)
                    required[selector] = "y"
                elif name not in metadata:
                    self.error("package_metadata", name, "exact runtime APK name in recipe metadata", "MISSING")
                else:
                    selector = "CONFIG_PACKAGE_" + metadata[name]
            selectors[name] = selector
            required.setdefault(selector, ["y", "m"])
        required.update({"CONFIG_PACKAGE_" + name: "y" for name in self.policy["required_packages"]})
        optional_policy = (load_b_optional_package_policy(
            self.baseline, source_lock, optional_package_review, upstream_evidence
        ) if source_lock else None)
        if optional_policy:
            for name in B_OPTIONAL_PACKAGE_NAMES:
                selector = selectors.get(name)
                if selector is None:
                    self.error("optional_package_metadata", name, "exact recipe selector", "MISSING")
                    continue
                required[selector] = "n"
        optional = []
        if optional_packages:
            for line in Path(optional_packages).read_text().splitlines():
                name = line.split("#", 1)[0].strip()
                if not name:
                    continue
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", name) or name in optional:
                    raise ValueError("Invalid or duplicate optional package: " + name)
                optional.append(name)
                # A shared dependency may already be preinstalled. Never relax
                # stronger original/increment requirements to optional status.
                required.setdefault("CONFIG_PACKAGE_" + name, ["y", "m"])
            if not optional:
                raise ValueError("Empty optional package list: " + str(optional_packages))
        for key, expected in sorted(required.items()):
            allowed = expected if isinstance(expected, list) else [expected]
            value = values.get(key, "n")
            if value not in allowed:
                self.error("config_symbol", key, expected, values.get(key, "MISSING"))
        selected = {key[len("CONFIG_PACKAGE_"):] for key, value in values.items()
                    if key.startswith("CONFIG_PACKAGE_") and value == "y"}
        self.checks["config"] = {"sha256": digest(Path(path).read_bytes()),
                                 "required_symbols": len(required),
                                 "optional_packages": sorted(optional),
                                 "optional_packages_list_sha256": (digest(Path(optional_packages).read_bytes())
                                                                   if optional_packages else None),
                                 "package_info_sha256": (digest(Path(package_info).read_bytes())
                                                         if package_info else None),
                                 "original_package_selectors": selectors,
                                 "b_optional_package_omission": ({
                                     "upstream_commit": optional_policy["upstream_commit"],
                                     "packages": list(B_OPTIONAL_PACKAGE_NAMES),
                                     "review_sha256": B_OPTIONAL_PACKAGE_REVIEW_SHA256,
                                     "store_feed_key_sha256": B_LUCKY_STORE_KEY_SHA256,
                                 } if optional_policy else None),
                                 "b_optional_packages": (list(B_OPTIONAL_PACKAGE_NAMES) if optional_policy else None),
                                 "unsupported_fragment_symbols": unsupported,
                                 "additional_selected_package_symbols": sorted(selected - set(self.original)),
                                 "scope": "required symbols only; original expanded config is unavailable"}

    def source_lock(self, path):
        self._source_evidence = None
        if not path:
            self.error("source_lock_missing", "source-lock.json")
            return False
        lock = read_json(path)
        count = len(self.errors)
        for key in ("wrapper_commit", "upstream_commit"):
            if lock.get(key) != self.policy[key]:
                self.error("source_lock", key, self.policy[key], lock.get(key))
        for key in ("wrapper_tree_hash", "build_fingerprint"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(lock.get(key, ""))):
                self.error("source_lock", key, "new SHA256", lock.get(key))
        if lock.get("build_fingerprint") == self.policy["original_fingerprint"]:
            self.error("original_fingerprint", "build_fingerprint", "new build identity",
                       lock["build_fingerprint"])
        sources = lock.get("sources")
        if not isinstance(sources, list) or not sources:
            self.error("source_lock", "sources", "nonempty source inventory", sources)
        else:
            names, urls = set(), set()
            for item in sources:
                if not isinstance(item, dict):
                    self.error("source_lock", "source_identity", "source object", item)
                    continue
                name = item.get("name")
                url = item.get("url")
                if (not isinstance(name, str) or not name or name in names or
                        not isinstance(url, str) or not url or url in urls):
                    self.error("source_lock", "source_identity", "unique name and URL", item)
                if isinstance(name, str):
                    names.add(name)
                if isinstance(url, str):
                    urls.add(url)
                if not re.fullmatch(r"[0-9a-f]{40}", str(item.get("commit", ""))):
                    self.error("source_lock", str(name) + ".commit", "full Git commit", item.get("commit"))
                if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("tree_hash", ""))):
                    self.error("source_lock", str(name) + ".tree_hash", "SHA256", item.get("tree_hash"))
            try:
                require_tracked_main_source(sources, lock.get("upstream_commit"), "resolved")
            except ValueError as error:
                self.error("source_lock", "main_source",
                           "exact tracked repository/ref/commit", str(error))
        self.checks["source_lock"] = {"sha256": digest(Path(path).read_bytes()),
                                      "recorded_sources": len(sources) if isinstance(sources, list) else 0,
                                      "build_fingerprint": lock.get("build_fingerprint"),
                                      "scope": "headers/shape only; exact original resolved feeds/assets unknown"}
        if isinstance(sources, list):
            for source in sources:
                if not isinstance(source, dict):
                    continue
                self.difference("resolved_source", source.get("name", "MISSING"),
                                "original resolved revision unknown", source, "expected",
                                "new build lock; not evidence of original feed/asset equality")
        passed = count == len(self.errors)
        if passed:
            self._source_evidence = (Path(path), lock)
        return passed

    def security_removals(self, root, provenance_ok, packages, baseline):
        """Permit two reviewed absences only after fixed B source/output proof.

        Successful guards still produce exact review_required differences. All
        other missing paths remain hard errors in files(); restoring the revoked
        CA is itself a hard error, even if it would match the original baseline.
        """
        if self.policy["upstream_commit"] != SECURITY_REMOVAL_COMMIT:
            if self.policy["upstream_commit"] != "03eb30617955519e35845838c6255ebe687eb717":
                self.error("security_removal_guard", "unreviewed upstream CA transition",
                           "a source-bound CA review for the exact upstream commit",
                           self.policy["upstream_commit"])
            return {}
        check = {"status": "fail", "scope": "two fixed emSign G3 records at fixed B source",
                 "review_sha256": SECURITY_REMOVAL_REVIEW_SHA256}
        self.checks["security_removals"] = check

        def require(condition, message):
            if not condition:
                raise ValueError(message)

        def regular_bytes(path):
            require(path.is_file() and not path.is_symlink(), "regular evidence required: " + str(path))
            return path.read_bytes()

        def repository(url):
            return url.removesuffix(".git") if isinstance(url, str) else None

        def strict_json(raw):
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    require(key not in result, "duplicate JSON key: " + key)
                    result[key] = value
                return result
            return json.loads(raw, object_pairs_hook=unique)

        def manifest(path):
            rows = strict_json(regular_bytes(path))
            require(isinstance(rows, list) and bool(rows), "nonempty prepared/wrapper manifest required")
            seen = set()
            for row in rows:
                require(isinstance(row, dict), "invalid source manifest row")
                name = row.get("path")
                require(isinstance(name, str) and name and not name.startswith("/") and
                        "\\" not in name and "\0" not in name and
                        ".." not in PurePosixPath(name).parts and
                        str(PurePosixPath(name)) == name and name != "." and name not in seen,
                        "duplicate/unsafe source manifest path: " + str(name))
                seen.add(name)
                mode = row.get("mode")
                require(isinstance(mode, str) and re.fullmatch(r"0o[0-7]{1,4}", mode) is not None and
                        oct(int(mode, 8)) == mode, "invalid source manifest mode: " + name)
                if "target" in row:
                    require(set(row) == {"path", "mode", "target"} and
                            isinstance(row["target"], str) and bool(row["target"]) and "\0" not in row["target"],
                            "invalid source symlink row: " + name)
                else:
                    require(set(row) == {"path", "mode", "size", "sha256"} and
                            type(row["size"]) is int and row["size"] >= 0 and
                            re.fullmatch(r"[0-9a-f]{64}", str(row["sha256"])) is not None,
                            "invalid regular source row: " + name)
            require(not any(str(parent) in seen for name in seen for parent in PurePosixPath(name).parents),
                    "source file/symlink cannot be an ancestor of another input")
            return rows

        try:
            review_bytes = regular_bytes(SECURITY_REMOVAL_REVIEW)
            require(digest(review_bytes) == SECURITY_REMOVAL_REVIEW_SHA256, "security-removal review digest mismatch")
            review = strict_json(review_bytes)
            original = regular_bytes(self.baseline / "full-rootfs-manifest.json")
            require(digest(original) == review["original_manifest_sha256"], "original full manifest digest mismatch")
            require(not any(e["category"] in {"baseline_checksum", "missing_baseline_checksum", "baseline_count"}
                            for e in self.errors), "baseline integrity failed")
            for record in review["removed_records"]:
                require(baseline.get(record["path"]) == record, "original security-removal record mismatch: " + record["path"])
            require(provenance_ok and self._source_evidence is not None, "validated source-lock evidence required")
            path, lock = self._source_evidence
            require(strict_json(regular_bytes(path)) == lock, "source-lock changed after validation")
            source = review["source"]
            require(lock.get("upstream_commit") == source["commit"] == SECURITY_REMOVAL_COMMIT,
                    "fixed B upstream commit required")
            actual_main = [row for row in lock["sources"] if repository(row.get("url")) == source["repository"]]
            require(len(actual_main) == 1 and actual_main[0].get("commit") == source["commit"],
                    "unique actual fixed B source required")
            requested_bytes = regular_bytes(path.with_name("requested-sources.lock.json"))
            requested = strict_json(requested_bytes)
            require(requested == lock.get("source_lock") and digest(requested_bytes) == lock.get("source_lock_sha256"),
                    "requested/embedded source lock mismatch")
            require(isinstance(requested.get("sources"), list) and
                    all(isinstance(row, dict) for row in requested["sources"]), "invalid requested sources")
            main = [row for row in requested["sources"] if row.get("role") == "main-source"]
            urls = [repository(row.get("url")) for row in requested["sources"]]
            require(None not in urls and len(urls) == len(set(urls)), "duplicate requested source repository")
            require(len(main) == 1 and repository(main[0].get("url")) == source["repository"] and
                    main[0].get("commit") == source["commit"], "unique embedded fixed B main-source required")
            prepared = manifest(path.with_name("prepared-source-files.json"))
            wrapper = manifest(path.with_name("wrapper-source-files.json"))
            # These are provenance.py identities, deliberately not canonical().
            prepared_hash = digest(json.dumps(prepared, sort_keys=True).encode())
            wrapper_hash = digest(json.dumps(wrapper, sort_keys=True).encode())
            fingerprint = digest(json.dumps({"prepared": prepared, "wrapper": wrapper,
                                             "lock": requested}, sort_keys=True).encode())
            require(prepared_hash == lock.get("prepared_tree_hash") and
                    wrapper_hash == lock.get("wrapper_tree_hash") and fingerprint == lock.get("build_fingerprint"),
                    "prepared/wrapper/build fingerprint mismatch")
            expected_rows = []
            for row in source["prepared_files"]:
                evidence = regular_bytes(SECURITY_REMOVAL_REVIEW.parent / row["evidence_file"])
                require(len(evidence) == row["size"] and digest(evidence) == row["sha256"],
                        "reviewed source evidence mismatch: " + row["path"])
                expected_rows.append({key: value for key, value in row.items() if key != "evidence_file"})
            subtree = [row for row in prepared if row["path"] == "package/system/ca-certificates" or
                       row["path"].startswith("package/system/ca-certificates/")]
            require(sorted(subtree, key=lambda row: row["path"]) == sorted(expected_rows, key=lambda row: row["path"]),
                    "CA prepared recipe/patch set differs from exact reviewed source")
            reproduction = regular_bytes(SECURITY_REMOVAL_REVIEW.parent / review["reproduction"]["evidence_file"])
            require(digest(reproduction) == review["reproduction"]["sha256"], "source reproduction evidence mismatch")
            for name, version in review["required_packages"].items():
                require(packages.get(name, {}).get("version") == version, "fixed CA package version required: " + name)
            for record in review["removed_records"]:
                removed = safe_path(root, record["path"])
                require(not os.path.lexists(removed), "revoked path restored: " + record["path"])
            bundle = review["bundle"]
            bundle_path = safe_path(root, bundle["path"])
            bundle_bytes = regular_bytes(bundle_path)
            require(len(bundle_bytes) == bundle["size"] and digest(bundle_bytes) == bundle["sha256"],
                    "exact fixed B CA bundle required")
            alias = safe_path(root, bundle["alias_path"])
            require(alias.is_symlink() and os.readlink(alias) == bundle["alias_target"], "CA bundle alias target changed")
            self.revoked_ca_absent(root, review["revoked_certificate"]["der_sha256"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            check["failure"] = str(error)
            self.error("security_removal_guard", "fixed B CA security removal", "all fixed-source/output guards", str(error))
            return {}

        check.update(status="pass", original_manifest_sha256=digest(original), source_commit=source["commit"],
                     prepared_tree_hash=prepared_hash, wrapper_tree_hash=wrapper_hash,
                     build_fingerprint=fingerprint, source_lock_sha256=digest(requested_bytes),
                     bundle_sha256=digest(bundle_bytes), removed_paths=[row["path"] for row in review["removed_records"]])
        for record in review["removed_records"]:
            self.difference("security_removal", record["path"], record,
                            {"absent": True, "source_repository": source["repository"], "source_commit": source["commit"],
                             "review_sha256": SECURITY_REMOVAL_REVIEW_SHA256,
                             "blacklist_patch_sha256": source["prepared_files"][2]["sha256"],
                             "revoked_der_sha256": review["revoked_certificate"]["der_sha256"]},
                            reason="fixed-source emSign G3 trust revocation; exact two-record security removal requires independent approval")
        return {record["path"]: record for record in review["removed_records"]}

    @staticmethod
    def revoked_ca_absent(root, revoked_der_sha256):
        """Check PEM DER identity, including rewrapping, aliases and other bundles."""
        def exact_der_certificate(data, label):
            # OpenSSL accepts trailing bytes after one certificate. Hashing that
            # entire decoded buffer would let a revoked DER gain a new identity.
            if len(data) < 2 or data[0] != 0x30:
                raise ValueError("certificate DER outer SEQUENCE required: " + label)
            size, start = data[1], 2
            if size & 0x80:
                count = size & 0x7f
                if not 1 <= count <= 4 or len(data) < 2 + count or data[2] == 0:
                    raise ValueError("invalid/nonminimal certificate DER length: " + label)
                size, start = int.from_bytes(data[2:2 + count], "big"), 2 + count
                if size < 128:
                    raise ValueError("nonminimal certificate DER length: " + label)
            if size != len(data) - start:
                raise ValueError("certificate DER trailing/truncated data: " + label)
            if digest(data) == revoked_der_sha256:
                raise ValueError("revoked CA DER restored: " + label)

        trust = safe_path(root, "/etc/ssl/certs")
        if not trust.is_dir() or trust.is_symlink():
            raise ValueError("regular CA trust directory required")
        for parent, dirs, files in os.walk(trust, followlinks=False):
            for name in dirs + files:
                path = Path(parent) / name
                label = "/" + path.relative_to(root).as_posix()
                if path.is_symlink():
                    target = os.readlink(path)
                    resolved = posixpath.normpath(target if target.startswith("/") else
                                                  str(PurePosixPath(label).parent / target))
                    if not resolved.startswith("/etc/ssl/certs/"):
                        raise ValueError("external CA trust alias: " + label)
                    destination = safe_path(root, resolved)
                    if not destination.is_file() or destination.is_symlink():
                        raise ValueError("non-regular/dangling CA trust alias: " + label)
                    continue  # The regular destination is scanned independently.
                if path.is_dir():
                    continue
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError("non-regular CA trust entry: " + label)
                content = path.read_bytes()
                if content.startswith(b"\x30"):
                    exact_der_certificate(content, label)
                labels = re.findall(rb"-----BEGIN ([A-Z0-9 ]*CERTIFICATE)-----", content)
                if any(value != b"CERTIFICATE" for value in labels):
                    raise ValueError("unsupported certificate PEM label: " + label)
                blocks = re.findall(rb"-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----", content, re.S)
                if len(labels) != len(blocks) or content.count(b"-----END CERTIFICATE-----") != len(blocks):
                    raise ValueError("malformed certificate PEM: " + label)
                for block in blocks:
                    der = base64.b64decode(re.sub(rb"\s+", b"", block), validate=True)
                    exact_der_certificate(der, label)

    def packages(self, root, inventory, optional_package_policy=None):
        installed = safe_path(root, "/lib/apk/db/installed")
        actual = parse_packages(installed)
        manifest = parse_packages(inventory)
        if {k: p["version"] for k, p in manifest.items()} != {k: p["version"] for k, p in actual.items()}:
            self.error("package_db_mismatch", "build manifest vs actual APK DB",
                       {k: p["version"] for k, p in manifest.items()},
                       {k: p["version"] for k, p in actual.items()})
        if optional_package_policy:
            try:
                omitted = reviewed_b_optional_package_absences(optional_package_policy, self.original, actual)
            except ValueError as error:
                self.error("optional_package_omission", "Lucky", "exact B-only reviewed absence", str(error))
                omitted = set()
            for name in sorted(omitted):
                self.difference("removed_optional_package", name, self.original[name], None,
                                "expected", "fixed B-only Lucky omission; hash-pinned review and signed compatible iStore feed")
        else:
            omitted = set()
        for name in sorted((set(self.original) - omitted) | set(self.policy["required_packages"])):
            if name not in actual:
                self.error("missing_package", name)
        kernel = actual.get("kernel", {}).get("version", "")
        if kernel == self.policy["original_kernel"]:
            self.error("original_abi", "kernel", "new ABI from increment build", kernel)
        if not re.fullmatch(re.escape(self.policy["kernel_version"]) + r"~[0-9a-f]{32}-r\d+", kernel):
            self.error("kernel_version", "kernel", self.policy["kernel_version"] + "~NEW_ABI-rN", kernel)
        for name, package in sorted(actual.items()):
            if name.startswith("kmod-"):
                matches = re.findall(r"(?:^|\s)kernel=([^\s]+)", package.get("depends", ""))
                if matches != [kernel]:
                    self.error("kernel_dependency", name, [kernel], matches)
                for filename in package.get("files", []):
                    if not filename.endswith(".ko"):
                        continue
                    module = safe_path(root, filename)
                    if not module.is_file() or module.is_symlink():
                        self.error("missing_kmod_file", name, filename, "missing/not regular")
                        continue
                    data = module.read_bytes()
                    vermagic = re.search(rb"(?:^|\x00)vermagic=([^\x00]+)\x00", data)
                    if not data.startswith(b"\x7fELF") or not vermagic:
                        self.error("module_vermagic", filename, "ELF module with vermagic", "missing")
                    elif vermagic[1].split()[0].decode() != self.policy["kernel_version"]:
                        self.error("module_vermagic", filename, self.policy["kernel_version"],
                                   vermagic[1].decode(errors="replace"))
        reachable = set(self.policy["required_packages"])
        pending = list(reachable)
        while pending:
            name = pending.pop()
            for dependency in dependency_names(actual.get(name, {}).get("depends", "")):
                if dependency in actual and dependency not in reachable:
                    reachable.add(dependency)
                    pending.append(dependency)
        for name, package in sorted(actual.items()):
            original = self.original.get(name)
            if original is None:
                self.difference("added_package", name, None, package,
                                "expected" if name in reachable else "review_required",
                                "requested increment dependency closure" if name in reachable else "outside increment closure")
                continue
            if package["version"] != original["version"]:
                self.difference("package_version", name, original["version"], package["version"],
                                "expected" if name == "kernel" and kernel != self.policy["original_kernel"] else "review_required",
                                "same-build generated ABI" if name == "kernel" else "source/version delta")
            old_deps = original.get("depends", "").replace("kernel=" + self.policy["original_kernel"], "kernel=" + kernel)
            if sorted(old_deps.split()) != sorted(package.get("depends", "").split()):
                self.difference("package_dependencies", name, old_deps, package.get("depends", ""))
        self.checks["packages"] = {"actual_count": len(actual), "kernel": kernel,
                                   "original_count": len(self.original),
                                   "reviewed_optional_absent": sorted(omitted),
                                   "optional_package_review_sha256": (B_OPTIONAL_PACKAGE_REVIEW_SHA256
                                                                       if optional_package_policy else None),
                                   "additional_packages": sorted(set(actual) - set(self.original)),
                                   "kmods": sum(n.startswith("kmod-") for n in actual),
                                   "luci_apps": sum(n.startswith("luci-app-") for n in actual)}
        return actual, reachable

    def kernel_config(self, path):
        values = parse_config(path)
        for key, expected in self.policy["required_kernel_config"].items():
            allowed = expected if isinstance(expected, list) else [expected]
            if values.get(key) not in allowed:
                self.error("kernel_config", key, allowed, values.get(key, "MISSING"))
        self.checks["kernel_config"] = {"sha256": digest(Path(path).read_bytes()),
                                        "required_symbols": len(self.policy["required_kernel_config"]),
                                        "scope": "required invariants, not unavailable original full kernel config"}

    def dtb(self, path):
        original = semantic_dtb(parse_dtb(self.baseline / "original-board.dtb"))
        actual = semantic_dtb(parse_dtb(path))
        for node in sorted(set(original) | set(actual)):
            if original.get(node) != actual.get(node):
                self.error("dtb_semantics", node, original.get(node), actual.get(node))
        self.checks["dtb"] = {"original_sha256": digest((self.baseline / "original-board.dtb").read_bytes()),
                               "actual_sha256": digest(Path(path).read_bytes()),
                               "compared_nodes": len(original),
                               "scope": "all nodes/properties; known phandle references normalized, unknown formats exact"}

    def signing_key(self, root, packages, public_key, signed_feeds):
        """Bind a fresh image key to capture's verified same-build feed evidence."""
        if public_key is None and signed_feeds is None:
            return False
        if public_key is None or signed_feeds is None:
            self.error("signing_key", "both public key and signed feed evidence are required")
            return False
        before = len(self.errors)
        key = Path(public_key).read_bytes()
        report = read_json(signed_feeds)
        if not isinstance(report, dict) or not isinstance(report.get("image_trust"), dict):
            self.error("signing_key", "signed feeds report/image_trust must be objects")
            return False
        trust = report.get("image_trust", {})
        image_path = "/etc/apk/keys/public-key.pem"
        path = safe_path(root, image_path)
        if (not key.startswith(b"-----BEGIN PUBLIC KEY-----\n") or
                not key.rstrip().endswith(b"-----END PUBLIC KEY-----") or
                path.is_symlink() or not path.is_file() or path.read_bytes() != key):
            self.error("signing_key", image_path, "exact same-build public key bytes", "mismatch/invalid")
        expected_trust = {"status": "pass", "comparison": "exact-public-key-bytes",
                          "public_key_sha256": digest(key)}
        for name, expected in expected_trust.items():
            if trust.get(name) != expected:
                self.error("signing_key", "image_trust." + name, expected, trust.get(name))
        paths = trust.get("matching_key_paths")
        if not isinstance(paths, list) or image_path not in paths:
            self.error("signing_key", "matching_key_paths", image_path, paths)
        for name, expected in (("status", "pass"), ("installed_packages_matched", len(packages)),
                               ("kernel_abi", packages.get("kernel", {}).get("version"))):
            if report.get(name) != expected:
                self.error("signing_key", "signed_feeds." + name, expected, report.get(name))
        feeds = report.get("feeds")
        if not isinstance(feeds, list) or not feeds:
            self.error("signing_key", "signed_feeds.feeds", "nonempty verified feeds", feeds)
        else:
            for feed in feeds:
                if not isinstance(feed, dict):
                    self.error("signing_key", "feed", "verified feed object", feed)
                    continue
                for name, expected in (("signature", "verified"),
                                       ("payload_identity", "native-fetch-verified"),
                                       ("index_metadata", "matches-all-packages")):
                    if feed.get(name) != expected:
                        self.error("signing_key", "feed." + name, expected, feed.get(name))
                if type(feed.get("packages")) is not int or feed["packages"] < 1:
                    self.error("signing_key", "feed.packages", "positive package count", feed.get("packages"))
        passed = before == len(self.errors)
        self.checks["signing_key"] = {"status": "pass" if passed else "fail",
                                      "public_key_sha256": digest(key),
                                      "signed_feeds_sha256": digest(Path(signed_feeds).read_bytes()),
                                      "image_key_path": image_path,
                                      "scope": "capture verified native signed feeds, exact rootfs key binding"}
        return passed

    def files(self, root, provenance_ok, packages, reachable, signing_key_ok=False,
              optional_package_policy=None):
        baseline = file_index(read_json(self.baseline / "full-rootfs-manifest.json")["files"])
        critical = file_index(read_json(self.baseline / "critical-files-manifest.json")["files"])
        if not baseline or not critical or not set(critical).issubset(baseline):
            raise ValueError("Invalid/incomplete rootfs baseline")
        expected_count = self.policy.get("rootfs_entries")
        if expected_count and len(baseline) != expected_count:
            self.error("baseline_count", "rootfs_entries", expected_count, len(baseline))
        if self.policy.get("critical_entries") and len(critical) != self.policy["critical_entries"]:
            self.error("baseline_count", "critical_entries", self.policy["critical_entries"], len(critical))
        actual = walk_files(root)
        removed = self.security_removals(root, provenance_ok, packages, baseline)
        optional_removed = (reviewed_b_optional_file_absences(optional_package_policy, baseline, actual,
                                                              preapproved_absences=set(removed))
                            if optional_package_policy else set())
        for name, expected in baseline.items():
            # Reject redirected parent directories even when walk() correctly
            # avoided following the link and the descendant appears missing.
            safe_path(root, name)
            current = actual.get(name)
            if current is None:
                if name in removed:
                    continue
                if name in optional_removed:
                    self.difference("removed_optional_file", name, expected, None, "expected",
                                    "fixed B-only Lucky package-owned path; exact baseline record in hash-pinned review")
                    continue
                self.error("missing_file", name)
                continue
            if current["type"] != expected["type"]:
                self.error("file_type", name, expected["type"], current["type"])
                continue
            policy = critical.get(name, {}).get("policy", "report-all-differences")
            for field in ("mode", "uid", "gid", "target", "device_major", "device_minor"):
                if expected.get(field) != current.get(field):
                    self.difference("file_" + field, name, expected.get(field), current.get(field))
            if expected.get("sha256") == current.get("sha256"):
                continue
            path = safe_path(root, name)
            recompiled = policy == "recompiled-version-config-abi-equivalence"
            is_elf = current["type"] == "file" and read_prefix(path, 4) == b"\x7fELF"
            generated = name.startswith("/lib/apk/") or bool(re.match(r"/lib/modules/[^/]+/modules\.", name))
            disposition, reason = "review_required", policy
            if name == "/etc/apk/keys/public-key.pem" and signing_key_ok and provenance_ok:
                disposition, reason = "expected", "fresh same-build key; native signed feeds and exact image key binding verified"
            elif policy.startswith("exact-"):
                pass
            elif generated and provenance_ok:
                disposition, reason = "expected", "generated metadata; actual package DB and kernel dependencies checked"
            elif (recompiled or is_elf) and provenance_ok:
                disposition, reason = "expected", "recompiled binary; recorded sources/config/ABI, not byte-identical claim"
            elif policy.startswith("original-package-set"):
                world = {re.split(r"[<>=~]", line.strip())[0]
                         for line in path.read_text().splitlines() if line.strip()}
                # World lists explicit roots, not all transitive packages.
                self.checks["apk_world"] = {"roots": sorted(world)}
                reason = "world roots changed; package closure checked but root selection needs explicit review"
            self.difference("file_sha256", name, expected.get("sha256"), current.get("sha256"), disposition, reason)
        additions = sorted(set(actual) - set(baseline))
        for name in additions:
            current = actual[name]
            generated = name.startswith("/lib/apk/") or current["type"] == "directory"
            self.difference("added_file", name, None, current,
                            "expected" if generated else "review_required",
                            "explicit new rootfs entry; verify owning added package" if not generated else "generated rootfs metadata/directory")
        self.checks["rootfs"] = {"baseline_entries": len(baseline), "actual_entries": len(actual),
                                  "critical_entries": len(critical), "added_entries": len(additions),
                                  "reviewed_optional_absent": sorted(optional_removed),
                                  "optional_package_review_sha256": (B_OPTIONAL_PACKAGE_REVIEW_SHA256
                                                                      if optional_package_policy else None),
                                  "xattrs": "not checked; original baseline did not inventory xattrs",
                                  "mtime": "not an equivalence requirement"}

    def result(self):
        pending = [r for r in self.differences if r["disposition"] == "review_required"]
        status = "fail" if self.errors else "review_required" if pending else "pass"
        return {"schema_version": 1, "phase": self.phase, "status": status,
                "errors": self.errors, "differences": self.differences,
                "checks": self.checks,
                "pending_review_count": len(pending),
                "claims": {"bit_identical_original": False, "hardware_or_boot_tested": False,
                           "scope": "this phase's checked artifact invariants only"},
                "unverified": ["original expanded configs and resolved feed/asset/environment equality",
                               "original xattrs", "boot, hardware and service behavior"]}


def read_prefix(path, size):
    with path.open("rb") as stream:
        return stream.read(size)


def safe_path(root, name):
    parts = PurePosixPath(name)
    if not name.startswith("/") or ".." in parts.parts or "\0" in name:
        raise ValueError("Invalid rootfs path: " + name)
    current = root
    for part in parts.parts[1:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink_parent: " + name)
    return root.joinpath(*parts.parts[1:])


def file_index(records):
    result = {}
    for record in records:
        name = record["path"]
        if name in result:
            raise ValueError("Duplicate rootfs record: " + name)
        safe_path(Path("/unused"), name)
        result[name] = record
    return result


def file_record(root, path):
    info = path.lstat()
    record = {"path": "/" + path.relative_to(root).as_posix() if path != root else "/",
              "mode": format(stat.S_IMODE(info.st_mode), "04o"),
              "uid": info.st_uid, "gid": info.st_gid, "sha256": None, "size": 0}
    if stat.S_ISLNK(info.st_mode):
        record.update(type="symlink", target=os.readlink(path))
        content = record["target"].encode()
        record.update(sha256=digest(content), size=len(content))
    elif stat.S_ISREG(info.st_mode):
        sha = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha.update(chunk)
        record.update(type="file", sha256=sha.hexdigest(), size=info.st_size)
    elif stat.S_ISDIR(info.st_mode):
        record["type"] = "directory"
    elif stat.S_ISCHR(info.st_mode) or stat.S_ISBLK(info.st_mode):
        record.update(type="char-device" if stat.S_ISCHR(info.st_mode) else "block-device",
                      device_major=os.major(info.st_rdev), device_minor=os.minor(info.st_rdev))
    else:
        raise ValueError("Unsupported rootfs inode: " + str(path))
    return record


def walk_files(root):
    if not root.is_dir():
        raise ValueError("rootfs must be an extracted directory")
    records = {"/": file_record(root, root)}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            record = file_record(root, path)
            records[record["path"]] = record
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    phases = parser.add_subparsers(dest="phase", required=True)
    for phase in ("config", "image"):
        command = phases.add_parser(phase)
        command.add_argument("--baseline-dir", required=True, type=Path)
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--approve-differences", type=Path)
        if phase == "config":
            command.add_argument("--config", required=True, type=Path)
            command.add_argument("--optional-packages", type=Path,
                                 help="package names that must survive defconfig as m or y")
            command.add_argument("--package-info", type=Path,
                                 help="actual tmp/.packageinfo for exact runtime ABI-to-recipe names")
            command.add_argument("--source-lock", type=Path,
                                 help="requested source lock used to bind the fixed B-only package policy")
            command.add_argument("--optional-package-review", type=Path,
                                 default=B_OPTIONAL_PACKAGE_REVIEW)
            command.add_argument("--upstream-evidence", type=Path,
                                 help="GitHub compare evidence used for a moving 25.12 source head")
        else:
            command.add_argument("--rootfs", required=True, type=Path)
            command.add_argument("--kernel-config", required=True, type=Path)
            command.add_argument("--packages", required=True, type=Path)
            command.add_argument("--dtb", required=True, type=Path)
            command.add_argument("--source-lock", type=Path)
            command.add_argument("--public-key", type=Path)
            command.add_argument("--signed-feeds", type=Path)
            command.add_argument("--upstream-evidence", type=Path,
                                 help="GitHub compare evidence used for a moving 25.12 source head")
    args = parser.parse_args(argv)
    gate = None
    try:
        gate = Gate(args.phase, args.baseline_dir, args.approve_differences)
        if args.phase == "config":
            gate.config(args.config, args.optional_packages, args.package_info,
                        args.source_lock, args.optional_package_review, args.upstream_evidence)
        else:
            root = args.rootfs.resolve()
            provenance_ok = gate.source_lock(args.source_lock)
            gate.b_optional_package_policy = load_b_optional_package_policy(
                args.baseline_dir, args.source_lock, upstream_evidence_path=args.upstream_evidence)
            actual, reachable = gate.packages(root, args.packages, gate.b_optional_package_policy)
            gate.kernel_config(args.kernel_config)
            gate.dtb(args.dtb)
            signing_key_ok = gate.signing_key(root, actual, args.public_key, args.signed_feeds)
            gate.files(root, provenance_ok, actual, reachable, signing_key_ok,
                       gate.b_optional_package_policy)
        report = gate.result()
    except (OSError, ValueError, KeyError, TypeError, struct.error) as error:
        if gate:
            gate.error("invalid_or_missing_evidence", str(error))
            report = gate.result()
        else:
            report = {"schema_version": 1, "phase": args.phase, "status": "fail",
                      "errors": [{"category": "invalid_or_missing_baseline", "subject": str(error)}],
                      "differences": [], "checks": {}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"phase": args.phase, "status": report["status"], "output": str(args.output)}))
    return {"pass": 0, "fail": 1, "review_required": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
