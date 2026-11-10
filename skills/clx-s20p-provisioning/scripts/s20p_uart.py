#!/usr/bin/env python3
"""Receive-only UART capture and offline boot-log diagnosis. Never sends serial data or flashes."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import select
import stat
import termios
import time

from s20p_health import interpret


def analyze(text):
    """Report observed patterns; a historical log is not target identity or write permission."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text.replace("\r", ""))
    starts = re.findall(r"(?m)^.*\bLinux version \d[^\n]*", text)
    panics = re.findall(r"(?m)^.*Kernel panic - not syncing:\s*([^\n]*)", text)
    missing_pattern = r"(?:Error\s+2\s+opening|No such file[^\n]*)\s+/permanent_config/unique_id\b"
    segments = re.split(r"(?m)^.*\bLinux version \d[^\n]*", text)
    paired = 0
    for segment in segments:
        missing = re.search(missing_pattern, segment, re.I)
        panic = re.search(r"Kernel panic - not syncing:\s*very bad firmware\b", segment)
        paired += bool(missing and panic and missing.end() <= panic.start())
    return dict(kernel_start_count=len(starts), kernel_panic_count=len(panics),
                panic_reasons=sorted(set(panics)),
                repeated_panic_evidence=len(starts) >= 2 and len(panics) >= 2,
                missing_identity_open_errors=len(re.findall(missing_pattern, text, re.I)),
                missing_identity_then_panic_count=paired,
                failsafe_prompt_seen=bool(re.search(r"(?:Press|press)[^\n]*failsafe", text)),
                failsafe_active_marker_seen="FAILSAFE MODE" in text,
                link_up_count=len(re.findall(r"\bLink is Up\b", text)),
                link_down_count=len(re.findall(r"\bLink is Down\b", text)),
                storage_error_lines=interpret("0x01 0x01", "0x01", text)["storage_error_lines"],
                current_target_verified=False, hardware_health_proven=False, flash_authorized=False)


def capture(device, prefix, seconds, baud=115200):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Capture duration must be positive and finite")
    if not stat.S_ISCHR(os.stat(device).st_mode):
        raise ValueError("UART must be a character device, not a regular file")
    speed = getattr(termios, "B" + str(baud), None)
    if speed is None:
        raise ValueError("Unsupported local baud rate")
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    raw_path = prefix.with_name(prefix.name + ".bin")
    event_path = prefix.with_name(prefix.name + ".jsonl")
    fd, total, started = None, 0, time.monotonic()
    # Exclusive files and restrictive permissions keep old evidence and credentials private.
    with raw_path.open("xb") as raw, event_path.open("x") as events:
        def event(kind, **fields):
            value = dict(utc=datetime.now(timezone.utc).isoformat(),
                         elapsed_seconds=round(time.monotonic() - started, 6), event=kind, **fields)
            events.write(json.dumps(value) + "\n")
            events.flush()

        try:
            fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
            if not stat.S_ISCHR(os.fstat(fd).st_mode):
                raise ValueError("Device changed before open")
            fcntl.ioctl(fd, termios.TIOCEXCL)
            settings = termios.tcgetattr(fd)
            settings[0] = settings[1] = settings[3] = 0
            settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            settings[4] = settings[5] = speed
            settings[6][termios.VMIN] = settings[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, settings)
            event("ready", device=device, open_mode="O_RDONLY", baud=baud, framing="8N1", flow_control=False)
            print("READY: receive only; " + str(raw_path), flush=True)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], min(1, max(0, deadline - time.monotonic())))
                if not ready:
                    continue
                try:
                    block = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not block:
                    raise OSError("UART disconnected; partial evidence retained")
                raw.write(block)
                raw.flush()
                event("received", offset=total, bytes=len(block))
                total += len(block)
            event("ended", reason="duration_reached", total_bytes=total)
        except KeyboardInterrupt:
            event("ended", reason="interrupted", total_bytes=total)
        except BaseException as error:
            event("error", error=str(error), total_bytes=total)
            raise
        finally:
            if fd is not None:
                os.close(fd)
        print("STOPPED: total_bytes=" + str(total), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    cap = sub.add_parser("capture", help="Receive-only; leave adapter TXD and VCC disconnected")
    cap.add_argument("--device", required=True)
    cap.add_argument("--output-prefix", required=True, type=Path)
    cap.add_argument("--seconds", type=float, default=60)
    cap.add_argument("--baud", type=int, choices=[9600, 38400, 57600, 115200], default=115200)
    scan = sub.add_parser("analyze", help="Analyze an existing raw/text boot log offline")
    scan.add_argument("--log", required=True, type=Path)
    scan.add_argument("--output", type=Path, help="Create a new JSON file; never overwrite")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.mode == "capture":
            capture(args.device, args.output_prefix, args.seconds, args.baud)
        else:
            report = analyze(args.log.read_bytes().decode(errors="replace"))
            value = json.dumps(report, indent=2) + "\n"
            if args.output:
                with args.output.open("x") as output:
                    output.write(value)
            else:
                print(value, end="")
    except (OSError, ValueError) as error:
        parser.exit(1, "UART operation stopped: " + str(error) + "\n")


if __name__ == "__main__":
    main()
