"""Keep the AxonHub Go toolchain hook wired into the post-install feed fixups."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXES = ROOT / "wrt_core/modules/target_fixes.sh"

# The real selector needs the network to read the pinned go.mod; this test only
# proves the shell hook passes the reviewed paths and propagates a hard failure.
STUB = """#!/bin/sh
printf '%s\\n' "$@" > "$S20P_STUB_ARGS"
exit "${S20P_STUB_STATUS:-0}"
"""


class GoRequirementHookTests(unittest.TestCase):
    def run_fixup(self, status=0, capture=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            build = root / "action_build"
            recipe = build / "feeds/custom_feed/axonhub/Makefile"
            recipe.parent.mkdir(parents=True)
            recipe.write_text("PKG_BUILD_DEPENDS:=golang/host\n")
            lang = build / "feeds/packages/lang/golang"
            (lang / "golang1.26").mkdir(parents=True)
            (lang / "golang1.27").mkdir()
            stub_bin = root / "bin"
            stub_bin.mkdir()
            stub = stub_bin / "python3"
            stub.write_text(STUB)
            stub.chmod(0o755)
            base = root / "wrt_core"
            base.mkdir()
            env = {**os.environ, "PATH": str(stub_bin) + os.pathsep + os.environ["PATH"],
                   "BUILD_DIR": str(build), "BASE_PATH": str(base),
                   "S20P_CI_DIR": str(root / "ci"), "S20P_STUB_ARGS": str(root / "args.txt"),
                   "S20P_STUB_STATUS": str(status)}
            if capture:
                env["S20P_CAPTURE_DIR"] = str(root / "capture")
            # Normalise CRLF the way the build does, then source the real file.
            module = root / "target_fixes.sh"
            module.write_bytes(FIXES.read_bytes().replace(b"\r\n", b"\n"))
            result = subprocess.run(["bash", "-c",
                'source "$1"; apply_custom_feed_patches', "fixup-test", str(module)],
                env=env, capture_output=True, text=True)
            args = [line for line in (root / "args.txt").read_text().splitlines() if line] \
                if (root / "args.txt").exists() else []
            self.root = root
            return result, args

    def test_hook_selects_the_axonhub_toolchain_from_the_build_tree(self):
        result, args = self.run_fixup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(args[0].endswith("ci/go_requirement.py"), args[0])
        self.assertEqual(args[1], "select")
        self.assertIn("--package", args)
        self.assertEqual(args[args.index("--package") + 1], "axonhub")
        feed = args[args.index("--feed") + 1]
        self.assertTrue(feed.endswith("action_build/feeds/custom_feed"), feed)
        self.assertTrue(args[args.index("--lang") + 1].endswith(
            "action_build/feeds/packages/lang/golang"))
        self.assertNotIn("--evidence", args)

    def test_evidence_path_is_passed_when_the_run_captures_output(self):
        result, args = self.run_fixup(capture=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = args[args.index("--evidence") + 1]
        self.assertTrue(evidence.endswith("capture/provenance/go-requirement.json"), evidence)

    def test_unresolvable_requirement_stops_the_build(self):
        result, _ = self.run_fixup(status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("axonhub", result.stderr)


if __name__ == "__main__":
    unittest.main()
