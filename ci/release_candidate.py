#!/usr/bin/env python3
"""Verify and prepare directly downloadable assets for one exact B Actions run."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
import upstream_sync
import go_security


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
    expected = {
        "schema_version": 1,
        "repository": UPSTREAM_URL,
        "ref": UPSTREAM_REF,
        "base_commit": UPSTREAM_BASE_COMMIT,
        "head_commit": expected_commit,
        "compare_status": "identical" if expected_commit == UPSTREAM_BASE_COMMIT else "ahead",
        "merge_base": UPSTREAM_BASE_COMMIT,
        "should_build": True,
    }
    if (any(evidence.get(key) != value for key, value in expected.items()) or
            type(evidence.get("forced")) is not bool or
            type(evidence.get("previous_attempted")) is not bool or
            (expected_commit == UPSTREAM_BASE_COMMIT and evidence.get("forced") is not True) or
            (evidence.get("previous_attempted") is True and evidence.get("forced") is not True)):
        raise ValueError("upstream resolution evidence does not match the candidate SHA")
    upstream_sync.validate_previous_comparison(expected_commit, evidence.get("previous_comparison"))
    return evidence


def verify_candidate_bundle(root, run_id, run_attempt, build_commit, upstream_commit):
    root = Path(root)
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
        upstream_resolution["previous_attempted"], upstream_resolution.get("previous_comparison"))
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
    verify_sysupgrade_payload(image_path, kernel_payload, rootfs_payload, safety)
    images = [path for path in root.rglob("*.bin") if IMAGE_RE.fullmatch(path.relative_to(root).as_posix())]
    if len(images) != 1 or images[0] != image_path:
        raise ValueError("bundle must contain exactly one captured S20P sysupgrade image")
    return {
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
        "go_security": {"policy_sha256": go_report["policy_sha256"],
                        "binary_count": len(go_report["binaries"]),
                        "versions": sorted({row["go_version"] for row in go_report["binaries"]})},
    }


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
        image_name = "s20p-A-25.12-" + upstream_commit[:8] + "-sysupgrade.bin"
        shutil.copyfile(image, output / image_name)
        archive_name = archive.name
        shutil.copyfile(archive, output / archive_name)
        (output / "archive.SHA256SUMS").write_text(
            f"{sha256_file(output / archive_name)}  {archive_name}\n")
    guide = f"""# S20P A rolling 25.12 prerelease candidate

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

The exact run passed its same-build package, signed-feed, source, ABI, image-safety, CA revocation, Go x509 and preservation checks. Go checks cover image ELF build metadata and linked verifier constants; post-install downloads and private service binaries are outside this image audit. This does not establish stable operation, Wi-Fi/USB behavior, private service compatibility, or configuration migration. This build is not bit-identical to the historical image.
"""
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
        "live_acceptance": "not-run", "stable_eligible": False,
    }
    (output / "BUILD-VERIFICATION.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    (output / "SHA256SUMS").write_text(checksum_lines(output, exclude={"SHA256SUMS"}))
    verify_release_assets(output, run_id=run_id, run_attempt=run_attempt,
                          build_commit=build_commit, upstream_commit=upstream_commit)
    return {"image": image_name, "archive": archive.name, "verified": metadata}


def verify_release_assets(output_dir, *, run_id, run_attempt, build_commit, upstream_commit):
    output = Path(output_dir)
    checksums = verify_checksums(output)
    expected_names = {
        "s20p-A-25.12-" + upstream_commit[:8] + "-sysupgrade.bin",
        f"s20p-samebuild-{run_id}-{run_attempt}.tar.gz",
        "archive.SHA256SUMS", "INSTALLATION.md", "BUILD-VERIFICATION.json",
    }
    if set(checksums) != expected_names:
        raise ValueError("release asset set differs from verified rolling candidate asset set")
    archive = output / f"s20p-samebuild-{run_id}-{run_attempt}.tar.gz"
    archive_sum = parse_sha256sums(output / "archive.SHA256SUMS")
    if archive_sum != {archive.name: sha256_file(archive)}:
        raise ValueError("original same-build archive checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="s20p-b-verify-") as temp:
        root = Path(temp) / "bundle"
        extract_verified_archive(archive, root)
        verified = verify_candidate_bundle(root, run_id, run_attempt, build_commit, upstream_commit)
    image = output / ("s20p-A-25.12-" + upstream_commit[:8] + "-sysupgrade.bin")
    if sha256_file(image) != verified["image_sha256"]:
        raise ValueError("direct ROM does not match the same-build archive")
    metadata = read_json(output, "BUILD-VERIFICATION.json")
    if (metadata.get("run_id") != str(run_id) or metadata.get("run_attempt") != str(run_attempt) or
            metadata.get("wrapper_commit") != build_commit or
            metadata.get("image_sha256") != verified["image_sha256"] or
            metadata.get("upstream_commit") != upstream_commit or
            metadata.get("go_security") != verified["go_security"] or
            metadata.get("live_acceptance") != "not-run" or metadata.get("stable_eligible") is not False):
        raise ValueError("release build verification record does not match exact candidate")
    guide = (output / "INSTALLATION.md").read_text()
    if str(run_id) not in guide or build_commit not in guide or "not a stable release" not in guide.lower():
        raise ValueError("release installation guidance lacks candidate provenance/limits")
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
