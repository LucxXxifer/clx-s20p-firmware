"""Build integration must reject drift and require the fixed image payload."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class DashboardTests(unittest.TestCase):
    def module(self):
        path = ROOT / "ci/dashboard.py"
        self.assertTrue(path.is_file(), "daily build lacks the dashboard preparation/image gate")
        spec = importlib.util.spec_from_file_location("dashboard", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_image_requires_all_five_files_and_binds_payload(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            payload = root / "rootfs.squashfs"
            payload.write_bytes(b"squashfs payload")
            with self.assertRaisesRegex(ValueError, "missing|payload|dashboard"):
                mod.inspect_rootfs(root, payload)
            for path, source in mod.PAYLOAD_FILES.items():
                dest = root / path.lstrip("/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(source.read_bytes())
            # The actual built JS must match the pinned generated hash too.
            js = root / "www/luci-static/quickstart/index.js"
            js.parent.mkdir(parents=True, exist_ok=True)
            js.write_bytes(b"unpatched frontend")
            with self.assertRaisesRegex(ValueError, "dashboard|hash|SHA"):
                mod.inspect_rootfs(root, payload)

    def test_prepare_rejects_unreviewed_source_before_writes(self):
        mod = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            base = root / "feeds/custom_feed/luci-app-quickstart"
            for name in mod.SOURCE_PINS:
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"upstream changed")
            before = {p: p.read_bytes() for p in base.rglob("*") if p.is_file()}
            with self.assertRaisesRegex(ValueError, "source|SHA|hash"):
                mod.prepare(root)
            self.assertEqual(before, {p: p.read_bytes() for p in base.rglob("*") if p.is_file()})

    def test_release_report_rejects_incomplete_or_changed_payload(self):
        mod = self.module()
        valid = mod.report("a" * 64)
        mod.verify_report(valid, "a" * 64)
        for edit in (lambda r: r.update(rootfs_sha256="b" * 64),
                     lambda r: r["files"].pop(),
                     lambda r: r["files"][0].update(sha256="0" * 64)):
            import copy
            value = copy.deepcopy(valid)
            edit(value)
            with self.assertRaises(ValueError):
                mod.verify_report(value, "a" * 64)

if __name__ == "__main__":
    unittest.main()
