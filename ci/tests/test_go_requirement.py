"""Route each Go recipe to the versioned host toolchain its pinned go.mod needs.

OpenWrt 25.12 compiles with the golang1.26 series unless the recipe names a
versioned ``golang1.XX/host`` build dependency, and it never lets the Go
compiler download a newer toolchain (GOTOOLCHAIN=local).  A recipe that tracks
upstream head therefore has to be re-pointed when its go.mod floor moves.
"""
from pathlib import Path
import json
import re
import tempfile
import unittest

from ci.go_requirement import (RequirementError, decide, load_recipes, select,
                               satisfies)

RECIPE = b"""include $(TOPDIR)/rules.mk

PKG_NAME:=axonhub
PKG_VERSION:=20261109
PKG_SOURCE_VERSION:=4eebba5fbba09cf87e213ae2cfccc86f30432ea1
PKG_SOURCE_URL:=https://codeload.github.com/looplj/axonhub/tar.gz/$(PKG_SOURCE_VERSION)?
PKG_HASH:=5ea8113ec622569f41f1eb1aa68e29fc8c4666e41cd3359be25e0c843cd5453b

PKG_BUILD_DEPENDS:=golang/host node/host node-pnpm/host

GO_PKG:=github.com/looplj/axonhub
"""


def compiler(series, patch):
    major, minor = series.split('.')
    return ("PKG_NAME:=golang" + series + "\n"
            "GO_VERSION_MAJOR_MINOR:=" + series + "\n"
            "GO_VERSION_PATCH:=" + str(patch) + "\n")


def tree(root, series):
    lang = Path(root) / "lang"
    for name, patch in series.items():
        makefile = lang / ("golang" + name) / "Makefile"
        makefile.parent.mkdir(parents=True, exist_ok=True)
        makefile.write_text(compiler(name, patch))
    (lang / "golang-values.mk").write_text("GO_DEFAULT_VERSION:=1.26\n")
    return lang


def fetcher(gomod, seen=None):
    def fetch(url):
        if seen is not None:
            seen.append(url)
        if isinstance(gomod, Exception):
            raise gomod
        return gomod
    return fetch


class GoRequirementTests(unittest.TestCase):
    def test_moves_only_the_host_selector_when_the_floor_moved(self):
        with tempfile.TemporaryDirectory() as folder:
            recipes = load_recipes(tree(folder, {"1.26": 10, "1.27": 3}))
            row, rewritten = decide(RECIPE, "axonhub", recipes, "1.26",
                                    fetcher(b"module github.com/looplj/axonhub\n\ngo 1.27.3\n"))
        self.assertEqual(row["status"], "selected")
        self.assertEqual(row["required_go"], "go1.27.3")
        self.assertEqual(row["selected_go"], "go1.27.3")
        self.assertEqual(row["go_mod_url"],
                         "https://raw.githubusercontent.com/looplj/axonhub/"
                         "4eebba5fbba09cf87e213ae2cfccc86f30432ea1/go.mod")
        self.assertEqual(rewritten,
                         RECIPE.replace(b"PKG_BUILD_DEPENDS:=golang/host",
                                        b"PKG_BUILD_DEPENDS:=golang1.27/host"))
        # Source pin, archive hash and the other host dependencies are untouched.
        self.assertIn(b"PKG_HASH:=5ea8113ec622569f41f1eb1aa68e29fc8c4666e41cd3359be25e0c843cd5453b", rewritten)
        self.assertIn(b"node/host node-pnpm/host", rewritten)

    def test_default_series_satisfying_the_floor_keeps_the_recipe(self):
        with tempfile.TemporaryDirectory() as folder:
            recipes = load_recipes(tree(folder, {"1.26": 10, "1.27": 3}))
            row, rewritten = decide(RECIPE, "axonhub", recipes, "1.26",
                                    fetcher(b"module github.com/looplj/axonhub\n\ngo 1.26.0\n"))
        self.assertIsNone(rewritten)
        self.assertEqual(row["status"], "default-satisfies")
        self.assertEqual(row["recipe_sha256_after"], row["recipe_sha256_before"])

    def test_missing_reviewed_series_stops_instead_of_downloading_a_toolchain(self):
        with tempfile.TemporaryDirectory() as folder:
            recipes = load_recipes(tree(folder, {"1.26": 10, "1.27": 3}))
            with self.assertRaises(RequirementError) as caught:
                decide(RECIPE, "axonhub", recipes, "1.26",
                       fetcher(b"module github.com/looplj/axonhub\n\ngo 1.28.0\n"))
        self.assertIn("go1.28.0", str(caught.exception))
        self.assertIn("ci/security/go-x509.json", str(caught.exception))

    def test_patch_level_below_the_floor_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            recipes = load_recipes(tree(folder, {"1.26": 10, "1.27": 3}))
            with self.assertRaises(RequirementError) as caught:
                decide(RECIPE, "axonhub", recipes, "1.26",
                       fetcher(b"module github.com/looplj/axonhub\n\ngo 1.27.9\n"))
        self.assertIn("no reviewed Go host recipe satisfies go1.27.9", str(caught.exception))

    def test_newer_series_covers_an_older_floor(self):
        with tempfile.TemporaryDirectory() as folder:
            recipes = load_recipes(tree(folder, {"1.26": 10, "1.27": 3, "1.28": 1}))
            row, rewritten = decide(RECIPE, "axonhub", recipes, "1.26",
                                    fetcher(b"module github.com/looplj/axonhub\n\ngo 1.27.3\n"))
        self.assertEqual(row["selected_series"], "1.27")
        self.assertIsNotNone(rewritten)

    def test_go_mod_without_directive_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            recipes = load_recipes(tree(folder, {"1.26": 10, "1.27": 3}))
            with self.assertRaises(RequirementError) as caught:
                decide(RECIPE, "axonhub", recipes, "1.26", fetcher(b"module github.com/looplj/axonhub\n"))
        self.assertIn("no go directive", str(caught.exception))

    def test_unreadable_go_mod_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            recipes = load_recipes(tree(folder, {"1.26": 10, "1.27": 3}))
            with self.assertRaises(RequirementError) as caught:
                decide(RECIPE, "axonhub", recipes, "1.26",
                       fetcher(RequirementError("cannot read https://example.invalid/go.mod")))
        self.assertIn("cannot read", str(caught.exception))

    def test_recipe_without_a_go_host_dependency_is_not_touched(self):
        seen = []
        with tempfile.TemporaryDirectory() as folder:
            recipes = load_recipes(tree(folder, {"1.26": 10, "1.27": 3}))
            row, rewritten = decide(b"PKG_NAME:=not-go\n", "not-go", recipes, "1.26", fetcher(b"", seen))
        self.assertEqual(row["status"], "no-go-host-dependency")
        self.assertIsNone(rewritten)
        self.assertEqual(seen, [])

    def test_explicit_versioned_selector_is_validated_not_rewritten(self):
        explicit = RECIPE.replace(b"golang/host", b"golang1.27/host")
        with tempfile.TemporaryDirectory() as folder:
            recipes = load_recipes(tree(folder, {"1.26": 10, "1.27": 3}))
            row, rewritten = decide(explicit, "axonhub", recipes, "1.26",
                                    fetcher(b"module github.com/looplj/axonhub\n\ngo 1.27.3\n"))
            self.assertEqual(row["status"], "versioned-satisfies")
            self.assertIsNone(rewritten)
            with self.assertRaises(RequirementError):
                decide(explicit, "axonhub", recipes, "1.26",
                       fetcher(b"module github.com/looplj/axonhub\n\ngo 1.28.0\n"))

    def test_select_writes_evidence_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lang = tree(root, {"1.26": 10, "1.27": 3})
            package = root / "feed/axonhub"
            package.mkdir(parents=True)
            (package / "Makefile").write_bytes(RECIPE)
            evidence = root / "provenance/go-requirement.json"
            go_mod = b"module github.com/looplj/axonhub\n\ngo 1.27.3\n"
            select(package.parent, lang, ["axonhub"], evidence, fetcher(go_mod))
            self.assertEqual((package / "Makefile").read_bytes(),
                             RECIPE.replace(b"PKG_BUILD_DEPENDS:=golang/host",
                                            b"PKG_BUILD_DEPENDS:=golang1.27/host"))
            first = json.loads(evidence.read_text())
            self.assertEqual(first["packages"][0]["status"], "selected")
            select(package.parent, lang, ["axonhub"], evidence, fetcher(go_mod))
            second = json.loads(evidence.read_text())
            self.assertEqual(second["packages"][0]["status"], "versioned-satisfies")
            self.assertEqual(second["packages"][0]["recipe_sha256_before"],
                             first["packages"][0]["recipe_sha256_after"])

    def test_missing_recipe_directory_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lang = tree(root, {"1.26": 10, "1.27": 3})
            (root / "feed").mkdir()
            with self.assertRaises(RequirementError) as caught:
                select(root / "feed", lang, ["axonhub"], None, fetcher(b""))
        self.assertIn("Go recipe missing", str(caught.exception))

    def test_satisfies_compares_series_then_patch(self):
        self.assertTrue(satisfies("1.27", {"patch": 3}, ((1, 27), 3)))
        self.assertFalse(satisfies("1.27", {"patch": 2}, ((1, 27), 3)))
        self.assertFalse(satisfies("1.26", {"patch": 10}, ((1, 27), 3)))
        self.assertTrue(satisfies("1.28", {"patch": 0}, ((1, 27), 3)))

    def test_real_tree_recipes_parse_although_only_one_marks_host_only(self):
        # The shipped golang1.26 recipe has no PKG_HOST_ONLY line while
        # golang1.27 has one; neither shape may be treated as drift.
        fixtures = Path(__file__).resolve().parent / "fixtures/go-security"
        with tempfile.TemporaryDirectory() as folder:
            lang = Path(folder) / "lang"
            for series, patch in (("1.26", "10"), ("1.27", "3")):
                target = lang / ("golang" + series) / "Makefile"
                target.parent.mkdir(parents=True)
                data = (fixtures / (series + ".mk")).read_text()
                data = re.sub(r"^GO_VERSION_PATCH:=\d+$", "GO_VERSION_PATCH:=" + patch,
                              data, flags=re.MULTILINE)
                target.write_text(data)
            (lang / "golang-values.mk").write_text("GO_DEFAULT_VERSION:=1.26\n")
            recipes = load_recipes(lang)
            self.assertEqual(recipes["1.26"]["patch"], 10)
            self.assertEqual(recipes["1.27"]["patch"], 3)
            self.assertNotIn("PKG_HOST_ONLY", recipes["1.26"]["path"].read_text())


if __name__ == "__main__":
    unittest.main()
