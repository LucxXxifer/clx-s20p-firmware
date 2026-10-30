#!/usr/bin/env python3
"""Capture prepared source identities, including repositories moved by wrapper.

Do not scan live device backups. Only dedicated build input directories are read.
No environment, SSH material, signing private key or home directory is captured.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import source_lock

UPSTREAM_URL = "https://github.com/chasey-dev/immortalwrt-mt798x-rebase"
UPSTREAM_REF = "25.12"
UPSTREAM_BASE = "9f397975c7b47ce6986a6e39529d2f72fd5c729d"
UPSTREAM_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
WRAPPER = "d46704afb327fce99e7d770c08fc46b48d5bb1b7"
ROOT_EXCLUDE = {"bin", "build_dir", "staging_dir", "tmp", "logs", "dl", ".ccache", "output", "action_build", "immortalwrt", ".tools"}


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def source(path):
    remotes = git(path, "remote").splitlines()
    remote = git(path, "remote", "get-url", "origin" if "origin" in remotes else "upstream")
    if "@" in remote or not remote.startswith("https://"):
        raise ValueError("only public HTTPS source origins may be captured")
    canonical = source_lock.canonical_url(remote)
    return {"name": canonical.removeprefix("https://"), "url": canonical,
            "commit": git(path, "rev-parse", "HEAD"), "tree": git(path, "rev-parse", "HEAD^{tree}"),
            "tree_hash": hashlib.sha256(git(path, "ls-tree", "-r", "--full-tree", "HEAD").encode()).hexdigest(),
            "tree_hash_method": "SHA256 of recursive Git tree listing (paths, modes, content object IDs); prepared modifications separately hashed"}


def validate_upstream_resolution(lock, path):
    rows = lock.get("sources") if isinstance(lock, dict) else None
    if not isinstance(rows, list):
        raise ValueError("requested source lock has no source inventory")
    main = [row for row in rows if isinstance(row, dict) and row.get("role") == "main-source"]
    if len(main) != 1:
        raise ValueError("requested source lock requires one main-source")
    row = main[0]
    commit = row.get("commit")
    if (row.get("url", "").removesuffix(".git") != UPSTREAM_URL or
            row.get("ref") != UPSTREAM_REF or
            not UPSTREAM_COMMIT_RE.fullmatch(str(commit or "")) or
            source_lock.lookup(lock, UPSTREAM_URL, UPSTREAM_REF) != commit):
        raise ValueError("requested main-source does not match exact upstream repository/ref/SHA")
    if path is None:
        if commit == UPSTREAM_BASE:
            return commit, None
        raise ValueError("upstream resolution evidence is required for a moving source SHA")
    evidence_path = Path(path)
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise ValueError("regular upstream resolution evidence is required")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate upstream resolution evidence key: " + key)
            result[key] = value
        return result
    evidence = json.loads(evidence_path.read_text(), object_pairs_hook=unique)
    import upstream_sync
    try:
        upstream_sync.validate_build_evidence(evidence, commit,
                                             evidence_path.with_name("component-resolution.json"))
    except ValueError as error:
        raise ValueError("upstream resolution evidence does not bind the exact reviewed descendant SHA: " + str(error)) from error
    return commit, evidence


def file_manifest(root):
    entries = []
    repos = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        here = Path(parent)
        if ".git" in dirs or ".git" in files:
            repos.append(source(here))
        for name in sorted(dirs):
            child = here / name
            if child.is_symlink():
                entries.append({"path": str(child.relative_to(root)),
                                "mode": oct(child.lstat().st_mode & 0o7777),
                                "target": os.readlink(child)})
        excluded = {".git", "__pycache__"} | (ROOT_EXCLUDE if here == root else set())
        dirs[:] = sorted(d for d in dirs if d not in excluded and not (here / d).is_symlink())
        for name in sorted(files):
            path = here / name
            relative = str(path.relative_to(root))
            if name.startswith("key-build") or name == ".git":
                continue
            if name.endswith((".key", ".pem")):
                # Generated signing material is not a stable source input.
                # Public keys/certificates shipped by source keyrings are.
                if here == root or path.is_symlink():
                    continue
                with path.open("rb") as handle:
                    header = handle.read(96)
                if not header.startswith((b"-----BEGIN PUBLIC KEY-----", b"-----BEGIN CERTIFICATE-----")):
                    continue
            info = path.lstat()
            entry = {"path": relative, "mode": oct(info.st_mode & 0o7777)}
            if path.is_symlink():
                entry["target"] = os.readlink(path)
            elif path.is_file():
                entry.update(sha256=sha(path), size=info.st_size)
            else:
                raise ValueError(f"unexpected special input: {relative}")
            entries.append(entry)
    return entries, repos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--wrapper", type=Path)
    parser.add_argument("--upstream-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if str(args.checkout) == "record-clone":
        parser.error("record-clone requires its own CLI path")
    lock = json.loads(args.lock.read_text())
    try:
        expected_upstream, resolution = validate_upstream_resolution(lock, args.upstream_evidence)
    except (ValueError, source_lock.LockError, OSError, json.JSONDecodeError) as error:
        raise SystemExit("upstream resolution evidence rejected: " + str(error))
    if git(args.checkout, "rev-parse", "HEAD") != expected_upstream:
        raise SystemExit("upstream commit drift")
    inputs, repos = file_manifest(args.checkout)
    for row in repos:
        if source_lock.lookup(lock, row["url"]) != row["commit"]:
            raise SystemExit("prepared checkout source drift: " + row["url"])
    wrapper_files, _ = file_manifest(args.wrapper)
    wrapper_files = [e for e in wrapper_files if not e["path"].startswith(("action_build/", "immortalwrt/"))]
    log = Path(os.environ["S20P_GIT_LOG"])
    cloned = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    for row in cloned:
        if source_lock.lookup(lock, row["url"]) != row["commit"]:
            raise SystemExit("historical clone source drift: " + row["url"])
    encoded = json.dumps({"prepared": inputs, "wrapper": wrapper_files,
                          "lock": lock}, sort_keys=True).encode()
    fingerprint = hashlib.sha256(encoded).hexdigest()
    sources = {}
    for row in cloned + repos:
        previous = sources.get(row["url"])
        if previous and previous["commit"] != row["commit"]:
            raise SystemExit("multiple commits used for one locked repository: " + row["url"])
        sources[row["url"]] = row
    data = {"wrapper_commit": WRAPPER, "wrapper_checkout_commit": git(args.wrapper, "rev-parse", "HEAD"),
            "upstream_commit": expected_upstream, "upstream_resolution": resolution,
            "build_fingerprint": fingerprint,
            "wrapper_tree_hash": hashlib.sha256(json.dumps(wrapper_files, sort_keys=True).encode()).hexdigest(),
            "prepared_tree_hash": hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
            "sources": sorted(sources.values(), key=lambda row: row["name"]), "source_lock_sha256": sha(args.lock),
            "source_lock": lock,
            "original_feeds_recovered": False, "bit_identical_claim": False,
            "mutable_http_policy": "wrapper responses and downloaded recipe inputs recorded; version and file drift requires image review"}
    args.output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    args.output.with_name("prepared-source-files.json").write_text(json.dumps(inputs, indent=2) + "\n")
    args.output.with_name("wrapper-source-files.json").write_text(json.dumps(wrapper_files, indent=2) + "\n")


if __name__ == "__main__":
    if len(__import__("sys").argv) > 1 and __import__("sys").argv[1] == "record-clone":
        import sys
        parser = argparse.ArgumentParser()
        parser.add_argument("command")
        parser.add_argument("checkout", type=Path)
        parser.add_argument("--output", type=Path, required=True)
        args = parser.parse_args()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("a") as handle:
            handle.write(json.dumps(source(args.checkout), sort_keys=True) + "\n")
    else:
        main()
