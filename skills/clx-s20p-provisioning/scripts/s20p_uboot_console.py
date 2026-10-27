"""Reviewed HTTP-console protocol, parameterized by target/interface. No command retries.

Library only. The caller must first verify identity, authorization and exclusive console
ownership. This protocol was observed on one FIP family; revalidate on a changed build.
"""
import gzip
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit


class Console:
    def __init__(self, directory, base_url, interface, prompt="MT7986> "):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or
                parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Use the verified isolated U-Boot HTTP origin")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]*", interface):
            raise ValueError("Specify the discovered direct-link interface")
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise ValueError("Prepare an operation transcript directory")
        self.base_url, self.interface, self.prompt = base_url.rstrip("/"), interface, prompt

    def request(self, endpoint, fields):
        if endpoint not in ("/console/exec", "/console/poll"):
            raise ValueError("Unreviewed console endpoint")
        args = ["curl", "--noproxy", "*", "--interface", self.interface, "--connect-timeout", "4",
                "--max-time", "20", "-fsS", self.base_url + endpoint, "-X", "POST"]
        for key, value in fields.items():
            args += ["--form-string", key + "=" + value]
        result = subprocess.run(args, capture_output=True, timeout=25)
        if result.returncode:
            raise RuntimeError("Console HTTP response failed; inspect state, never retry writes: " + result.stderr.decode(errors="replace")[:500])
        body = result.stdout
        if body.startswith(b"\x1f\x8b"):
            body = gzip.decompress(body)
        return json.loads(body)

    def run(self, command, name):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or len(command.encode()) > 255 or "\n" in command or "\r" in command:
            raise ValueError("Use a unique transcript name and one reviewed short command")
        record = dict(command=command, base_url=self.base_url, interface=self.interface, exec=None, polls=[])
        # Allocate before execution. Existing records always prevent accidental re-execution.
        with (self.directory / (name + ".json")).open("x") as output:
            try:
                record["exec"] = self.request("/console/exec", {"cmd": command})
                for _ in range(12):
                    polled = self.request("/console/poll", {})
                    record["polls"].append(polled)
                    if polled.get("data") and not polled.get("avail"):
                        break
                    time.sleep(0.1)
            except BaseException as error:
                record["failure"] = str(error)
                raise
            finally:
                json.dump(record, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
        response = record["exec"]
        if response.get("ok") is not True or response.get("cmd") != command:
            raise RuntimeError("Console exec response mismatch; do not repeat a possible write")
        if response.get("ret") != 0:
            raise RuntimeError("Nonzero U-Boot return; review transcript: " + str(response.get("ret")))
        if any(item.get("overflow") for item in record["polls"]) or record["polls"][-1].get("avail"):
            raise RuntimeError("Incomplete/overflowed console output; inspect device state")
        text = "".join(item.get("data", "") for item in record["polls"])
        if self.prompt + command not in text:
            raise RuntimeError("Console echo missing: another browser may consume output; do not retry writes")
        return text
