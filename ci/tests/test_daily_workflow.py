"""Static and executed-shell guardrails for the rolling Actions workflow."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/s20p-daily.yml"
sys.path.insert(0, str(WORKFLOW.parents[2] / 'ci'))


class DailyWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text()

    def test_schedule_and_manual_force_input_are_present(self):
        self.assertRegex(self.text, r"(?m)^  schedule:")
        self.assertRegex(self.text, r"(?m)^  workflow_dispatch:")
        self.assertIn("force:", self.text)
        self.assertIn("type: boolean", self.text)

    def test_resolver_uses_exact_upstream_compare_and_reusable_state(self):
        self.assertIn("/git/ref/heads/${UPSTREAM_REF}", self.text)
        self.assertIn("/compare/", self.text)
        self.assertIn("s20p-upstream-state-", self.text)
        self.assertIn("verify-state-zip", self.text)
        self.assertIn("should_build", self.text)

    def test_missing_state_artifact_falls_back_to_rebuilding(self):
        self.assertIn("if workflow_id=$(gh api", self.text)
        self.assertIn("if runs=$(gh api --paginate", self.text)
        self.assertIn("if previous_artifacts=$(gh api", self.text)
        self.assertIn("if gh api \"repos/${GITHUB_REPOSITORY}/actions/artifacts/${state_id}/zip\"", self.text)
        self.assertIn("state lookup unavailable; rebuilding without prior state.", self.text)

    def test_compares_a_changed_head_against_the_previous_verified_head(self):
        self.assertIn('/compare/${previous_head}...${head}', self.text)
        self.assertIn('--previous-status', self.text)
        self.assertIn('--previous-merge-base', self.text)

    def test_rejected_run_without_state_does_not_erase_older_verified_head(self):
        base, head, previous, wrapper = "9f397975c7b47ce6986a6e39529d2f72fd5c729d", "a" * 40, "b" * 40, "c" * 40
        shell = textwrap.dedent(self.text.split('id: resolve', 1)[1].split('run: |\n', 1)[1]
                                .split('\n      - name:', 1)[0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'trusted.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('upstream-state.json', json.dumps({
                    'schema_version': 1, 'wrapper_commit': wrapper, 'upstream_commit': previous,
                    'build_started': True, 'outcome': 'success', 'run_id': '7', 'run_attempt': '1',
                }))
            prefix, upstream = 'repos/test/firmware', 'repos/chasey-dev/immortalwrt-mt798x-rebase'
            responses = {
                upstream + '/git/ref/heads/25.12': head,
                upstream + '/compare/' + base + '...' + head:
                    {'status': 'ahead', 'merge_base_commit': {'sha': base}},
                upstream + '/compare/' + previous + '...' + head:
                    {'status': 'behind', 'merge_base_commit': {'sha': head}},
                prefix + '/actions/workflows/s20p-daily.yml': '123',
                prefix + '/actions/workflows/123/runs?branch=main&per_page=100': {
                    'workflow_runs': [dict(id=n, run_number=n, run_attempt=2 if n == 7 else 1,
                                           head_sha=wrapper, event='schedule') for n in (8, 7)]},
                prefix + '/actions/runs/8/artifacts': {'artifacts': []},
                prefix + '/actions/runs/7/artifacts': {'artifacts': [{
                    'id': 70, 'name': 's20p-upstream-state-' + wrapper + '-7-1', 'expired': False,
                    'digest': 'sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest(),
                }]},
            }
            (root / 'responses.json').write_text(json.dumps(responses))
            gh = root / 'gh'
            gh.write_text('#!' + sys.executable + '\n' + textwrap.dedent('''\
                import json, os, pathlib, sys
                root = pathlib.Path(os.environ['TEST_GH_FIXTURES'])
                args = sys.argv[1:]
                path = args[2] if args[1] == '--paginate' else args[1]
                if path.endswith('/actions/artifacts/70/zip'):
                    sys.stdout.buffer.write((root / 'trusted.zip').read_bytes())
                else:
                    value = json.loads((root / 'responses.json').read_text())[path]
                    print(value if isinstance(value, str) else json.dumps(value))
                '''))
            gh.chmod(0o755)
            # Component resolution is tested separately. This shell fixture must
            # remain offline while checking previous-head rejection ordering.
            from component_fixtures import inputs
            (root / 'components.json').write_text(json.dumps(inputs()))
            python = root / 'python3'
            python.write_text('#!' + sys.executable + '\n' + textwrap.dedent('''\
                import os, pathlib, shutil, sys
                if sys.argv[1:3] == ['ci/components.py', 'resolve']:
                    target = sys.argv[sys.argv.index('--output') + 1]
                    shutil.copyfile(pathlib.Path(os.environ['TEST_GH_FIXTURES']) / 'components.json', target)
                else:
                    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
                '''))
            python.chmod(0o755)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'TEST_GH_FIXTURES': str(root), 'RUNNER_TEMP': str(root),
                   'GITHUB_REPOSITORY': 'test/firmware', 'GITHUB_SHA': wrapper,
                   'GITHUB_RUN_ID': '9', 'GITHUB_RUN_ATTEMPT': '1',
                   'GITHUB_OUTPUT': str(root / 'outputs'), 'FORCE': 'true',
                   'UPSTREAM_REPOSITORY': 'chasey-dev/immortalwrt-mt798x-rebase',
                   'UPSTREAM_REF': '25.12', 'UPSTREAM_BASE': base}
            result = subprocess.run(['bash', '-c', shell], cwd=WORKFLOW.parents[2],
                                    env=env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('previous verified head', result.stderr)

    def test_actions_rerun_requires_a_new_manual_force_dispatch(self):
        shell = textwrap.dedent(self.text.split('id: resolve', 1)[1].split('run: |\n', 1)[1]
                                .split('\n      - name:', 1)[0])
        result = subprocess.run(['bash', '-c', shell], env={**os.environ, 'GITHUB_RUN_ATTEMPT': '2'},
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Run workflow with force=true', result.stderr)

    def test_fixed_dispatch_uses_snapshot_without_resolving_mutable_heads(self):
        shell = textwrap.dedent(self.text.split('id: resolve', 1)[1].split('run: |\n', 1)[1]
                                .split('\n      - name:', 1)[0])
        repo = WORKFLOW.parents[2]
        manifest = json.loads((repo / 'ci/fixed/20261031/manifest.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gh = root / 'gh'
            gh.write_text('#!' + sys.executable + '\n' + textwrap.dedent('''\
                import json, sys
                path = sys.argv[2]
                if '/compare/' in path:
                    base, head = path.split('/compare/')[1].split('...')
                    print(json.dumps({'status': 'identical', 'merge_base_commit': {'sha': base}}))
                elif '/actions/workflows/' in path:
                    sys.exit(1)
                else:
                    raise SystemExit('unexpected mutable request: ' + path)
                '''))
            gh.chmod(0o755)
            result = subprocess.run(['bash', '-c', shell], cwd=repo, capture_output=True, text=True,
                                    env={**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                                         'FIXED': 'true', 'FORCE': 'true', 'GITHUB_RUN_ATTEMPT': '1',
                                         'GITHUB_WORKSPACE': str(repo), 'RUNNER_TEMP': str(root),
                                         'GITHUB_REPOSITORY': 'test/firmware', 'GITHUB_SHA': 'c' * 40,
                                         'GITHUB_RUN_ID': '42', 'GITHUB_OUTPUT': str(root / 'outputs'),
                                         'UPSTREAM_REPOSITORY': 'chasey-dev/immortalwrt-mt798x-rebase',
                                         'UPSTREAM_REF': '25.12', 'UPSTREAM_BASE': manifest['upstream_commit']})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('fixed=true', (root / 'outputs').read_text())
            self.assertIn('should_build=true', (root / 'outputs').read_text())
            self.assertEqual((root / 's20p-components.json').read_bytes(),
                             (repo / 'ci/fixed/20261031/components.json').read_bytes())

    def test_build_state_marks_only_an_executed_build_as_attempted(self):
        record = self.text.split("- name: Record exact run state", 1)[1].split("- name: Upload exact run state", 1)[0]
        self.assertIn("steps.build.outputs.build_started", record)
        self.assertIn("--previous-attempted", record)
        self.assertIn("started=false", record)

    def test_private_evidence_is_uploaded_only_after_successful_audit(self):
        upload = self.text.split("- name: Upload same-build evidence", 1)[1].split("- name: Upload validated candidate bundle", 1)[0]
        self.assertIn("steps.audit.outcome == 'success'", upload)
        self.assertIn("steps.audit.outputs.safe == 'true'", upload)

    def test_evidence_audit_runs_once(self):
        audit = self.text.split("- name: Audit all evidence before retention", 1)[1].split("- name: Upload same-build evidence", 1)[0]
        self.assertEqual(audit.count("bash ci/capture.sh --audit-output"), 1)

    def test_only_main_builds_can_publish_rolling_prereleases(self):
        self.assertRegex(self.text, r"(?m)^  publish-rolling-a-prerelease:")
        self.assertIn("github.ref == 'refs/heads/main'", self.text)
        self.assertIn("github.event_name == 'schedule'", self.text)
        self.assertIn("github.event_name == 'workflow_dispatch'", self.text)
        self.assertIn("--prerelease --latest=false", self.text)
        self.assertIn("upstream_commit", self.text)

    def test_build_and_publication_permissions_are_separated(self):
        self.assertIn("actions: read", self.text)
        self.assertIn("contents: read", self.text)
        self.assertIn("contents: write", self.text)
        self.assertIn("if: ${{ always()", self.text)
        self.assertIn("retention-days: 7", self.text)


if __name__ == "__main__":
    unittest.main()
