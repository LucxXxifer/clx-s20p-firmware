#!/usr/bin/env python3
"""Offline inspection of S20P GPT captures. Never connects to or writes a device."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

SECTOR = 512


def require(condition, message):
    if not condition:
        raise ValueError(message)


def header(raw):
    require(len(raw) == SECTOR and raw[:8] == b"EFI PART", "Bad GPT header sector")
    revision, size, crc, reserved = struct.unpack_from("<IIII", raw, 8)
    require(revision == 0x10000 and 92 <= size <= SECTOR and reserved == 0,
            "Unsupported GPT revision/header size/reserved field")
    checked = bytearray(raw[:size])
    checked[16:20] = b"\0" * 4
    require(zlib.crc32(checked) & 0xffffffff == crc, "GPT header CRC mismatch")
    current, alternate, first, last = struct.unpack_from("<QQQQ", raw, 24)
    entries_lba, count, width, entries_crc = struct.unpack_from("<QIII", raw, 72)
    # Fail explicitly on unfamiliar layouts; never guess read/write ranges.
    require(count == 128 and width == 128, "Only reviewed 512-byte / 128 x 128-byte GPT is supported")
    return dict(size=size, header_crc=crc, current=current, alternate=alternate,
                first=first, last=last, guid=raw[56:72].hex(), entries_lba=entries_lba,
                count=count, width=width, entries_crc=entries_crc)


def inspect_primary(image, sectors, strict=False):
    """CRC/bounds checked evidence capture; stock self-reference is preserved."""
    require(100 < sectors < 2**63 and len(image) == 34 * SECTOR,
            "Primary capture must contain exactly the first 34 sectors")
    require(image[510:512] == b"\x55\xaa", "MBR signature missing")
    pmbr = [image[446 + i * 16:462 + i * 16] for i in range(4)]
    protective = [item for item in pmbr if item[4] == 0xee]
    require(len(protective) == 1, "Unique protective MBR entry missing")
    start, count = struct.unpack_from("<II", protective[0], 8)
    h = header(image[SECTOR:2 * SECTOR])
    require(h["current"] == 1 and h["entries_lba"] == 2, "Unsupported primary GPT location")
    require(1 <= h["alternate"] < sectors, "Primary alternate outside physical eMMC")
    require(34 <= h["first"] <= h["last"] <= sectors - 34, "Invalid GPT usable bounds")
    entries = image[2 * SECTOR:]
    require(zlib.crc32(entries) & 0xffffffff == h["entries_crc"], "Primary entry CRC mismatch")
    parts = []
    for index in range(h["count"]):
        item = entries[index * h["width"]:(index + 1) * h["width"]]
        if item[:16] == b"\0" * 16:
            continue
        begin, end = struct.unpack_from("<QQ", item, 32)
        require(h["first"] <= begin <= end <= h["last"], "Partition outside GPT usable region")
        require(not any(begin <= p["end"] and p["start"] <= end for p in parts), "Overlapping partitions")
        label = item[56:128].decode("utf-16-le").rstrip("\0")
        parts.append(dict(index=index + 1, label=label, start=begin, end=end, sectors=end - begin + 1))
    warnings = []
    if start != 1 or count != min(sectors - 1, 0xffffffff):
        warnings.append("PMBR does not describe the complete physical geometry")
    if h["alternate"] != sectors - 1:
        warnings.append("Original primary alternate is not the physical final LBA; preserved without repair")
    if h["last"] != sectors - 34:
        warnings.append("Original last usable LBA is below the physical maximum")
    if strict:
        require(not warnings, "; ".join(warnings))
        require(all(item[4] in (0, 0xee) for item in pmbr), "Hybrid MBR is unsupported")
    return dict(sectors=sectors, sector_size=SECTOR, primary=h, partitions=parts,
                warnings=warnings, header_and_entries_crc_valid=True), entries


def pair(first34, final33, sectors, allow_known_backpointer=False):
    result, entries = inspect_primary(first34, sectors, strict=True)
    p = result["primary"]
    require(len(final33) == 33 * SECTOR, "Secondary capture must be the physical final 33 sectors")
    s = header(final33[-SECTOR:])
    require((s["current"], s["entries_lba"]) == (sectors - 1, sectors - 33), "Secondary location mismatch")
    require(s["alternate"] == 1 or (allow_known_backpointer and s["alternate"] == sectors - 1),
            "Unexpected secondary alternate LBA")
    for key in ("first", "last", "guid", "count", "width", "entries_crc", "size"):
        require(s[key] == p[key], "Primary/secondary disagreement: " + key)
    require(final33[:-SECTOR] == entries, "Primary/secondary entries differ")
    require(zlib.crc32(final33[:-SECTOR]) & 0xffffffff == s["entries_crc"], "Secondary entry CRC mismatch")
    result.update(secondary=s, standard_backpointer=s["alternate"] == 1, all_crcs_valid=True,
                  repair_needed=s["alternate"] != 1)
    return result


def corrected_header(first34, final33, sectors):
    review = pair(first34, final33, sectors, allow_known_backpointer=True)
    require(review["repair_needed"], "GPT is already valid; no repair needed")
    patched = bytearray(final33[-SECTOR:])
    struct.pack_into("<Q", patched, 32, 1)
    struct.pack_into("<I", patched, 16, 0)
    struct.pack_into("<I", patched, 16, zlib.crc32(patched[:review["secondary"]["size"]]) & 0xffffffff)
    allowed = set(range(16, 20)) | set(range(32, 40))
    require(all(a == b or i in allowed for i, (a, b) in enumerate(zip(final33[-SECTOR:], patched))),
            "Repair exceeded the alternate-LBA and header-CRC fields")
    pair(first34, final33[:-SECTOR] + patched, sectors)
    return bytes(patched)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first34", required=True, type=Path)
    parser.add_argument("--final33", type=Path)
    parser.add_argument("--sectors", required=True, type=int)
    parser.add_argument("--stock-evidence", action="store_true", help="Inspect original bytes without requiring standard reciprocal pointers")
    parser.add_argument("--prepare-secondary-header", type=Path, help="Create a new local 512-byte candidate only for the known secondary self-reference")
    args = parser.parse_args()
    try:
        first = args.first34.read_bytes()
        if args.stock_evidence:
            require(not args.final33 and not args.prepare_secondary_header, "Stock evidence mode never repairs GPT")
            result, _ = inspect_primary(first, args.sectors)
        elif not args.final33:
            require(not args.prepare_secondary_header, "Repair needs both readbacks")
            result, _ = inspect_primary(first, args.sectors, strict=True)
            result["secondary_verified"] = False
        else:
            tail = args.final33.read_bytes()
            result = pair(first, tail, args.sectors, bool(args.prepare_secondary_header))
            if args.prepare_secondary_header and result["repair_needed"]:
                patched = corrected_header(first, tail, args.sectors)
                with args.prepare_secondary_header.open("xb") as output:
                    output.write(patched)
                result.update(candidate_only_not_written_to_device=str(args.prepare_secondary_header),
                              candidate_sha256=hashlib.sha256(patched).hexdigest())
            elif args.prepare_secondary_header:
                result["candidate_created"] = False
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (OSError, ValueError) as error:
        parser.exit(1, "GPT check stopped: " + str(error) + "\n")


if __name__ == "__main__":
    main()
