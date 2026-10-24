"""Keep the locked feed's versioned Go compiler for the S20P CI build."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from ci.verify_go_host_deps import verify

ROOT = Path(__file__).resolve().parents[2]
UPDATES = ROOT / "wrt_core/modules/package_source_updates.sh"


class GoSourceTests(unittest.TestCase):
    def run_update(self, root, ci=True):
        env = {**os.environ, "S20P_AUTOBUILD": "1" if ci else "0"}
        return subprocess.run(["bash", "-c",
            'source "$1"; git_retry() { return 71; }; GOLANG_BRANCH=26.x; GOLANG_REPO=https://example.invalid/go; update_golang',
            "go-test", str(UPDATES)], cwd=root, env=env, capture_output=True, text=True)

    def tree(self, root, patch="1"):
        parent = root / "feeds/packages/lang/golang"
        compiler = parent / "golang1.27/Makefile"
        compiler.parent.mkdir(parents=True)
        compiler.write_text("PKG_NAME:=golang1.27\nGO_VERSION_MAJOR_MINOR:=1.27\nGO_VERSION_PATCH:=" + patch + "\n")
        dummy = parent / "golang/Makefile"
        dummy.parent.mkdir()
        dummy.write_text("PKG_NAME:=golang\nHOST_BUILD_DEPENDS:=golang$(PKG_VERSION)/host\n")
        return compiler

    def test_ci_preserves_versioned_go_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            compiler = self.tree(root)
            before = compiler.read_bytes()
            result = self.run_update(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(compiler.read_bytes(), before)

    def test_ci_rejects_wrong_compiler_without_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            compiler = self.tree(root, patch="2")
            before = compiler.read_bytes()
            result = self.run_update(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(compiler.read_bytes(), before)

    def test_ci_rejects_missing_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_update(Path(folder))
            self.assertNotEqual(result.returncode, 0)

    def test_non_ci_keeps_original_override_behavior(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            compiler = self.tree(root)
            result = self.run_update(root, ci=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(compiler.exists())
            self.assertIn("克隆 golang", result.stdout + result.stderr)


class GoHostDependencyTests(unittest.TestCase):
    def metadata(self, root, name, packages=(), dependencies=(), host=False):
        source = "package/feeds/packages/" + name + "/Makefile"
        target = root / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
        return ("Source-Makefile: " + source + "\nBuild-Depends: " + " ".join(dependencies) +
                ("\nBuild-Types: host\n" if host else "\n") +
                "\n".join("\nPackage: " + package for package in packages) + "\n")

    def test_selected_tailscale_requires_versioned_host(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            info = self.metadata(root, "tailscale", ["tailscale"], ["golang1.27/host"])
            result = verify("CONFIG_PACKAGE_tailscale=y\n", info, root)
            self.assertEqual(result["status"], "fail")
            self.assertIn("missing host recipe: golang1.27/host", result["errors"])

    def test_real_host_provider_and_transitive_default_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            info = self.metadata(root, "adguardhome", ["adguardhome"], ["golang/host"])
            info += self.metadata(root, "golang", ["golang"], ["golang1.26/host"], True)
            result = verify("CONFIG_PACKAGE_adguardhome=m\n", info, root)
            self.assertEqual(result["status"], "fail")
            info += self.metadata(root, "golang1.26", ["golang1.26"], [], True)
            result = verify("CONFIG_PACKAGE_adguardhome=m\n", info, root)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["resolved_go_host_recipes"], ["golang", "golang1.26"])

    def test_unselected_package_does_not_create_required_host_dependency(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            info = self.metadata(root, "unused", ["unused"], ["golang1.27/host"])
            self.assertEqual(verify("# CONFIG_PACKAGE_unused is not set\n", info, root)["status"], "pass")


if __name__ == "__main__":
    unittest.main()
