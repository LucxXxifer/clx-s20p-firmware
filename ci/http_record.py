#!/usr/bin/env python3
"""Record public wrapper HTTP inputs without changing stdout or payload bytes.

This is provenance, not a claim that mutable release APIs are frozen. Recipe
hash validation and the image difference gate remain mandatory.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit


def public_url(url):
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ValueError("credential-bearing URL must not enter public provenance")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def run(log, command):
    urls = [public_url(a) for a in command if a.startswith(("https://", "http://"))]
    output = None
    for i, arg in enumerate(command):
        if arg in ("-o", "--output", "-O", "--output-document") and i + 1 < len(command):
            output = command[i + 1]
        elif arg.startswith("--output=") or arg.startswith("--output-document="):
            output = arg.split("=", 1)[1]
        elif command[0] == "wget" and arg.startswith("-qO"):
            output = arg[3:]
    with tempfile.TemporaryFile() as stdout:
        result = subprocess.run(command, stdout=stdout, check=False)
        stdout.seek(0)
        digest = hashlib.sha256()
        while chunk := stdout.read(1024 * 1024):
            digest.update(chunk)
            sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    entry = {"urls": urls, "exit_code": result.returncode,
             "stdout_sha256": digest.hexdigest()}
    if output and output != "-" and Path(output).is_file():
        digest = hashlib.sha256()
        with open(output, "rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        entry.update(payload_sha256=digest.hexdigest(), payload_bytes=Path(output).stat().st_size)
    target = Path(log)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return result.returncode


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[2] not in ("curl", "wget"):
        raise SystemExit("usage: http_record.py LOG {curl|wget} ARGS...")
    raise SystemExit(run(sys.argv[1], sys.argv[2:]))
