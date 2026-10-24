#!/usr/bin/env python3
"""Fail-closed, offline preservation gates for the pinned dqsq S20P rebuild.

Exit 0: checks in this phase passed; 1: missing/invalid evidence or invariant
failure; 2: exact, hash-addressed differences still require review. This checks
build artifacts, not boot, hardware behavior, or bit-identical reproduction.
"""

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def read_json(path):
    return json.loads(Path(path).read_text())


def parse_config(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        match = re.fullmatch(r"(CONFIG_[A-Za-z0-9_+.-]+)=(.*)", line.strip())
        unset = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_+.-]+) is not set", line.strip())
        if not match and not unset:
            continue
        key, value = match.groups() if match else (unset.group(1), "n")
        if key in values and values[key] != value:
            raise ValueError("Conflicting config assignments: " + key)
        values[key] = value
    if not values:
        raise ValueError("Empty or unrecognized config: " + str(path))
    return values


def package_selectors(path):
    """Map emitted APK names to real recipe selectors from tmp/.packageinfo.

    Pinned upstream include/feeds.mk FormatABISuffix adds ABI-Version to
    non-kmods, with a separating '-' only when the recipe ends in a digit.
    Provides aliases do not establish that an original runtime package exists.
    """
    rows, current = [], None
    for line in Path(path).read_text().splitlines():
        if line.startswith("Package: "):
            current = {"Package": line[len("Package: "):].strip()}
            rows.append(current)
        elif line == "@@":
            current = None
        elif current is not None and line.startswith("ABI-Version: "):
            if "ABI-Version" in current:
                raise ValueError("Duplicate package metadata ABI-Version")
            current["ABI-Version"] = line[len("ABI-Version: "):].strip()
    if not rows:
        raise ValueError("Empty or unrecognized package metadata: " + str(path))
    result = {}
    for row in rows:
        name, abi = row["Package"], row.get("ABI-Version", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", name):
            raise ValueError("Invalid package metadata name: " + name)
        if abi and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", abi):
            raise ValueError("Invalid package metadata ABI-Version: " + name)
        suffix = ("-" if name[-1].isdigit() else "") + abi if abi else ""
        runtime = name if name.startswith("kmod-") else name + suffix
        if runtime in result and result[runtime] != name:
            raise ValueError("ambiguous package metadata runtime name: " + runtime)
        result[runtime] = name
    return result


def parse_packages(path):
    """Read native APK installed DB, release manifest, TSV, or explicit JSON."""
    raw = Path(path).read_text()
    if raw.lstrip().startswith(("[", "{")):
        rows = json.loads(raw)
        if isinstance(rows, dict):
            rows = rows["packages"]
        rows = [({"name": row[0], "version": row[1], "depends": row[2]}
                 if isinstance(row, list) else row) for row in rows]
    elif re.search(r"^P:", raw, re.M):
        rows = []
        for block in re.split(r"\n\s*\n", raw.strip()):
            fields = {}
            package_files, directory = [], ""
            for line in block.splitlines():
                if len(line) > 1 and line[1] == ":" and line[0] in "PVDp":
                    if line[0] in fields:
                        raise ValueError("Duplicate APK field: " + line[0])
                    fields[line[0]] = line[2:]
                elif line.startswith("F:"):
                    directory = line[2:]
                elif line.startswith("R:"):
                    package_files.append("/" + str(PurePosixPath(directory) / line[2:]).lstrip("/"))
            if fields:
                rows.append({"name": fields["P"], "version": fields["V"],
                             "depends": fields.get("D", ""), "provides": fields.get("p", ""),
                             "files": package_files})
    elif raw.startswith("name\t"):
        rows = list(csv.DictReader(io.StringIO(raw), delimiter="\t"))
    else:
        rows = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"([^\s]+)\s+-\s+([^\s]+)", line.strip())
            if not match:
                raise ValueError("Unrecognized package record: " + line[:120])
            rows.append({"name": match[1], "version": match[2]})
    packages = {}
    for row in rows:
        name, version = row["name"], row["version"]
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise ValueError("Package name and version must be nonempty strings")
        if name in packages:
            raise ValueError("Duplicate package: " + name)
        package = {"name": name, "version": version}
        depends = row.get("depends", row.get("dependencies"))
        if depends is not None:
            package["depends"] = " ".join(depends) if isinstance(depends, list) else depends
        package["provides"] = row.get("provides", "")
        if "files" in row:
            package["files"] = row["files"]
        packages[name] = package
    if not packages:
        raise ValueError("Empty package inventory")
    return packages


def dependency_names(depends):
    return {re.split(r"[<>=~]", item)[0]
            for token in depends.split() if not token.startswith("!")
            for item in token.split("|")}


def parse_dtb(path):
    """Decode real flattened DT nodes/properties without executing target tools."""
    data = Path(path).read_bytes()
    if len(data) < 40:
        raise ValueError("Truncated DTB header")
    magic, total, start, strings, _, version, _, _, string_size, tree_size = struct.unpack(">10I", data[:40])
    if magic != 0xD00DFEED or version < 16 or total > len(data):
        raise ValueError("Invalid DTB magic/version/size")
    if min(start, strings) < 40 or start + tree_size > total or strings + string_size > total:
        raise ValueError("Invalid DTB section bounds")
    table = data[strings:strings + string_size]
    nodes, stack = {}, []
    pos, limit, ended = start, start + tree_size, False
    while pos + 4 <= limit:
        token = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        if token == 1:
            end = data.find(b"\0", pos, limit)
            if end < 0:
                raise ValueError("Unterminated DTB node")
            name = data[pos:end].decode()
            if "/" in name or (not stack and name):
                raise ValueError("Invalid DTB node name")
            stack.append(name)
            node = "/" + "/".join(stack[1:])
            if node in nodes:
                raise ValueError("Duplicate DTB node: " + node)
            nodes[node] = {}
            pos = (end + 4) & ~3
        elif token == 2:
            if not stack:
                raise ValueError("Unbalanced DTB end-node")
            stack.pop()
        elif token == 3:
            if not stack or pos + 8 > limit:
                raise ValueError("Invalid DTB property")
            size, offset = struct.unpack_from(">II", data, pos)
            pos += 8
            if offset >= len(table) or pos + size > limit:
                raise ValueError("Invalid DTB property bounds")
            name_end = table.find(b"\0", offset)
            if name_end < 0:
                raise ValueError("Unterminated DTB property name")
            name = table[offset:name_end].decode()
            props = nodes["/" + "/".join(stack[1:])]
            if name in props:
                raise ValueError("Duplicate DTB property: " + name)
            props[name] = data[pos:pos + size]
            pos = (pos + size + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            if stack:
                raise ValueError("Unclosed DTB nodes")
            ended = True
            break
        else:
            raise ValueError("Unknown DTB token: " + str(token))
    if not ended or "/" not in nodes:
        raise ValueError("Incomplete DTB structure")
    return nodes


def semantic_dtb(nodes):
    """Normalize documented phandle references; unknown properties stay exact.

    Unknown reference formats fail conservatively on changed bytes rather than
    assuming arbitrary integers are references and hiding a hardware change.
    """
    handles = {}
    for path, props in nodes.items():
        for key in ("phandle", "linux,phandle"):
            if key in props:
                if len(props[key]) != 4:
                    raise ValueError("Invalid phandle size")
                handle = struct.unpack(">I", props[key])[0]
                if handle in handles and handles[handle] != path:
                    raise ValueError("Duplicate phandle")
                handles[handle] = path
    arrays = {"clocks": "#clock-cells", "assigned-clocks": "#clock-cells",
              "assigned-clock-parents": "#clock-cells", "resets": "#reset-cells",
              "phys": "#phy-cells", "dmas": "#dma-cells", "pwms": "#pwm-cells",
              "iommus": "#iommu-cells", "power-domains": "#power-domain-cells",
              "interrupts-extended": "#interrupt-cells"}
    single = {"phy-handle", "interrupt-parent", "memory-region", "nvmem-cells",
              "cpu", "ethernet", "mdio", "msi-parent", "mediatek,ethsys",
              "mediatek,infracfg", "mediatek,wed", "mediatek,wed2"}
    result = {}
    for path, props in nodes.items():
        result[path] = {}
        for key, value in props.items():
            if key in ("phandle", "linux,phandle"):
                continue
            gpio = key == "gpios" or key.endswith("-gpios")
            cells_key = "#gpio-cells" if gpio else arrays.get(key)
            reference = cells_key or key in single or key.endswith("-supply") or re.fullmatch(r"pinctrl-\d+", key)
            if path.startswith(("/__symbols__", "/__fixups__", "/__local_fixups__")):
                reference = False
            if not reference:
                result[path][key] = value.hex()
                continue
            if len(value) % 4:
                raise ValueError("Unaligned phandle reference: " + key)
            cells = list(struct.unpack(">" + "I" * (len(value) // 4), value))
            normalized, index = [], 0
            while index < len(cells):
                handle = cells[index]
                index += 1
                if handle == 0:
                    normalized.append(None)
                    continue
                if handle not in handles:
                    raise ValueError("Unresolved phandle in " + path + ":" + key)
                provider = handles[handle]
                count = 0
                if cells_key:
                    count_bytes = nodes[provider].get(cells_key)
                    if count_bytes is None or len(count_bytes) != 4:
                        raise ValueError("Missing provider " + cells_key + " at " + provider)
                    count = struct.unpack(">I", count_bytes)[0]
                if index + count > len(cells):
                    raise ValueError("Truncated phandle arguments")
                normalized.append({"node": provider, "args": cells[index:index + count]})
                index += count
            result[path][key] = normalized
    return result


class Gate:
    def __init__(self, phase, baseline, approval=None):
        self.phase = phase
        self.baseline = Path(baseline)
        self.errors, self.differences, self.checks = [], [], {}
        self.approvals = {}
        if approval:
            for item in read_json(approval)["approved"]:
                if not re.fullmatch(r"[0-9a-f]{64}", item["id"]) or not item.get("reason", "").strip():
                    raise ValueError("Approval requires an exact difference ID and review reason")
                self.approvals[item["id"]] = item["reason"]
        self.policy = read_json(self.baseline / "policy.json")
        integrity = self.baseline / "baseline-sha256.json"
        if integrity.exists():
            for name, expected in read_json(integrity).items():
                if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise ValueError("Invalid baseline checksum entry")
                actual = digest((self.baseline / name).read_bytes())
                if expected != actual:
                    self.error("baseline_checksum", name, expected, actual)
        elif self.policy.get("integrity_required"):
            self.error("missing_baseline_checksum", "baseline-sha256.json")
        self.original = parse_packages(self.baseline / "original-package-manifest.json")
        self.mtk = parse_config(self.baseline / "baseline-mtk-config.txt")
        counts = {"packages": len(self.original),
                  "kmods": sum(name.startswith("kmod-") for name in self.original),
                  "luci_apps": sum(name.startswith("luci-app-") for name in self.original),
                  "mtk_symbols": len(self.mtk)}
        for key, value in counts.items():
            if value != self.policy["counts"][key]:
                self.error("baseline_count", key, self.policy["counts"][key], value)
        self.checks["baseline_counts"] = counts

    def error(self, category, subject, expected=None, actual=None):
        self.errors.append({"category": category, "subject": subject,
                            "expected": expected, "actual": actual})

    def difference(self, category, subject, expected, actual, disposition="review_required", reason=""):
        record = {"category": category, "subject": subject, "expected": expected,
                  "actual": actual, "reason": reason}
        identifier = digest(canonical(record))
        record["id"] = identifier
        record["disposition"] = disposition
        if disposition == "review_required" and identifier in self.approvals:
            record["disposition"] = "approved"
            record["approval_reason"] = self.approvals[identifier]
        self.differences.append(record)

    def config(self, path, optional_packages=None, package_info=None):
        values = parse_config(path)
        required = dict(self.policy["required_config"])
        required.update(self.mtk)
        unsupported = self.policy.get("unsupported_fragment_symbols", {})
        for symbol, review in unsupported.items():
            if (not isinstance(review, dict) or not review.get("reason") or
                    required.get(symbol) != review.get("input_value")):
                raise ValueError("Invalid unsupported fragment symbol policy: " + symbol)
            del required[symbol]
            if values.get(symbol, "n") != "n":
                self.error("unsupported_fragment_symbol", symbol,
                           "absent/disabled after defconfig; not an enabled feature", values[symbol])
        metadata = package_selectors(package_info) if package_info else None
        selectors = {}
        # Multi-profile/per-device rootfs may build original extras as =m;
        # only the actual image gate can establish that they were installed.
        for name in self.original:
            selector = "CONFIG_PACKAGE_" + name
            if metadata is not None:
                if name == "kernel":
                    # kernel is implicitly emitted, not a selectable recipe.
                    release = self.policy["kernel_version"].split(".")[:2]
                    selector = "CONFIG_LINUX_" + "_".join(release)
                    required[selector] = "y"
                elif name not in metadata:
                    self.error("package_metadata", name, "exact runtime APK name in recipe metadata", "MISSING")
                else:
                    selector = "CONFIG_PACKAGE_" + metadata[name]
            selectors[name] = selector
            required.setdefault(selector, ["y", "m"])
        required.update({"CONFIG_PACKAGE_" + name: "y" for name in self.policy["required_packages"]})
        optional = []
        if optional_packages:
            for line in Path(optional_packages).read_text().splitlines():
                name = line.split("#", 1)[0].strip()
                if not name:
                    continue
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", name) or name in optional:
                    raise ValueError("Invalid or duplicate optional package: " + name)
                optional.append(name)
                # A shared dependency may already be preinstalled. Never relax
                # stronger original/increment requirements to optional status.
                required.setdefault("CONFIG_PACKAGE_" + name, ["y", "m"])
            if not optional:
                raise ValueError("Empty optional package list: " + str(optional_packages))
        for key, expected in sorted(required.items()):
            allowed = expected if isinstance(expected, list) else [expected]
            value = values.get(key, "n")
            if value not in allowed:
                self.error("config_symbol", key, expected, values.get(key, "MISSING"))
        selected = {key[len("CONFIG_PACKAGE_"):] for key, value in values.items()
                    if key.startswith("CONFIG_PACKAGE_") and value == "y"}
        self.checks["config"] = {"sha256": digest(Path(path).read_bytes()),
                                 "required_symbols": len(required),
                                 "optional_packages": sorted(optional),
                                 "optional_packages_list_sha256": (digest(Path(optional_packages).read_bytes())
                                                                   if optional_packages else None),
                                 "package_info_sha256": (digest(Path(package_info).read_bytes())
                                                         if package_info else None),
                                 "original_package_selectors": selectors,
                                 "unsupported_fragment_symbols": unsupported,
                                 "additional_selected_package_symbols": sorted(selected - set(self.original)),
                                 "scope": "required symbols only; original expanded config is unavailable"}

    def source_lock(self, path):
        if not path:
            self.error("source_lock_missing", "source-lock.json")
            return False
        lock = read_json(path)
        count = len(self.errors)
        for key in ("wrapper_commit", "upstream_commit"):
            if lock.get(key) != self.policy[key]:
                self.error("source_lock", key, self.policy[key], lock.get(key))
        for key in ("wrapper_tree_hash", "build_fingerprint"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(lock.get(key, ""))):
                self.error("source_lock", key, "new SHA256", lock.get(key))
        if lock.get("build_fingerprint") == self.policy["original_fingerprint"]:
            self.error("original_fingerprint", "build_fingerprint", "new build identity",
                       lock["build_fingerprint"])
        sources = lock.get("sources")
        if not isinstance(sources, list) or not sources:
            self.error("source_lock", "sources", "nonempty source inventory", sources)
        else:
            names, urls = set(), set()
            for item in sources:
                if not isinstance(item, dict):
                    self.error("source_lock", "source_identity", "source object", item)
                    continue
                name = item.get("name")
                url = item.get("url")
                if (not isinstance(name, str) or not name or name in names or
                        not isinstance(url, str) or not url or url in urls):
                    self.error("source_lock", "source_identity", "unique name and URL", item)
                if isinstance(name, str):
                    names.add(name)
                if isinstance(url, str):
                    urls.add(url)
                if not re.fullmatch(r"[0-9a-f]{40}", str(item.get("commit", ""))):
                    self.error("source_lock", str(name) + ".commit", "full Git commit", item.get("commit"))
                if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("tree_hash", ""))):
                    self.error("source_lock", str(name) + ".tree_hash", "SHA256", item.get("tree_hash"))
        self.checks["source_lock"] = {"sha256": digest(Path(path).read_bytes()),
                                      "recorded_sources": len(sources) if isinstance(sources, list) else 0,
                                      "build_fingerprint": lock.get("build_fingerprint"),
                                      "scope": "headers/shape only; exact original resolved feeds/assets unknown"}
        if isinstance(sources, list):
            for source in sources:
                if not isinstance(source, dict):
                    continue
                self.difference("resolved_source", source.get("name", "MISSING"),
                                "original resolved revision unknown", source, "expected",
                                "new build lock; not evidence of original feed/asset equality")
        return count == len(self.errors)

    def packages(self, root, inventory):
        installed = safe_path(root, "/lib/apk/db/installed")
        actual = parse_packages(installed)
        manifest = parse_packages(inventory)
        if {k: p["version"] for k, p in manifest.items()} != {k: p["version"] for k, p in actual.items()}:
            self.error("package_db_mismatch", "build manifest vs actual APK DB",
                       {k: p["version"] for k, p in manifest.items()},
                       {k: p["version"] for k, p in actual.items()})
        for name in sorted(set(self.original) | set(self.policy["required_packages"])):
            if name not in actual:
                self.error("missing_package", name)
        kernel = actual.get("kernel", {}).get("version", "")
        if kernel == self.policy["original_kernel"]:
            self.error("original_abi", "kernel", "new ABI from increment build", kernel)
        if not re.fullmatch(re.escape(self.policy["kernel_version"]) + r"~[0-9a-f]{32}-r\d+", kernel):
            self.error("kernel_version", "kernel", self.policy["kernel_version"] + "~NEW_ABI-rN", kernel)
        for name, package in sorted(actual.items()):
            if name.startswith("kmod-"):
                matches = re.findall(r"(?:^|\s)kernel=([^\s]+)", package.get("depends", ""))
                if matches != [kernel]:
                    self.error("kernel_dependency", name, [kernel], matches)
                for filename in package.get("files", []):
                    if not filename.endswith(".ko"):
                        continue
                    module = safe_path(root, filename)
                    if not module.is_file() or module.is_symlink():
                        self.error("missing_kmod_file", name, filename, "missing/not regular")
                        continue
                    data = module.read_bytes()
                    vermagic = re.search(rb"(?:^|\x00)vermagic=([^\x00]+)\x00", data)
                    if not data.startswith(b"\x7fELF") or not vermagic:
                        self.error("module_vermagic", filename, "ELF module with vermagic", "missing")
                    elif vermagic[1].split()[0].decode() != self.policy["kernel_version"]:
                        self.error("module_vermagic", filename, self.policy["kernel_version"],
                                   vermagic[1].decode(errors="replace"))
        reachable = set(self.policy["required_packages"])
        pending = list(reachable)
        while pending:
            name = pending.pop()
            for dependency in dependency_names(actual.get(name, {}).get("depends", "")):
                if dependency in actual and dependency not in reachable:
                    reachable.add(dependency)
                    pending.append(dependency)
        for name, package in sorted(actual.items()):
            original = self.original.get(name)
            if original is None:
                self.difference("added_package", name, None, package,
                                "expected" if name in reachable else "review_required",
                                "requested increment dependency closure" if name in reachable else "outside increment closure")
                continue
            if package["version"] != original["version"]:
                self.difference("package_version", name, original["version"], package["version"],
                                "expected" if name == "kernel" and kernel != self.policy["original_kernel"] else "review_required",
                                "same-build generated ABI" if name == "kernel" else "source/version delta")
            old_deps = original.get("depends", "").replace("kernel=" + self.policy["original_kernel"], "kernel=" + kernel)
            if sorted(old_deps.split()) != sorted(package.get("depends", "").split()):
                self.difference("package_dependencies", name, old_deps, package.get("depends", ""))
        self.checks["packages"] = {"actual_count": len(actual), "kernel": kernel,
                                   "original_count": len(self.original),
                                   "additional_packages": sorted(set(actual) - set(self.original)),
                                   "kmods": sum(n.startswith("kmod-") for n in actual),
                                   "luci_apps": sum(n.startswith("luci-app-") for n in actual)}
        return actual, reachable

    def kernel_config(self, path):
        values = parse_config(path)
        for key, expected in self.policy["required_kernel_config"].items():
            allowed = expected if isinstance(expected, list) else [expected]
            if values.get(key) not in allowed:
                self.error("kernel_config", key, allowed, values.get(key, "MISSING"))
        self.checks["kernel_config"] = {"sha256": digest(Path(path).read_bytes()),
                                        "required_symbols": len(self.policy["required_kernel_config"]),
                                        "scope": "required invariants, not unavailable original full kernel config"}

    def dtb(self, path):
        original = semantic_dtb(parse_dtb(self.baseline / "original-board.dtb"))
        actual = semantic_dtb(parse_dtb(path))
        for node in sorted(set(original) | set(actual)):
            if original.get(node) != actual.get(node):
                self.error("dtb_semantics", node, original.get(node), actual.get(node))
        self.checks["dtb"] = {"original_sha256": digest((self.baseline / "original-board.dtb").read_bytes()),
                               "actual_sha256": digest(Path(path).read_bytes()),
                               "compared_nodes": len(original),
                               "scope": "all nodes/properties; known phandle references normalized, unknown formats exact"}

    def files(self, root, provenance_ok, packages, reachable):
        baseline = file_index(read_json(self.baseline / "full-rootfs-manifest.json")["files"])
        critical = file_index(read_json(self.baseline / "critical-files-manifest.json")["files"])
        if not baseline or not critical or not set(critical).issubset(baseline):
            raise ValueError("Invalid/incomplete rootfs baseline")
        expected_count = self.policy.get("rootfs_entries")
        if expected_count and len(baseline) != expected_count:
            self.error("baseline_count", "rootfs_entries", expected_count, len(baseline))
        if self.policy.get("critical_entries") and len(critical) != self.policy["critical_entries"]:
            self.error("baseline_count", "critical_entries", self.policy["critical_entries"], len(critical))
        actual = walk_files(root)
        for name, expected in baseline.items():
            # Reject redirected parent directories even when walk() correctly
            # avoided following the link and the descendant appears missing.
            safe_path(root, name)
            current = actual.get(name)
            if current is None:
                self.error("missing_file", name)
                continue
            if current["type"] != expected["type"]:
                self.error("file_type", name, expected["type"], current["type"])
                continue
            policy = critical.get(name, {}).get("policy", "report-all-differences")
            for field in ("mode", "uid", "gid", "target", "device_major", "device_minor"):
                if expected.get(field) != current.get(field):
                    self.difference("file_" + field, name, expected.get(field), current.get(field))
            if expected.get("sha256") == current.get("sha256"):
                continue
            path = safe_path(root, name)
            recompiled = policy == "recompiled-version-config-abi-equivalence"
            is_elf = current["type"] == "file" and read_prefix(path, 4) == b"\x7fELF"
            generated = name.startswith("/lib/apk/") or bool(re.match(r"/lib/modules/[^/]+/modules\.", name))
            disposition, reason = "review_required", policy
            if policy.startswith("exact-"):
                pass
            elif generated and provenance_ok:
                disposition, reason = "expected", "generated metadata; actual package DB and kernel dependencies checked"
            elif (recompiled or is_elf) and provenance_ok:
                disposition, reason = "expected", "recompiled binary; recorded sources/config/ABI, not byte-identical claim"
            elif policy.startswith("original-package-set"):
                world = {re.split(r"[<>=~]", line.strip())[0]
                         for line in path.read_text().splitlines() if line.strip()}
                # World lists explicit roots, not all transitive packages.
                self.checks["apk_world"] = {"roots": sorted(world)}
                reason = "world roots changed; package closure checked but root selection needs explicit review"
            self.difference("file_sha256", name, expected.get("sha256"), current.get("sha256"), disposition, reason)
        additions = sorted(set(actual) - set(baseline))
        for name in additions:
            current = actual[name]
            generated = name.startswith("/lib/apk/") or current["type"] == "directory"
            self.difference("added_file", name, None, current,
                            "expected" if generated else "review_required",
                            "explicit new rootfs entry; verify owning added package" if not generated else "generated rootfs metadata/directory")
        self.checks["rootfs"] = {"baseline_entries": len(baseline), "actual_entries": len(actual),
                                  "critical_entries": len(critical), "added_entries": len(additions),
                                  "xattrs": "not checked; original baseline did not inventory xattrs",
                                  "mtime": "not an equivalence requirement"}

    def result(self):
        pending = [r for r in self.differences if r["disposition"] == "review_required"]
        status = "fail" if self.errors else "review_required" if pending else "pass"
        return {"schema_version": 1, "phase": self.phase, "status": status,
                "errors": self.errors, "differences": self.differences,
                "checks": self.checks,
                "pending_review_count": len(pending),
                "claims": {"bit_identical_original": False, "hardware_or_boot_tested": False,
                           "scope": "this phase's checked artifact invariants only"},
                "unverified": ["original expanded configs and resolved feed/asset/environment equality",
                               "original xattrs", "boot, hardware and service behavior"]}


def read_prefix(path, size):
    with path.open("rb") as stream:
        return stream.read(size)


def safe_path(root, name):
    parts = PurePosixPath(name)
    if not name.startswith("/") or ".." in parts.parts or "\0" in name:
        raise ValueError("Invalid rootfs path: " + name)
    current = root
    for part in parts.parts[1:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink_parent: " + name)
    return root.joinpath(*parts.parts[1:])


def file_index(records):
    result = {}
    for record in records:
        name = record["path"]
        if name in result:
            raise ValueError("Duplicate rootfs record: " + name)
        safe_path(Path("/unused"), name)
        result[name] = record
    return result


def file_record(root, path):
    info = path.lstat()
    record = {"path": "/" + path.relative_to(root).as_posix() if path != root else "/",
              "mode": format(stat.S_IMODE(info.st_mode), "04o"),
              "uid": info.st_uid, "gid": info.st_gid, "sha256": None, "size": 0}
    if stat.S_ISLNK(info.st_mode):
        record.update(type="symlink", target=os.readlink(path))
        content = record["target"].encode()
        record.update(sha256=digest(content), size=len(content))
    elif stat.S_ISREG(info.st_mode):
        sha = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha.update(chunk)
        record.update(type="file", sha256=sha.hexdigest(), size=info.st_size)
    elif stat.S_ISDIR(info.st_mode):
        record["type"] = "directory"
    elif stat.S_ISCHR(info.st_mode) or stat.S_ISBLK(info.st_mode):
        record.update(type="char-device" if stat.S_ISCHR(info.st_mode) else "block-device",
                      device_major=os.major(info.st_rdev), device_minor=os.minor(info.st_rdev))
    else:
        raise ValueError("Unsupported rootfs inode: " + str(path))
    return record


def walk_files(root):
    if not root.is_dir():
        raise ValueError("rootfs must be an extracted directory")
    records = {"/": file_record(root, root)}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            record = file_record(root, path)
            records[record["path"]] = record
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    phases = parser.add_subparsers(dest="phase", required=True)
    for phase in ("config", "image"):
        command = phases.add_parser(phase)
        command.add_argument("--baseline-dir", required=True, type=Path)
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--approve-differences", type=Path)
        if phase == "config":
            command.add_argument("--config", required=True, type=Path)
            command.add_argument("--optional-packages", type=Path,
                                 help="package names that must survive defconfig as m or y")
            command.add_argument("--package-info", type=Path,
                                 help="actual tmp/.packageinfo for exact runtime ABI-to-recipe names")
        else:
            command.add_argument("--rootfs", required=True, type=Path)
            command.add_argument("--kernel-config", required=True, type=Path)
            command.add_argument("--packages", required=True, type=Path)
            command.add_argument("--dtb", required=True, type=Path)
            command.add_argument("--source-lock", type=Path)
    args = parser.parse_args(argv)
    gate = None
    try:
        gate = Gate(args.phase, args.baseline_dir, args.approve_differences)
        if args.phase == "config":
            gate.config(args.config, args.optional_packages, args.package_info)
        else:
            root = args.rootfs.resolve()
            provenance_ok = gate.source_lock(args.source_lock)
            actual, reachable = gate.packages(root, args.packages)
            gate.kernel_config(args.kernel_config)
            gate.dtb(args.dtb)
            gate.files(root, provenance_ok, actual, reachable)
        report = gate.result()
    except (OSError, ValueError, KeyError, TypeError, struct.error) as error:
        if gate:
            gate.error("invalid_or_missing_evidence", str(error))
            report = gate.result()
        else:
            report = {"schema_version": 1, "phase": args.phase, "status": "fail",
                      "errors": [{"category": "invalid_or_missing_baseline", "subject": str(error)}],
                      "differences": [], "checks": {}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"phase": args.phase, "status": report["status"], "output": str(args.output)}))
    return {"pass": 0, "fail": 1, "review_required": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
