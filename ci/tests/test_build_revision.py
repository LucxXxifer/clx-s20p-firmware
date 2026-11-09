"""Guard ensuring base-files can be packaged as a valid APK version."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "ci/verify-build-revision.sh"
BUILD_SCRIPT = REPO / "ci/build.sh"


def run_guard(tree):
    return subprocess.run(["bash", str(GUARD), str(tree)],
                          text=True, capture_output=True)


def make_tree(root, body):
    (root / "scripts").mkdir(parents=True)
    probe = root / "scripts/getver.sh"
    probe.write_text(body)
    return root


class BuildRevisionTests(unittest.TestCase):
    def test_accepts_revision_with_hexadecimal_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            tree = make_tree(Path(temp), '#!/bin/sh\necho r0-56cadf22\n')
            result = run_guard(tree)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('build revision: r0-56cadf22', result.stdout)

    def test_accepts_deep_history_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            tree = make_tree(Path(temp), '#!/bin/sh\necho r28531-03eb306\n')
            self.assertEqual(run_guard(tree).returncode, 0)

    def test_rejects_revision_without_commit_hash(self):
        # This is the exact value the shallow build produced when an exported
        # UPSTREAM_BASE hid the commit from scripts/getver.sh; base-files then
        # failed with "package version is invalid" after four hours of building.
        with tempfile.TemporaryDirectory() as temp:
            tree = make_tree(Path(temp), '#!/bin/sh\necho r0-\n')
            result = run_guard(tree)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("'r0-' is not usable as an APK package version", result.stderr)
            self.assertIn('base-files', result.stderr)

    def test_rejects_non_hexadecimal_suffix(self):
        with tempfile.TemporaryDirectory() as temp:
            tree = make_tree(Path(temp), '#!/bin/sh\necho r12-deadbeeg\n')
            self.assertNotEqual(run_guard(tree).returncode, 0)

    def test_rejects_unknown_and_empty_revisions(self):
        for body in ('#!/bin/sh\necho unknown\n', '#!/bin/sh\nexit 0\n'):
            with tempfile.TemporaryDirectory() as temp:
                tree = make_tree(Path(temp), body)
                self.assertNotEqual(run_guard(tree).returncode, 0, body)

    def test_rejects_missing_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_guard(Path(temp))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('revision probe is missing', result.stderr)

    def test_build_script_runs_guard_before_compilation(self):
        text = BUILD_SCRIPT.read_text()
        invocation = 'bash ci/verify-build-revision.sh "$repo/action_build"'
        self.assertIn(invocation, text)
        self.assertLess(text.index(invocation), text.index("bash ./build.sh S20P-WIFI-YES ''"))
        self.assertLess(text.index('bash ./wrt_core/pre_clone_action.sh'), text.index(invocation))

    def test_guard_is_syntax_checked_by_the_workflow(self):
        workflow = (REPO / ".github/workflows/s20p-daily.yml").read_text()
        self.assertIn('bash -n ci/build.sh ci/capture.sh ci/setup-runner.sh ci/verify-build-revision.sh build.sh',
                      workflow)
        syntax = subprocess.run(['bash', '-n', str(GUARD)], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_build_script_clears_variables_owned_by_the_upstream_tree(self):
        # Guarding the value is not enough: getver.sh writes UPSTREAM_BASE
        # itself, so any inherited binding must be gone before the tree runs.
        text = BUILD_SCRIPT.read_text()
        cleared = {name for line in re.findall(r'(?m)^unset (.+)$', text)
                   for name in line.split()}
        for name in ('UPSTREAM_BASE', 'UPSTREAM_REV', 'REBOOT', 'REVISION'):
            self.assertIn(name, cleared)
        self.assertLess(text.index('unset UPSTREAM_BASE'),
                        text.index("bash ./build.sh S20P-WIFI-YES ''"))


if __name__ == '__main__':
    unittest.main()
