#!/usr/bin/env python3
"""Verify and prepare mainline release assets for one exact CLX-S20P Actions run."""

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
import upstream_sync
import go_security
import dashboard
import component_gate
from preservation import (OMITTED_APPLICATION_PACKAGES, REQUIRED_APPLICATION_PACKAGES, EXCLUDED_APPLICATION_PACKAGES, load_application_selection,
                          application_selection_evidence)


UPSTREAM_BASE_COMMIT = "9f397975c7b47ce6986a6e39529d2f72fd5c729d"
UPSTREAM_URL = "https://github.com/chasey-dev/immortalwrt-mt798x-rebase"
UPSTREAM_REF = "25.12"
WRAPPER_COMMIT = "d46704afb327fce99e7d770c08fc46b48d5bb1b7"
B_OPTIONAL_PACKAGE_NAMES = ("lucky", "luci-app-lucky", "luci-i18n-lucky-zh-cn")
B_OPTIONAL_PACKAGE_REVIEW_SHA256 = "03d11294764d575f08a14d0e260603ca89fbc7532f6c453c291e244415cbd8aa"
IMAGE_RE = re.compile(r"^bin/targets/mediatek/filogic/[^/]*clx_s20p[^/]*sysupgrade\.bin$")
SHA_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")
MAX_ARCHIVE_EXPANDED = 20 * 1024 * 1024 * 1024
PARTITION_CAPACITIES = {"kernel": 32 * 1024 * 1024, "rootfs": 2 * 1024 * 1024 * 1024}
FIXED_MANIFESTS = tuple(Path(__file__).parent / "fixed" / date / "manifest.json"
                        for date in ("20261030", "20261031"))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(root, name):
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError("required regular evidence file is missing: " + name)
    try:
        value = json.loads(path.read_text())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid JSON evidence: " + name) from error
    if not isinstance(value, dict):
        raise ValueError("evidence object required: " + name)
    return value


def safe_relative(raw):
    if not isinstance(raw, str) or not raw or "\\" in raw or raw.startswith("/"):
        raise ValueError("unsafe archive path")
    path = PurePosixPath(raw)
    if any(part in ("..", "") for part in path.parts):
        raise ValueError("unsafe archive path: " + raw)
    normalized = path.as_posix()
    return "" if normalized == "." else normalized.removeprefix("./")


def parse_sha256sums(path):
    rows = {}
    for line in Path(path).read_text().splitlines():
        match = SHA_LINE.fullmatch(line)
        if not match:
            raise ValueError("malformed SHA256SUMS entry")
        digest, name = match.groups()
        safe = safe_relative(name)
        if not safe or safe in rows:
            raise ValueError("duplicate or empty SHA256SUMS path")
        rows[safe] = digest
    if not rows:
        raise ValueError("empty SHA256SUMS")
    return rows


def verify_checksums(root, manifest_name="SHA256SUMS"):
    manifest = root / manifest_name
    rows = parse_sha256sums(manifest)
    actual = {}
    for path in root.rglob("*"):
        if path == manifest:
            continue
        if path.is_symlink():
            raise ValueError("symlink in checksummed bundle: " + str(path.relative_to(root)))
        if path.is_file():
            name = path.relative_to(root).as_posix()
            actual[name] = sha256_file(path)
    if set(rows) != set(actual):
        raise ValueError("SHA256SUMS coverage differs from bundle files")
    mismatched = [name for name, digest in rows.items() if actual[name] != digest]
    if mismatched:
        raise ValueError("SHA256SUMS mismatch: " + ", ".join(sorted(mismatched)[:5]))
    return rows


def verify_artifact_zip(zip_path, destination, expected_digest, run_id, run_attempt):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(expected_digest or "")):
        raise ValueError("GitHub artifact API SHA256 digest is required")
    if sha256_file(zip_path) != expected_digest.split(":", 1)[1]:
        raise ValueError("GitHub artifact API SHA256 digest differs from candidate artifact ZIP")
    dest = Path(destination)
    if dest.exists():
        raise ValueError("artifact extraction destination already exists")
    dest.mkdir(parents=True)
    try:
        expanded = 0
        validated = []
        with zipfile.ZipFile(zip_path) as archive:
            seen = set()
            for item in archive.infolist():
                name = safe_relative(item.filename)
                expanded += item.file_size
                if expanded > MAX_ARCHIVE_EXPANDED:
                    raise ValueError("candidate artifact ZIP exceeds expansion limit")
                mode = item.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError("unsafe candidate artifact ZIP member type: " + item.filename)
                if name and name in seen:
                    raise ValueError("duplicate candidate artifact ZIP member")
                if name:
                    seen.add(name)
                validated.append((item, name))
            bad = archive.testzip()
            if bad is not None:
                raise ValueError("candidate artifact ZIP CRC failure: " + bad)
            for item, name in validated:
                target = dest.joinpath(*PurePosixPath(name).parts) if name else dest
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as error:
        shutil.rmtree(dest, ignore_errors=True)
        if isinstance(error, ValueError):
            raise
        raise ValueError("invalid candidate artifact ZIP") from error
    try:
        expected_name = f"s20p-samebuild-{run_id}-{run_attempt}.tar.gz"
        if {p.name for p in dest.iterdir()} != {"SHA256SUMS", expected_name}:
            raise ValueError("candidate artifact does not contain the exact run archive")
        verify_outer_artifact(dest, run_id, run_attempt)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return expected_name


def verify_outer_artifact(artifact_dir, run_id, run_attempt):
    root = Path(artifact_dir)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("candidate artifact directory is invalid")
    expected_archive = f"s20p-samebuild-{run_id}-{run_attempt}.tar.gz"
    entries = {path.name for path in root.iterdir()}
    if entries != {"SHA256SUMS", expected_archive}:
        raise ValueError("unexpected or missing candidate artifact files")
    rows = parse_sha256sums(root / "SHA256SUMS")
    if set(rows) != {expected_archive} or sha256_file(root / expected_archive) != rows[expected_archive]:
        raise ValueError("candidate archive outer SHA256SUMS mismatch")
    return root / expected_archive


def extract_verified_archive(archive_path, destination):
    dest = Path(destination)
    dest.mkdir(parents=True)
    member_digests, total = {}, 0
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive:
                name = safe_relative(member.name)
                if not name:
                    if member.isdir():
                        continue
                    raise ValueError("invalid tar root member")
                if name in member_digests or (dest / name).exists():
                    raise ValueError("duplicate candidate archive member: " + name)
                if not (member.isdir() or member.isfile()):
                    raise ValueError("unsafe archive member type: " + name)
                target = dest.joinpath(*PurePosixPath(name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += member.size
                if total > MAX_ARCHIVE_EXPANDED or member.size < 0:
                    raise ValueError("candidate archive exceeds expansion limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("candidate archive member has no content: " + name)
                digest = hashlib.sha256()
                remaining = member.size
                with source, target.open("xb") as output:
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("truncated archive member: " + name)
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                member_digests[name] = digest.hexdigest()
    except (OSError, tarfile.TarError, ValueError) as error:
        shutil.rmtree(dest, ignore_errors=True)
        if isinstance(error, ValueError):
            raise
        raise ValueError("invalid candidate tar archive") from error
    if "SHA256SUMS" not in member_digests:
        raise ValueError("candidate archive has no inner SHA256SUMS")
    manifest = parse_sha256sums(dest / "SHA256SUMS")
    actual = {name: digest for name, digest in member_digests.items() if name != "SHA256SUMS"}
    if set(manifest) != set(actual):
        raise ValueError("inner SHA256SUMS coverage differs from archive files")
    mismatched = [name for name, digest in manifest.items() if actual[name] != digest]
    if mismatched:
        raise ValueError("inner SHA256SUMS mismatch: " + ", ".join(sorted(mismatched)[:5]))
    return actual


def verify_upstream_resolution(root, expected_commit):
    if not re.fullmatch(r"[0-9a-f]{40}", str(expected_commit or "")):
        raise ValueError("exact resolved upstream commit SHA is required")
    evidence = read_json(Path(root), "provenance/upstream-resolution.json")
    try:
        upstream_sync.validate_build_evidence(evidence, expected_commit,
                                             Path(root) / "provenance/component-resolution.json")
    except ValueError as error:
        raise ValueError("upstream resolution evidence does not match the candidate SHA: " + str(error)) from error
    return evidence


def verify_fixed_bundle(root, upstream_commit):
    import fixed_inputs
    root = Path(root)
    captured = root / "provenance/fixed/manifest.json"
    expected = os.environ.get("S20P_FIXED_INPUTS")
    if not captured.exists() and not captured.is_symlink() and not expected:
        return None
    # A fixed publication must bind captured bytes to the reviewed wrapper
    # snapshot, not merely to a self-consistent manifest inside the artifact.
    if captured.is_symlink() or not captured.is_file():
        raise ValueError("regular captured fixed manifest is required")
    if expected:
        expected = Path(expected)
    else:
        captured_bytes = captured.read_bytes()
        matches = [path for path in FIXED_MANIFESTS
                   if path.is_file() and not path.is_symlink() and path.read_bytes() == captured_bytes]
        if len(matches) != 1:
            raise ValueError("captured fixed manifest differs from reviewed snapshot")
        expected = matches[0]
    value = fixed_inputs.load(expected)
    if captured.read_bytes() != expected.read_bytes():
        raise ValueError("captured fixed manifest differs from reviewed snapshot")
    fixed_inputs.verify(captured, upstream_commit, root / "provenance/component-resolution.json")
    fixed_inputs.verify_http(captured, root / "provenance/http-inputs.jsonl")
    result = {"manifest_sha256": sha256_file(expected), "source_run": value["source_run"],
              "component_fingerprint": value["components"]["fingerprint"]}
    if value["schema_version"] == 3:
        result["profile"] = value["profile"]
    return result


def verify_build_date(root, *, required=False):
    path = Path(root) / "provenance/build-date.txt"
    if not path.exists() and not path.is_symlink():
        if required:
            raise ValueError("minimal profile requires a same-build build date")
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size not in (8, 9):
        raise ValueError("regular canonical build date evidence is required")
    raw = path.read_bytes()
    if not re.fullmatch(rb"[0-9]{8}\n?", raw):
        raise ValueError("build date must be canonical YYYYMMDD")
    value = raw.removesuffix(b"\n").decode("ascii")
    try:
        date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError as error:
        raise ValueError("build date is not a valid calendar date") from error
    return value


def release_image_name(upstream_commit, build_date=None):
    if build_date:
        return "clx-s20p-" + upstream_commit[:8] + "-" + build_date + "-sysupgrade.bin"
    return "s20p-A-25.12-" + upstream_commit[:8] + "-sysupgrade.bin"


def verify_candidate_bundle(root, run_id, run_attempt, build_commit, upstream_commit):
    root = Path(root)
    fixed = verify_fixed_bundle(root, upstream_commit)
    policy_id = fixed.get("profile", {}).get("id", "legacy") if fixed else "legacy"
    selection = load_application_selection(Path(__file__).parent / "baseline", policy_id)
    build_date = verify_build_date(root, required=policy_id == "minimal-20261031")
    build = read_json(root, "reports/build-status.json")
    preservation = read_json(root, "reports/preservation-image.json")
    capture = read_json(root, "reports/capture.json")
    signed = read_json(root, "reports/signed-feeds.json")
    config = read_json(root, "provenance/config-gate.json")
    source = read_json(root, "provenance/source-lock.json")
    safety = read_json(root, "reports/image-safety.json")
    fwtool = read_json(root, "inspection/fwtool-metadata.json")
    inventory = read_json(root, "inspection/installed-packages.json")
    upstream_resolution = verify_upstream_resolution(root, upstream_commit)

    if (build.get("status") != "candidate" or build.get("preservation") != "pass" or
            build.get("live_acceptance") != "not-run" or build.get("stable_eligible") is not False or
            build.get("bit_identical_claim") is not False):
        raise ValueError("build status is not a verified non-stable candidate")
    if (preservation.get("status") != "pass" or preservation.get("errors") != [] or
            preservation.get("pending_review_count") != 0):
        raise ValueError("image preservation gate did not pass")
    pkg_check = preservation.get("checks", {}).get("packages", {})
    requested_omission = application_selection_evidence(selection)
    if preservation.get("checks", {}).get("application_selection") != requested_omission:
        raise ValueError("preservation report lacks the exact reviewed application selection")
    if (pkg_check.get("original_count") != 483 or
            pkg_check.get("reviewed_optional_absent") != sorted(B_OPTIONAL_PACKAGE_NAMES)):
        raise ValueError("preservation report does not show exactly the reviewed Lucky package omission")
    if (capture.get("status") != "captured" or capture.get("same_build_signed_feeds") is not True or
            capture.get("private_keys") != "excluded-and-scanned"):
        raise ValueError("same-build capture/private-data checks are incomplete")
    if signed.get("status") != "pass" or signed.get("image_trust", {}).get("status") != "pass":
        raise ValueError("same-build signed feed or image key validation failed")
    public_key = root / "public-keys/public-key.pem"
    image_trust = signed["image_trust"]
    if (public_key.is_symlink() or not public_key.is_file() or
            sha256_file(public_key) != image_trust.get("public_key_sha256") or
            "/etc/apk/keys/public-key.pem" not in image_trust.get("matching_key_paths", [])):
        raise ValueError("same-build image public key does not match signed-feed evidence")
    feeds = signed.get("feeds")
    if not isinstance(feeds, list) or len(feeds) != 5 or any(
            row.get("signature") != "verified" or row.get("payload_identity") != "native-fetch-verified" or
            row.get("index_metadata") != "matches-all-packages" for row in feeds):
        raise ValueError("same-build signed feed inventory is incomplete")
    config_check = config.get("checks", {}).get("config", {})
    if config.get("checks", {}).get("application_selection") != requested_omission:
        raise ValueError("config report lacks the exact reviewed application selection")
    omission = config_check.get("b_optional_package_omission", {})
    if (config.get("status") != "pass" or config.get("errors") != [] or
            config_check.get("b_optional_packages") != list(B_OPTIONAL_PACKAGE_NAMES) or
            omission.get("upstream_commit") != upstream_commit or
            omission.get("packages") != list(B_OPTIONAL_PACKAGE_NAMES) or
            omission.get("review_sha256") != B_OPTIONAL_PACKAGE_REVIEW_SHA256):
        raise ValueError("config gate did not record the exact reviewed Lucky omission")
    if source.get("wrapper_checkout_commit") != build_commit:
        raise ValueError("bundle wrapper commit does not match workflow run commit")
    if (source.get("upstream_commit") != upstream_commit or
            source.get("wrapper_commit") != WRAPPER_COMMIT):
        raise ValueError("bundle has the wrong resolved upstream commit or wrapper source")
    if source.get("upstream_resolution") != upstream_resolution:
        raise ValueError("source provenance does not embed the validated upstream resolution")
    requested = source.get("source_lock", {})
    request_rows = [row for row in requested.get("sources", []) if row.get("role") == "main-source"]
    resolved_rows = [row for row in source.get("sources", [])
                     if row.get("url", "").removesuffix(".git") == UPSTREAM_URL]
    if (len(request_rows) != 1 or request_rows[0].get("commit") != upstream_commit or
            request_rows[0].get("ref") != UPSTREAM_REF or
            request_rows[0].get("url", "").removesuffix(".git") != UPSTREAM_URL or
            len(resolved_rows) != 1 or resolved_rows[0].get("commit") != upstream_commit):
        raise ValueError("requested or resolved main-source does not match the exact upstream SHA")
    requested_lock = root / "provenance/requested-sources.lock.json"
    if (not requested_lock.is_file() or requested_lock.is_symlink() or
            hashlib.sha256(requested_lock.read_bytes()).hexdigest() != source.get("source_lock_sha256")):
        raise ValueError("requested source lock hash does not match source provenance")
    if read_json(root, "provenance/requested-sources.lock.json") != requested:
        raise ValueError("saved requested source lock differs from embedded provenance")
    committed_lock = json.loads(Path(__file__).with_name("sources.lock.json").read_text())
    resolution = upstream_sync.Resolution(
        upstream_commit, True, upstream_resolution["compare_status"],
        upstream_resolution["merge_base"], upstream_resolution["forced"],
        upstream_resolution["previous_attempted"], upstream_resolution.get("previous_comparison"),
        upstream_resolution.get("component_fingerprint"), upstream_resolution.get("plugin_sources"))
    if requested != upstream_sync.update_lock(committed_lock, resolution):
        raise ValueError("requested lock differs from reviewed source pins and exact upstream SHA")
    if (safety.get("status") != "pass" or safety.get("supported_devices") != ["clx,s20p"] or
            safety.get("partition_layout") != "existing GPT kernel32MiB/rootfs2GiB" or
            fwtool.get("supported_devices") != ["clx,s20p"] or
            fwtool.get("version", {}).get("target") != "mediatek/filogic" or
            fwtool.get("version", {}).get("board") != "clx_s20p"):
        raise ValueError("ROM device or partition safety metadata does not match S20P")
    packages = inventory.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("installed package inventory is missing")
    names = [row.get("name") for row in packages if isinstance(row, dict)]
    if len(names) != len(packages) or len(names) != len(set(names)):
        raise ValueError("installed package inventory is malformed or duplicated")
    if set(names) & set(B_OPTIONAL_PACKAGE_NAMES):
        raise ValueError("one of the three reviewed Lucky packages remains in the B image")
    if set(names) & set(selection["package_names"]):
        raise ValueError("omitted application package remains in the image")
    if set(names) & set(selection["excluded_packages"]):
        raise ValueError("excluded application package remains in the image")
    if not set(selection["required_packages"]) <= set(names):
        raise ValueError("required application package is missing from the image")
    package_count = len(packages)
    if package_count != pkg_check.get("actual_count") or package_count != signed.get("installed_packages_matched"):
        raise ValueError("installed package inventory count disagrees across build gates")
    kernel = next((row for row in packages if row["name"] == "kernel"), None)
    if (kernel is None or kernel.get("version") != pkg_check.get("kernel") or
            kernel.get("version") != signed.get("kernel_abi") or
            kernel.get("version") != capture.get("kernel_abi")):
        raise ValueError("kernel ABI differs across image and signed feed reports")
    release = (root / "metadata/kernel/kernel.release").read_text().strip()
    vermagic = (root / "metadata/kernel/.vermagic").read_text().strip()
    match = re.fullmatch(r"6\.12\.112~([0-9a-f]{32})-r1", kernel["version"])
    if (not match or release != "6.12.112" or vermagic != match.group(1) or
            signed.get("kmod_packages_kernel_checked") != 153):
        raise ValueError("kernel release/vermagic/ABI inventory does not match B candidate")
    image = capture.get("image")
    if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
        raise ValueError("capture points to an unexpected S20P sysupgrade image")
    image_path = root / image
    if image_path.is_symlink() or not image_path.is_file():
        raise ValueError("captured S20P sysupgrade image is missing")
    kernel_payload = root / "inspection/kernel.fit"
    rootfs_payload = root / "inspection/rootfs.squashfs"
    for payload in (kernel_payload, rootfs_payload):
        if payload.is_symlink() or not payload.is_file():
            raise ValueError("captured S20P partition payload is missing: " + str(payload.relative_to(root)))
    go_report = read_json(root, "reports/go-x509-security.json")
    go_security.verify_report(go_report, sha256_file(rootfs_payload))
    dashboard.verify_report(read_json(root, "reports/dashboard.json"), sha256_file(rootfs_payload))
    if policy_id == "minimal-20261031":
        import minimal_security
        report = read_json(root, "reports/minimal-security.json")
        minimal_security.verify_report(report, sha256_file(rootfs_payload), root / "provenance",
                                       {row["name"]: row for row in packages})
        if report != preservation.get("checks", {}).get("minimal_security"):
            raise ValueError("release minimal security report differs from preservation evidence")
    if upstream_resolution.get("schema_version") == 2:
        report = read_json(root, "reports/components.json")
        component_gate.verify_release(root / "provenance", report, sha256_file(rootfs_payload),
                                      {row["name"]: row for row in packages})
        if report != preservation.get("checks", {}).get("daily_components"):
            raise ValueError("release component report differs from preservation evidence")
    verify_sysupgrade_payload(image_path, kernel_payload, rootfs_payload, safety)
    images = [path for path in root.rglob("*.bin") if IMAGE_RE.fullmatch(path.relative_to(root).as_posix())]
    if len(images) != 1 or images[0] != image_path:
        raise ValueError("bundle must contain exactly one captured S20P sysupgrade image")
    result = {
        "fixed_inputs": fixed,
        "image_path": image,
        "image_sha256": sha256_file(image_path),
        "kernel_abi": kernel["version"],
        "package_count": package_count,
        "kmod_count": pkg_check.get("kmods"),
        "luci_app_count": pkg_check.get("luci_apps"),
        "wrapper_commit": build_commit,
        "upstream_commit": upstream_commit,
        "source_lock_sha256": source.get("source_lock_sha256"),
        "public_key_sha256": signed["image_trust"]["public_key_sha256"],
        "go_security": {"status": go_report["status"],
                        "known_exceptions": go_report["known_exceptions"],
                        "policy_sha256": go_report["policy_sha256"],
                        "binary_count": len(go_report["binaries"]),
                        "versions": sorted({row["go_version"] for row in go_report["binaries"]})},
    }
    if build_date:
        result["build_date"] = build_date
    return result


def verify_sysupgrade_payload(image, kernel_payload, rootfs_payload, safety):
    expected = {"kernel": Path(kernel_payload), "rootfs": Path(rootfs_payload)}
    seen, selected_prefix = {}, None
    try:
        with tarfile.open(image, "r:*") as archive:
            for member in archive:
                parts = PurePosixPath(member.name).parts
                if not parts or parts[-1] not in {"kernel", "root"}:
                    continue
                kind = "kernel" if parts[-1] == "kernel" else "rootfs"
                if (len(parts) != 2 or not parts[0].startswith("sysupgrade-") or
                        not member.isfile() or kind in seen):
                    raise ValueError("unsafe or duplicate S20P sysupgrade payload member")
                if selected_prefix is not None and selected_prefix != parts[0]:
                    raise ValueError("S20P sysupgrade image mixes board payload directories")
                selected_prefix = parts[0]
                if member.size <= 0 or member.size > PARTITION_CAPACITIES[kind]:
                    raise ValueError("S20P " + kind + " payload exceeds its partition capacity")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("S20P sysupgrade payload cannot be read")
                digest = hashlib.sha256()
                size = 0
                with stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                payload = expected[kind]
                if size != member.size or size != payload.stat().st_size or digest.hexdigest() != sha256_file(payload):
                    raise ValueError("S20P " + kind + " payload differs from preserved inspection payload")
                seen[kind] = member
    except (OSError, tarfile.TarError) as error:
        raise ValueError("invalid S20P sysupgrade payload archive") from error
    if set(seen) != set(PARTITION_CAPACITIES):
        raise ValueError("S20P sysupgrade image must contain one kernel and one root payload")
    partitions = safety.get("partitions")
    if not isinstance(partitions, dict):
        raise ValueError("S20P partition-size evidence is missing")
    for kind, member in seen.items():
        record = partitions.get(kind)
        capacity = PARTITION_CAPACITIES[kind]
        if (not isinstance(record, dict) or record.get("size") != member.size or
                record.get("capacity") != capacity or record.get("remaining") != capacity - member.size):
            raise ValueError("S20P " + kind + " partition metadata differs from the image payload")
        if kind == "rootfs":
            overlay_start = (member.size + 65535) & ~65535
            if (record.get("overlay_start") != overlay_start or
                    record.get("overlay_remaining") != capacity - overlay_start or
                    overlay_start + 4096 > capacity):
                raise ValueError("S20P rootfs overlay boundary metadata is invalid")


def checksum_lines(directory, exclude=()):
    rows = []
    for path in sorted(Path(directory).rglob("*")):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise ValueError("symlink in release assets")
        if path.is_file() and relative not in set(exclude):
            rows.append(f"{sha256_file(path)}  {relative}")
    return "\n".join(rows) + "\n"


def security_exception_notice(security):
    return "\n\n".join(
        "QuickStart remains unpatched for GO-2026-6809 (CVE-2026-111943). "
        f"The retained official 0.13.0 prebuilt at `{row['path']}` embeds `{row['go_version']}` "
        f"and has SHA256 `{row['sha256']}`. It is the sole exact-byte legacy exception; "
        "other image Go executables must pass the patched-version and linked-x509 checks. "
        "The CA trust-store update only protects TLS calls that use that store and does not patch this binary. "
        f"Official source archive: {row['source_archive_url']} (SHA256 `{row['source_archive_sha256']}`). "
        "This candidate is not fully patched for this advisory."
        for row in security["known_exceptions"])


def prepare_release_assets(artifact_dir, output_dir, *, run_id, run_attempt, build_commit, upstream_commit):
    outer = Path(artifact_dir)
    archive = verify_outer_artifact(outer, run_id, run_attempt)
    output = Path(output_dir)
    if output.exists():
        raise ValueError("release asset output already exists")
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="s20p-b-release-") as temp:
        extracted = Path(temp) / "bundle"
        extract_verified_archive(archive, extracted)
        verified = verify_candidate_bundle(extracted, run_id, run_attempt, build_commit, upstream_commit)
        image = extracted / verified["image_path"]
        image_name = release_image_name(upstream_commit, verified.get("build_date"))
        shutil.copyfile(image, output / image_name)
        archive_name = archive.name
        shutil.copyfile(archive, output / archive_name)
        (output / "archive.SHA256SUMS").write_text(
            f"{sha256_file(output / archive_name)}  {archive_name}\n")
    guide = f"""# CLX-S20P 主線韌體 · rolling 25.12 prerelease candidate

This is a directly flashable S20P sysupgrade image from GitHub Actions run {run_id} (attempt {run_attempt}), wrapper commit `{build_commit}`, resolved upstream commit `{upstream_commit}`. It is a rolling prerelease candidate, not a stable release. Hardware boot and migration have not been validated.

## Before using it

Keep the current router online until you have read the release notes and verified `SHA256SUMS`. Back up the current router configuration and partitions, and confirm that you have a separate recovery path. Do not restore an old full-device backup over this image. This candidate omits the three built-in Lucky packages (`lucky`, `luci-app-lucky`, and `luci-i18n-lucky-zh-cn`) under the existing narrowly scoped package review. A trusted signed iStore feed was audited, but the available Lucky release is older than the image baseline and its behavior/configuration migration has not been validated.

## Verify the download

Download all six assets from this release into the same directory, then run:

```sh
sha256sum -c SHA256SUMS
```

`SHA256SUMS` lists the five other assets; downloading only the `.bin` will make this full check report missing files.

## Install

Use only on the S20P (`clx,s20p`) running the matching 25.12/APK family. In LuCI, open **System → Backup / Flash Firmware**, select the `.bin`, and let the router validate the image before confirming the upgrade. In SSH, first run the firmware's read-only test mode (`sysupgrade -T /tmp/<image>.bin`); proceed only if it exits successfully and the model is reported as S20P. Do not preserve settings from a different firmware family. Keep power connected until the router has rebooted and been checked locally.

## Validation limits

The exact run passed its same-build package, signed-feed, source, ABI, image-safety, CA revocation, scoped Go x509 and preservation checks. Go checks cover image ELF build metadata and linked verifier constants, with every known exception disclosed below; post-install downloads and private service binaries are outside this image audit. This does not establish stable operation, Wi-Fi/USB behavior, private service compatibility, or configuration migration. This build is not bit-identical to the historical image.

## Go x509 security status: {verified['go_security']['status']}

{security_exception_notice(verified['go_security']) or 'No legacy prebuilt exception is present in this image Go inventory.'}
"""
    if verified["fixed_inputs"]:
        guide = guide.replace("rolling 25.12 prerelease candidate", "fixed-source 25.12 prerelease candidate").replace(
            "It is a rolling prerelease candidate", "It is a fixed-source prerelease candidate")
        guide += ("\n## Fixed inputs\n\nSystem, plugin/feed and security inputs are frozen to source run "
                  + verified["fixed_inputs"]["source_run"] + ". Manifest SHA256: `"
                  + verified["fixed_inputs"]["manifest_sha256"]
                  + "`. The same-build archive includes the manifest and reviewed wrapper HTTP bytes. "
                  "Runner tools are not bit-for-bit frozen; this does not claim reproducible binary output.\n")
        if verified["fixed_inputs"].get("profile", {}).get("id") == "minimal-20261031":
            guide += ("\n## Scoped management backports\n\n"
                      "uhttpd and uhttpd-mod-ubus are rebuilt as r2 with official fix "
                      "64dcf47aa7f84ae60db9a3950e5c4b8fbe5ed6f1. The LuCI mounts ACL "
                      "loses only its root crontab write grant; dedicated cron permissions remain. "
                      "The three EasyTier packages are excluded; shared dependencies remain. "
                      "QuickStart retains its existing packages and disclosed unpatched Go exception. "
                      "No kernel, ubus ABI or Docker migration is included. Existing overlay files "
                      "and post-install packages still require separate migration/runtime checks.\n")
    (output / "INSTALLATION.md").write_text(guide)
    metadata = {
        "schema_version": 1,
        "run_id": str(run_id), "run_attempt": str(run_attempt),
        "wrapper_commit": build_commit, "upstream_commit": upstream_commit,
        "image": image_name, "image_sha256": verified["image_sha256"],
        "kernel_abi": verified["kernel_abi"], "package_count": verified["package_count"],
        "kmod_count": verified["kmod_count"], "luci_app_count": verified["luci_app_count"],
        "source_lock_sha256": verified["source_lock_sha256"],
        "public_key_sha256": verified["public_key_sha256"],
        "go_security": verified["go_security"],
        "fixed_inputs": verified["fixed_inputs"],
        "live_acceptance": "not-run", "stable_eligible": False,
    }
    if "build_date" in verified:
        metadata["build_date"] = verified["build_date"]
    (output / "BUILD-VERIFICATION.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    (output / "SHA256SUMS").write_text(checksum_lines(output, exclude={"SHA256SUMS"}))
    verify_release_assets(output, run_id=run_id, run_attempt=run_attempt,
                          build_commit=build_commit, upstream_commit=upstream_commit)
    return {"image": image_name, "archive": archive.name, "verified": metadata}


def verify_release_assets(output_dir, *, run_id, run_attempt, build_commit, upstream_commit):
    output = Path(output_dir)
    checksums = verify_checksums(output)
    archive = output / f"s20p-samebuild-{run_id}-{run_attempt}.tar.gz"
    if archive.name not in checksums:
        raise ValueError("release asset set has no exact same-build archive")
    archive_sum = parse_sha256sums(output / "archive.SHA256SUMS")
    if archive_sum != {archive.name: sha256_file(archive)}:
        raise ValueError("original same-build archive checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="s20p-b-verify-") as temp:
        root = Path(temp) / "bundle"
        extract_verified_archive(archive, root)
        verified = verify_candidate_bundle(root, run_id, run_attempt, build_commit, upstream_commit)
    image_name = release_image_name(upstream_commit, verified.get("build_date"))
    expected_names = {image_name, archive.name,
                      "archive.SHA256SUMS", "INSTALLATION.md", "BUILD-VERIFICATION.json"}
    if set(checksums) != expected_names:
        raise ValueError("release asset set differs from verified candidate asset set")
    image = output / image_name
    if sha256_file(image) != verified["image_sha256"]:
        raise ValueError("direct ROM does not match the same-build archive")
    metadata = read_json(output, "BUILD-VERIFICATION.json")
    if (metadata.get("run_id") != str(run_id) or metadata.get("run_attempt") != str(run_attempt) or
            metadata.get("wrapper_commit") != build_commit or
            metadata.get("image") != image_name or
            metadata.get("image_sha256") != verified["image_sha256"] or
            metadata.get("upstream_commit") != upstream_commit or
            metadata.get("go_security") != verified["go_security"] or
            metadata.get("fixed_inputs") != verified["fixed_inputs"] or
            metadata.get("build_date") != verified.get("build_date") or
            ("build_date" in metadata) != ("build_date" in verified) or
            metadata.get("live_acceptance") != "not-run" or metadata.get("stable_eligible") is not False):
        raise ValueError("release build verification record does not match exact candidate")
    guide = (output / "INSTALLATION.md").read_text()
    if str(run_id) not in guide or build_commit not in guide or "not a stable release" not in guide.lower():
        raise ValueError("release installation guidance lacks candidate provenance/limits")
    notice = security_exception_notice(verified["go_security"])
    if notice and notice not in guide:
        raise ValueError("release installation guidance hides a known security exception")
    return {"pass": True, "image_sha256": verified["image_sha256"], "kernel_abi": verified["kernel_abi"]}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    zip_cmd = sub.add_parser("verify-artifact-zip")
    zip_cmd.add_argument("zip", type=Path)
    zip_cmd.add_argument("destination", type=Path)
    zip_cmd.add_argument("--api-digest", required=True)
    zip_cmd.add_argument("--run-id", required=True)
    zip_cmd.add_argument("--run-attempt", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("artifact_dir", type=Path)
    prepare.add_argument("output_dir", type=Path)
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--run-attempt", required=True)
    prepare.add_argument("--build-commit", required=True)
    prepare.add_argument("--upstream-commit", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("output_dir", type=Path)
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--run-attempt", required=True)
    verify.add_argument("--build-commit", required=True)
    verify.add_argument("--upstream-commit", required=True)
    args = parser.parse_args()
    if args.command == "verify-artifact-zip":
        result = {"archive": verify_artifact_zip(args.zip, args.destination, args.api_digest,
                                                  args.run_id, args.run_attempt)}
    elif args.command == "prepare":
        result = prepare_release_assets(args.artifact_dir, args.output_dir, run_id=args.run_id,
                                        run_attempt=args.run_attempt, build_commit=args.build_commit,
                                        upstream_commit=args.upstream_commit)
    else:
        result = verify_release_assets(args.output_dir, run_id=args.run_id,
                                       run_attempt=args.run_attempt, build_commit=args.build_commit,
                                       upstream_commit=args.upstream_commit)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
