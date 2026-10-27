#!/usr/bin/env python3
"""Interpret saved eMMC estimates and kernel logs offline. Does not contact a device."""
import argparse
import json
from pathlib import Path
import re


def codes(value, count):
    fields = str(value).strip().split()
    if len(fields) != count or not all(re.fullmatch(r"0[xX][0-9a-fA-F]{1,2}", x) for x in fields):
        return None
    return [int(x, 16) for x in fields]


def interpret(life_time, pre_eol_info, kernel_log=None):
    life = codes(life_time, 2)
    eol_values = codes(pre_eol_info, 1)
    eol = eol_values[0] if eol_values else None
    reasons = []
    estimates = []
    for index in range(2):
        code = life[index] if life else None
        item = {"type": "AB"[index], "code": code, "estimated_used_endurance_percent_band": None}
        if code is None or code == 0 or code > 11:
            item["meaning"] = "unavailable_or_undefined_or_reserved"
            reasons.append("Type " + "AB"[index] + " endurance estimate is unknown")
        elif code == 11:
            item["meaning"] = "estimated_endurance_exceeded"
            reasons.append("Type " + "AB"[index] + " exceeded estimated endurance")
        else:
            item["meaning"] = "controller_estimated_used_endurance_band"
            item["estimated_used_endurance_percent_band"] = [(code - 1) * 10, code * 10]
            if code == 10:
                reasons.append("Type " + "AB"[index] + " is in the 90-100% used band")
        estimates.append(item)
    spare = {1: "normal", 2: "warning", 3: "urgent"}.get(eol, "unknown")
    if eol != 1:
        reasons.append("Spare-block condition is " + spare)
    errors = []
    if kernel_log is None:
        reasons.append("Storage kernel log was not read successfully")
    else:
        for line in kernel_log.splitlines():
            if (re.search(r"\bmmc\w*\b", line, re.I) and
                    re.search(r"\berror\b|\btimeout\b|timed out|crc error|\bfailed\b", line, re.I)) or re.search(
                        r"I/O error|Buffer I/O|blk_update_request.*error|(?:EXT4|F2FS)-fs.*(?:error|corrupt)", line, re.I):
                errors.append(line)
        if errors:
            reasons.append("Storage-related kernel errors require review")
    return dict(life_time_raw=life_time, pre_eol_info_raw=pre_eol_info, estimates=estimates,
                spare_block_condition=spare, storage_error_lines=errors,
                flash_gate_passed=not reasons, reasons=reasons,
                rescue_backup_allowed=True, precise_remaining_lifetime_known=False,
                hardware_test_complete=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--life-time", required=True)
    parser.add_argument("--pre-eol-info", required=True)
    parser.add_argument("--dmesg", type=Path)
    args = parser.parse_args()
    log = args.dmesg.read_text() if args.dmesg else None
    print(json.dumps(interpret(args.life_time, args.pre_eol_info, log), indent=2))


if __name__ == "__main__":
    main()
