"""Unit tests for the pre-make recipe index used by the image gate."""

import hashlib
import json
import sys
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "upstream_rotation.py"
sys.path.insert(0, str(SCRIPT.parent))
import upstream_rotation as rotation  # noqa: E402

GEOSITE = """\
include $(TOPDIR)/rules.mk

PKG_NAME:=v2ray-geodata
PKG_RELEASE:=1

GEOIP_VER:=202611080027
GEOSITE_VER:=20261110004853

define Download/geoip
  URL:=https://github.com/v2fly/geoip/releases/download/$(GEOIP_VER)/
  URL_FILE:=geoip.dat
  HASH:=eedb24b3b9fcba27e06b92c0428dab90d9496083091ae4cf39e11387fd99d0e2
endef

define Download/geosite
  URL:=https://github.com/v2fly/domain-list-community/releases/download/$(GEOSITE_VER)/
  URL_FILE:=dlc.dat
  HASH:=5ce9f05c8968772940e29b7898adf232464a2a2a820252d17c5b33ffaecd1da9
endef

define Package/v2ray-geoip
  VERSION:=$(GEOIP_VER)-r$(PKG_RELEASE)
endef

define Package/v2ray-geosite
  VERSION:=$(GEOSITE_VER)-r$(PKG_RELEASE)
endef
"""

AXONHUB = """\
include $(TOPDIR)/rules.mk

PKG_NAME:=axonhub
PKG_VERSION:=20261110
PKG_RELEASE:=1
PKG_HASH:=441c32d3fd0c12d2eeb140e505f6256306fd52c257dfc81408ac0beb75437dd2
"""

PLAIN = """\
include $(TOPDIR)/rules.mk
PKG_NAME:=luci-app-passwall
PKG_VERSION:=26.11.10
PKG_RELEASE:=2
"""


class RecipeParseTests(unittest.TestCase):
    def test_package_blocks_resolve_recipe_variables(self):
        variables = rotation.assignments(GEOSITE)
        packages = rotation.package_versions(GEOSITE, variables)
        self.assertEqual(packages["v2ray-geosite"], "20261110004853-r1")
        self.assertEqual(packages["v2ray-geoip"], "202611080027-r1")

    def test_plain_recipe_uses_pkg_version_and_release(self):
        packages = rotation.package_versions(AXONHUB, rotation.assignments(AXONHUB))
        self.assertEqual(packages, {"axonhub": "20261110-r1"})

    def test_default_release_applies_when_recipe_omits_it(self):
        text = "PKG_NAME:=thing\nPKG_VERSION:=1.2.3\n"
        self.assertEqual(rotation.package_versions(text, rotation.assignments(text)),
                         {"thing": "1.2.3-r1"})

    def test_crlf_recipes_parse_like_lf(self):
        windows = GEOSITE.replace("\n", "\r\n")
        self.assertEqual(rotation.package_versions(windows, rotation.assignments(windows)),
                         rotation.package_versions(GEOSITE, rotation.assignments(GEOSITE)))

    def test_declared_hashes_include_every_download_block(self):
        hashes = rotation.declared_hashes(GEOSITE, rotation.assignments(GEOSITE))
        self.assertIn("5ce9f05c8968772940e29b7898adf232464a2a2a820252d17c5b33ffaecd1da9", hashes)
        self.assertIn("eedb24b3b9fcba27e06b92c0428dab90d9496083091ae4cf39e11387fd99d0e2", hashes)
        self.assertEqual(len(hashes), 2)

    def test_unresolved_version_is_not_declared(self):
        text = "PKG_NAME:=thing\nPKG_VERSION:=$(SOMETHING_ELSE)\n"
        self.assertEqual(rotation.package_versions(text, rotation.assignments(text)), {})

    def test_unterminated_package_block_is_an_error(self):
        text = "define Package/broken\n  VERSION:=1\n"
        with self.assertRaises(rotation.RotationError):
            rotation.package_versions(text, rotation.assignments(text))


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = self.root / "tree"

    def write(self, relative, text, newline="\n"):
        path = self.tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.replace("\n", newline).encode())
        return path

    def manifest(self, entries):
        path = self.root / "prepared-source-files.json"
        path.write_text(json.dumps(entries))
        return path

    def entry(self, path, relative):
        return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def build(self, *recipes, mutate=None):
        entries = []
        for relative, text in recipes:
            path = self.write(relative, text,
                              "\r\n" if relative.endswith("windows/Makefile") else "\n")
            if mutate:
                path = mutate(relative, path)
            entries.append(self.entry(path, relative))
        return rotation.build_index(self.manifest(entries), self.tree)

    def test_index_records_declared_versions_and_hashes(self):
        index = self.build(("custom_feed/v2ray-geodata/Makefile", GEOSITE),
                           ("custom_feed/axonhub/Makefile", AXONHUB))
        self.assertEqual(index["recipe_count"], 2)
        self.assertEqual(index["packages"]["v2ray-geosite"]["version"], "20261110004853-r1")
        self.assertEqual(index["packages"]["axonhub"]["version"], "20261110-r1")
        self.assertEqual(len(index["packages"]["v2ray-geosite"]["recipe_sha256"]), 64)
        self.assertFalse(index["packages"]["axonhub"]["local_patches"])

    def test_recipe_changed_after_provenance_is_not_indexed(self):
        path = self.write("custom_feed/axonhub/Makefile", AXONHUB)
        manifest = self.manifest([self.entry(path, "custom_feed/axonhub/Makefile")])
        path.write_bytes((AXONHUB + "EXTRA:=1\n").encode())
        index = rotation.build_index(manifest, self.tree)
        self.assertEqual(index["packages"], {})
        self.assertEqual(index["skipped_recipes"], 1)

    def test_only_recipe_roots_are_indexed(self):
        index = self.build(("build_dir/target/Makefile", AXONHUB),
                           ("custom_feed/axonhub/Makefile", AXONHUB))
        self.assertEqual(index["recipe_count"], 1)
        self.assertEqual(list(index["packages"]), ["axonhub"])

    def test_conflicting_recipes_disable_attribution(self):
        index = self.build(("custom_feed/one/Makefile", AXONHUB),
                           ("feeds/other/Makefile",
                            AXONHUB.replace("PKG_VERSION:=20261110", "PKG_VERSION:=20261111")))
        self.assertEqual(index["packages"], {})
        self.assertEqual(index["conflicts"], ["axonhub"])
        self.assertIsNone(rotation.attest_version(index, "axonhub", "20261110-r1"))

    def test_identical_duplicate_recipes_stay_usable(self):
        index = self.build(("custom_feed/one/Makefile", AXONHUB),
                           ("feeds/other/Makefile", AXONHUB))
        self.assertEqual(index["conflicts"], [])
        self.assertEqual(rotation.attest_version(index, "axonhub", "20261110-r1")["declared_version"],
                         "20261110-r1")

    def link(self, relative, target):
        return {"path": relative, "mode": "0o777", "target": target}

    def test_installed_link_selects_the_compiled_duplicate(self):
        """Only the copy feeds install linked is built, so it is the answer."""
        entries = []
        for relative, text in (("custom_feed/axonhub/Makefile", AXONHUB),
                               ("feeds/other/axonhub/Makefile",
                                AXONHUB.replace("PKG_VERSION:=20261110",
                                                "PKG_VERSION:=20261111"))):
            path = self.write(relative, text)
            entries.append(self.entry(path, relative))
        entries.append(self.link("feeds/custom_feed", str(self.tree / "custom_feed")))
        entries.append(self.link("package/feeds/custom_feed/axonhub",
                                 "../../../feeds/custom_feed/axonhub"))
        index = rotation.build_index(self.manifest(entries), self.tree)
        self.assertEqual(index["conflicts"], [])
        self.assertEqual(index["packages"]["axonhub"]["recipe"], "custom_feed/axonhub/Makefile")
        self.assertEqual(index["packages"]["axonhub"]["version"], "20261110-r1")
        self.assertEqual(index["packages"]["axonhub"]["shadowed_recipes"],
                         ["feeds/other/axonhub/Makefile"])
        self.assertEqual(index["resolved_duplicates"],
                         [{"package": "axonhub", "recipe": "custom_feed/axonhub/Makefile",
                           "shadowed_recipes": ["feeds/other/axonhub/Makefile"]}])
        evidence = rotation.attest_version(index, "axonhub", "20261110-r1")
        self.assertEqual(evidence["shadowed_recipes"], ["feeds/other/axonhub/Makefile"])

    def test_two_installed_links_remain_a_conflict(self):
        """Both copies linked means the winner cannot be told apart by layout."""
        entries = []
        for relative, text in (("custom_feed/axonhub/Makefile", AXONHUB),
                               ("feeds/other/axonhub/Makefile",
                                AXONHUB.replace("PKG_VERSION:=20261110",
                                                "PKG_VERSION:=20261111"))):
            path = self.write(relative, text)
            entries.append(self.entry(path, relative))
        entries.append(self.link("package/feeds/custom_feed/axonhub",
                                 "../../../custom_feed/axonhub"))
        entries.append(self.link("package/feeds/other/axonhub", "../../../feeds/other/axonhub"))
        index = rotation.build_index(self.manifest(entries), self.tree)
        self.assertEqual(index["packages"], {})
        self.assertEqual(index["conflicts"], ["axonhub"])
        self.assertEqual(index["resolved_duplicates"], [])

    def test_no_links_means_no_duplicate_resolution(self):
        """Without the install links nothing may be resolved away."""
        index = self.build(("custom_feed/axonhub/Makefile", AXONHUB),
                           ("feeds/other/axonhub/Makefile",
                            AXONHUB.replace("PKG_VERSION:=20261110", "PKG_VERSION:=20261111")))
        self.assertEqual(index["conflicts"], ["axonhub"])
        self.assertEqual(index["resolved_duplicates"], [])

    def test_base_tree_recipe_survives_a_shadowed_feed_recipe(self):
        """package/ is scanned directly, so it needs no install link."""
        entries = []
        for relative, text in (("package/base-files/Makefile",
                                "PKG_NAME:=base-files\nPKG_VERSION:=1\n"
                                "define Package/conffiles\n  VERSION:=$(PKG_VERSION)-r1\nendef\n"),
                               ("custom_feed/base-files/Makefile",
                                "PKG_NAME:=base-files\nPKG_VERSION:=9\n"
                                "define Package/conffiles\n  VERSION:=$(PKG_VERSION)-r1\nendef\n")):
            path = self.write(relative, text)
            entries.append(self.entry(path, relative))
        entries.append(self.link("package/feeds/custom_feed/base-files",
                                 "../../../custom_feed/base-files"))
        index = rotation.build_index(self.manifest(entries), self.tree)
        self.assertEqual(index["conflicts"], ["conffiles"])
        entries = entries[:2]
        index = rotation.build_index(self.manifest(entries), self.tree)
        self.assertEqual(index["conflicts"], [])
        self.assertEqual(index["packages"]["conffiles"]["recipe"], "package/base-files/Makefile")

    def test_load_rejects_malformed_shadowed_paths(self):
        index = self.build(("custom_feed/axonhub/Makefile", AXONHUB))
        index["packages"]["axonhub"]["shadowed_recipes"] = "feeds/other/Makefile"
        path = self.root / "index.json"
        path.write_text(json.dumps(index))
        with self.assertRaises(rotation.RotationError):
            rotation.load(path)

    def test_local_patch_directory_is_recorded(self):
        self.write("custom_feed/axonhub/patches/100-fix.patch", "diff\n")
        index = self.build(("custom_feed/axonhub/Makefile", AXONHUB))
        self.assertTrue(index["packages"]["axonhub"]["local_patches"])

    def test_kernel_is_never_attributed(self):
        text = "PKG_NAME:=kernel\nPKG_VERSION:=6.12.112\nPKG_RELEASE:=1\n"
        index = self.build(("package/kernel/Makefile", text))
        self.assertEqual(index["packages"], {})
        self.assertIsNone(rotation.attest_version(index, "kernel", "6.12.112-r1"))

    def test_version_attestation_requires_exact_match(self):
        index = self.build(("custom_feed/axonhub/Makefile", AXONHUB))
        self.assertIsNotNone(rotation.attest_version(index, "axonhub", "20261110-r1"))
        self.assertIsNone(rotation.attest_version(index, "axonhub", "20261111-r1"))
        self.assertIsNone(rotation.attest_version(index, "missing", "20261110-r1"))
        self.assertIsNone(rotation.attest_version(None, "axonhub", "20261110-r1"))

    def test_hash_attestation_binds_the_owning_recipe(self):
        index = self.build(("custom_feed/v2ray-geodata/Makefile", GEOSITE))
        digest = "5ce9f05c8968772940e29b7898adf232464a2a2a820252d17c5b33ffaecd1da9"
        self.assertIsNotNone(rotation.attest_hash(index, "v2ray-geosite", digest))
        self.assertIsNone(rotation.attest_hash(index, "v2ray-geosite", "0" * 64))
        self.assertIsNone(rotation.attest_hash(index, "", digest))
        self.assertIsNone(rotation.attest_hash(index, "unknown", digest))

    def test_load_rejects_unsupported_schema(self):
        index = self.build(("custom_feed/axonhub/Makefile", AXONHUB))
        path = self.root / "index.json"
        path.write_text(json.dumps(dict(index, schema_version=99)))
        with self.assertRaises(rotation.RotationError):
            rotation.load(path)

    def test_load_rejects_missing_manifest_digest(self):
        index = self.build(("custom_feed/axonhub/Makefile", AXONHUB))
        index.pop("manifest_sha256")
        path = self.root / "index.json"
        path.write_text(json.dumps(index))
        with self.assertRaises(rotation.RotationError):
            rotation.load(path)

    def test_load_round_trips_a_written_index(self):
        index = self.build(("custom_feed/axonhub/Makefile", AXONHUB))
        path = self.root / "index.json"
        path.write_text(json.dumps(index))
        self.assertEqual(rotation.load(path)["packages"]["axonhub"]["version"], "20261110-r1")


if __name__ == "__main__":
    unittest.main()
