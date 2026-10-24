"""End-to-end fixtures for the build preservation gates (standard library only)."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "preservation.py"
sys.path.insert(0, str(SCRIPT.parent))
PROVENANCE_SPEC = importlib.util.spec_from_file_location("preservation_provenance", SCRIPT.with_name("provenance.py"))
provenance = importlib.util.module_from_spec(PROVENANCE_SPEC)
PROVENANCE_SPEC.loader.exec_module(provenance)


def dtb(phandle=1, model="CLX S20P"):
    """Make a real, tiny FDT with a reference whose numeric ID may change."""
    names = ["model", "compatible", "phandle", "phy-handle", "reg"]
    strings = b""
    offsets = {}
    for name in names:
        offsets[name] = len(strings)
        strings += name.encode() + b"\0"

    def pad(data):
        return data + b"\0" * (-len(data) % 4)

    def node(name):
        return struct.pack(">I", 1) + pad(name.encode() + b"\0")

    def prop(name, data):
        return struct.pack(">III", 3, len(data), offsets[name]) + pad(data)

    end = struct.pack(">I", 2)
    tree = node("") + prop("model", model.encode() + b"\0")
    tree += prop("compatible", b"clx,s20p\0mediatek,mt7986a\0")
    tree += node("phy@5") + prop("phandle", struct.pack(">I", phandle))
    tree += prop("reg", struct.pack(">I", 5)) + end
    tree += node("ethernet") + prop("phy-handle", struct.pack(">I", phandle)) + end
    tree += end + struct.pack(">I", 9)
    offset = 56
    header = struct.pack(">10I", 0xD00DFEED, offset + len(tree) + len(strings),
                         offset, offset + len(tree), 40, 17, 16, 0,
                         len(strings), len(tree))
    return header + b"\0" * 16 + tree + strings


class PreservationFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.baseline = self.base / "baseline"
        self.baseline.mkdir()
        self.rootfs = self.base / "root"
        self.rootfs.mkdir()
        self.old_kernel = "6.12.112~" + "a" * 32 + "-r1"
        self.kernel = "6.12.112~" + "b" * 32 + "-r1"
        self.original = [
            {"name": "kernel", "version": self.old_kernel, "depends": "libc"},
            {"name": "kmod-mt_wifi", "version": "6.12.112.7.6.7.3-r1",
             "depends": "kernel=" + self.old_kernel},
            {"name": "libc", "version": "1-r1", "depends": ""},
            {"name": "luci-app-honk", "version": "1-r1", "depends": "libc"},
        ]
        self.packages = json.loads(json.dumps(self.original))
        self.packages[0]["version"] = self.kernel
        self.packages[1]["depends"] = "kernel=" + self.kernel
        self.policy = {
            "schema_version": 1,
            "counts": {"packages": 4, "kmods": 1, "luci_apps": 1, "mtk_symbols": 3},
            "wrapper_commit": "c" * 40, "upstream_commit": "d" * 40,
            "original_kernel": self.old_kernel, "kernel_version": "6.12.112",
            "original_fingerprint": "e" * 64,
            "required_packages": [],
            "required_config": {"CONFIG_TARGET_mediatek": "y"},
            "required_kernel_config": {"CONFIG_DEBUG_INFO_BTF": "y"},
        }
        self.write_json(self.baseline / "policy.json", self.policy)
        self.write_json(self.baseline / "original-package-manifest.json",
                        {"packages": self.original})
        (self.baseline / "baseline-mtk-config.txt").write_text(
            'CONFIG_MTK_WIFI_SKU_TYPE="AX6000"\nCONFIG_MTK_DBDC_MODE=y\n'
            '# CONFIG_MTK_MULTI_PROFILE_SUPPORT is not set\n')
        self.config = self.base / ".config"
        self.config.write_text(
            'CONFIG_TARGET_mediatek=y\nCONFIG_MTK_WIFI_SKU_TYPE="AX6000"\n'
            'CONFIG_MTK_DBDC_MODE=y\n# CONFIG_MTK_MULTI_PROFILE_SUPPORT is not set\n' +
            "".join("CONFIG_PACKAGE_" + p["name"] + "=y\n" for p in self.original))
        self.kernel_config = self.base / "kernel.config"
        self.kernel_config.write_text("CONFIG_DEBUG_INFO_BTF=y\n")
        self.dtb = self.base / "board.dtb"
        self.dtb.write_bytes(dtb())
        (self.baseline / "original-board.dtb").write_bytes(dtb())
        self.lock = self.base / "source-lock.json"
        self.write_json(self.lock, {
            "wrapper_commit": "c" * 40, "upstream_commit": "d" * 40,
            "wrapper_tree_hash": "1" * 64, "build_fingerprint": "2" * 64,
            "sources": [{"name": "packages", "url": "https://example.invalid/source",
                         "commit": "3" * 40, "tree_hash": "4" * 64}],
        })
        fixed = self.rootfs / "etc/config/honk"
        fixed.parent.mkdir(parents=True)
        fixed.write_bytes(b"enabled=0\n")
        fixed.chmod(0o644)
        binary = self.rootfs / "bin/tool"
        binary.parent.mkdir()
        binary.write_bytes(b"\x7fELForiginal")
        binary.chmod(0o755)
        (self.rootfs / "bin/link").symlink_to("tool")
        records = [self.file_record(p) for p in [fixed, binary, self.rootfs / "bin/link"]]
        self.write_json(self.baseline / "full-rootfs-manifest.json", {"files": records})
        critical = json.loads(json.dumps(records[:2]))
        critical[0]["policy"] = "exact-bytes-type-mode-owner-or-reviewed-explicit-delta"
        critical[1]["policy"] = "recompiled-version-config-abi-equivalence"
        self.write_json(self.baseline / "critical-files-manifest.json", {"files": critical})
        self.package_path = self.base / "packages.json"
        self.sync_packages()

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value))

    def file_record(self, path):
        info = path.lstat()
        link = path.is_symlink()
        content = os.readlink(path).encode() if link else path.read_bytes()
        record = {"path": "/" + str(path.relative_to(self.rootfs)),
                  "type": "symlink" if link else "file",
                  "mode": format(stat.S_IMODE(info.st_mode), "04o"),
                  "uid": info.st_uid, "gid": info.st_gid,
                  "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        if link:
            record["target"] = os.readlink(path)
        return record

    def sync_packages(self):
        self.write_json(self.package_path, self.packages)
        installed = self.rootfs / "lib/apk/db/installed"
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_text("\n\n".join(
            "P:{name}\nV:{version}\nD:{depends}".format(**p) for p in self.packages) + "\n")

    def invoke(self, phase="image", extra=()):
        output = self.base / "result.json"
        command = [sys.executable, str(SCRIPT), phase, "--baseline-dir", str(self.baseline),
                   "--output", str(output)]
        if phase == "config":
            command += ["--config", str(self.config)]
        else:
            command += ["--rootfs", str(self.rootfs), "--kernel-config", str(self.kernel_config),
                        "--packages", str(self.package_path), "--dtb", str(self.dtb),
                        "--source-lock", str(self.lock)]
        result = subprocess.run(command + list(extra), text=True, capture_output=True)
        data = json.loads(output.read_text()) if output.exists() else {"stderr": result.stderr}
        return result.returncode, data

    def optional_packages(self):
        path = SCRIPT.with_name("mobile-usb-packages.txt")
        names = [line.split("#", 1)[0].strip() for line in path.read_text().splitlines()]
        return path, [name for name in names if name]

    def real_source(self, folder, url):
        path = self.base / folder
        path.mkdir()

        def git(*args):
            return subprocess.check_output([
                "git", "-C", str(path), "-c", "commit.gpgsign=false",
                "-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid", *args], text=True).strip()

        git("init", "-q")
        (path / "recipe").write_text(folder + "\n")
        git("add", "recipe")
        git("commit", "-qm", "fixture")
        git("remote", "add", "origin", url)
        record = provenance.source(path)
        self.assertEqual(record["tree_hash"], hashlib.sha256(
            git("ls-tree", "-r", "--full-tree", "HEAD").encode()).hexdigest())
        return record

    def install_source_records(self):
        lock = json.loads(self.lock.read_text())
        lock["sources"] = [
            self.real_source("one", "https://github.com/One/Packages.git"),
            self.real_source("two", "https://github.com/Two/Packages.git"),
        ]
        self.write_json(self.lock, lock)
        return lock

    def package_metadata(self, abi_packages=None):
        abi_packages = abi_packages or {}
        path = self.base / "packageinfo"
        rows = ["Package: " + package["name"] + "\nType: ipkg\n@@\n"
                for package in self.original
                if package["name"] != "kernel" and package["name"] not in abi_packages]
        rows += ["Package: " + recipe + "\nABI-Version: " + abi + "\nType: ipkg\n@@\n"
                 for recipe, abi in abi_packages.values()]
        path.write_text("\n".join(rows))
        self.config.write_text(self.config.read_text() + "CONFIG_LINUX_6_12=y\n")
        return path

    def test_config_fixture_passes_with_all_original_symbols(self):
        code, result = self.invoke("config")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "pass")

    def test_optional_mobile_packages_survive_defconfig_as_m_or_y(self):
        path, names = self.optional_packages()
        self.assertEqual(len(names), 28)
        self.assertIn("r8152-firmware", names)
        self.config.write_text(self.config.read_text() + "".join(
            "CONFIG_PACKAGE_" + name + ("=y\n" if index % 2 else "=m\n")
            for index, name in enumerate(names)))
        code, result = self.invoke("config", ["--optional-packages", str(path)])
        self.assertEqual(code, 0, result)
        self.assertEqual(result["checks"]["config"]["optional_packages"], sorted(names))

    def test_each_optional_mobile_package_dropped_by_defconfig_fails_early(self):
        path, names = self.optional_packages()
        original = self.config.read_text()
        for missing in names:
            with self.subTest(package=missing):
                self.config.write_text(original + "".join(
                    "CONFIG_PACKAGE_" + name + "=m\n" for name in names if name != missing))
                code, result = self.invoke("config", ["--optional-packages", str(path)])
                self.assertEqual(code, 1, result)
                self.assertIn("CONFIG_PACKAGE_" + missing,
                              [error["subject"] for error in result.get("errors", [])])

    def test_optional_list_cannot_weaken_required_preinstalled_root(self):
        self.policy["required_packages"] = ["kmod-usb-net-cdc-ether"]
        self.write_json(self.baseline / "policy.json", self.policy)
        path = self.base / "optional.txt"
        path.write_text("kmod-usb-net-cdc-ether\n")
        self.config.write_text(self.config.read_text() + "CONFIG_PACKAGE_kmod-usb-net-cdc-ether=m\n")
        code, result = self.invoke("config", ["--optional-packages", str(path)])
        self.assertEqual(code, 1, result)
        self.assertIn("config_symbol", json.dumps(result))

    def test_invalid_or_empty_optional_list_fails_closed(self):
        path = self.base / "optional.txt"
        for content in ("# no packages\n", "kmod-*\n", "usbutils\nusbutils\n"):
            with self.subTest(content=content):
                path.write_text(content)
                code, result = self.invoke("config", ["--optional-packages", str(path)])
                self.assertEqual(code, 1, result)
                self.assertIn("invalid_or_missing_evidence", json.dumps(result))

    def test_metadata_maps_exact_runtime_abi_names_to_recipe_selectors(self):
        self.original[2]["name"] = "libubox20260213"
        self.write_json(self.baseline / "original-package-manifest.json", self.original)
        self.config.write_text(self.config.read_text().replace("CONFIG_PACKAGE_libc=y",
                                                              "CONFIG_PACKAGE_libubox=m"))
        metadata = self.package_metadata({"libubox20260213": ("libubox", "20260213")})
        code, result = self.invoke("config", ["--package-info", str(metadata)])
        self.assertEqual(code, 0, result)
        self.assertEqual(result["checks"]["config"]["original_package_selectors"]["libubox20260213"],
                         "CONFIG_PACKAGE_libubox")
        self.assertEqual(result["checks"]["config"]["original_package_selectors"]["kernel"],
                         "CONFIG_LINUX_6_12")

    def test_metadata_numeric_recipe_suffix_uses_exact_upstream_hyphen_rule(self):
        abi = {"libbz2-1.0": ("libbz2", "1.0"), "libfuse3-3": ("libfuse3", "3"),
               "libnghttp2-14": ("libnghttp2", "14")}
        for name, (recipe, _) in abi.items():
            self.original.append({"name": name, "version": "1-r1", "depends": "libc"})
            self.config.write_text(self.config.read_text() + "CONFIG_PACKAGE_" + recipe + "=m\n")
        self.policy["counts"]["packages"] += len(abi)
        self.write_json(self.baseline / "policy.json", self.policy)
        self.write_json(self.baseline / "original-package-manifest.json", self.original)
        metadata = self.package_metadata(abi)
        code, result = self.invoke("config", ["--package-info", str(metadata)])
        self.assertEqual(code, 0, result)

    def test_metadata_cannot_map_a_changed_abi_or_accept_stale_unknown_symbol(self):
        self.original[2]["name"] = "libubox20260213"
        self.write_json(self.baseline / "original-package-manifest.json", self.original)
        self.config.write_text(self.config.read_text() + "CONFIG_PACKAGE_libubox20260213=y\n"
                               "CONFIG_PACKAGE_libubox=m\n")
        metadata = self.package_metadata({"libubox20260213": ("libubox", "20270101")})
        code, result = self.invoke("config", ["--package-info", str(metadata)])
        self.assertEqual(code, 1, result)
        self.assertIn("libubox20260213", json.dumps(result))
        self.assertIn("package_metadata", json.dumps(result))

    def test_metadata_does_not_append_abi_suffix_to_kmod_recipe(self):
        metadata = self.package_metadata({"kmod-mt_wifi": ("kmod-mt_wifi", "99")})
        code, result = self.invoke("config", ["--package-info", str(metadata)])
        self.assertEqual(code, 0, result)

    def test_metadata_implicit_kernel_requires_matching_linux_selector(self):
        metadata = self.package_metadata()
        self.config.write_text(self.config.read_text().replace("CONFIG_LINUX_6_12=y\n", ""))
        code, result = self.invoke("config", ["--package-info", str(metadata)])
        self.assertEqual(code, 1, result)
        self.assertIn("CONFIG_LINUX_6_12", json.dumps(result))

    def test_metadata_ambiguous_runtime_name_fails_closed(self):
        metadata = self.package_metadata({"libc": ("libc", "1")})
        metadata.write_text(metadata.read_text() + "\nPackage: libc1\nType: ipkg\n@@\n")
        code, result = self.invoke("config", ["--package-info", str(metadata)])
        self.assertEqual(code, 1, result)
        self.assertIn("ambiguous", json.dumps(result))

    def test_reviewed_legacy_input_symbol_is_reported_as_unsupported_not_enabled(self):
        key = "CONFIG_PACKAGE_TURBOACC_INCLUDE_NO_FASTPATH"
        self.policy["required_config"][key] = "y"
        self.policy["unsupported_fragment_symbols"] = {
            key: {"input_value": "y", "reason": "pinned MTK recipe has no Config.in selector"}}
        self.write_json(self.baseline / "policy.json", self.policy)
        code, result = self.invoke("config")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["checks"]["config"]["unsupported_fragment_symbols"],
                         self.policy["unsupported_fragment_symbols"])

    def test_unsupported_symbol_cannot_be_claimed_enabled_in_final_config(self):
        key = "CONFIG_PACKAGE_TURBOACC_INCLUDE_NO_FASTPATH"
        self.policy["required_config"][key] = "y"
        self.policy["unsupported_fragment_symbols"] = {
            key: {"input_value": "y", "reason": "pinned MTK recipe has no Config.in selector"}}
        self.write_json(self.baseline / "policy.json", self.policy)
        self.config.write_text(self.config.read_text() + key + "=y\n")
        code, result = self.invoke("config")
        self.assertEqual(code, 1, result)
        self.assertIn("unsupported_fragment_symbol", json.dumps(result))

    def test_config_missing_original_package_fails(self):
        self.config.write_text(self.config.read_text().replace("CONFIG_PACKAGE_libc=y\n", ""))
        code, result = self.invoke("config")
        self.assertEqual(code, 1, result)
        self.assertIn("libc", json.dumps(result))

    def test_per_device_original_package_m_is_accepted_until_image_gate(self):
        self.config.write_text(self.config.read_text().replace("CONFIG_PACKAGE_libc=y", "CONFIG_PACKAGE_libc=m"))
        code, result = self.invoke("config")
        self.assertEqual(code, 0, result)

    def test_required_increment_root_m_is_not_preinstalled(self):
        self.policy["required_packages"] = ["python3-base"]
        self.write_json(self.baseline / "policy.json", self.policy)
        self.config.write_text(self.config.read_text() + "CONFIG_PACKAGE_python3-base=m\n")
        code, result = self.invoke("config")
        self.assertEqual(code, 1, result)

    def test_omitted_negative_kconfig_symbol_is_disabled(self):
        self.config.write_text(self.config.read_text().replace(
            "# CONFIG_MTK_MULTI_PROFILE_SUPPORT is not set\n", ""))
        code, result = self.invoke("config")
        self.assertEqual(code, 0, result)

    def test_disabled_mtk_symbol_must_exist_and_remain_disabled(self):
        self.config.write_text(self.config.read_text().replace(
            "# CONFIG_MTK_MULTI_PROFILE_SUPPORT is not set", "CONFIG_MTK_MULTI_PROFILE_SUPPORT=y"))
        code, result = self.invoke("config")
        self.assertEqual(code, 1, result)

    def test_original_baseline_missing_record_fails(self):
        self.write_json(self.baseline / "original-package-manifest.json",
                        {"packages": self.original[:-1]})
        code, result = self.invoke("config")
        self.assertEqual(code, 1, result)

    def test_new_abi_and_recompiled_elf_pass_with_diff_record(self):
        (self.rootfs / "bin/tool").write_bytes(b"\x7fELFrecompiled")
        code, result = self.invoke()
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["differences"], result)
        self.assertIn("recompiled", json.dumps(result))

    def test_kmod_wrong_kernel_dependency_fails(self):
        self.packages[1]["depends"] = "kernel=" + self.old_kernel
        self.sync_packages()
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("kernel_dependency", json.dumps(result))

    def test_apk_kmod_file_inventory_cannot_claim_a_missing_module(self):
        installed = self.rootfs / "lib/apk/db/installed"
        installed.write_text(installed.read_text().replace(
            "P:kmod-mt_wifi\n", "P:kmod-mt_wifi\nF:lib/modules/6.12.112\nR:mt_wifi.ko\n"))
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("missing_kmod_file", json.dumps(result))

    def test_apk_module_vermagic_must_match_running_build_release(self):
        module = self.rootfs / "lib/modules/6.12.112/mt_wifi.ko"
        module.parent.mkdir(parents=True)
        module.write_bytes(b"\x7fELF\0vermagic=6.6.93 SMP mod_unload aarch64\0")
        installed = self.rootfs / "lib/apk/db/installed"
        installed.write_text(installed.read_text().replace(
            "P:kmod-mt_wifi\n", "P:kmod-mt_wifi\nF:lib/modules/6.12.112\nR:mt_wifi.ko\n"))
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("module_vermagic", json.dumps(result))

    def test_used_baseline_file_checksum_detects_tampering(self):
        original = self.baseline / "baseline-mtk-config.txt"
        self.write_json(self.baseline / "baseline-sha256.json", {
            original.name: hashlib.sha256(original.read_bytes()).hexdigest()})
        original.write_text(original.read_text().replace('"AX6000"', '"AX3000"'))
        code, result = self.invoke("config")
        self.assertEqual(code, 1, result)
        self.assertIn("baseline_checksum", json.dumps(result))

    def test_original_abi_cannot_be_reused_for_increment_build(self):
        self.packages[0]["version"] = self.old_kernel
        self.packages[1]["depends"] = "kernel=" + self.old_kernel
        self.sync_packages()
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("original_abi", json.dumps(result))

    def test_missing_critical_file_is_failure_not_reviewable(self):
        (self.rootfs / "etc/config/honk").unlink()
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("missing", json.dumps(result))

    def test_critical_hash_change_requires_precise_approval(self):
        (self.rootfs / "etc/config/honk").write_bytes(b"enabled=1\n")
        code, result = self.invoke()
        self.assertEqual(code, 2, result)
        self.assertEqual(result["status"], "review_required")
        pending = [d for d in result["differences"] if d["disposition"] == "review_required"]
        approval = self.base / "approved.json"
        self.write_json(approval, {"approved": [{"id": d["id"], "reason": "explicit fixture review"}
                                               for d in pending]})
        code, result = self.invoke(extra=["--approve-differences", str(approval)])
        self.assertEqual(code, 0, result)

    def test_stale_approval_does_not_approve_another_hash(self):
        (self.rootfs / "etc/config/honk").write_bytes(b"enabled=1\n")
        _, first = self.invoke()
        approval = self.base / "approved.json"
        self.write_json(approval, {"approved": [{"id": d["id"], "reason": "review"}
                                               for d in first["differences"]]})
        (self.rootfs / "etc/config/honk").write_bytes(b"enabled=2\n")
        code, result = self.invoke(extra=["--approve-differences", str(approval)])
        self.assertEqual(code, 2, result)

    def test_package_version_change_requires_review(self):
        self.packages[2]["version"] = "2-r1"
        self.sync_packages()
        code, result = self.invoke()
        self.assertEqual(code, 2, result)
        self.assertIn("package_version", json.dumps(result))

    def test_build_manifest_cannot_hide_different_installed_database(self):
        self.packages[2]["version"] = "2-r1"
        self.write_json(self.package_path, self.packages)
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("package_db_mismatch", json.dumps(result))

    def test_equivalent_dtb_with_renumbered_phandle_passes(self):
        self.dtb.write_bytes(dtb(phandle=55))
        code, result = self.invoke()
        self.assertEqual(code, 0, result)

    def test_original_dtb_symbol_names_are_strings_not_phandle_properties(self):
        # The real public baseline includes /__symbols__:mdio as a path string.
        repo = SCRIPT.parents[1]
        baseline = repo / "ci/baseline/original-board.dtb"
        if not baseline.exists():
            self.skipTest("public baseline not copied yet")
        (self.baseline / "original-board.dtb").write_bytes(baseline.read_bytes())
        self.dtb.write_bytes(baseline.read_bytes())
        code, result = self.invoke()
        self.assertEqual(code, 0, result)

    def test_dtb_semantic_change_fails(self):
        self.dtb.write_bytes(dtb(model="Wrong device"))
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("dtb_semantics", json.dumps(result))

    def test_missing_kernel_evidence_fails(self):
        self.kernel_config.unlink()
        code, result = self.invoke()
        self.assertEqual(code, 1, result)

    def test_symlink_parent_escape_fails_without_reading_outside_root(self):
        fixed = self.rootfs / "etc/config/honk"
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "honk").write_bytes(fixed.read_bytes())
        fixed.unlink()
        fixed.parent.rmdir()
        fixed.parent.symlink_to(outside, target_is_directory=True)
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("symlink_parent", json.dumps(result))

    def test_missing_source_lock_cannot_pass(self):
        self.lock.unlink()
        code, result = self.invoke()
        self.assertEqual(code, 1, result)

    def test_source_fingerprint_cannot_impersonate_original(self):
        lock = json.loads(self.lock.read_text())
        lock["build_fingerprint"] = self.policy["original_fingerprint"]
        self.write_json(self.lock, lock)
        code, result = self.invoke()
        self.assertEqual(code, 1, result)

    def test_actual_provenance_sources_with_equal_basenames_pass(self):
        lock = self.install_source_records()
        self.assertEqual([item["name"] for item in lock["sources"]],
                         ["github.com/one/packages", "github.com/two/packages"])
        code, result = self.invoke()
        self.assertEqual(code, 0, result)
        self.assertEqual(result["checks"]["source_lock"]["recorded_sources"], 2)

    def test_actual_provenance_duplicate_source_name_fails(self):
        lock = self.install_source_records()
        lock["sources"][1]["name"] = lock["sources"][0]["name"]
        self.write_json(self.lock, lock)
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("source_identity", json.dumps(result))

    def test_actual_provenance_duplicate_url_with_distinct_names_fails(self):
        lock = self.install_source_records()
        lock["sources"][1]["url"] = lock["sources"][0]["url"]
        self.write_json(self.lock, lock)
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("source_identity", json.dumps(result))

    def test_actual_provenance_git_tree_id_is_not_tree_hash(self):
        lock = self.install_source_records()
        lock["sources"][0]["tree_hash"] = lock["sources"][0]["tree"]
        self.write_json(self.lock, lock)
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn(".tree_hash", json.dumps(result))

    def test_nonobject_source_inventory_fails_closed(self):
        lock = json.loads(self.lock.read_text())
        lock["sources"] = [None]
        self.write_json(self.lock, lock)
        code, result = self.invoke()
        self.assertEqual(code, 1, result)
        self.assertIn("source_identity", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
