"""Reject Kconfig false success and preserve the WeeChat default provider."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "wrt_core/modules/verify.sh"
FIXES = ROOT / "wrt_core/modules/target_fixes.sh"


class DefconfigTests(unittest.TestCase):
    def run_fake_make(self, output, status=0):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake = root / "make"
            fake.write_text("#!/bin/sh\ncat <<'MESSAGE'\n" + output + "\nMESSAGE\nexit " + str(status) + "\n")
            fake.chmod(0o755)
            env = {**os.environ, "PATH": folder + os.pathsep + os.environ["PATH"]}
            result = subprocess.run(["bash", "-c",
                'source "$1"; run_defconfig_checked "$2"', "defconfig-test", str(VERIFY),
                str(root / "logs/defconfig.log")], env=env, capture_output=True, text=True)
            log = (root / "logs/defconfig.log").read_text() if (root / "logs/defconfig.log").exists() else ""
            return result, log

    def test_success_is_logged(self):
        result, log = self.run_fake_make("# configuration written to .config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("configuration written", log)

    def test_make_failure_status_is_preserved(self):
        result, _ = self.run_fake_make("make failed", 17)
        self.assertEqual(result.returncode, 17)

    def test_rc_zero_recursive_dependency_is_failure(self):
        result, _ = self.run_fake_make("tmp/.config-package.in:41:error: recursive dependency detected!")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Kconfig diagnostics", result.stderr)

    def test_rc_zero_kconfig_syntax_error_is_failure(self):
        result, _ = self.run_fake_make("Config.in:41: syntax error")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Kconfig diagnostics", result.stderr)

    def test_existing_package_warning_is_not_global_failure(self):
        result, _ = self.run_fake_make("WARNING: Makefile 'other/Makefile' has a dependency on 'missing', which does not exist")
        self.assertEqual(result.returncode, 0, result.stderr)


class WeeChatProviderTests(unittest.TestCase):
    def apply_fix(self, root):
        return subprocess.run(["bash", "-c", 'source "$1"; BUILD_DIR="$2"; fix_weechat_provider_dependency',
            "weechat-test", str(FIXES), str(root)], capture_output=True, text=True)

    def tree(self, root, dependency):
        recipe = root / "feeds/luci/applications/luci-app-weechat/Makefile"
        recipe.parent.mkdir(parents=True)
        recipe.write_text("LUCI_TITLE:=WeeChat\n" + dependency + "\n")
        provider = root / "feeds/packages/net/weechat/Makefile"
        provider.parent.mkdir(parents=True)
        provider.write_text("define Package/weechat-minimal\n  PROVIDES:=weechat\nendef\n")
        return recipe

    def test_virtual_provider_is_replaced_with_default_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            recipe = self.tree(root, "LUCI_DEPENDS:=+luci-base +weechat +rpcd-mod-ucode")
            for _ in range(2):
                result = self.apply_fix(root)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(recipe.read_text(), "LUCI_TITLE:=WeeChat\nLUCI_DEPENDS:=+luci-base +weechat-minimal +rpcd-mod-ucode\n")

    def test_unknown_recipe_fails_without_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            recipe = self.tree(root, "LUCI_DEPENDS:=+weechat +unknown-runtime")
            before = recipe.read_bytes()
            result = self.apply_fix(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported luci-app-weechat", result.stderr)
            self.assertEqual(recipe.read_bytes(), before)

    def test_unrelated_tree_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.apply_fix(Path(folder)).returncode, 0)


if __name__ == "__main__":
    unittest.main()
