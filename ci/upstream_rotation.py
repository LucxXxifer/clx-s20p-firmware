#!/usr/bin/env python3
"""Attribute package differences to verified upstream recipe rotation.

The image gate compares a rolling build against the reviewed baseline and asks a
human to approve every difference; the approval key is the SHA256 of
{category, subject, expected, actual, reason}.  Upstream feeds move every day
(date-stamped PKG_VERSION, daily geosite data), so an image that is exactly what
the reviewed recipes produced still mints new keys daily and can never pass.

This module records what each recipe in the prepared tree declares *before*
``make`` runs: PKG_VERSION/PKG_RELEASE, per-``Package/<name>`` VERSION overrides
and every declared download HASH.  Each entry is bound to the recipe bytes that
``provenance.py`` already hashed into ``prepared-source-files.json`` (and through
``build_fingerprint``/``prepared_tree_hash`` into the run's source lock), so the
gate can attribute a version or payload difference to the verified recipe
instead of to an unreviewed local change.

Nothing is relaxed.  Download hashes, signatures, Go/x509 floors, source pins and
per-file checks all still run; the index only answers "did this difference come
from the recipe the reviewed sources declare, or from something else?".

Examples:
  upstream_rotation.py index --prepared output/provenance/prepared-source-files.json \
      --tree action_build --output output/provenance/recipe-index.json

No command accesses the network.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SCHEMA_VERSION = 1
COMMIT = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
ASSIGNMENT = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:?=\s*(?P<value>.*)$")
PACKAGE_BLOCK = re.compile(r"^define[ \t]+Package/(?P<name>[A-Za-z0-9_.+-]+)[ \t]*$", re.M)
ENDEF = re.compile(r"^endef[ \t]*$", re.M)
VERSION_LINE = re.compile(r"^[ \t]*VERSION\s*:?=\s*(?P<value>.*)$", re.M)
HASH_LINE = re.compile(r"^[ \t]*(?:PKG_)?HASH\s*:?=\s*(?P<value>\S+)[ \t]*$", re.M)
REFERENCE = re.compile(r"\$\((?P<key>[A-Za-z_][A-Za-z0-9_]*)\)")
RECIPE_ROOTS = ("feeds/", "custom_feed/", "package/")
SKIP_PACKAGES = ("kernel",)


class RotationError(ValueError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def normalized(text):
    """Recipes in the wild mix LF and CRLF; anchors must see one line break."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def expand(value, variables, depth=8):
    """Resolve $(VAR) references from the recipe's own assignments."""
    for _ in range(depth):
        replaced = REFERENCE.sub(lambda match: variables.get(match.group("key"), match.group(0)), value)
        if replaced == value:
            break
        value = replaced
    return value.strip()


def assignments(text):
    """First assignment wins, matching how OpenWrt recipes define PKG_* once."""
    values = {}
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            values.setdefault(match.group("key"), match.group("value"))
    return values


def declared_hashes(text, variables):
    hashes = set()
    for match in HASH_LINE.finditer(normalized(text)):
        value = expand(match.group("value"), variables).split("#", 1)[0].strip().strip('"').strip("'")
        if HEX64.fullmatch(value):
            hashes.add(value)
    return sorted(hashes)


def package_versions(text, variables):
    """Return {package: declared_version} for one recipe file."""
    text = normalized(text)
    default_release = expand(variables.get("PKG_RELEASE", "1"), variables) or "1"
    base_version = expand(variables.get("PKG_VERSION", ""), variables)
    fallback = ""
    if base_version and "$(" not in base_version:
        fallback = base_version + "-r" + default_release
    result = {}
    for match in PACKAGE_BLOCK.finditer(text):
        end = ENDEF.search(text, match.end())
        if not end:
            raise RotationError("unterminated Package block: " + match.group("name"))
        block = text[match.end():end.start()]
        override = VERSION_LINE.search(block)
        version = expand(override.group("value"), variables) if override else fallback
        if not version or "$(" in version or " " in version:
            continue
        result[match.group("name")] = version
    if not result and fallback:
        name = expand(variables.get("PKG_NAME", ""), variables)
        if name and "$(" not in name and len(name.split()) == 1:
            result[name] = fallback
    return result


def has_local_patches(tree, recipe_relative):
    directory = tree / Path(recipe_relative).parent / "patches"
    if not directory.is_dir() or directory.is_symlink():
        return False
    return any(path.is_file() and not path.is_symlink() for path in directory.rglob("*"))


def build_index(manifest_path, tree):
    """Index recipes that the prepared-source manifest already vouches for."""
    manifest = json.loads(Path(manifest_path).read_text())
    if not isinstance(manifest, list):
        raise RotationError("prepared-source manifest must be a file list")
    tree = Path(tree)
    packages, conflicts, skipped, recipes = {}, set(), 0, 0
    for entry in manifest:
        if not isinstance(entry, dict):
            continue
        relative = entry.get("path")
        recorded = entry.get("sha256")
        if not isinstance(relative, str) or not HEX64.fullmatch(str(recorded)):
            continue
        if Path(relative).name != "Makefile" or not relative.startswith(RECIPE_ROOTS):
            continue
        if ".." in Path(relative).parts:
            continue
        path = tree / relative
        if not path.is_file() or path.is_symlink():
            skipped += 1
            continue
        data = path.read_bytes()
        if sha256(data) != recorded:
            # The recipe changed after provenance captured the tree: no
            # attribution, the difference stays a review item.
            skipped += 1
            continue
        recipes += 1
        text = data.decode("utf-8", "replace")
        variables = assignments(text)
        hashes = declared_hashes(text, variables)
        patched = has_local_patches(tree, relative)
        for package, version in package_versions(text, variables).items():
            if package in SKIP_PACKAGES:
                continue
            record = {"version": version, "recipe": relative, "recipe_sha256": recorded,
                      "hashes": hashes, "local_patches": patched}
            previous = packages.get(package)
            if previous is None:
                packages[package] = record
            elif previous["version"] != version or previous["hashes"] != hashes:
                # Two recipes disagree: neither may be used for attribution.
                conflicts.add(package)
    for package in conflicts:
        packages.pop(package, None)
    return {"schema_version": SCHEMA_VERSION,
            "scope": ("recipe-declared package versions and download hashes in the prepared tree; "
                      "each entry bound to prepared-source-files.json, no network access"),
            "manifest_sha256": sha256(Path(manifest_path).read_bytes()),
            "recipe_count": recipes,
            "skipped_recipes": skipped,
            "packages": packages,
            "conflicts": sorted(conflicts)}


def load(path):
    """Load and validate an index written by ``index``."""
    if not path:
        return None
    index = json.loads(Path(path).read_text())
    if not isinstance(index, dict) or index.get("schema_version") != SCHEMA_VERSION:
        raise RotationError("unsupported recipe index schema")
    packages = index.get("packages")
    if not isinstance(packages, dict):
        raise RotationError("recipe index requires a package map")
    if not HEX64.fullmatch(str(index.get("manifest_sha256", ""))):
        raise RotationError("recipe index requires the prepared manifest digest")
    for name, record in packages.items():
        if not isinstance(record, dict):
            raise RotationError("invalid recipe record: " + str(name))
        if not isinstance(record.get("version"), str) or not record["version"]:
            raise RotationError("recipe record requires a version: " + str(name))
        if not isinstance(record.get("recipe"), str) or not record["recipe"]:
            raise RotationError("recipe record requires a path: " + str(name))
        if not HEX64.fullmatch(str(record.get("recipe_sha256", ""))):
            raise RotationError("recipe record requires a recipe digest: " + str(name))
        hashes = record.get("hashes", [])
        if not isinstance(hashes, list) or any(not HEX64.fullmatch(str(value)) for value in hashes):
            raise RotationError("recipe record hashes must be SHA256 values: " + str(name))
        if not isinstance(record.get("local_patches"), bool):
            raise RotationError("recipe record requires the local-patch flag: " + str(name))
    conflicts = index.setdefault("conflicts", [])
    if not isinstance(conflicts, list) or any(not isinstance(value, str) for value in conflicts):
        raise RotationError("recipe index conflicts must be package names")
    for conflict in conflicts:
        packages.pop(conflict, None)
    index.setdefault("recipe_count", len(packages))
    index.setdefault("skipped_recipes", 0)
    return index


def evidence(record):
    """Compact, auditable evidence copied into the gate report."""
    return {"recipe": record["recipe"], "recipe_sha256": record["recipe_sha256"],
            "declared_version": record["version"], "local_patches": record["local_patches"],
            "declared_download_hashes": record["hashes"]}


def attest_version(index, package, version):
    """Return evidence when the recipe declares exactly the installed version."""
    if not index:
        return None
    record = index["packages"].get(package)
    if not record or record["version"] != version:
        return None
    return evidence(record)


def attest_hash(index, package, value):
    """Return evidence when the file bytes are a download the recipe declares."""
    if not index or not package:
        return None
    record = index["packages"].get(package)
    if not record or value not in record["hashes"]:
        return None
    return evidence(record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    phases = parser.add_subparsers(dest="phase", required=True)
    index = phases.add_parser("index", help="record recipe declarations from the prepared tree")
    index.add_argument("--prepared", required=True, type=Path,
                       help="provenance/prepared-source-files.json captured before make")
    index.add_argument("--tree", required=True, type=Path, help="prepared build tree root")
    index.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    value = build_index(args.prepared, args.tree)
    value["tree"] = str(args.tree)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"recipes": value["recipe_count"], "packages": len(value["packages"]),
                      "conflicts": value["conflicts"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
