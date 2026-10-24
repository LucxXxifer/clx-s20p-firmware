#!/usr/bin/env bash
# Called after make succeeds, before firmware collection or make clean.
# Interface: capture.sh BUILD_DIR OUTPUT_DIR; capture.sh --audit-output OUTPUT_DIR.
set -euo pipefail
[[ $# == 2 ]] || { echo "usage: $0 BUILD_DIR OUTPUT_DIR | --audit-output OUTPUT_DIR" >&2; exit 2; }
export S20P_MOBILE_PACKAGES_FILE=${S20P_MOBILE_PACKAGES_FILE:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/mobile-usb-packages.txt}
# Preserve SquashFS numeric owners and device-node stat results on an unprivileged
# runner. The same inode metadata state is reused by the later image gate.
if [[ $(id -u) != 0 && -z ${FAKEROOTKEY:-} ]]; then
    command -v fakeroot >/dev/null || { echo 'capture: fakeroot is required for non-root metadata preservation' >&2; exit 1; }
    mkdir -p -- "$2"
    state_file="$2/.fakeroot.state"
    fakeroot_args=(-s "$state_file")
    [[ ! -f $state_file ]] || fakeroot_args=(-i "$state_file" "${fakeroot_args[@]}")
    exec fakeroot "${fakeroot_args[@]}" -- bash "$0" "$@"
fi
python3 - "$1" "$2" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile


def fail(message):
    raise RuntimeError(message)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


PRIVATE = re.compile(rb"-----BEGIN (?:[A-Z0-9 ]* )?PRIVATE KEY-----")
PRIVATE_NAMES = re.compile(r"^(?:private[-_]key(?:\..*)?|key-build|"
                           r"id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pem)?|"
                           r"dropbear_.*_host_key|ssh_host_.*_key)$")


def check_publication_file(path):
    if PRIVATE_NAMES.fullmatch(path.name) or path.suffix == ".key":
        fail("private key filename in publication: " + str(path))
    tail = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            data = tail + chunk
            if PRIVATE.search(data):
                fail("private key content in publication: " + str(path))
            tail = data[-128:]


def audit(output):
    for path in output.rglob("*"):
        if path.is_file() and not path.is_symlink():
            check_publication_file(path)


def copy_file(source, destination):
    if not source.is_file():
        fail("required file missing: " + str(source))
    check_publication_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_bin(source, destination, root, ancestors=()):
    resolved = source.resolve()
    if not resolved.is_relative_to(root):
        fail("bin symlink escapes publication tree: " + str(source))
    if resolved.is_dir():
        if resolved in ancestors:
            fail("bin symlink cycle: " + str(source))
        destination.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir()):
            copy_bin(child, destination / child.name, root, ancestors + (resolved,))
    elif resolved.is_file():
        copy_file(source, destination)
    else:
        fail("unsupported bin entry or broken symlink: " + str(source))


def run(arguments):
    result = subprocess.run([str(x) for x in arguments], capture_output=True, text=True)
    if result.returncode:
        fail("command failed: " + str(arguments[0]) + " " + " ".join(map(str, arguments[1:3])) +
             "\n" + result.stderr[-6000:] + result.stdout[-6000:])
    return result.stdout


def fdt_nodes(data):
    if len(data) < 40:
        fail("truncated FIT/DTB")
    header = struct.unpack_from(">10I", data)
    magic, total, struct_off, string_off = header[:4]
    if magic != 0xD00DFEED or total > len(data):
        fail("invalid FIT/DTB header")
    strings = data[string_off:string_off + header[8]]
    cursor, end = struct_off, struct_off + header[9]
    stack, nodes = [], {}
    while cursor + 4 <= end:
        token = struct.unpack_from(">I", data, cursor)[0]
        cursor += 4
        if token == 1:
            terminator = data.index(b"\0", cursor, end)
            name = data[cursor:terminator].decode()
            stack.append(name)
            nodes["/" + "/".join(stack[1:])] = {}
            cursor = (terminator + 4) & ~3
        elif token == 2:
            if not stack:
                fail("invalid DTB node nesting")
            stack.pop()
        elif token == 3:
            length, name_off = struct.unpack_from(">II", data, cursor)
            cursor += 8
            if not stack or cursor + length > end or name_off >= len(strings):
                fail("invalid FIT property")
            name = strings[name_off:strings.index(b"\0", name_off)].decode()
            nodes["/" + "/".join(stack[1:])][name] = data[cursor:cursor + length]
            cursor = (cursor + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            return nodes, total
        else:
            fail("invalid FIT token")
    fail("FIT has no end token")


def selected_dtb(kernel):
    data = kernel.read_bytes()
    nodes, total = fdt_nodes(data)
    configurations = nodes.get("/configurations", {})
    default = configurations.get("default", b"").rstrip(b"\0").decode()
    if not default:
        fail("FIT missing default configuration")
    config = nodes.get("/configurations/" + default, {})
    name = config.get("fdt", b"").rstrip(b"\0").decode()
    image = nodes.get("/images/" + name, {})
    if image.get("type", b"").rstrip(b"\0") != b"flat_dt":
        fail("default FIT configuration does not select a DTB")
    dtb = image.get("data")
    if dtb is None:
        def integer(value):
            if len(value) not in (4, 8):
                fail("invalid external FIT data offset")
            return int.from_bytes(value, "big")
        length = integer(image.get("data-size", b""))
        if "data-position" in image:
            offset = integer(image["data-position"])
        else:
            offset = ((total + 3) & ~3) + integer(image.get("data-offset", b""))
        dtb = data[offset:offset + length]
        if len(dtb) != length:
            fail("truncated external DTB")
    fdt_nodes(dtb)
    return dtb, default, name


def installed_packages(path):
    root = path.parents[3].resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        fail("installed package database escapes actual rootfs")
    packages = {}
    for block in path.read_text().strip().split("\n\n"):
        fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        if "P" in fields and "V" in fields:
            packages[fields["P"]] = fields["V"]
    if not packages or "kernel" not in packages:
        fail("actual image installed package database is missing kernel")
    return packages


def image_feed_trust(output):
    root = (output / "inspection/rootfs").resolve()
    keyring = root / "etc/apk/keys"
    if not keyring.resolve().is_relative_to(root) or not keyring.is_dir():
        fail("image trust keyring is missing or escapes actual rootfs")
    public_key = (output / "public-keys/public-key.pem").read_bytes()
    matching = []
    for key in sorted(keyring.iterdir()):
        if not key.resolve().is_relative_to(root):
            fail("image trust key escapes actual rootfs: " + str(key))
        if key.is_file() and key.read_bytes() == public_key:
            matching.append("/" + key.relative_to(root).as_posix())
    if not matching:
        fail("image trust keyring does not trust the preserved feed public key")
    return {"status": "pass", "comparison": "exact-public-key-bytes",
            "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
            "matching_key_paths": matching}


def verify_feeds(build, output, installed):
    image_trust = image_feed_trust(output)
    apk = build / "staging_dir/host/bin/apk"
    if not apk.is_file():
        fail("same-build host apk is missing")
    keys = output / "public-keys"
    tool = output / "host-tools/apk"
    copy_file(apk, tool)
    version = run([apk, "--version"]).strip()
    all_apks = sorted((output / "bin").rglob("*.apk"))
    if not all_apks:
        fail("no same-build APK packages")
    feeds = sorted({p.parent for p in all_apks})
    available, reports = {}, []
    kmod_checked = 0
    for feed in feeds:
        index = feed / "packages.adb"
        if not index.is_file():
            fail("APK feed index missing: " + str(feed))
        packages = sorted(feed.glob("*.apk"))
        # Individual APKs may be unsigned (CONFIG_SIGN_EACH_PACKAGE=n). Trust
        # comes from this signed index, followed by native fetch identity checks.
        verify_log = run([apk, "--keys-dir", keys, "verify", index])
        metadata = json.loads(run([apk, "adbdump", "--format", "json", index]))
        records = metadata.get("packages", [])
        identities = {(p["name"], p["version"]): p for p in records}
        names = [p["name"] for p in records]
        if not records or len(set(names)) != len(names):
            fail("signed index must have one version per package name: " + str(feed))
        for record in records:
            if record["name"].startswith("kmod-"):
                dependencies = record.get("depends", [])
                if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
                    fail("kmod exact image kernel dependency is malformed: " + record["name"])
                kernel_dependencies = [d for d in dependencies if re.match(r"^kernel(?:[<>=~]|$)", d)]
                if kernel_dependencies != ["kernel=" + installed["kernel"]]:
                    fail("kmod must have exactly one exact image kernel dependency: " + record["name"])
                kmod_checked += 1
        found = set()
        for package in packages:
            info = json.loads(run([apk, "adbdump", "--format", "json", package])).get("info", {})
            identity = (info.get("name"), info.get("version"))
            expected = identities.get(identity, {})
            if not expected or any(expected.get(key) != value for key, value in info.items()):
                fail("signed index metadata differs from APK metadata: " + str(package))
            found.add(identity)
        if found != set(identities):
            fail("signed index metadata lists missing or duplicate APKs: " + str(feed))
        architectures = {p.get("arch") for p in records} - {"all", "noarch", None}
        configured_arch = re.search(r'^CONFIG_TARGET_ARCH_PACKAGES="([^"]+)"$',
                                    (build / ".config").read_text(), re.M)
        if configured_arch:
            architecture = configured_arch[1]
        elif len(architectures) == 1:
            architecture = next(iter(architectures))
        else:
            fail("cannot determine target APK architecture: " + str(feed))
        with tempfile.TemporaryDirectory(prefix="s20p-index-") as temporary:
            temporary = Path(temporary)
            apk_root, fetched = temporary / "apk-root", temporary / "fetched"
            shutil.copytree(keys, apk_root / "keys")
            fetched.mkdir()
            for start in range(0, len(names), 64):
                # No network, no install/scripts, no allow-untrusted, no --link:
                # fetch must parse payloads against the original signed index.
                verify_log += run([apk, "--root", apk_root, "--keys-dir", "keys",
                    "--arch", architecture, "--no-network", "--repositories-file", "/dev/null",
                    "--repository", index, "fetch", "--output", fetched, *names[start:start + 64]])
            original_hashes = sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in packages)
            fetched_hashes = sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in fetched.glob("*.apk"))
            if original_hashes != fetched_hashes:
                fail("native signed-index fetch did not verify every preserved APK: " + str(feed))
        for package in records:
            available.setdefault(package["name"], set()).add(package["version"])
        relative = feed.relative_to(output).as_posix()
        digest = hashlib.sha256(relative.encode()).hexdigest()[:16]
        write_json(output / "reports/feeds" / (digest + ".json"), metadata)
        (output / "reports/feeds" / (digest + ".log")).write_text(verify_log)
        reports.append({"path": relative, "packages": len(packages), "signature": "verified",
                        "index_metadata": "matches-all-packages", "payload_identity": "native-fetch-verified"})
    missing = [name + "=" + version for name, version in installed.items()
               if version not in available.get(name, set())]
    if missing:
        fail("image packages missing exact versions in same-build feeds: " + ", ".join(missing))
    mobile_file = Path(os.environ["S20P_MOBILE_PACKAGES_FILE"])
    mobile = [line.split("#", 1)[0].strip() for line in mobile_file.read_text().splitlines()]
    mobile = [name for name in mobile if name]
    absent = sorted(set(mobile) - set(available))
    if absent:
        fail("optional mobile packages missing from same-build signed index: " + ", ".join(absent))
    write_json(output / "reports/signed-feeds.json", {
        "status": "pass", "apk_version": version, "feeds": reports,
        "image_trust": image_trust,
        "installed_packages_matched": len(installed), "kernel_abi": installed["kernel"],
        "kmod_packages_kernel_checked": kmod_checked,
        "optional_mobile_packages": sorted(set(mobile)),
    })


def inspect_image(build, output):
    images = sorted((build / "bin/targets/mediatek/filogic").glob("*s20p*squashfs-sysupgrade.bin"))
    if len(images) != 1:
        fail("expected exactly one S20P sysupgrade image, found " + str(len(images)))
    image = images[0]
    inspection = output / "inspection"
    inspection.mkdir(parents=True, exist_ok=True)
    fwtool = build / "staging_dir/host/bin/fwtool"
    if not fwtool.is_file():
        fail("same-build fwtool is missing")
    metadata_path = inspection / "fwtool-metadata.json"
    run([fwtool, "-i", metadata_path, image])
    metadata = json.loads(metadata_path.read_text())
    if metadata.get("supported_devices") != ["clx,s20p"]:
        fail("image supported_devices must be exactly clx,s20p")
    target = metadata.get("version", {}).get("target")
    if target is not None and target != "mediatek/filogic":
        fail("image target must be mediatek/filogic")
    partitions = {}
    prefix = None
    with tarfile.open(image, "r:*") as archive:
        for member in archive:
            parts = member.name.rstrip("/").split("/")
            if parts[-1] not in ("kernel", "root"):
                continue
            if len(parts) != 2 or not parts[0].startswith("sysupgrade-") or not member.isfile():
                fail("sysupgrade kernel/root members must be regular board files")
            if prefix is not None and parts[0] != prefix:
                fail("sysupgrade mixes board directory names")
            prefix = parts[0]
            name = "kernel" if parts[-1] == "kernel" else "rootfs"
            capacity = 32 * 1024 ** 2 if name == "kernel" else 2 * 1024 ** 3
            if member.size <= 0 or member.size > capacity:
                fail(name + " partition capacity exceeded: " + str(member.size) + " > " + str(capacity))
            if name in partitions:
                fail("duplicate sysupgrade " + name + " member")
            partitions[name] = {"size": member.size, "capacity": capacity,
                                "remaining": capacity - member.size}
            if name == "rootfs":
                # emmc.sh rounds rootfs writes to 64KiB, then clears 8 sectors
                # at the overlay start; those bytes must remain inside p5.
                overlay_start = (member.size + 65535) & ~65535
                if overlay_start + 4096 > capacity:
                    fail("rootfs overlay marker does not fit inside 2GiB partition")
                partitions[name].update(overlay_start=overlay_start,
                                        overlay_remaining=capacity - overlay_start)
            destination = "kernel.fit" if name == "kernel" else "rootfs.squashfs"
            with archive.extractfile(member) as source, (inspection / destination).open("wb") as sink:
                shutil.copyfileobj(source, sink)
    if set(partitions) != {"kernel", "rootfs"}:
        fail("sysupgrade must contain one kernel and one rootfs member")
    dtb, configuration, dtb_name = selected_dtb(inspection / "kernel.fit")
    nodes, _ = fdt_nodes(dtb)
    if b"clx,s20p" not in nodes.get("/", {}).get("compatible", b"").split(b"\0"):
        fail("selected kernel DTB is not compatible with clx,s20p")
    (inspection / "board.dtb").write_bytes(dtb)
    write_json(output / "reports/image-safety.json", {
        "status": "pass", "supported_devices": metadata["supported_devices"],
        "partitions": partitions, "partition_layout": "existing GPT kernel32MiB/rootfs2GiB",
        "scope": "image contents and size only; live GPT labels and p6 preservation require pre-flash validation",
        "fit_configuration": configuration, "fit_dtb": dtb_name,
    })
    copy_file(fwtool, output / "host-tools/fwtool")
    return image, configuration, dtb_name


def capture(build, output):
    if build == output or output.is_relative_to(build):
        fail("OUTPUT_DIR must be outside BUILD_DIR")
    if (output / "reports/capture.json").exists():
        fail("capture output already complete; use a fresh OUTPUT_DIR")
    config = (build / ".config").read_text()
    for required in ("CONFIG_USE_APK=y", "CONFIG_SIGNED_PACKAGES=y"):
        if required not in config.splitlines():
            fail("required signing configuration missing: " + required)
    image, configuration, dtb_name = inspect_image(build, output)
    copy_bin(build / "bin", output / "bin", (build / "bin").resolve())
    copy_file(build / ".config", output / "metadata/final.config")
    # Deliberately select public files; never copy the build root/key directory.
    public_key = build / "public-key.pem"
    if not public_key.is_file() or b"-----BEGIN PUBLIC KEY-----" not in public_key.read_bytes():
        fail("APK signing public key is missing or invalid")
    copy_file(public_key, output / "public-keys/public-key.pem")
    usign = build / "key-build.pub"
    if usign.is_file():
        if not usign.read_text().startswith("untrusted comment:"):
            fail("invalid usign public key")
        copy_file(usign, output / "public-keys/key-build.pub")
    kernels = sorted(build.glob("build_dir/target-*/linux-mediatek_filogic/linux-*/.config"))
    if len(kernels) != 1:
        fail("expected one filogic kernel .config, found " + str(len(kernels)))
    kernel_dir = kernels[0].parent
    for name in (".config", "Module.symvers"):
        copy_file(kernel_dir / name, output / "metadata/kernel" / name)
    for name in (".vermagic", "include/config/kernel.release"):
        source = kernel_dir / name
        if source.is_file():
            copy_file(source, output / "metadata/kernel" / Path(name).name)
    for name in ("config.buildinfo", "feeds.buildinfo", "version.buildinfo"):
        copy_file(image.parent / name, output / "metadata/buildinfo" / name)
    inspection = output / "inspection"
    # The locked upstream installs squashfs4 tools with their version suffix.
    unsquashfs = build / "staging_dir/host/bin/unsquashfs4"
    if not unsquashfs.is_file():
        fail("same-build unsquashfs4 is missing")
    listing = run([unsquashfs, "-lln", inspection / "rootfs.squashfs"])
    (output / "metadata/squashfs-listing.txt").write_text(listing)
    run([unsquashfs, "-no-progress", "-d", inspection / "rootfs", inspection / "rootfs.squashfs"])
    installed = installed_packages(inspection / "rootfs/lib/apk/db/installed")
    write_json(inspection / "installed-packages.json", {"packages": [
        {"name": name, "version": version} for name, version in sorted(installed.items())]})
    verify_feeds(build, output, installed)
    audit(output)
    write_json(output / "reports/capture.json", {
        "status": "captured", "acceptance": "pending-preservation-and-live-validation",
        "image": (Path("bin") / image.relative_to(build / "bin")).as_posix(), "fit_configuration": configuration,
        "fit_dtb": dtb_name, "kernel_abi": installed["kernel"],
        "same_build_signed_feeds": True, "private_keys": "excluded-and-scanned",
        "rootfs_metadata": "persistent-fakeroot" if os.environ.get("FAKEROOTKEY") else "native-root",
    })


try:
    output = Path(sys.argv[2]).resolve()
    if sys.argv[1] == "--audit-output":
        audit(output)
        checksums = []
        for path in sorted(output.rglob("*")):
            if (path.is_file() and not path.is_symlink() and path.name not in ("SHA256SUMS", ".fakeroot.state")
                    and not path.is_relative_to(output / "release")
                    and not path.is_relative_to(output / "inspection/rootfs")):
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                checksums.append(digest + "  " + path.relative_to(output).as_posix())
        (output / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
    else:
        capture(Path(sys.argv[1]).resolve(), output)
except Exception as error:
    print("capture: " + str(error), file=sys.stderr)
    sys.exit(1)
PY
