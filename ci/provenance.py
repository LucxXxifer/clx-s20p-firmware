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
import subprocess
import source_lock

UPSTREAM = "9f397975c7b47ce6986a6e39529d2f72fd5c729d"
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if str(args.checkout) == "record-clone":
        parser.error("record-clone requires its own CLI path")
    if git(args.checkout, "rev-parse", "HEAD") != UPSTREAM:
        raise SystemExit("upstream commit drift")
    inputs, repos = file_manifest(args.checkout)
    lock = json.loads(args.lock.read_text())
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
            "upstream_commit": UPSTREAM, "build_fingerprint": fingerprint,
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
