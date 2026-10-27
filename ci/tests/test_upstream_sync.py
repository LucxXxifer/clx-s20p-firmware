"""Contract tests for safely resolving the daily upstream build input."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile


CI = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("upstream_sync", CI / "upstream_sync.py")
upstream_sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upstream_sync)


class UpstreamResolutionTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((CI / "sources.lock.json").read_text())
        self.baseline = CI / "baseline"
        self.base_commit = "9f397975c7b47ce6986a6e39529d2f72fd5c729d"
        self.next_commit = "a" * 40

    def test_identical_head_skips_scheduled_build_but_allows_manual_build(self):
        scheduled = upstream_sync.resolve_head(
            self.base_commit, self.base_commit, "identical", self.base_commit, force=False
        )
        manual = upstream_sync.resolve_head(
            self.base_commit, self.base_commit, "identical", self.base_commit, force=True
        )
        self.assertFalse(scheduled.should_build)
        self.assertTrue(manual.should_build)
        self.assertEqual(manual.commit, self.base_commit)

    def test_reuses_only_a_verified_attempt_for_the_same_wrapper_and_upstream_sha(self):
        wrapper = "c" * 40
        previous = {
            "schema_version": 1,
            "wrapper_commit": wrapper,
            "upstream_commit": self.next_commit,
            "build_started": True,
            "outcome": "failure",
        }
        skipped = upstream_sync.resolve_head(
            self.base_commit, self.next_commit, "ahead", self.base_commit,
            force=False, previous_state=previous, wrapper_commit=wrapper,
        )
        retried = upstream_sync.resolve_head(
            self.base_commit, self.next_commit, "ahead", self.base_commit,
            force=True, previous_state=previous, wrapper_commit=wrapper,
        )
        different_wrapper = upstream_sync.resolve_head(
            self.base_commit, self.next_commit, "ahead", self.base_commit,
            force=False, previous_state=previous, wrapper_commit="d" * 40,
        )
        self.assertFalse(skipped.should_build)
        self.assertTrue(retried.should_build)
        self.assertTrue(different_wrapper.should_build)

    def test_fast_forward_head_is_selected_without_changing_other_source_pins(self):
        resolved = upstream_sync.resolve_head(
            self.base_commit, self.next_commit, "ahead", self.base_commit, force=False
        )
        self.assertTrue(resolved.should_build)
        self.assertEqual(resolved.commit, self.next_commit)
        original_main = next(row for row in self.lock["sources"] if row.get("role") == "main-source")
        other_hash = upstream_sync.canonical_hash(
            [row for row in self.lock["sources"] if row.get("role") != "main-source"]
        )
        updated = upstream_sync.update_lock(self.lock, resolved)
        updated_main = next(row for row in updated["sources"] if row.get("role") == "main-source")
        self.assertEqual(updated_main["url"], original_main["url"])
        self.assertEqual(updated_main["ref"], original_main["ref"])
        self.assertEqual(updated_main["commit"], self.next_commit)
        self.assertEqual(
            upstream_sync.canonical_hash(
                [row for row in updated["sources"] if row.get("role") != "main-source"]
            ),
            other_hash,
        )

    def test_rejects_malformed_or_non_fast_forward_heads(self):
        for head, status, merge_base in (
            ("not-a-commit", "ahead", self.base_commit),
            ("f" * 40, "behind", self.base_commit),
            ("e" * 40, "diverged", "d" * 40),
            ("c" * 40, "ahead", "b" * 40),
        ):
            with self.subTest(status=status, head=head):
                with self.assertRaises(upstream_sync.UpstreamSyncError):
                    upstream_sync.resolve_head(
                        self.base_commit, head, status, merge_base, force=False
                    )

    def test_rejects_a_changed_build_base(self):
        with self.assertRaises(upstream_sync.UpstreamSyncError):
            upstream_sync.resolve_head("0" * 40, self.next_commit, "ahead", "0" * 40, force=False)

    def test_requires_fast_forward_from_the_previous_verified_head_even_when_forced(self):
        wrapper, previous_head = "c" * 40, "b" * 40
        previous = {
            "schema_version": 1, "wrapper_commit": wrapper,
            "upstream_commit": previous_head, "build_started": True, "outcome": "success",
        }
        for force in (False, True):
            for status, merge_base in ((None, None), ("behind", self.next_commit),
                                       ("diverged", self.base_commit), ("ahead", self.base_commit)):
                with self.subTest(force=force, status=status):
                    with self.assertRaisesRegex(upstream_sync.UpstreamSyncError, "previous"):
                        upstream_sync.resolve_head(
                            self.base_commit, self.next_commit, "ahead", self.base_commit,
                            force=force, previous_state=previous, wrapper_commit=wrapper,
                            previous_status=status, previous_merge_base=merge_base)

    def test_records_previous_head_comparison_and_rejects_tampered_evidence(self):
        wrapper, previous_head = "c" * 40, "b" * 40
        previous = {
            "schema_version": 1, "wrapper_commit": wrapper,
            "upstream_commit": previous_head, "build_started": False, "outcome": "skipped",
        }
        resolved = upstream_sync.resolve_head(
            self.base_commit, self.next_commit, "ahead", self.base_commit,
            force=False, previous_state=previous, wrapper_commit=wrapper,
            previous_status="ahead", previous_merge_base=previous_head)
        with tempfile.TemporaryDirectory() as temp:
            runtime_lock, baseline, evidence_path = upstream_sync.prepare(
                CI / "sources.lock.json", self.baseline, Path(temp) / "prepared", resolved)
            evidence = json.loads(evidence_path.read_text())
            self.assertEqual(evidence["previous_comparison"], {
                "base_commit": previous_head, "compare_status": "ahead", "merge_base": previous_head,
            })
            evidence["previous_comparison"]["compare_status"] = "behind"
            evidence_path.write_text(json.dumps(evidence))
            with self.assertRaisesRegex(upstream_sync.UpstreamSyncError, "previous"):
                upstream_sync.verify_prepared(
                    CI / "sources.lock.json", self.baseline, runtime_lock, baseline, evidence_path)

    def test_runtime_baseline_changes_only_the_expected_upstream_and_integrity_digest(self):
        resolved = upstream_sync.resolve_head(
            self.base_commit, self.next_commit, "ahead", self.base_commit, force=False
        )
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "baseline"
            upstream_sync.prepare_baseline(self.baseline, destination, resolved.commit)
            original_policy = json.loads((self.baseline / "policy.json").read_text())
            runtime_policy = json.loads((destination / "policy.json").read_text())
            original_policy.pop("upstream_commit")
            runtime_policy.pop("upstream_commit")
            self.assertEqual(runtime_policy, original_policy)
            integrity = json.loads((destination / "baseline-sha256.json").read_text())
            self.assertEqual(
                integrity["policy.json"],
                hashlib.sha256((destination / "policy.json").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                integrity["artifact-sha256.json"],
                hashlib.sha256((destination / "artifact-sha256.json").read_bytes()).hexdigest(),
            )

    def test_prepare_and_verify_bind_lock_evidence_and_baseline_to_one_exact_head(self):
        resolved = upstream_sync.resolve_head(
            self.base_commit, self.next_commit, "ahead", self.base_commit, force=False
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = root / "committed-lock.json"
            lock.write_text(json.dumps(self.lock))
            output = root / "prepared"
            runtime_lock, baseline, evidence = upstream_sync.prepare(
                lock, self.baseline, output, resolved
            )
            result = upstream_sync.verify_prepared(lock, self.baseline, runtime_lock, baseline, evidence)
            self.assertEqual(result.commit, self.next_commit)
            mutated_lock = json.loads(runtime_lock.read_text())
            mutated_lock["sources"][0]["commit"] = "b" * 40
            runtime_lock.write_text(json.dumps(mutated_lock))
            with self.assertRaisesRegex(upstream_sync.UpstreamSyncError, "source lock differs"):
                upstream_sync.verify_prepared(lock, self.baseline, runtime_lock, baseline, evidence)

    def test_baseline_manifest_accepts_only_sha256_digests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "baseline"
            upstream_sync.prepare_baseline(self.baseline, root, self.base_commit)
            manifest_path = root / "baseline-sha256.json"
            manifest = json.loads(manifest_path.read_text())
            name = next(iter(manifest))
            manifest[name] = "a" * 40
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(upstream_sync.UpstreamSyncError, "invalid baseline file record"):
                upstream_sync.verify_baseline(root)

    def test_state_zip_is_verified_before_it_can_suppress_a_duplicate_build(self):
        wrapper, upstream = "c" * 40, "a" * 40
        state = {
            "schema_version": 1,
            "wrapper_commit": wrapper,
            "upstream_commit": upstream,
            "build_started": True,
            "outcome": "failure",
        }
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "state.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr("upstream-state.json", json.dumps(state))
            sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(
                upstream_sync.verify_state_zip(archive, "sha256:" + sha, wrapper)["upstream_commit"],
                upstream,
            )
            with self.assertRaisesRegex(upstream_sync.UpstreamSyncError, "digest differs"):
                upstream_sync.verify_state_zip(archive, "sha256:" + "0" * 64, wrapper)

    def test_build_rejects_tampered_runtime_policy_before_cloning_and_after_build(self):
        for tamper_after_build in (False, True):
            with self.subTest(tamper_after_build=tamper_after_build), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                wrapper = root / "wrapper"
                (wrapper / "ci").mkdir(parents=True)
                for name in ("build.sh", "upstream_sync.py", "sources.lock.json", "source_lock.py",
                             "mobile-usb-packages.txt", "preservation.py", "provenance.py"):
                    shutil.copyfile(CI / name, wrapper / "ci" / name)
                shutil.copytree(self.baseline, wrapper / "ci/baseline")
                resolved = upstream_sync.resolve_head(
                    self.base_commit, self.base_commit, "identical", self.base_commit, force=True)
                lock, baseline, evidence = upstream_sync.prepare(
                    wrapper / "ci/sources.lock.json", wrapper / "ci/baseline", root / "inputs", resolved)
                tampered = json.loads((baseline / "policy.json").read_text())
                del tampered["required_kernel_config"]["CONFIG_DEBUG_INFO_BTF"]
                policy_bytes = (json.dumps(tampered, indent=2) + "\n").encode()
                manifest = json.loads((baseline / "baseline-sha256.json").read_text())
                manifest["policy.json"] = hashlib.sha256(policy_bytes).hexdigest()
                tamper = wrapper / "tamper.py"
                tamper.write_text(
                    "from pathlib import Path\n" +
                    "Path(" + repr(str(baseline / "policy.json")) + ").write_bytes(" + repr(policy_bytes) + ")\n" +
                    "Path(" + repr(str(baseline / "baseline-sha256.json")) + ").write_text(" +
                    repr(json.dumps(manifest)) + ")\n")
                if not tamper_after_build:
                    subprocess.run(["python3", str(tamper)], check=True)
                (wrapper / "wrt_core").mkdir()
                (wrapper / "wrt_core/pre_clone_action.sh").write_text("touch clone-started\n")
                (wrapper / "build.sh").write_text(
                    ("python3 tamper.py\n" if tamper_after_build else "") +
                    "mkdir -p output/reports\n" +
                    "touch output/reports/capture.json output/reports/signed-feeds.json\n")
                result = subprocess.run(["bash", "ci/build.sh"], cwd=wrapper, capture_output=True, text=True,
                                        env={**os.environ, "S20P_SOURCE_LOCK": str(lock),
                                             "S20P_BASELINE_DIR": str(baseline),
                                             "S20P_UPSTREAM_EVIDENCE": str(evidence),
                                             "S20P_UPSTREAM_COMMIT": self.base_commit})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("runtime baseline policy differs", result.stderr)
                self.assertEqual((wrapper / "clone-started").exists(), tamper_after_build)

    def test_skipped_runs_preserve_attempt_history_for_consecutive_days(self):
        wrapper = "c" * 40
        with tempfile.TemporaryDirectory() as temp:
            state = upstream_sync.write_state(
                Path(temp) / "first.json", wrapper_commit=wrapper,
                upstream_commit=self.next_commit, build_started=True, outcome="failure",
                run_id="1", run_attempt="1")
            for day in range(2, 6):
                resolution = upstream_sync.resolve_head(
                    self.base_commit, self.next_commit, "ahead", self.base_commit,
                    force=False, previous_state=state, wrapper_commit=wrapper)
                self.assertFalse(resolution.should_build)
                state = upstream_sync.write_state(
                    Path(temp) / (str(day) + ".json"), wrapper_commit=wrapper,
                    upstream_commit=self.next_commit, build_started=False, outcome="skipped",
                    previous_attempted=resolution.previous_attempted,
                    run_id=str(day), run_attempt="1")

    def test_prebuild_failure_does_not_suppress_the_next_attempt(self):
        wrapper = "c" * 40
        with tempfile.TemporaryDirectory() as temp:
            state = upstream_sync.write_state(
                Path(temp) / "state.json", wrapper_commit=wrapper,
                upstream_commit=self.next_commit, build_started=False, outcome="skipped",
                run_id="1", run_attempt="1")
            resolution = upstream_sync.resolve_head(
                self.base_commit, self.next_commit, "ahead", self.base_commit,
                force=False, previous_state=state, wrapper_commit=wrapper)
            self.assertTrue(resolution.should_build)


if __name__ == "__main__":
    unittest.main()
