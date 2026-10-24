"""Guard the complete OpenWrt kernel version, not just KERNEL_PATCHVER."""
from pathlib import Path
import subprocess
import tempfile
import unittest

VERIFY = Path(__file__).resolve().parents[2] / "wrt_core/modules/verify.sh"


class KernelVersionTests(unittest.TestCase):
    def check_version(self, expected, suffix=None, series="6.12"):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "target/linux/mediatek/Makefile"
            target.parent.mkdir(parents=True)
            target.write_text("KERNEL_PATCHVER:=" + series + "\n")
            if suffix is not None:
                details = root / ("target/linux/generic/kernel-" + series)
                details.parent.mkdir()
                details.write_text("LINUX_VERSION-" + series + " = " + suffix + "\n")
            return subprocess.run(["bash", "-c",
                'source "$1"; verify_expected_kernel_version "$2" "$3"',
                "kernel-test", str(VERIFY), expected, str(target)], capture_output=True, text=True)

    def test_complete_version_uses_generic_patch_suffix(self):
        result = self.check_version("6.12.112", ".112")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_patch_fails_even_when_series_matches(self):
        result = self.check_version("6.12.112", ".113")
        self.assertNotEqual(result.returncode, 0)

    def test_missing_patch_cannot_satisfy_exact_version(self):
        self.assertNotEqual(self.check_version("6.12.112").returncode, 0)

    def test_original_series_only_guards_remain_supported(self):
        self.assertEqual(self.check_version("6.12").returncode, 0)
        self.assertNotEqual(self.check_version("6.18").returncode, 0)


if __name__ == "__main__":
    unittest.main()
