#!/usr/bin/env python3
"""Build/package the reviewed read-only dashboard adapters and verify image bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve().with_name("dashboard")
SOURCE_PINS = {
    "luasrc/controller/istore_backend.lua": "3ee45ab6217c3f311c3003cbf8d4d562597399ada25c406a9502d66cd146d604",
    "htdocs/luci-static/quickstart/index.js": "6d3accdd96d0183ad2d3b2770c43fb214147b545a5a5db30e5accdbafa74d8ab",
    # Feed source still has PKG_VERSION placeholders; LuCI substitutes them
    # during installation. The other pins follow apply_custom_feed_patches().
    "luasrc/view/quickstart/main.htm": "806d968acb47afadc44f2bcb2c482f7149763a03f83934dc7323a9b95ae85fc2",
}
PAYLOAD_FILES = {
    "/usr/lib/lua/luci/controller/istore_backend.lua": HERE / "istore_backend.lua",
    "/usr/lib/lua/luci/model/s20p_dashboard.lua": HERE / "luci/model/s20p_dashboard.lua",
    "/usr/lib/lua/luci/model/s20p_ports.lua": HERE / "luci/model/s20p_ports.lua",
    "/usr/lib/lua/luci/view/quickstart/main.htm": HERE / "main.htm",
}
FRONTEND = "/www/luci-static/quickstart/index.js"
FRONTEND_SHA = "da83d26620b0b1799f0103051911dc440aad3c47e73ecfaf8b327f3a62e4f12c"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing/nonregular dashboard payload: " + str(path))
    return path.read_bytes()


def policy_hash():
    inputs = {str(p.relative_to(HERE)): sha(regular(p)) for p in
              [*PAYLOAD_FILES.values(), HERE / "frontend-build.cjs"]}
    inputs["frontend_sha256"] = FRONTEND_SHA
    inputs["source_pins"] = SOURCE_PINS
    return sha(json.dumps(inputs, sort_keys=True).encode())


def report(payload_sha):
    files = [{"path": path, "sha256": sha(regular(source))}
             for path, source in PAYLOAD_FILES.items()]
    files.append({"path": FRONTEND, "sha256": FRONTEND_SHA})
    return {"schema_version": 1, "status": "pass", "rootfs_sha256": payload_sha,
            "policy_sha256": policy_hash(), "files": sorted(files, key=lambda row: row["path"]),
            "scope": "five exact installed files; authenticated read-only adapters; no network configuration"}


def verify_report(value, payload_sha):
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload_sha)) or value != report(payload_sha):
        raise ValueError("dashboard image report policy/payload/files mismatch")


def inspect_rootfs(root, payload):
    root = Path(root)
    expected = report(sha(regular(Path(payload))))
    for row in expected["files"]:
        path = root / row["path"].lstrip("/")
        if any(parent.is_symlink() for parent in path.parents if parent != root):
            raise ValueError("dashboard symlink parent")
        if sha(regular(path)) != row["sha256"]:
            raise ValueError("dashboard image SHA mismatch: " + row["path"])
    return expected


def prepare(source_root):
    package = Path(source_root) / "feeds/custom_feed/luci-app-quickstart"
    for relative, expected in SOURCE_PINS.items():
        if sha(regular(package / relative)) != expected:
            raise ValueError("dashboard upstream source SHA changed: " + relative)
    for name in ("s20p_dashboard.lua", "s20p_ports.lua"):
        if (package / "luasrc/model" / name).exists():
            raise ValueError("dashboard helper already exists; do not blindly reapply")
    # Complete generation and validation before modifying the feed package.
    with tempfile.TemporaryDirectory() as name:
        staging = Path(name)
        subprocess.run(["node", str(HERE / "frontend-build.cjs"),
                        str(package / "htdocs/luci-static/quickstart/index.js"), str(staging)],
                       check=True, stdout=subprocess.DEVNULL)
        frontend = regular(staging / "frontend-index.js")
    if sha(frontend) != FRONTEND_SHA:
        raise ValueError("dashboard generated frontend hash changed")
    writes = {package / "htdocs/luci-static/quickstart/index.js": frontend}
    for installed, source in PAYLOAD_FILES.items():
        relative = installed.removeprefix("/usr/lib/lua/luci/")
        writes[package / "luasrc" / relative] = regular(source)
    for path, data in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            tmp = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
                os.chmod(tmp, 0o644)
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
    return {"status": "pass", "policy_sha256": policy_hash(), "files": report("0" * 64)["files"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare")
    command.add_argument("--source-root", type=Path, required=True)
    command = sub.add_parser("inspect")
    command.add_argument("--rootfs", type=Path, required=True)
    command.add_argument("--payload", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.source_root)
        else:
            result = inspect_rootfs(args.rootfs, args.payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, "Dashboard gate: " + str(error) + "\n")


if __name__ == "__main__":
    main()
