"""Exercise capture against a small build tree; external Linux tools are fixtures."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
CAPTURE = REPO / "ci/capture.sh"


def mock_fakeroot(test, root):
    """External tool fixture; real owner/device preservation is tested on Linux."""
    tools = root / "test-tools"
    tools.mkdir()
    executable = tools / "fakeroot"
    executable.write_text('''#!/usr/bin/env python3
import os, pathlib, subprocess, sys
args = sys.argv[1:]
if '-s' in args:
    pathlib.Path(args[args.index('-s') + 1]).write_text('fixture-state')
command = args[args.index('--') + 1:]
sys.exit(subprocess.run(command, env=dict(os.environ, FAKEROOTKEY='fixture')).returncode)
''')
    executable.chmod(0o755)
    environment = patch.dict(os.environ, {"PATH": str(tools) + os.pathsep + os.environ["PATH"]})
    environment.start()
    test.addCleanup(environment.stop)


def fdt(nodes):
    strings = bytearray()
    offsets = {}
    tree = bytearray()

    def word(value):
        return struct.pack(">I", value)

    def pad(data):
        return data + b"\0" * (-len(data) % 4)

    def node(name, props, children):
        tree.extend(word(1) + pad(name.encode() + b"\0"))
        for key, value in props.items():
            if key not in offsets:
                offsets[key] = len(strings)
                strings.extend(key.encode() + b"\0")
            tree.extend(word(3) + word(len(value)) + word(offsets[key]) + pad(value))
        for child in children:
            node(*child)
        tree.extend(word(2))

    node(*nodes)
    tree.extend(word(9))
    offset = 56
    return struct.pack(">10I", 0xD00DFEED, offset + len(tree) + len(strings),
                       offset, offset + len(tree), 40, 17, 16, 0,
                       len(strings), len(tree)) + b"\0" * 16 + tree + strings


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        mock_fakeroot(self, self.base)
        self.build = self.base / "build"
        self.output = self.base / "output"
        self.mobile_policy = self.base / "mobile-packages.txt"
        self.mobile_policy.write_text("# fixture optional feed package\nkernel\n")
        self.bin = self.build / "bin"
        self.target = self.bin / "targets/mediatek/filogic"
        self.feed = self.bin / "packages/aarch64_cortex-a53/base"
        self.feed.mkdir(parents=True)
        self.target.mkdir(parents=True)
        (self.build / ".config").write_text("CONFIG_USE_APK=y\nCONFIG_SIGNED_PACKAGES=y\n")
        (self.build / "private-key.pem").write_text("-----BEGIN PRIVATE KEY-----\nSECRET\n")
        (self.build / "public-key.pem").write_text("-----BEGIN PUBLIC KEY-----\nPUBLIC\n-----END PUBLIC KEY-----\n")
        shutil.copy2(self.build / "public-key.pem", self.build / "image-key.pem")
        self.kernel = self.build / "build_dir/target-aarch64/linux-mediatek_filogic/linux-6.12.112"
        self.kernel.mkdir(parents=True)
        (self.kernel / ".config").write_text("CONFIG_USB_NET_CDCETHER=m\n")
        (self.kernel / "Module.symvers").write_text("0x1234\tsymbol\tvmlinux\tEXPORT_SYMBOL\n")
        for name in ("config.buildinfo", "feeds.buildinfo", "version.buildinfo"):
            (self.target / name).write_text(name + "\n")
        self.package = {"name": "kernel", "version": "6.12.112~test-r1", "arch": "aarch64_cortex-a53"}
        (self.feed / "kernel-6.12.112~test-r1.apk").write_text(json.dumps({"info": self.package}))
        (self.feed / "packages.adb").write_text(json.dumps({"packages": [self.package]}))
        self.dtb = fdt(("", {"compatible": b"clx,s20p\0"}, []))
        self.fit = fdt(("", {}, [
            ("images", {}, [("fdt-1", {"type": b"flat_dt\0", "data": self.dtb}, [])]),
            ("configurations", {"default": b"conf-1\0"}, [("conf-1", {"fdt": b"fdt-1\0"}, [])]),
        ]))
        self.image = self.target / "immortalwrt-mediatek-filogic-clx_s20p-squashfs-sysupgrade.bin"
        with tarfile.open(self.image, "w") as archive:
            for name, data in (("kernel", self.fit), ("root", b"hsqsfixture")):
                item = tarfile.TarInfo("sysupgrade-clx_s20p/" + name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        host = self.build / "staging_dir/host/bin"
        host.mkdir(parents=True)
        self.metadata = self.image.with_suffix(".metadata.json")
        self.metadata.write_text(json.dumps({"supported_devices": ["clx,s20p"],
                                             "version": {"target": "mediatek/filogic"}}))
        self.write_tool(host / "fwtool", '''
import pathlib, shutil, sys
source = pathlib.Path(sys.argv[-1]).with_suffix('.metadata.json')
shutil.copy2(source, pathlib.Path(sys.argv[sys.argv.index('-i') + 1]))
''')
        # tools/squashfs4 installs this exact name in the locked upstream.
        self.write_tool(host / "unsquashfs4", '''
import pathlib, sys
if '-lln' in sys.argv:
    print('drwxr-xr-x 0/0 0 2026-10-17 00:00 squashfs-root')
    sys.exit(0)
root = pathlib.Path(sys.argv[sys.argv.index('-d') + 1])
(root / 'lib/apk/db').mkdir(parents=True)
(root / 'lib/apk/db/installed').write_text('P:kernel\\nV:6.12.112~test-r1\\nA:aarch64_cortex-a53\\n\\n')
(root / 'etc/apk/keys').mkdir(parents=True)
(root / 'etc/apk/keys/public-key.pem').write_bytes((pathlib.Path(__file__).parents[3] / 'image-key.pem').read_bytes())
''')
        self.write_tool(host / "apk", '''
import json, pathlib, sys
args = sys.argv[1:]
if '--version' in args:
    print('apk-tools 3.0.5 test fixture')
elif 'verify' in args:
    for arg in args[args.index('verify') + 1:]:
        if arg.endswith('.apk') or pathlib.Path(arg).read_text().startswith('UNTRUSTED'):
            sys.exit(1)
elif 'adbdump' in args:
    source = pathlib.Path(args[-1])
    metadata = json.loads(source.read_text())
    if source.suffix == '.apk':
        metadata['info'].setdefault('hashes', 'a' * 40)
    else:
        for record in metadata['packages']:
            record.setdefault('hashes', 'b' * 64)
            for package in source.parent.glob('*.apk'):
                info = json.loads(package.read_text())['info']
                if (info['name'], info['version']) == (record['name'], record['version']):
                    record.setdefault('file-size', package.stat().st_size)
    print(json.dumps(metadata))
elif 'fetch' in args:
    import shutil
    dest = pathlib.Path(args[args.index('--output') + 1])
    feed = pathlib.Path(args[args.index('--repository') + 1]).parent
    names = args[args.index('--output') + 2:]
    for source in feed.glob('*.apk'):
        package = json.loads(source.read_text())
        if package.get('corrupt'):
            sys.exit(1)
        if package['info']['name'] in names:
            shutil.copy2(source, dest / source.name)
else:
    sys.exit('unexpected apk invocation: ' + repr(args))
''')

    def write_tool(self, path, source):
        path.write_text("#!/usr/bin/env python3\n" + source)
        path.chmod(0o755)

    def run_capture(self):
        environment = os.environ.copy()
        environment["S20P_MOBILE_PACKAGES_FILE"] = str(self.mobile_policy)
        return subprocess.run(["bash", str(CAPTURE), str(self.build), str(self.output)],
                              capture_output=True, text=True, env=environment)

    def test_capture_preserves_bin_selected_rootfs_kernel_and_public_key(self):
        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.output / "bin" / self.image.relative_to(self.bin)).read_bytes(), self.image.read_bytes())
        self.assertEqual((self.output / "inspection/board.dtb").read_bytes(), self.dtb)
        self.assertTrue((self.output / "inspection/rootfs/lib/apk/db/installed").is_file())
        self.assertTrue((self.output / "metadata/kernel/Module.symvers").is_file())
        self.assertTrue((self.output / "metadata/final.config").is_file())
        self.assertTrue((self.output / "public-keys/public-key.pem").is_file())
        self.assertFalse(any(p.name == "private-key.pem" for p in self.output.rglob("*")))
        self.assertTrue((self.output / "reports/capture.json").is_file())
        self.assertTrue((self.output / "metadata/squashfs-listing.txt").is_file())
        safety = json.loads((self.output / "reports/image-safety.json").read_text())
        self.assertEqual(safety["supported_devices"], ["clx,s20p"])
        self.assertEqual(safety["partitions"]["kernel"]["capacity"], 32 * 1024 * 1024)
        self.assertEqual(safety["partitions"]["rootfs"]["capacity"], 2 * 1024 ** 3)
        trust = json.loads((self.output / "reports/signed-feeds.json").read_text())["image_trust"]
        self.assertEqual(trust["status"], "pass")
        self.assertEqual(trust["comparison"], "exact-public-key-bytes")
        self.assertEqual(trust["matching_key_paths"], ["/etc/apk/keys/public-key.pem"])

    def test_wrong_supported_device_is_rejected(self):
        self.metadata.write_text(json.dumps({"supported_devices": ["clx,s20l"]}))
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("supported_devices", result.stderr)

    def test_kernel_member_must_fit_32_mib(self):
        entry = tarfile.TarInfo("sysupgrade-clx_s20p/kernel")
        entry.size = 32 * 1024 * 1024 + 1
        self.image.write_bytes(entry.tobuf() + b"\0" * 1024)
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kernel partition capacity", result.stderr)

    def test_root_member_must_fit_2_gib(self):
        entry = tarfile.TarInfo("sysupgrade-clx_s20p/root")
        entry.size = 2 * 1024 ** 3 + 1
        self.image.write_bytes(entry.tobuf() + b"\0" * 1024)
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rootfs partition capacity", result.stderr)

    def test_root_member_must_leave_aligned_overlay_marker_space(self):
        entry = tarfile.TarInfo("sysupgrade-clx_s20p/root")
        entry.size = 2 * 1024 ** 3 - 1
        self.image.write_bytes(entry.tobuf() + b"\0" * 1024)
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("overlay", result.stderr)

    def test_fails_closed_if_bin_contains_private_key(self):
        (self.feed / "leaked.pem").write_text("-----BEGIN EC PRIVATE KEY-----\nSECRET\n")
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr.lower())
        self.assertFalse((self.output / "reports/capture.json").exists())

    def test_fails_closed_for_symlink_outside_bin(self):
        (self.feed / "escaped.apk").symlink_to(self.build / "private-key.pem")
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())

    def test_dereferences_contained_package_symlink(self):
        (self.target / "packages").symlink_to(self.feed)
        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        copied = self.output / "bin/targets/mediatek/filogic/packages/kernel-6.12.112~test-r1.apk"
        self.assertTrue(copied.is_file())
        self.assertFalse(copied.is_symlink())

    def test_missing_kernel_symbols_prevents_completion(self):
        (self.kernel / "Module.symvers").unlink()
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Module.symvers", result.stderr)

    def test_unsigned_index_cannot_complete_capture(self):
        (self.feed / "packages.adb").write_text("UNTRUSTED")
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("command failed", result.stderr)
        self.assertFalse((self.output / "reports/capture.json").exists())

    def test_signed_index_metadata_must_match_packages(self):
        altered = dict(self.package, version="6.12.111~wrong-r1")
        (self.feed / "packages.adb").write_text(json.dumps({"packages": [altered]}))
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata", result.stderr.lower())

    def test_image_must_trust_the_preserved_feed_public_key(self):
        (self.build / "image-key.pem").write_text("-----BEGIN PUBLIC KEY-----\nOTHER-KEY\n-----END PUBLIC KEY-----\n")
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("image trust", result.stderr)
        self.assertFalse((self.output / "reports/capture.json").exists())

    def test_image_trust_cannot_use_a_host_keyring_outside_rootfs(self):
        external = self.base / "host-keyring"
        external.mkdir()
        shutil.copy2(self.build / "public-key.pem", external / "public-key.pem")
        unsquashfs = self.build / "staging_dir/host/bin/unsquashfs4"
        source = unsquashfs.read_text()
        start = source.index("(root / 'etc/apk/keys').mkdir(parents=True)")
        source = source[:start] + (
            "(root / 'etc/apk').mkdir(parents=True)\n"
            f"(root / 'etc/apk/keys').symlink_to({str(external)!r})\n")
        unsquashfs.write_text(source)
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("image trust keyring", result.stderr)
        self.assertFalse((self.output / "reports/capture.json").exists())

    def test_native_fetch_rejects_corrupt_unsigned_payload(self):
        package = self.feed / "kernel-6.12.112~test-r1.apk"
        package.write_text(json.dumps({"info": self.package, "corrupt": True}))
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("command failed", result.stderr)
        self.assertFalse((self.output / "reports/capture.json").exists())

    def test_all_optional_mobile_packages_must_be_in_signed_feed(self):
        self.mobile_policy.write_text("# optional\nkernel\nkmod-usb-net-rndis\n")
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kmod-usb-net-rndis", result.stderr)

    def test_optional_kmod_must_have_the_exact_image_kernel_dependency(self):
        package = {"name": "kmod-usb-net-rndis", "version": "6.12.112-r1",
                   "arch": "aarch64_cortex-a53", "depends": ["kernel=6.12.112~wrong-r1"]}
        (self.feed / "kmod-usb-net-rndis-6.12.112-r1.apk").write_text(json.dumps({"info": package}))
        (self.feed / "packages.adb").write_text(json.dumps({"packages": [self.package, package]}))
        self.mobile_policy.write_text("kmod-usb-net-rndis\n")
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact image kernel dependency", result.stderr)
        self.assertFalse((self.output / "reports/capture.json").exists())

    def test_optional_kmod_matching_the_image_kernel_can_be_preserved(self):
        package = {"name": "kmod-usb-net-rndis", "version": "6.12.112-r1",
                   "arch": "aarch64_cortex-a53", "depends": ["kernel=" + self.package["version"]]}
        (self.feed / "kmod-usb-net-rndis-6.12.112-r1.apk").write_text(json.dumps({"info": package}))
        (self.feed / "packages.adb").write_text(json.dumps({"packages": [self.package, package]}))
        self.mobile_policy.write_text("kmod-usb-net-rndis\n")
        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads((self.output / "reports/signed-feeds.json").read_text())
        self.assertEqual(report["kmod_packages_kernel_checked"], 1)

    def test_audit_writes_hashes_for_published_files(self):
        self.output.mkdir()
        published = self.output / "public-file.txt"
        published.write_text("same-build evidence\n")
        excluded = self.output / "inspection/rootfs/etc"
        excluded.mkdir(parents=True)
        (excluded / "example").write_text("also in raw squashfs\n")
        result = subprocess.run(["bash", str(CAPTURE), "--audit-output", str(self.output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / "SHA256SUMS").read_text(),
                         hashlib.sha256(published.read_bytes()).hexdigest() + "  public-file.txt\n")

    def test_private_key_in_log_prevents_evidence_upload_audit(self):
        self.output.mkdir()
        (self.output / "build.log").write_text("-----BEGIN PRIVATE KEY-----\nsecret\n")
        result = subprocess.run(["bash", str(CAPTURE), "--audit-output", str(self.output)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)
        self.assertFalse((self.output / "SHA256SUMS").exists())

    def audit_file(self, filename, data):
        self.output.mkdir()
        (self.output / filename).write_bytes(data)
        return subprocess.run(["bash", str(CAPTURE), "--audit-output", str(self.output)],
                              capture_output=True, text=True)

    @staticmethod
    def elf_with_data(data, flags=4):
        # One read-only PT_LOAD containing ELF64 constants, as in crypto libraries.
        return (b"\x7fELF\x02\x01\x01" + b"\0" * 9
                + struct.pack("<HHIQQQIHHHHHH", 3, 183, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
                + struct.pack("<IIQQQQQQ", 1, flags, 0, 0, 0, 120 + len(data), 120 + len(data), 1)
                + data)

    def test_elf_private_key_parser_label_is_not_private_key_material(self):
        result = self.audit_file("parser.so", self.elf_with_data(
            b"\0-----BEGIN RSA PRIVATE KEY-----\0-----END RSA PRIVATE KEY-----\0"))
        self.assertEqual(result.returncode, 0, result.stderr)

    @staticmethod
    def relocatable_with_data(data, flags=2):
        # Kernel modules use allocated read-only sections, with no program table.
        return (b"\x7fELF\x02\x01\x01" + b"\0" * 9
                + struct.pack("<HHIQQQIHHHHHH", 1, 183, 1, 0, 0, 64, 0, 64, 0, 0, 64, 2, 0)
                + b"\0" * 64
                + struct.pack("<IIQQQQIIQQ", 0, 1, flags, 0, 192, len(data), 0, 0, 1, 0)
                + data)

    def test_kernel_module_readonly_parser_label_is_accepted(self):
        result = self.audit_file("wifi.ko", self.relocatable_with_data(
            b"-----BEGIN EC PRIVATE KEY-----\0"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_kernel_module_writable_parser_label_is_rejected(self):
        result = self.audit_file("wifi.ko", self.relocatable_with_data(
            b"-----BEGIN EC PRIVATE KEY-----\0", flags=3))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_kernel_module_unknown_key_is_rejected(self):
        result = self.audit_file("wifi.ko", self.relocatable_with_data(
            b"-----BEGIN EC PRIVATE KEY-----\nUNKNOWN\n-----END EC PRIVATE KEY-----\0"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    @staticmethod
    def hello_sample():
        fixture = REPO / "ci/tests/fixtures/cloudflared-public-hello-key.json"
        value = json.loads(fixture.read_text())["helloKey"]
        return value[value.index("-----BEGIN EC PRIVATE KEY-----"):].encode()

    def test_exact_public_hello_key_in_go_string_pool_is_accepted(self):
        result = self.audit_file("hello", self.elf_with_data(self.hello_sample() + b"GC cycle"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_go_sample_manifest_matches_source_fixture(self):
        fixture = json.loads((REPO / "ci/tests/fixtures/cloudflared-public-hello-key.json").read_text())
        policy = json.loads((REPO / "ci/public-test-keys.json").read_text())["go_samples"]
        self.assertEqual(len(policy), 1)
        self.assertEqual(policy[0]["source"]["url"], fixture["source"]["url"])
        self.assertEqual(policy[0]["source"]["sha256"], fixture["source"]["sha256"])
        self.assertEqual(policy[0]["bytes"], len(self.hello_sample()))
        self.assertEqual(policy[0]["sha256"], hashlib.sha256(self.hello_sample()).hexdigest())

    def test_public_go_sample_across_scan_boundary_is_accepted(self):
        value = self.hello_sample()
        data = b"\0" * (1024 * 1024 - 120 - 15) + value + b"GC cycle"
        result = self.audit_file("hello", self.elf_with_data(data))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_go_sample_in_writable_segment_is_rejected(self):
        result = self.audit_file("hello", self.elf_with_data(self.hello_sample() + b"GC cycle", flags=6))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_modified_hello_key_in_go_string_pool_is_rejected(self):
        value = bytearray(self.hello_sample())
        first_body_byte = value.index(b"\n") + 1
        value[first_body_byte] = ord("N") if value[first_body_byte] != ord("N") else ord("M")
        result = self.audit_file("hello", self.elf_with_data(value + b"GC cycle"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_public_hello_key_in_non_elf_file_is_rejected(self):
        result = self.audit_file("hello.pem", self.hello_sample())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_unknown_key_after_go_sample_is_rejected(self):
        result = self.audit_file("hello", self.elf_with_data(
            self.hello_sample() + b"-----BEGIN PRIVATE KEY-----\nUNKNOWN\0"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    @staticmethod
    def public_placeholders():
        return json.loads((REPO / "ci/tests/fixtures/openclash-public-placeholders.json").read_text())["vectors"]

    def test_exact_public_documentation_placeholders_are_accepted(self):
        value = b"\n".join(e["literal"].encode() for e in self.public_placeholders())
        result = self.audit_file("documentation.txt", value)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_placeholder_policy_matches_source_fixture(self):
        policy = json.loads((REPO / "ci/public-test-keys.json").read_text())["placeholders"]
        self.assertEqual(len(policy), 2)
        for expected, fixture in zip(policy, self.public_placeholders()):
            self.assertEqual(expected["source"], fixture["source"])
            self.assertEqual(expected["bytes"], len(fixture["literal"].encode()))
            self.assertEqual(expected["sha256"], hashlib.sha256(fixture["literal"].encode()).hexdigest())

    def test_placeholder_across_scan_boundary_is_accepted(self):
        value = self.public_placeholders()[0]["literal"].encode()
        result = self.audit_file("documentation.txt", b"x" * (1024 * 1024 - 15) + value)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_replacing_placeholder_body_with_key_material_is_rejected(self):
        value = self.public_placeholders()[0]["literal"].replace("MIIE...example", "QUJDREVGR0g=").encode()
        result = self.audit_file("documentation.txt", value)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_unknown_key_after_exact_placeholder_is_rejected(self):
        value = self.public_placeholders()[0]["literal"].encode()
        result = self.audit_file("documentation.txt", value + b"-----BEGIN PRIVATE KEY-----\nUNKNOWN")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_elf_private_key_label_split_across_scan_boundary_is_safe(self):
        data = b"\0" * (1024 * 1024 - 120 - 15) + b"-----BEGIN PRIVATE KEY-----\0"
        result = self.audit_file("parser.so", self.elf_with_data(data))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_private_key_in_elf_is_rejected(self):
        result = self.audit_file("parser.so", self.elf_with_data(
            b"\0-----BEGIN PRIVATE KEY-----\nQUJD\n-----END PRIVATE KEY-----\n\0"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    @staticmethod
    def public_test_vectors():
        fixture = REPO / "ci/tests/fixtures/gnutls-public-test-keys.json"
        return [value.encode() for value in json.loads(fixture.read_text())["vectors"].values()]

    def test_public_vector_policy_matches_pinned_source_fixture(self):
        policy = json.loads((REPO / "ci/public-test-keys.json").read_text())
        fixture = json.loads((REPO / "ci/tests/fixtures/gnutls-public-test-keys.json").read_text())
        self.assertEqual(policy["source"]["url"], fixture["source"])
        self.assertEqual(len(policy["vectors"]), 12)
        self.assertEqual({entry["symbol"] for entry in policy["vectors"]}, set(fixture["vectors"]))
        for entry in policy["vectors"]:
            value = fixture["vectors"][entry["symbol"]].encode()
            self.assertEqual(entry["bytes"], len(value))
            self.assertEqual(entry["sha256"], hashlib.sha256(value).hexdigest())

    def test_exact_public_gnutls_vectors_in_elf_are_accepted(self):
        result = self.audit_file("crypto.so", self.elf_with_data(
            b"\0" + b"\0".join(self.public_test_vectors()) + b"\0"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_vector_split_across_scan_boundary_is_accepted(self):
        vector = self.public_test_vectors()[0]
        data = b"\0" * (1024 * 1024 - 120 - len(vector) // 2) + vector + b"\0"
        result = self.audit_file("crypto.so", self.elf_with_data(data))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_vector_in_writable_segment_is_rejected(self):
        result = self.audit_file("crypto.so", self.elf_with_data(
            self.public_test_vectors()[0] + b"\0", flags=6))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_public_vector_appended_outside_load_segment_is_rejected(self):
        result = self.audit_file("crypto.so", self.elf_with_data(b"\0")
                                 + self.public_test_vectors()[0] + b"\0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_modified_public_vector_is_rejected(self):
        vector = self.public_test_vectors()[0].replace(b"MII", b"NII", 1)
        result = self.audit_file("crypto.so", self.elf_with_data(vector + b"\0"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_appending_private_material_to_known_vector_is_rejected(self):
        vector = self.public_test_vectors()[0]
        result = self.audit_file("crypto.so", self.elf_with_data(vector + b"SECRET\0"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_unknown_key_after_known_vector_is_rejected(self):
        data = self.public_test_vectors()[0] + b"\0-----BEGIN PRIVATE KEY-----\nSECRET\0"
        result = self.audit_file("crypto.so", self.elf_with_data(data))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_public_vector_in_non_elf_file_is_rejected(self):
        result = self.audit_file("unknown.pem", self.public_test_vectors()[0])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_pem_marker_in_non_elf_binary_is_still_rejected(self):
        result = self.audit_file("unknown.bin", b"\0-----BEGIN PRIVATE KEY-----\0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_invalid_elf_header_cannot_bypass_private_key_scan(self):
        result = self.audit_file("fake.so", b"\x7fELF" + b"\0" * 60
                                 + b"-----BEGIN PRIVATE KEY-----\0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_binary_dropbear_private_key_is_rejected_by_filename(self):
        self.output.mkdir()
        (self.output / "dropbear_rsa_host_key").write_bytes(b"\x00\x00\x00\x07ssh-rsa\x00secret")
        result = subprocess.run(["bash", str(CAPTURE), "--audit-output", str(self.output)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_audit_reports_multiple_unsafe_files_and_blocks_upload(self):
        self.output.mkdir()
        for name in ("first.pem", "second.pem"):
            (self.output / name).write_bytes(b"-----BEGIN PRIVATE KEY-----\nUNKNOWN\n")
        result = subprocess.run(["bash", str(CAPTURE), "--audit-output", str(self.output)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("first.pem", result.stderr)
        self.assertIn("second.pem", result.stderr)
        self.assertFalse((self.output / "SHA256SUMS").exists())

    def test_private_material_cannot_disguise_as_public_key(self):
        (self.build / "public-key.pem").write_text(
            "-----BEGIN PUBLIC KEY-----\npublic\n-----BEGIN PRIVATE KEY-----\nsecret\n")
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private", result.stderr)

    def test_output_inside_build_tree_is_rejected(self):
        result = subprocess.run(["bash", str(CAPTURE), str(self.build), str(self.build / "output")],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside BUILD_DIR", result.stderr)

    def test_unindexed_apk_prevents_completion(self):
        orphan = self.bin / "packages/aarch64_cortex-a53/orphan"
        orphan.mkdir()
        (orphan / "orphan.apk").write_text(json.dumps({"info": self.package}))
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("index", result.stderr.lower())

    def test_workflow_schedule_and_candidate_policy(self):
        workflow = REPO / ".github/workflows/s20p-daily.yml"
        self.assertTrue(workflow.is_file(), "daily workflow has not been implemented")
        source = workflow.read_text()
        self.assertIn("20 20 * * *", source)
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("--prerelease", source)
        self.assertIn("--latest=false", source)
        self.assertNotIn("secrets: inherit", source)
        self.assertIn("needs.build.result == 'success'", source)
        self.assertIn("github.event_name == 'workflow_dispatch'", source)
        self.assertIn("github.ref == 'refs/heads/codex/s20p-b-25-12'", source)
        self.assertIn('expected_digest="${GH_EXPECTED_ARTIFACT_DIGEST#sha256:}"', source)
        self.assertIn('[[ $expected_digest =~ ^[0-9a-f]{64}$ ]]', source)
        self.assertIn('[[ $api_digest == "sha256:$expected_digest" ]]', source)


class BuildPolicyTests(unittest.TestCase):
    """Run the real wrapper with tiny builders; never invoke a compiler/network."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        mock_fakeroot(self, self.repo)
        (self.repo / "ci/baseline").mkdir(parents=True)
        (self.repo / "wrt_core").mkdir()
        for name in ("build.sh", "capture.sh"):
            shutil.copy2(REPO / "ci" / name, self.repo / "ci" / name)
        shutil.copy2(REPO / "ci/public-test-keys.json", self.repo / "ci/public-test-keys.json")
        for name in ("source_lock.py", "provenance.py", "sources.lock.json", "mobile-usb-packages.txt"):
            (self.repo / "ci" / name).write_text("")
        (self.repo / "ci/baseline/policy.json").write_text("{}")
        (self.repo / "wrt_core/pre_clone_action.sh").write_text('''#!/usr/bin/env bash
set -eu
[[ $S20P_AUTOBUILD == 1 && $S20P_CAPTURE_DIR == "$PWD/output" ]]
[[ $S20P_SOURCE_LOCK == "$PWD/ci/sources.lock.json" ]]
mkdir action_build
''')
        (self.repo / "build.sh").write_text('''#!/usr/bin/env bash
set -eu
if [[ ${S20P_TEST_BUILD_EXIT:-0} != 0 ]]; then exit "$S20P_TEST_BUILD_EXIT"; fi
mkdir -p output/reports output/inspection/rootfs
printf '%s\\n' fixture-state > output/.fakeroot.state
printf '%s\n' '{"status":"captured"}' > output/reports/capture.json
printf '%s\n' '{"status":"pass"}' > output/reports/signed-feeds.json
printf '%s\n' raw-root > output/inspection/rootfs.squashfs
printf '%s\n' extracted-root > output/inspection/rootfs/example
''')
        (self.repo / "ci/preservation.py").write_text('''import json, os, pathlib, sys
status = int(os.environ.get('S20P_TEST_GATE_EXIT', '0'))
approval = pathlib.Path('ci/approved-differences.json')
if approval.exists():
    assert pathlib.Path(sys.argv[sys.argv.index('--approve-differences') + 1]).resolve() == approval.resolve()
elif '--approve-differences' in sys.argv:
    sys.exit('unexpected approval file')
path = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])
path.write_text(json.dumps({'status': {0:'pass', 1:'fail', 2:'review_required'}[status]}))
sys.exit(status)
''')
        self.github_output = self.repo / "github-output"

    def build(self, gate=0, compile_exit=0):
        environment = dict(os.environ, S20P_TEST_GATE_EXIT=str(gate),
                           S20P_TEST_BUILD_EXIT=str(compile_exit), GITHUB_RUN_ID="123",
                           GITHUB_RUN_ATTEMPT="1", GITHUB_OUTPUT=str(self.github_output))
        return subprocess.run(["bash", str(self.repo / "ci/build.sh")],
                              capture_output=True, text=True, env=environment)

    def test_review_required_retains_evidence_without_candidate(self):
        result = self.build(gate=2)
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        status = json.loads((self.repo / "output/reports/build-status.json").read_text())
        self.assertEqual(status["status"], "review_required")
        self.assertFalse(status["stable_eligible"])
        self.assertFalse(self.github_output.exists())
        self.assertFalse((self.repo / "output/release").exists())
        self.assertTrue((self.repo / "output/SHA256SUMS").is_file())

    def test_passed_gate_builds_candidate_but_never_marks_stable(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = json.loads((self.repo / "output/reports/build-status.json").read_text())
        self.assertFalse(status["stable_eligible"])
        self.assertEqual(status["live_acceptance"], "not-run")
        self.assertIn("publish_candidate=true", self.github_output.read_text())
        archive = self.repo / "output/release/s20p-samebuild-123-1.tar.gz"
        with tarfile.open(archive) as stream:
            names = stream.getnames()
        self.assertIn("./inspection/rootfs.squashfs", names)
        self.assertNotIn("./inspection/rootfs/example", names)
        self.assertNotIn("./release", names)

    def test_build_failure_propagates_and_cannot_publish(self):
        result = self.build(compile_exit=7)
        self.assertEqual(result.returncode, 7, result.stderr + result.stdout)
        status = json.loads((self.repo / "output/reports/build-status.json").read_text())
        self.assertEqual(status["build_exit"], 7)
        self.assertFalse(self.github_output.exists())
        self.assertFalse((self.repo / "output/release").exists())

    def test_log_write_failure_prevents_candidate_even_if_compile_succeeds(self):
        tee = self.repo / "test-tools/tee"
        tee.write_text("#!/usr/bin/env bash\ncat >/dev/null\nexit 74\n")
        tee.chmod(0o755)
        result = self.build()
        self.assertEqual(result.returncode, 74, result.stderr + result.stdout)
        status = json.loads((self.repo / "output/reports/build-status.json").read_text())
        self.assertEqual(status["build_exit"], 0)
        self.assertEqual(status["log_exit"], 74)
        self.assertFalse(self.github_output.exists())
        self.assertFalse((self.repo / "output/release").exists())

    def test_exact_review_file_is_forwarded_only_when_present(self):
        (self.repo / "ci/approved-differences.json").write_text('{"approved": []}')
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
