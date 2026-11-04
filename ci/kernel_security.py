#!/usr/bin/env python3
"""Stage and verify the pinned S20P IPv4 TCP kernel security backport."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from minimal_security import atomic_write, regular
from component_gate import bound_sources, manifest, read

HERE = Path(__file__).resolve().parent / "security/kernel-cve-2026-98432"
MANIFEST_SHA256 = "d49f745206a41cab4c4c19b3a86c62c04d0cb9ff524b64902a3e9b12147e5af7"
SOURCE_COMMIT = "9f397975c7b47ce6986a6e39529d2f72fd5c729d"
BASE_WRAPPER_COMMIT = "d46704afb327fce99e7d770c08fc46b48d5bb1b7"
KERNEL_HASH = "164dc9d1f6c93c61a15e1f071c48379b467f2b17c469cce7223471968208ed03"
PATCH_NAME = "100-linux-ipv4-rcu-opt-free.patch"
PATCH_DESTINATION = "target/linux/generic/pending-6.12/999-s20p-cve-2026-98432-ipv4-rcu-opt-free.patch"
PREPARATION_NAME = "kernel-security-preparation.json"
BUILD_NAME = "kernel-security-build.json"
SOURCE_FILES = ("net/ipv4/af_inet.c", "net/ipv4/tcp_ipv4.c")
PIPELINE_FILES = ("ci/kernel_security.py", "ci/build.sh", "ci/capture.sh", "ci/provenance.py",
                  "ci/preservation.py", "ci/release_candidate.py", "wrt_core/update.sh")
FIXED_EXPRESSIONS = {
    "net/ipv4/af_inet.c": b"kfree_rcu(rcu_dereference_protected(inet->inet_opt, 1), rcu);",
    "net/ipv4/tcp_ipv4.c": b"kfree_rcu(rcu_dereference_protected(inet_rsk(req)->ireq_opt, 1), rcu);",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def file_row(path, data):
    return {"path": path, "mode": "0o644", "size": len(data), "sha256": digest(data)}


def profile():
    raw = regular(HERE / "manifest.json", HERE)
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError("kernel security profile digest mismatch")
    value = json.loads(raw)
    required = {
        "id": "s20p-kernel-cve-2026-98432-20261104",
        "cve": "CVE-2026-98432",
        "kernel_version": "6.12.112",
        "kernel_patchver": "6.12",
        "source_commit": "9f397975c7b47ce6986a6e39529d2f72fd5c729d",
        "upstream_patch_commit": "11a03ce3c4a62d84c10ccdfe47f8075006498568",
        "upstream_commit": "e3f33b0a1d89d738e2b913dab519226eaf76e15b",
        "destination": PATCH_DESTINATION,
        "affected_source_files": list(SOURCE_FILES),
        "runtime_tested": False,
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise ValueError("kernel security profile identity mismatch")
    listed = {row["path"] for row in value["files"]}
    actual = {path.relative_to(HERE).as_posix() for path in HERE.rglob("*")
              if path.is_file() and path.name != "manifest.json"}
    if listed != actual:
        raise ValueError("kernel security profile file inventory mismatch")
    for row in value["files"]:
        data = regular(HERE / row["path"], HERE)
        if file_row(row["path"], data) != {"path": row["path"], "mode": "0o644",
                                            "size": row["size"], "sha256": row["sha256"]}:
            raise ValueError("kernel security profile file hash mismatch: " + row["path"])
    return value


def profile_sha256():
    profile()
    return MANIFEST_SHA256


def patch_bytes():
    return regular(HERE / PATCH_NAME, HERE)


def wrapper_rows():
    value = profile()
    rows = [file_row("ci/security/kernel-cve-2026-98432/manifest.json",
                     regular(HERE / "manifest.json", HERE))]
    for row in value["files"]:
        rows.append(file_row("ci/security/kernel-cve-2026-98432/" + row["path"],
                             regular(HERE / row["path"], HERE)))
    wrapper = Path(__file__).resolve().parent.parent
    for relative in PIPELINE_FILES:
        path = wrapper / relative
        row = file_row(relative, regular(path, wrapper))
        row["mode"] = oct(path.stat().st_mode & 0o7777)
        rows.append(row)
    return sorted(rows, key=lambda row: row["path"])


def preparation_report():
    value = profile()
    return {
        "schema_version": 1,
        "status": "pass",
        "profile_id": value["id"],
        "profile_sha256": MANIFEST_SHA256,
        "cve": value["cve"],
        "source_commit": value["source_commit"],
        "kernel_patchver": value["kernel_patchver"],
        "kernel_version": value["kernel_version"],
        "destination": value["destination"],
        "patch_sha256": digest(patch_bytes()),
        "upstream_patch_commit": value["upstream_patch_commit"],
        "upstream_commit": value["upstream_commit"],
        "runtime_tested": False,
    }


def checkout_commit(root):
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=False)
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}\n?", result.stdout):
        raise ValueError("exact S20P kernel source checkout identity required")
    return result.stdout.strip()


def safe_parent(path, root):
    path, root = Path(path), Path(root)
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("kernel security path escaped source root") from error
    current = root
    if current.is_symlink():
        raise ValueError("kernel security source root symlink rejected")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("kernel security path symlink rejected: " + str(current))


def prepare(source_root, output):
    root, output = Path(source_root), Path(output)
    if ".." in root.parts or ".." in output.parts:
        raise ValueError("canonical kernel security paths required")
    root, output = Path(os.path.abspath(root)), Path(os.path.abspath(output))
    value = profile()
    if checkout_commit(root) != value["source_commit"]:
        raise ValueError("kernel security profile requires its exact fixed source commit")
    target_makefile = root / "target/linux/mediatek/Makefile"
    kernel_version_file = root / "target/linux/generic/kernel-6.12"
    if not re.search(rb"(?m)^KERNEL_PATCHVER\s*:=\s*6\.12\s*$", regular(target_makefile, root)):
        raise ValueError("S20P target kernel patch series is not 6.12")
    version_text = regular(kernel_version_file, root)
    if not re.search(rb"(?m)^LINUX_VERSION-6\.12\s*=\s*\.112\s*$", version_text):
        raise ValueError("fixed source kernel version is not 6.12.112")
    if not re.search(rb"(?m)^LINUX_KERNEL_HASH-6\.12\.112\s*=\s*" + KERNEL_HASH.encode() + rb"\s*$", version_text):
        raise ValueError("fixed source kernel archive hash mismatch")
    patch_path = root / PATCH_DESTINATION
    patch_dir = patch_path.parent
    safe_parent(patch_dir, root)
    if not patch_dir.is_dir():
        raise ValueError("generic pending-6.12 kernel patch directory is missing")
    if patch_path.exists() or patch_path.is_symlink():
        raise ValueError("kernel security patch destination already exists")
    if output.exists() or output.is_symlink():
        raise ValueError("kernel security preparation evidence already exists")
    for parent in output.parents:
        if parent.is_symlink():
            raise ValueError("kernel security evidence parent symlink rejected")
        if parent.exists():
            break
    report = preparation_report()
    atomic_write(patch_path, patch_bytes())
    atomic_write(output, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    return report


def verify_preparation(provenance):
    provenance = Path(provenance)
    expected = preparation_report()
    actual = json.loads(regular(provenance / PREPARATION_NAME, provenance))
    if actual != expected:
        raise ValueError("kernel security preparation evidence mismatch")
    _, prepared = bound_sources(provenance)
    source = read(provenance / "source-lock.json")
    if source.get("upstream_commit") != SOURCE_COMMIT:
        raise ValueError("kernel security exact upstream source mismatch")
    patch_rows = [row for row in prepared if row.get("path") == PATCH_DESTINATION]
    if patch_rows != [file_row(PATCH_DESTINATION, patch_bytes())]:
        raise ValueError("kernel security prepared source patch mismatch")
    wrapper = manifest(provenance / "wrapper-source-files.json")
    indexed = {row.get("path"): row for row in wrapper}
    for row in wrapper_rows():
        if indexed.get(row["path"]) != row:
            raise ValueError("kernel security wrapper/profile source mismatch: " + row["path"])
    return expected


def required(provenance, build_commit=None):
    """Only exact, frozen pre-backport pipelines may replay without this fix."""
    provenance = Path(provenance)
    source = read(provenance / "source-lock.json")
    commit = source.get("wrapper_checkout_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("kernel security requires an exact wrapper checkout commit")
    if build_commit is not None and commit != build_commit:
        raise ValueError("kernel security wrapper differs from selected build commit")
    profile()
    historical = read(HERE / "historical-wrappers.json")
    if commit not in historical:
        return True
    wrapper = manifest(provenance / "wrapper-source-files.json")
    if digest(json.dumps(wrapper, sort_keys=True).encode()) != source.get("wrapper_tree_hash"):
        raise ValueError("historical kernel security wrapper tree hash mismatch")
    indexed = {row["path"]: row for row in wrapper}
    if any(indexed.get(row["path"]) != row for row in historical[commit]):
        raise ValueError("historical kernel security pipeline source mismatch")
    if any(name.startswith(("ci/kernel_security.py", "ci/security/kernel-cve-2026-98432/"))
           for name in indexed):
        raise ValueError("historical wrapper cannot contain new kernel security sources")
    for path in (provenance / PREPARATION_NAME, provenance / BUILD_NAME,
                 provenance / "kernel-source-after", provenance.parent / "reports/kernel-security.json"):
        if path.exists() or path.is_symlink():
            raise ValueError("historical candidate cannot claim the new kernel security fix")
    return False


def inspect_kernel_source(build_root):
    build_root = Path(build_root)
    kernels = sorted(build_root.glob(
        "build_dir/target-*/linux-mediatek_filogic/linux-6.12.112"))
    if len(kernels) != 1:
        raise ValueError("expected one built S20P Linux 6.12.112 source tree")
    kernel = kernels[0]
    if kernel.is_symlink() or not kernel.is_dir():
        raise ValueError("built kernel source tree must be a regular directory")
    for relative in (".config", "Module.symvers", *SOURCE_FILES):
        safe_parent(kernel / relative, kernel)
        path = kernel / relative
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("bounded built kernel source input required: " + relative)
    rows = []
    # git apply inside the upstream checkout can report success after skipping
    # paths outside its prefix. Check copies outside that checkout instead.
    with tempfile.TemporaryDirectory(prefix="s20p-kernel-source-check-") as name:
        check_root = Path(name)
        for relative, expression in FIXED_EXPRESSIONS.items():
            data = regular(kernel / relative, kernel)
            if expression not in data:
                raise ValueError("built kernel source is missing a pinned RCU free")
            rows.append({"path": relative, "size": len(data), "sha256": digest(data)})
            destination = check_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        check_patched_sources(check_root)
    return {
        "kernel_version": "6.12.112",
        "patch_verified": True,
        "source_files": rows,
        "kernel_config_sha256": digest_file(kernel / ".config"),
        "module_symvers_sha256": digest_file(kernel / "Module.symvers"),
    }, kernel


def check_patched_sources(root):
    environment = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        environment.pop(key, None)
    environment["GIT_CEILING_DIRECTORIES"] = str(Path(root).parent)
    result = subprocess.run(["git", "apply", "--reverse", "--check", str(HERE / PATCH_NAME)],
                            cwd=root, env=environment, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError("pinned CVE patch is not applied to kernel sources: " + result.stderr.strip())


def package_version(inventory):
    packages = inventory.get("packages") if isinstance(inventory, dict) else None
    rows = [row for row in packages or [] if row.get("name") == "kernel"]
    if len(rows) != 1 or not isinstance(rows[0].get("version"), str):
        raise ValueError("exact installed kernel package record required")
    version = rows[0]["version"]
    if not re.fullmatch(r"6\.12\.112~[0-9a-f]{32}-r1", version):
        raise ValueError("candidate kernel package is not from the pinned 6.12.112 source")
    return version


def capture(build_root, output_root):
    build_root, output = Path(build_root), Path(output_root)
    provenance = output / "provenance"
    for path in (provenance / BUILD_NAME, provenance / "kernel-source-after"):
        if path.exists() or path.is_symlink():
            raise ValueError("kernel security build evidence already exists")
    prepared = verify_preparation(provenance)
    source, kernel = inspect_kernel_source(build_root)
    kernel_config = output / "metadata/kernel/.config"
    module_symvers = output / "metadata/kernel/Module.symvers"
    fit = output / "inspection/kernel.fit"
    for path in (kernel_config, module_symvers, fit):
        if path.is_symlink() or not path.is_file():
            raise ValueError("captured kernel artifact input missing: " + str(path))
    if (digest_file(kernel_config) != source["kernel_config_sha256"] or
            digest_file(module_symvers) != source["module_symvers_sha256"]):
        raise ValueError("captured kernel metadata differs from the compiled source tree")
    inventory = json.loads(regular(output / "inspection/installed-packages.json", output))
    version = package_version(inventory)
    captured = []
    for row in source["source_files"]:
        relative = "kernel-source-after/" + row["path"]
        data = regular(kernel / row["path"], kernel)
        atomic_write(provenance / relative, data)
        captured.append({"path": "provenance/" + relative, "source_path": row["path"],
                         "size": len(data), "sha256": digest(data)})
    report = {
        "schema_version": 1,
        "status": "pass",
        "profile_id": prepared["profile_id"],
        "profile_sha256": prepared["profile_sha256"],
        "cve": prepared["cve"],
        "source_commit": prepared["source_commit"],
        "kernel_version": source["kernel_version"],
        "patch_sha256": prepared["patch_sha256"],
        "upstream_patch_commit": prepared["upstream_patch_commit"],
        "patch_verified_in_compiled_sources": source["patch_verified"],
        "source_files": captured,
        "kernel_config_sha256": source["kernel_config_sha256"],
        "module_symvers_sha256": source["module_symvers_sha256"],
        "kernel_fit_sha256": digest_file(fit),
        "kernel_package_version": version,
        "runtime_tested": False,
    }
    atomic_write(provenance / BUILD_NAME, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    return report


def verify_artifact(root, wrapper_commit, upstream_commit):
    root = Path(root)
    provenance = root / "provenance"
    if not required(provenance, wrapper_commit):
        return None
    prepared = verify_preparation(provenance)
    if prepared["source_commit"] != upstream_commit:
        raise ValueError("kernel security evidence uses a different upstream source")
    report = json.loads(regular(provenance / BUILD_NAME, provenance))
    if (report.get("status") != "pass" or report.get("profile_id") != prepared["profile_id"] or
            report.get("profile_sha256") != prepared["profile_sha256"] or
            report.get("cve") != prepared["cve"] or report.get("source_commit") != upstream_commit or
            report.get("kernel_version") != "6.12.112" or
            report.get("patch_sha256") != prepared["patch_sha256"] or
            report.get("upstream_patch_commit") != prepared["upstream_patch_commit"] or
            report.get("patch_verified_in_compiled_sources") is not True or
            report.get("runtime_tested") is not False):
        raise ValueError("kernel security build evidence identity/status mismatch")
    source_root = provenance / "kernel-source-after"
    with tempfile.TemporaryDirectory() as name:
        check_root = Path(name)
        rows = report.get("source_files", [])
        if not isinstance(rows, list) or len(rows) != len(SOURCE_FILES):
            raise ValueError("kernel security source evidence is incomplete")
        for row in rows:
            relative = row.get("source_path")
            if relative not in SOURCE_FILES or row.get("path") != "provenance/kernel-source-after/" + relative:
                raise ValueError("kernel security captured source path mismatch")
            source_path = source_root / relative
            data = regular(source_path, provenance)
            if len(data) != row.get("size") or digest(data) != row.get("sha256"):
                raise ValueError("kernel security captured source hash mismatch")
            destination = check_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        if {row.get("source_path") for row in report["source_files"]} != set(SOURCE_FILES):
            raise ValueError("kernel security source evidence is incomplete")
        check_patched_sources(check_root)
        for relative, expression in FIXED_EXPRESSIONS.items():
            if expression not in (check_root / relative).read_bytes():
                raise ValueError("captured kernel source is missing a pinned RCU free")
    kernel_config = root / "metadata/kernel/.config"
    module_symvers = root / "metadata/kernel/Module.symvers"
    fit = root / "inspection/kernel.fit"
    for path in (kernel_config, module_symvers, fit):
        if path.is_symlink() or not path.is_file():
            raise ValueError("kernel security artifact metadata is missing: " + str(path))
    if (digest_file(kernel_config) != report.get("kernel_config_sha256") or
            digest_file(module_symvers) != report.get("module_symvers_sha256") or
            digest_file(fit) != report.get("kernel_fit_sha256")):
        raise ValueError("kernel security build hashes do not match captured kernel artifacts")
    inventory = json.loads(regular(root / "inspection/installed-packages.json", root))
    version = package_version(inventory)
    capture = json.loads(regular(root / "reports/capture.json", root))
    if (version != report.get("kernel_package_version") or
            capture.get("kernel_abi") != version):
        raise ValueError("kernel security package/kernel ABI evidence mismatch")
    source_lock = json.loads(regular(provenance / "source-lock.json", provenance))
    if (source_lock.get("wrapper_checkout_commit") != wrapper_commit or
            source_lock.get("wrapper_commit") != BASE_WRAPPER_COMMIT):
        raise ValueError("kernel security wrapper commit differs from candidate identity")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare")
    command.add_argument("--source-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = sub.add_parser("capture")
    command.add_argument("--build-root", type=Path, required=True)
    command.add_argument("--output-root", type=Path, required=True)
    command = sub.add_parser("verify-artifact")
    command.add_argument("--root", type=Path, required=True)
    command.add_argument("--wrapper-commit", required=True)
    command.add_argument("--upstream-commit", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.source_root, args.output)
        elif args.command == "capture":
            result = capture(args.build_root, args.output_root)
        else:
            result = verify_artifact(args.root, args.wrapper_commit, args.upstream_commit)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.CalledProcessError) as error:
        parser.exit(1, "Kernel security gate: " + str(error) + "\n")


if __name__ == "__main__":
    main()
