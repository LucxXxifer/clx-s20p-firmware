#!/usr/bin/env python3
"""Backport exact upstream Go recipes and inspect image ELF build metadata.

No target executable is run. Linked x509 verifier constants supplement the
compiler version evidence; they are not a runtime exploit/functional test.
"""
import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import tempfile

POLICY_PATH = Path(__file__).with_name("security") / "go-x509.json"
POLICY = json.loads(POLICY_PATH.read_text())
DISTRUSTED_SPKI = tuple(POLICY["distrusted_spki"])
REQUIRED_BINARIES = ("/usr/bin/docker", "/usr/bin/dockerd", "/usr/bin/containerd", "/usr/sbin/runc")
MAGIC = b"\xff Go buildinf:"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(source_root):
    root = Path(source_root)
    alias = root / "feeds/packages/lang/golang/golang/Makefile"
    if alias.is_symlink() or not alias.is_file():
        raise ValueError("required default Go host alias is missing")
    if sha256(alias) != POLICY["default_alias_sha256"]:
        raise ValueError("unexpected default Go host alias")
    changes = []
    # Validate both complete recipes before replacing either. An upstream
    # change needs explicit review, even if its version string looks familiar.
    for recipe in POLICY["recipes"]:
        path = root / recipe["path"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("required versioned Go recipe is missing: " + recipe["path"])
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest == recipe["after_sha256"]:
            continue
        if digest != recipe["before_sha256"]:
            raise ValueError("unreviewed Go recipe: " + recipe["path"])
        for old, new in (("GO_VERSION_PATCH:=" + recipe["old_patch"], "GO_VERSION_PATCH:=" + recipe["new_patch"]),
                         ("PKG_HASH:=" + recipe["old_source_sha256"], "PKG_HASH:=" + recipe["new_source_sha256"])):
            before, after = (old + "\n").encode(), (new + "\n").encode()
            if data.count(before) != 1:
                raise ValueError("ambiguous Go recipe field: " + recipe["path"])
            data = data.replace(before, after)
        if hashlib.sha256(data).hexdigest() != recipe["after_sha256"]:
            raise ValueError("backport differs from exact upstream Go recipe")
        changes.append((path, data))
    for path, data in changes:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
                os.chmod(temporary, path.stat().st_mode & 0o777)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    return {"status": "pass", "policy_sha256": sha256(POLICY_PATH),
            "recipes": [{"path": row["path"], "sha256": row["after_sha256"],
                         "upstream_commit": row["upstream_commit"]} for row in POLICY["recipes"]]}


def require_patched(version):
    match = re.fullmatch(r"go1\.(\d+)\.(\d+)", str(version))
    if not match:
        raise ValueError("stable Go release build info required: " + str(version))
    minor, patch = map(int, match.groups())
    if minor not in (26, 27) or patch < {26: 10, 27: 3}[minor]:
        raise ValueError("unpatched or unreviewed Go version: " + version)


def binary_security(row):
    for exception in POLICY.get("legacy_prebuilt_exceptions", []):
        if all(row.get(key) == exception[key] for key in
               ("path", "sha256", "go_version", "x509_verify_linked", "distrusted_spki_count")):
            return {"security_status": "legacy-unpatched-prebuilt", "exception_id": exception["id"]}
    require_patched(row.get("go_version"))
    if row["x509_verify_linked"] and row["distrusted_spki_count"] != 8:
        raise ValueError("linked x509 verifier lacks all eight distrust constants: " + row["path"])
    return {"security_status": "patched"}


def inspect_elf(data, name):
    if data[:4] != b"\x7fELF":
        return None
    offset = data.find(MAGIC)
    if offset < 0:
        return None
    # Go 1.18+ uses an aligned 32-byte header followed by a uvarint-prefixed
    # version string. Parse it rather than trusting arbitrary version text.
    version = None
    while offset >= 0:
        if (offset % 16 == 0 and offset + 33 <= len(data) and data[offset + 14] in (4, 8)
                and data[offset + 15] & 2):
            cursor, size, shift = offset + 32, 0, 0
            while cursor < len(data) and shift <= 7:
                value = data[cursor]
                size |= (value & 127) << shift
                cursor += 1
                if value < 128:
                    if 1 <= size <= 128 and cursor + size <= len(data):
                        version = bytes(data[cursor:cursor + size]).decode("ascii", errors="replace")
                    break
                shift += 7
            if version is not None:
                break
        offset = data.find(MAGIC, offset + 1)
    if version is None:
        raise ValueError("malformed/unsupported Go build info: " + name)
    linked = data.find(b"crypto/x509.(*Certificate).Verify") >= 0
    hashes = sum(data.find(value.encode()) >= 0 for value in DISTRUSTED_SPKI)
    row = {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "go_version": version,
           "x509_verify_linked": linked, "distrusted_spki_count": hashes}
    row.update(binary_security(row))
    return row


def inspect_rootfs(root, payload):
    root = Path(root)
    binaries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.stat().st_size < 4:
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                row = inspect_elf(data, "/" + path.relative_to(root).as_posix())
                if row is not None:
                    binaries.append(row)
    exceptions = [item for item in POLICY.get("legacy_prebuilt_exceptions", [])
                  if any(row.get("exception_id") == item["id"] for row in binaries)]
    report = {"schema_version": 2, "status": "pass_with_known_exception" if exceptions else "pass",
              "policy_sha256": sha256(POLICY_PATH), "known_exceptions": exceptions,
              "rootfs_sha256": sha256(payload), "binaries": binaries,
              "scope": "image ELF Go build info and linked x509 constants; no target execution or post-install downloads"}
    verify_report(report, report["rootfs_sha256"])
    return report


def verify_report(report, rootfs_sha256):
    if (report.get("schema_version") != 2
            or report.get("policy_sha256") != sha256(POLICY_PATH)
            or report.get("rootfs_sha256") != rootfs_sha256 or not SHA256.fullmatch(rootfs_sha256)):
        raise ValueError("Go security report policy/payload/status mismatch")
    rows = report.get("binaries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("required Go binary inventory is missing")
    names = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid Go binary security evidence")
        name = row.get("path")
        if (not isinstance(name, str) or not name.startswith("/") or ".." in Path(name).parts or name in names
                or not SHA256.fullmatch(str(row.get("sha256")))
                or type(row.get("x509_verify_linked")) is not bool
                or type(row.get("distrusted_spki_count")) is not int
                or not 0 <= row["distrusted_spki_count"] <= 8):
            raise ValueError("invalid Go binary security evidence")
        names.add(name)
        expected = binary_security(row)
        actual = {key: row[key] for key in ("security_status", "exception_id") if key in row}
        if actual != expected:
            raise ValueError("Go security report binary classification mismatch")
    if not set(REQUIRED_BINARIES) <= names:
        raise ValueError("required Docker Go binaries missing from image/security evidence")
    exceptions = [item for item in POLICY.get("legacy_prebuilt_exceptions", [])
                  if any(row.get("exception_id") == item["id"] for row in rows)]
    expected_status = "pass_with_known_exception" if exceptions else "pass"
    if report.get("status") != expected_status or report.get("known_exceptions") != exceptions:
        raise ValueError("Go security report known exception disclosure/status mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--source-root", type=Path, required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--rootfs", type=Path, required=True)
    inspect.add_argument("--payload", type=Path, required=True)
    inspect.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.source_root)
        else:
            result = inspect_rootfs(args.rootfs, args.payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError) as error:
        raise SystemExit("Go security gate: " + str(error))


if __name__ == "__main__":
    main()
