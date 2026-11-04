"""Evidence-based release notes; no firmware build or network is needed."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_inventory as inventory
import release_changelog as changelog


WRAPPER = "a" * 40
UPSTREAM = "b" * 40
REPO = "owner/firmware"


def write_json(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def bundle(root):
    write_json(root, "inspection/installed-packages.json", {"packages": [
        {"name": "kernel", "version": "6.12.112~abi-r1", "private": "never-export"},
        {"name": "busybox", "version": "1.37-r1"}]})
    write_json(root, "provenance/source-lock.json", {
        "wrapper_checkout_commit": WRAPPER, "upstream_commit": UPSTREAM,
        "sources": [{"url": "https://github.com/owner/upstream.git", "commit": UPSTREAM}],
        "secret": "never-export"})
    write_json(root, "provenance/wrapper-source-files.json", [
        {"path": "ci/security/fix-CVE-2026-12345.patch", "sha256": "c" * 64},
        {"path": "private/router.json", "sha256": "d" * 64}])
    write_json(root, "reports/kernel-security.json", {
        "status": "pass", "cve": "CVE-2026-12345", "profile_id": "test-profile",
        "profile_sha256": "e" * 64, "runtime_tested": False, "private": "never-export"})
    write_json(root, "reports/go-x509-security.json", {
        "status": "pass-with-known-exceptions", "binaries": [{"go_version": "go1.26.10"}],
        "known_exceptions": [{"path": "/usr/bin/quickstart", "advisory": "GO-2026-6809"}]})
    write_json(root, "reports/components.json", {
        "status": "pass", "ca": {"version": "20260816-r2", "certificate_count": 120,
                                      "bundle_sha256": "f" * 64, "private": "never-export"}})
    (root / "metadata").mkdir(exist_ok=True)
    (root / "metadata/final.config").write_text("CONFIG_BUSYBOX_CONFIG_FEATURE_SYSLOG_INFO=y\n")


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        bundle(self.root)

    def test_inventory_contains_only_public_image_evidence(self):
        got = inventory.build_inventory(self.root)
        self.assertEqual(got["packages"]["busybox"], "1.37-r1")
        self.assertEqual(got["sources"], {"https://github.com/owner/upstream": UPSTREAM})
        self.assertTrue(got["cron_syslog_info"])
        self.assertEqual(got["reports"]["kernel-security"]["cve"], "CVE-2026-12345")
        self.assertEqual(got["ca"]["certificate_count"], 120)
        self.assertEqual(got["go"]["versions"], ["go1.26.10"])
        self.assertNotIn("never-export", json.dumps(got))
        self.assertNotIn("private/router.json", got["patches"])

    def test_duplicate_package_and_unsafe_source_are_rejected(self):
        write_json(self.root, "inspection/installed-packages.json", {"packages": [
            {"name": "kernel", "version": "1"}, {"name": "kernel", "version": "2"}]})
        with self.assertRaises(ValueError):
            inventory.build_inventory(self.root)
        bundle(self.root)
        source = json.loads((self.root / "provenance/source-lock.json").read_text())
        source["sources"][0]["url"] = "https://token@github.com/owner/repo?secret=1"
        write_json(self.root, "provenance/source-lock.json", source)
        with self.assertRaises(ValueError):
            inventory.build_inventory(self.root)

    def test_missing_optional_reports_are_unknown_not_success(self):
        (self.root / "reports/kernel-security.json").unlink()
        (self.root / "metadata/final.config").unlink()
        got = inventory.build_inventory(self.root)
        self.assertNotIn("kernel-security", got["reports"])
        self.assertIsNone(got["cron_syslog_info"])

    def test_snapshot_is_bound_to_package_count_and_exact_source(self):
        got = inventory.build_inventory(self.root)
        metadata = {"wrapper_commit": WRAPPER, "upstream_commit": UPSTREAM,
                    "kernel_abi": got["packages"]["kernel"], "package_count": 2}
        inventory.validate_inventory(got, metadata)
        for key, value in [("wrapper_commit", "d" * 40), ("package_count", 3),
                           ("kernel_abi", "other")]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                inventory.validate_inventory(got, {**metadata, key: value})


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        bundle(root)
        self.current = inventory.build_inventory(root)
        self.previous = copy.deepcopy(self.current)
        self.meta = {"run_id": "100", "run_attempt": "1", "wrapper_commit": WRAPPER,
                     "upstream_commit": UPSTREAM, "kernel_abi": "6.12.112~abi-r1",
                     "image_sha256": "c" * 64, "live_acceptance": "not-run"}

    def render(self, previous=True, upstream=None):
        baseline = {"tag": "s20p-a-25.12-candidate-90-1-bbbbbbbb",
                    "inventory": self.previous} if previous else None
        return changelog.render_notes(REPO, self.meta, self.current, baseline, upstream or [])

    def test_changed_added_and_removed_packages_and_patch_hashes(self):
        self.previous["packages"]["busybox"] = "1.36-r1"
        self.previous["packages"]["old-plugin"] = "1"
        self.current["packages"]["new-plugin"] = "2"
        self.current["patches"]["ci/security/fix-CVE-2026-12345.patch"] = "f" * 64
        notes = self.render()
        for expected in ["busybox", "1.36-r1", "1.37-r1", "新增", "移除",
                         "old-plugin", "new-plugin", "補丁", "CVE-2026-12345"]:
            self.assertIn(expected, notes)
        self.assertIn("硬體", notes)
        self.assertIn("未驗收", notes)
        self.assertIn("上游宣稱", notes)

    def test_no_baseline_and_missing_security_evidence_are_explicit(self):
        self.current["reports"].pop("kernel-security")
        notes = self.render(previous=False)
        self.assertIn("首次", notes)
        self.assertIn("未提供", notes)
        self.assertNotIn("已修復 CVE", notes)

    def test_changed_go_ca_and_removed_security_reports_do_not_inherit_old_claims(self):
        self.previous['go']['versions'] = ['go1.26.9']
        self.previous['ca']['version'] = '20250101-r1'
        self.current['reports'].pop('kernel-security')
        notes = self.render()
        for expected in ('go1.26.9', 'go1.26.10', '20250101-r1', '20260816-r2',
                         '不繼承前版', 'kernel-security'):
            self.assertIn(expected, notes)

    def test_markdown_injection_and_incomplete_compare_do_not_claim_no_changes(self):
        notes = self.render(upstream=[{"repository": "owner/upstream", "base": "c" * 40,
            "head": UPSTREAM, "status": "ahead", "total_commits": 99,
            "commits": [{"sha": "d" * 40, "subject": "Fix CVE <script>@everyone [click](bad)\n# heading"}],
            "complete": False}])
        self.assertIn("上游宣稱", notes)
        self.assertIn("99", notes)
        self.assertIn("截斷", notes)
        self.assertNotIn("<script>", notes)
        self.assertNotIn("@everyone", notes)
        self.assertNotIn("[click](bad)", notes)


class FakeApi:
    def __init__(self):
        self.routes = {}
        self.files = {}
        self.calls = []

    def json(self, path):
        self.calls.append(path)
        value = self.routes[path]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def download(self, asset, destination):
        destination.write_bytes(self.files[asset["id"]])


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = FakeApi()
        root = self.root / "bundle"
        root.mkdir()
        bundle(root)
        self.inv = inventory.build_inventory(root)
        self.meta = {"schema_version": 1, "run_id": "90", "run_attempt": "1",
                     "wrapper_commit": WRAPPER, "upstream_commit": UPSTREAM,
                     "image": "clx-s20p-test-sysupgrade.bin", "image_sha256": hashlib.sha256(b"image").hexdigest(),
                     "kernel_abi": self.inv["packages"]["kernel"], "package_count": 2,
                     "live_acceptance": "not-run", "stable_eligible": False,
                     "changelog_inventory": self.inv}
        self.row = self.add_release(90, "2026-11-04T12:00:00Z")
        self.api.routes[f"repos/{REPO}/releases?per_page=100&page=1"] = [self.row]

    def add_release(self, run, published, *, legacy=False):
        metadata = {**self.meta, "run_id": str(run)}
        if legacy:
            metadata.pop("changelog_inventory")
        archive = f"s20p-samebuild-{run}-1.tar.gz"
        payload = b"unused for snapshot baseline"
        if legacy:
            root = self.root / "bundle"
            sums = "".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.relative_to(root).as_posix() + "\n"
                           for p in sorted(root.rglob("*")) if p.is_file() and p.name != "SHA256SUMS")
            (root / "SHA256SUMS").write_text(sums)
            target = self.root / archive
            with tarfile.open(target, "w:gz") as tf:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        tf.add(path, arcname=path.relative_to(root).as_posix())
            payload = target.read_bytes()
        files = {"BUILD-VERIFICATION.json": json.dumps(metadata).encode(),
                 metadata["image"]: b"image", archive: payload, "INSTALLATION.md": b"guide",
                 "archive.SHA256SUMS": (hashlib.sha256(payload).hexdigest() + "  " + archive + "\n").encode()}
        files["SHA256SUMS"] = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n"
                                         for name, data in sorted(files.items())).encode()
        assets = []
        for index, (name, data) in enumerate(files.items()):
            asset = {"id": run * 10 + index, "name": name, "size": len(data),
                     "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
            assets.append(asset)
            self.api.files[asset["id"]] = data
        tag = f"s20p-a-25.12-candidate-{run}-1-bbbbbbbb"
        self.api.routes[f"repos/{REPO}/actions/runs/{run}/attempts/1"] = {
            "id": run, "run_attempt": 1, "head_sha": WRAPPER, "status": "completed",
            "conclusion": "success", "workflow_id": 394714552}
        self.api.routes[f"repos/{REPO}/git/ref/tags/{tag}"] = {"object": {"type": "commit", "sha": WRAPPER}}
        return {"id": run, "tag_name": tag, "published_at": published, "draft": False, "assets": assets}

    def find(self):
        return changelog.find_baseline(self.api, REPO, "100", self.root / "cache")

    def test_latest_publication_not_api_order_or_highest_run_is_baseline(self):
        later = self.add_release(80, "2026-11-04T13:00:00Z")
        self.api.routes[f"repos/{REPO}/releases?per_page=100&page=1"].append(later)
        self.assertEqual(self.find()["tag"], later["tag_name"])

    def test_paginates_and_ignores_draft_current_run_and_failed_candidate(self):
        failed = self.add_release(99, "2026-11-04T14:00:00Z")
        self.api.routes[f"repos/{REPO}/actions/runs/99/attempts/1"]["conclusion"] = "failure"
        draft = {**failed, "draft": True}
        current = self.add_release(100, "2026-11-04T15:00:00Z")
        self.api.routes[f"repos/{REPO}/releases?per_page=100&page=1"] = [draft, failed, current] + [
            {"draft": False, "tag_name": "unrelated"}] * 97
        self.api.routes[f"repos/{REPO}/releases?per_page=100&page=2"] = [self.row]
        self.assertEqual(self.find()["tag"], self.row["tag_name"])
        self.assertIn(f"repos/{REPO}/releases?per_page=100&page=2", self.api.calls)

    def test_excludes_only_exact_attempt_even_though_workflow_disallows_reruns(self):
        prior_attempt = self.add_release(100, '2026-11-04T15:00:00Z')
        self.api.routes[f'repos/{REPO}/releases?per_page=100&page=1'].append(prior_attempt)
        got = changelog.find_baseline(self.api, REPO, '100', self.root / 'cache', current_attempt='2')
        self.assertEqual(got['tag'], prior_attempt['tag_name'])

    def test_missing_baseline_is_explicit_but_api_failure_is_not_hidden(self):
        key = f"repos/{REPO}/releases?per_page=100&page=1"
        self.api.routes[key] = []
        self.assertIsNone(self.find())
        self.api.routes[key] = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            self.find()

    def test_wrong_tag_commit_digest_checksum_or_inventory_blocks_notes(self):
        routes, files = copy.deepcopy(self.api.routes), copy.deepcopy(self.api.files)
        mutations = [
            lambda: self.api.routes[f"repos/{REPO}/git/ref/tags/{self.row['tag_name']}"]["object"].update(sha="d" * 40),
            lambda: self.api.files.update({900: b"tampered metadata"}),
            lambda: self.api.routes[f"repos/{REPO}/releases?per_page=100&page=1"][0]["assets"][0].update(digest="sha256:" + "0" * 64),
        ]
        for mutate in mutations:
            self.api.routes, self.api.files = copy.deepcopy(routes), copy.deepcopy(files)
            shutil.rmtree(self.root / "cache", ignore_errors=True)
            mutate()
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                self.find()

    def test_legacy_candidate_reads_checksum_verified_archive_without_running_tools(self):
        self.row = self.add_release(90, "2026-11-04T12:00:00Z", legacy=True)
        self.api.routes[f"repos/{REPO}/releases?per_page=100&page=1"] = [self.row]
        got = self.find()
        self.assertEqual(got["inventory"], self.inv)
        self.assertEqual(got["inventory_origin"], "checksum-verified-archive")

    def test_coherent_api_digest_with_wrong_outer_checksum_is_rejected(self):
        asset = self.api.routes[f'repos/{REPO}/releases?per_page=100&page=1'][0]['assets'][0]
        payload = self.api.files[asset['id']] + b' '
        self.api.files[asset['id']] = payload
        asset.update(size=len(payload), digest='sha256:' + hashlib.sha256(payload).hexdigest())
        with self.assertRaisesRegex(ValueError, 'checksums disagree'):
            self.find()

    def test_coherent_release_with_inconsistent_inventory_is_rejected(self):
        self.meta['changelog_inventory']['packages']['kernel'] = 'different-kernel'
        self.api.routes[f'repos/{REPO}/releases?per_page=100&page=1'] = [
            self.add_release(90, '2026-11-04T12:00:00Z')]
        with self.assertRaisesRegex(ValueError, 'exact release'):
            self.find()

    def test_annotated_tag_is_resolved_to_its_commit(self):
        self.api.routes[f'repos/{REPO}/git/ref/tags/{self.row["tag_name"]}'] = {
            'object': {'type': 'tag', 'sha': 'e' * 40}}
        self.api.routes[f'repos/{REPO}/git/tags/{"e" * 40}'] = {
            'object': {'type': 'commit', 'sha': WRAPPER}}
        self.assertEqual(self.find()['tag'], self.row['tag_name'])

    def test_incomplete_compare_and_api_unavailability_are_labeled(self):
        path = f"repos/owner/upstream/compare/{'c' * 40}...{UPSTREAM}?per_page=20&page=1"
        self.api.routes[path] = {"status": "ahead", "total_commits": 50,
                                 "commits": [{"sha": "d" * 40, "commit": {"message": "fix CVE claim\nbody"}}]}
        got = changelog.compare_source(self.api, "owner/upstream", "c" * 40, UPSTREAM)
        self.assertFalse(got["complete"])
        self.assertEqual(got["commits"][0]["subject"], "fix CVE claim")
        self.api.routes[path] = RuntimeError("unavailable")
        self.assertEqual(changelog.compare_source(self.api, "owner/upstream", "c" * 40, UPSTREAM)["status"], "unavailable")

    def test_optional_compare_content_failures_do_not_block_verified_image_notes(self):
        path = f'repos/owner/upstream/compare/{"c" * 40}...{UPSTREAM}?per_page=20&page=1'
        for response in [ValueError('response exceeds limit'), json.JSONDecodeError('bad', '', 0),
                         None, [], {'total_commits': 1, 'status': 'ahead', 'commits': [None]},
                         {'total_commits': 0, 'status': [], 'commits': []},
                         {'total_commits': 1, 'status': 'ahead', 'commits': [{'sha': 'd' * 40, 'commit': None}]}]:
            self.api.routes[path] = response
            with self.subTest(response=response):
                self.assertEqual(changelog.compare_source(self.api, 'owner/upstream', 'c' * 40, UPSTREAM)['status'], 'unavailable')


class GeneratorTests(unittest.TestCase):
    def test_real_prepared_six_asset_set_produces_notes_without_mutating_assets(self):
        from test_release_candidate import release_fixture, TEST_WRAPPER_COMMIT
        import release_candidate as release
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / 'artifact'
            artifact.mkdir()
            release_fixture(artifact, commit=TEST_WRAPPER_COMMIT)
            assets = root / 'assets'
            release.prepare_release_assets(artifact, assets, run_id='1234', run_attempt='2',
                build_commit=TEST_WRAPPER_COMMIT, upstream_commit=release.UPSTREAM_BASE_COMMIT)
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in assets.iterdir()}
            api = FakeApi()
            api.routes[f'repos/{REPO}/releases?per_page=100&page=1'] = []
            notes = changelog.generate(api, REPO, assets, root / 'cache')
            self.assertIn('首次', notes)
            self.assertIn('1234', notes)
            self.assertEqual(len(before), 6)
            self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in assets.iterdir()})


if __name__ == "__main__":
    unittest.main()
