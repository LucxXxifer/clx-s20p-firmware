#!/usr/bin/env python3
"""Resolve official security components and all configured S20P plugin feeds."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import plugin_sources

HERE = Path(__file__).resolve().parent
CA_FLOOR = (20260816, 2)
GO_FLOORS = {26: 10, 27: 3}
ADGUARD_FLOOR = (0, 107, 79)
SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
CA_REPOSITORY = "immortalwrt/immortalwrt"
QUICKSTART_REPOSITORY = "kenzok8/small-package"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def download(url, limit=32 * 1024 * 1024):
    allowed = ("https://raw.githubusercontent.com/", "https://go.dev/dl/",
               "https://codeload.github.com/AdguardTeam/AdGuardHome/",
               "https://github.com/AdguardTeam/AdGuardHome/releases/download/",
               "https://deb.debian.org/debian/pool/main/c/ca-certificates/")
    if not url.startswith(allowed):
        raise ValueError("unapproved component download origin")
    data = subprocess.check_output(["curl", "--fail", "--location", "--silent", "--show-error",
                                    "--retry", "2", "--retry-all-errors", "--connect-timeout", "15",
                                    "--max-time", "60", "--max-filesize", str(limit), url], timeout=200)
    if len(data) > limit:
        raise ValueError("component input exceeds size limit")
    return data


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", "--cache", "10m", path], timeout=60))


def field(data, name):
    values = re.findall(r"^" + re.escape(name) + r":=([^\r\n]*)$", data, re.MULTILINE)
    if len(values) != 1:
        raise ValueError("missing/ambiguous component recipe field: " + name)
    return values[0].strip()


def version(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("stable three-part release required: " + str(value))
    return tuple(map(int, value.split(".")))


def ca_version(data):
    release = field(data, "PKG_RELEASE")
    date = field(data, "PKG_VERSION")
    if not re.fullmatch(r"\d{8}", date) or not release.isdigit():
        raise ValueError("unsupported CA recipe version")
    return int(date), int(release)


def package_files(repo, commit, path):
    """Fetch only regular package inputs and bind each to its Git blob ID."""
    if not COMMIT.fullmatch(commit):
        raise ValueError("full component commit required")
    rows = []
    def visit(directory):
        listing = api(f"repos/{repo}/contents/{directory}?ref={commit}")
        if not isinstance(listing, list):
            raise ValueError("component package directory missing")
        for row in listing:
            if row["type"] == "dir":
                visit(row["path"])
            elif row["type"] == "file":
                relative = PurePosixPath(row["path"]).relative_to(path).as_posix()
                blob_value = api(f"repos/{repo}/git/blobs/{row['sha']}")
                if blob_value.get("encoding") != "base64":
                    raise ValueError("unsupported component Git blob encoding")
                data = base64.b64decode("".join(blob_value["content"].splitlines()), validate=True)
                blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                if row.get("sha") != blob:
                    raise ValueError("component Git blob mismatch")
                rows.append({"path": relative, "sha256": digest(data), "blob_sha1": blob,
                             "content": base64.b64encode(data).decode()})
            else:
                raise ValueError("nonregular component input")
        if len(rows) > 128:
            raise ValueError("unexpected component package size")
    visit(path)
    return sorted(rows, key=lambda row: row["path"])


def file_bytes(rows, name):
    matches = [row for row in rows if row["path"] == name]
    if len(matches) != 1:
        raise ValueError("required component input missing: " + name)
    return base64.b64decode(matches[0]["content"], validate=True)


def fingerprint(value):
    # Unrelated commits do not consume another firmware build. Byte changes,
    # package versions and downloaded source hashes do.
    identity = json.loads(json.dumps(value))
    identity.pop("fingerprint", None)
    for key in ("ca", "quickstart"):
        identity[key].pop("commit", None)
    identity["plugin_sources"] = plugin_sources.identity(identity["plugin_sources"])
    return digest(canonical(identity))


def validate(value):
    if (not isinstance(value, dict) or set(value) != {"schema_version", "ca", "go", "adguard", "quickstart", "plugin_sources", "fingerprint"}
            or value["schema_version"] != 1 or value.get("fingerprint") != fingerprint(value)):
        raise ValueError("component resolution fingerprint/schema mismatch")
    plugin_sources.validate(value["plugin_sources"])
    for key, repo, prefix in (("ca", CA_REPOSITORY, "package/system/ca-certificates"),
                              ("quickstart", QUICKSTART_REPOSITORY, "quickstart")):
        component = value[key]
        if (component.get("repository") != repo or component.get("path") != prefix
                or not COMMIT.fullmatch(str(component.get("commit")))
                or not isinstance(component.get("files"), list) or not component["files"]):
            raise ValueError("unapproved component source identity: " + key)
        seen = set()
        for row in component["files"]:
            name = row["path"]
            if (not isinstance(name, str) or name.startswith("/") or ".." in PurePosixPath(name).parts
                    or str(PurePosixPath(name)) != name or name in seen):
                raise ValueError("unsafe/duplicate component path")
            seen.add(name)
            data = base64.b64decode(row["content"], validate=True)
            if (digest(data) != row["sha256"] or
                    hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != row["blob_sha1"]):
                raise ValueError("component input content hash mismatch")
        recipe = file_bytes(component["files"], "Makefile").decode()
        if key == "ca" and ca_version(recipe) < CA_FLOOR:
            raise ValueError("resolved CA source is below the security floor")
        if not SHA.fullmatch(field(recipe, "PKG_HASH")):
            raise ValueError("component source archive SHA256 required")
        if key == "quickstart":
            version(field(recipe, "PKG_VERSION"))
            if field(recipe, "PKG_SOURCE_URL") != "https://github.com/linkease/istore-packages/releases/download/prebuilt/":
                raise ValueError("QuickStart must use the official prebuilt origin")
    go = value["go"]
    if not isinstance(go, list) or len(go) != len(GO_FLOORS):
        raise ValueError("required Go release series missing")
    for row, (minor, minimum) in zip(go, sorted(GO_FLOORS.items())):
        match = re.fullmatch(r"go1\." + str(minor) + r"\.(\d+)", str(row.get("version")))
        if not match or int(match[1]) < minimum or not SHA.fullmatch(str(row.get("sha256"))):
            raise ValueError("official stable Go release is below the security floor")
    adguard = value["adguard"]
    if adguard is not None and (version(adguard["version"]) < ADGUARD_FLOOR or
            not COMMIT.fullmatch(str(adguard.get("commit"))) or
            any(not SHA.fullmatch(str(adguard.get(key))) for key in ("source_sha256", "frontend_sha256"))):
        raise ValueError("invalid official AdGuard stable release")
    return value


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("regular component resolution required")
    return validate(json.loads(path.read_text()))


def resolve(previous_state=None):
    lock = json.loads(plugin_sources.LOCK.read_text())
    sources = plugin_sources.resolve(lock, (previous_state or {}).get("plugin_heads"))
    ca_commit = api(f"repos/{CA_REPOSITORY}/commits/master")["sha"]
    quick_commit = next(row["head_commit"] for row in sources
                        if row["repository"] == QUICKSTART_REPOSITORY)
    releases = json.loads(download("https://go.dev/dl/?mode=json&include=all"))
    go = []
    for minor, floor in sorted(GO_FLOORS.items()):
        available = [row for row in releases if row.get("stable") is True and
                     re.fullmatch(r"go1\." + str(minor) + r"\.\d+", str(row.get("version")))]
        selected = max(available, key=lambda row: version(row["version"].removeprefix("go")))
        source = [row for row in selected["files"] if row.get("kind") == "source" and
                  row.get("filename") == selected["version"] + ".src.tar.gz"]
        if len(source) != 1:
            raise ValueError("official Go source archive missing")
        go.append({"version": selected["version"], "sha256": source[0]["sha256"]})
    result = {"schema_version": 1, "plugin_sources": sources,
              "ca": {"repository": CA_REPOSITORY, "commit": ca_commit,
                     "path": "package/system/ca-certificates",
                     "files": package_files(CA_REPOSITORY, ca_commit, "package/system/ca-certificates")},
              "go": go,
              "adguard": None,
              "quickstart": {"repository": QUICKSTART_REPOSITORY, "commit": quick_commit, "path": "quickstart",
                             "files": package_files(QUICKSTART_REPOSITORY, quick_commit, "quickstart")}}
    result["fingerprint"] = fingerprint(result)
    return validate(result)


def replace_field(data, name, value):
    old = field(data, name)
    return data.replace(name + ":=" + old + "\n", name + ":=" + value + "\n")


def stage_download(root, filename, data, expected_sha256):
    root = Path(root)
    source = PurePosixPath(filename)
    if (root.is_symlink() or not root.is_dir() or source.is_absolute()
            or source.name != filename or filename in ("", ".", "..")
            or digest(data) != expected_sha256):
        raise ValueError("invalid verified OpenWrt download input")
    cache = root / "dl"
    if cache.is_symlink():
        raise ValueError("OpenWrt download cache symlink rejected")
    cache.mkdir(exist_ok=True)
    target = cache / filename
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("OpenWrt download target is not a regular file")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, prefix=".s20p-ca-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_package(directory, rows):
    directory = Path(directory)
    # Only dedicated, disposable build source packages may be replaced.
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("regular prepared package directory required")
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink inside component package")
    actual = {p.relative_to(directory).as_posix(): p for p in directory.rglob("*") if p.is_file()}
    expected = {row["path"] for row in rows}
    for name in set(actual) - expected:
        actual[name].unlink()
    for row in rows:
        dest = directory / row["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(row["content"], validate=True))
        dest.chmod(0o755 if row["path"].endswith(".init") else 0o644)


def snapshot_package(directory):
    directory = Path(directory)
    rows = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink in prepared component package")
        if path.is_file():
            data = path.read_bytes()
            rows.append({"path": path.relative_to(directory).as_posix(), "sha256": digest(data),
                         "content": base64.b64encode(data).decode(),
                         "blob_sha1": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()})
    return rows


def quickstart_recipe(data):
    """Reapply the wrapper's storage policy after refreshing official inputs."""
    lines = data.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if re.match(r"^[ \t]*DEPENDS[ \t]*:=", line)]
    if len(starts) != 1:
        raise ValueError("QuickStart dependency layout changed; review the storage policy")
    index = starts[0]
    optional = r"[ \t]+\+(?:smartmontools-drivedb|smartmontools|mdadm|parted|e2fsprogs)(?=[ \t\\\r\n]|$)"
    while True:
        line = lines[index]
        lines[index] = re.sub(optional, "", line)
        if not line.rstrip("\r\n").endswith("\\"):
            break
        index += 1
        if index == len(lines):
            raise ValueError("Unterminated QuickStart dependency continuation")
    return "".join(lines)


def prepare(root, inputs, output, fixed_manifest=None):
    value = load(inputs)
    root, output = Path(root), Path(output)
    ca = root / "package/system/ca-certificates"
    original_ca = (ca / "Makefile").read_text()
    official_ca = file_bytes(value["ca"]["files"], "Makefile").decode()
    if ca_version(original_ca) <= ca_version(official_ca):
        write_package(ca, value["ca"]["files"])
    if ca_version((ca / "Makefile").read_text()) < CA_FLOOR:
        raise ValueError("prepared CA source is below the security floor")
    quick = root / "feeds/custom_feed/quickstart"
    before = (quick / "Makefile").read_text()
    candidate = file_bytes(value["quickstart"]["files"], "Makefile").decode()
    if version(field(before, "PKG_VERSION")) <= version(field(candidate, "PKG_VERSION")):
        write_package(quick, value["quickstart"]["files"])
    recipe_path = quick / "Makefile"
    recipe_path.write_text(quickstart_recipe(recipe_path.read_text()))
    selected_adguard = value["adguard"]
    if selected_adguard is not None:
        adg = root / "feeds/custom_feed/adguardhome"
        recipe = (adg / "Makefile").read_text()
        current = version(field(recipe, "PKG_VERSION"))
        target = version(selected_adguard["version"])
        if current > target:
            raise ValueError("newer feed AdGuard version needs official stable release evidence; refusing downgrade")
        if field(recipe, "PKG_SOURCE_URL") != "https://codeload.github.com/AdguardTeam/AdGuardHome/tar.gz/v$(PKG_VERSION)?":
            raise ValueError("unexpected AdGuard source origin")
        for key, selected in (("PKG_VERSION", selected_adguard["version"]),
                              ("PKG_HASH", selected_adguard["source_sha256"]),
                              ("FRONTEND_HASH", selected_adguard["frontend_sha256"])):
            recipe = replace_field(recipe, key, selected)
        (adg / "Makefile").write_text(recipe)
        config = adg / "files/adguardhome.conf"
        config.write_text(config.read_text().replace("/var/lib/adguardhome", "/etc/adguardhome/work"))
        init = adg / "files/adguardhome.init"
        init.write_text(init.read_text().replace("work_dir:string:/var/lib/adguardhome", "work_dir:string:/etc/adguardhome/work"))
        keep = adg / "files/adguardhome.keep"
        keep.write_text("/etc/adguardhome/\n/etc/config/adguardhome\n")
        marker = "\t$(INSTALL_DIR) $(1)/etc/sysctl.d\n"
        addition = "\t$(INSTALL_DIR) $(1)/lib/upgrade/keep.d\n\t$(INSTALL_DATA) ./files/adguardhome.keep $(1)/lib/upgrade/keep.d/adguardhome\n\n"
        if recipe.count(marker) != 1:
            raise ValueError("AdGuard install recipe changed")
        (adg / "Makefile").write_text(recipe.replace(marker, addition + marker))
    import ca_security
    ca_date, _ = ca_version((ca / "Makefile").read_text())
    if fixed_manifest:
        import fixed_inputs
        fixed = fixed_inputs.load(fixed_manifest)
        if digest(Path(inputs).read_bytes()) != fixed["components"]["sha256"]:
            raise ValueError("prepared components differ from fixed source inputs")
        ca_archive = fixed_inputs.payload(Path(fixed_manifest).parent, fixed["ca_archive"])
        ca_recipe = (ca / "Makefile").read_text()
        if (field(ca_recipe, "PKG_NAME") != "ca-certificates"
                or field(ca_recipe, "PKG_SOURCE") != "$(PKG_NAME)_$(PKG_VERSION).tar.xz"):
            raise ValueError("unreviewed fixed CA download filename")
        stage_download(root, f"ca-certificates_{ca_date}.tar.xz", ca_archive, field(ca_recipe, "PKG_HASH"))
    else:
        ca_archive = download(f"https://deb.debian.org/debian/pool/main/c/ca-certificates/ca-certificates_{ca_date}.tar.xz")
    ca_report = ca_security.reproduce(ca, ca_archive)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"schema_version": 1, "status": "pass",
                                 "component_fingerprint": value["fingerprint"],
                                 "ca": ca_report, "ca_source_files": snapshot_package(ca),
                                 "ca_archive_base64": base64.b64encode(ca_archive).decode(),
                                 "adguard_version": selected_adguard["version"] if selected_adguard else None},
                                indent=2, sort_keys=True) + "\n")
    return {"status": "pass", "fingerprint": value["fingerprint"],
            "ca_version": ca_report["version"], "adguard_version": selected_adguard["version"] if selected_adguard else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("resolve")
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--previous-state", type=Path)
    command.add_argument("--wrapper-commit")
    command = sub.add_parser("prepare")
    command.add_argument("--source-root", type=Path, required=True)
    command.add_argument("--inputs", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--fixed-inputs", type=Path,
                         default=Path(os.environ["S20P_FIXED_INPUTS"]) if os.environ.get("S20P_FIXED_INPUTS") else None)
    args = parser.parse_args()
    try:
        if args.command == "resolve":
            previous = None
            if args.previous_state:
                import upstream_sync
                previous = upstream_sync.read_state(args.previous_state, args.wrapper_commit)
            result = resolve(previous)
            if args.output.exists():
                raise ValueError("component resolution output already exists")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            print(json.dumps({"fingerprint": result["fingerprint"], "status": "resolved"}))
        else:
            print(json.dumps(prepare(args.source_root, args.inputs, args.output,
                                     fixed_manifest=args.fixed_inputs), sort_keys=True))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, "Component policy: " + str(error) + "\n")


if __name__ == "__main__":
    main()
