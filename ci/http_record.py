#!/usr/bin/env python3
"""Record public wrapper HTTP inputs without changing stdout or payload bytes.

Rolling mode records network responses. Fixed mode replays only reviewed GET
payloads and rejects unknown commands/URLs. Recipe and image gates still apply.
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
    fixed = os.environ.get("S20P_FIXED_INPUTS")
    with tempfile.TemporaryFile() as stdout:
        if fixed:
            from fixed_inputs import load, payload
            value = load(fixed)
            # Only the exact public GET forms used by this reviewed wrapper.
            # Do not ignore unknown flags or fall back to a mutable download.
            get = command[:]
            if get[:6] == ["curl", "--retry", "3", "--retry-delay", "2", "--retry-all-errors"]:
                get = ["curl", *get[6:]]
            elif get[:3] == ["wget", "--tries=3", "--waitretry=2"]:
                get = ["wget", *get[3:]]
            file_get = len(get) == 5 and get[:3] == ["curl", "-fsSL", "-o"] and get[3] != "-"
            stdout_get = len(get) == 3 and get[:2] == ["wget", "-qO-"]
            if not (file_get or stdout_get) or urls != [command[-1]]:
                raise ValueError("unreviewed fixed HTTP command")
            matches = [row for row in value["http_inputs"] if row["url"] == command[-1]]
            if len(matches) != 1:
                raise ValueError("unreviewed fixed HTTP URL")
            data = payload(Path(fixed).parent, matches[0])
            if file_get:
                Path(output).write_bytes(data)
            else:
                stdout.write(data)
            returncode = 0
        else:
            returncode = subprocess.run(command, stdout=stdout, check=False).returncode
        stdout.seek(0)
        digest = hashlib.sha256()
        while chunk := stdout.read(1024 * 1024):
            digest.update(chunk)
            sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    entry = {"urls": urls, "exit_code": returncode,
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
    return returncode


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[2] not in ("curl", "wget"):
        raise SystemExit("usage: http_record.py LOG {curl|wget} ARGS...")
    raise SystemExit(run(sys.argv[1], sys.argv[2:]))
