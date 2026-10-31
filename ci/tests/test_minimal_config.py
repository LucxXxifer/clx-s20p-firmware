import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import minimal_config


class MinimalConfigTests(unittest.TestCase):
    def test_changes_only_three_roots_and_keeps_shared_tun(self):
        text = ("CONFIG_PACKAGE_easytier=y\nCONFIG_PACKAGE_luci-app-easytier=y\n"
                "CONFIG_PACKAGE_luci-i18n-easytier-zh-cn=y\nCONFIG_PACKAGE_kmod-tun=y\n"
                "CONFIG_PACKAGE_quickstart=y\n")
        result = minimal_config.transform(text)
        self.assertIn("CONFIG_PACKAGE_kmod-tun=y\nCONFIG_PACKAGE_quickstart=y\n", result)
        self.assertEqual(result.count("is not set"), 3)
        self.assertEqual(minimal_config.transform(result), result)

    def test_rejects_missing_or_duplicate_roots(self):
        with self.assertRaises(ValueError):
            minimal_config.transform("CONFIG_PACKAGE_kmod-tun=y\n")
        text = "".join("CONFIG_PACKAGE_" + name + "=y\n" for name in minimal_config.PACKAGES)
        with self.assertRaises(ValueError):
            minimal_config.transform(text + "CONFIG_PACKAGE_easytier=m\n")

    def test_disables_translation_selected_implicitly_by_luci(self):
        text = "CONFIG_PACKAGE_easytier=y\nCONFIG_PACKAGE_luci-app-easytier=y\n"
        self.assertEqual(minimal_config.transform(text).splitlines()[-1],
                         "# CONFIG_PACKAGE_luci-i18n-easytier-zh-cn is not set")
