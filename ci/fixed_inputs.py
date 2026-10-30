#!/usr/bin/env python3
"""Select reviewed immutable inputs; the normal upstream and image gates still apply."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.parse import urlsplit

import components


def payload(root, row):
    name = row.get("path")
    if (not isinstance(name, str) or PurePosixPath(name).is_absolute() or
            ".." in PurePosixPath(name).parts or str(PurePosixPath(name)) != name or name == "."):
        raise ValueError("unsafe fixed input path")
    path = root
    for part in PurePosixPath(name).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("fixed input symlink rejected")
    if not path.is_file() or path.stat().st_size > 8_000_000:
        raise ValueError("regular bounded fixed input required")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row.get("sha256"):
        raise ValueError("fixed input SHA256 mismatch")
    return data


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 100_000:
        raise ValueError("regular fixed manifest required")
    value = json.loads(path.read_text())
    if (set(value) != {"schema_version", "source_run", "upstream_commit", "artifact", "components",
                       "ca_archive", "http_inputs"}
            or value["schema_version"] != 2 or not re.fullmatch(r"[1-9]\d*", str(value["source_run"]))
            or not re.fullmatch(r"[0-9a-f]{40}", str(value["upstream_commit"]))
            or set(value["artifact"]) != {"id", "sha256"}
            or not re.fullmatch(r"[1-9]\d*", str(value["artifact"]["id"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(value["artifact"]["sha256"]))
            or set(value["components"]) != {"path", "sha256", "fingerprint"}
            or not isinstance(value["ca_archive"], dict)
            or set(value["ca_archive"]) != {"url", "path", "sha256"}
            or not isinstance(value["http_inputs"], list) or not value["http_inputs"]):
        raise ValueError("invalid fixed input manifest")
    component = components.validate(json.loads(payload(path.parent, value["components"])))
    if component["fingerprint"] != value["components"]["fingerprint"]:
        raise ValueError("fixed component fingerprint mismatch")
    ca_recipe = components.file_bytes(component["ca"]["files"], "Makefile").decode()
    ca_hash = components.field(ca_recipe, "PKG_HASH")
    archive_row = value["ca_archive"]
    archive_url = urlsplit(archive_row["url"])
    if (archive_url.scheme != "https" or archive_url.hostname != "snapshot.debian.org"
            or archive_url.username or archive_url.password or archive_url.port is not None
            or archive_url.query or archive_url.fragment
            or not re.fullmatch(r"/file/[0-9a-f]{40}", archive_url.path)
            or archive_row["url"] != "https://snapshot.debian.org" + archive_url.path
            or archive_row["sha256"] != ca_hash):
        raise ValueError("fixed CA archive identity differs from the pinned source recipe")
    ca_archive = payload(path.parent, archive_row)
    if hashlib.sha1(ca_archive).hexdigest() != archive_url.path.removeprefix("/file/"):
        raise ValueError("fixed CA archive does not match its Debian Snapshot content address")
    if hashlib.sha256(ca_archive).hexdigest() != ca_hash:
        raise ValueError("fixed CA archive differs from the pinned source SHA256")
    seen = set()
    paths = {"manifest.json", value["components"]["path"], archive_row["path"]}
    if len(paths) != 3:
        raise ValueError("duplicate fixed input path")
    for row in value["http_inputs"]:
        if set(row) != {"url", "path", "sha256"}:
            raise ValueError("invalid fixed HTTP input")
        url = urlsplit(row["url"])
        if (url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment
                or row["url"] in seen or row["path"] in paths):
            raise ValueError("unsafe or duplicate fixed HTTP input")
        seen.add(row["url"])
        paths.add(row["path"])
        payload(path.parent, row)
    return value


def verify(path, head, component_path):
    value = load(path)
    component_path = Path(component_path)
    if (head != value["upstream_commit"] or component_path.is_symlink() or
            not component_path.is_file() or hashlib.sha256(component_path.read_bytes()).hexdigest()
            != value["components"]["sha256"]):
        raise ValueError("runtime sources differ from fixed inputs")
    return value


def verify_http(path, log):
    value = load(path)
    expected = {row["url"]: row["sha256"] for row in value["http_inputs"]}
    rows = [json.loads(line) for line in Path(log).read_text().splitlines()]
    seen = set()
    empty = hashlib.sha256(b"").hexdigest()
    for row in rows:
        urls = row.get("urls")
        if not isinstance(urls, list) or len(urls) != 1 or urls[0] not in expected or urls[0] in seen:
            raise ValueError("fixed HTTP coverage mismatch")
        seen.add(urls[0])
        sha = expected[urls[0]]
        if (row.get("exit_code") != 0 or
                not ((row.get("stdout_sha256") == sha and "payload_sha256" not in row) or
                     (row.get("stdout_sha256") == empty and row.get("payload_sha256") == sha))):
            raise ValueError("fixed HTTP recorded bytes mismatch")
    if seen != set(expected):
        raise ValueError("fixed HTTP inputs were not all consumed")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select")
    select.add_argument("manifest", type=Path)
    select.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("verify")
    check.add_argument("manifest", type=Path)
    check.add_argument("--head", required=True)
    check.add_argument("--components", type=Path, required=True)
    check.add_argument("--capture", type=Path)
    http = sub.add_parser("verify-http")
    http.add_argument("manifest", type=Path)
    http.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "select":
        value = load(args.manifest)
        args.output.write_bytes(payload(args.manifest.parent, value["components"]))
    elif args.command == "verify-http":
        value = verify_http(args.manifest, args.log)
    else:
        value = verify(args.manifest, args.head, args.components)
        if args.capture:
            args.capture.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(args.manifest, args.capture / "manifest.json")
            for row in [value["components"], value["ca_archive"], *value["http_inputs"]]:
                dest = args.capture / row["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(payload(args.manifest.parent, row))
    print(json.dumps({"upstream_commit": value["upstream_commit"], "source_run": value["source_run"]}))


if __name__ == "__main__":
    main()
