"""B changes one source identity while retaining A's preservation evidence."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CI = Path(__file__).resolve().parents[1]
A_SOURCE = "03eb30617955519e35845838c6255ebe687eb717"
B_SOURCE = "9f397975c7b47ce6986a6e39529d2f72fd5c729d"
SOURCE_URL = "https://github.com/chasey-dev/immortalwrt-mt798x-rebase.git"


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class CommittedTransitionTests(unittest.TestCase):
    def test_only_main_source_changes_from_a_lock(self):
        lock = json.loads((CI / "sources.lock.json").read_text())
        main = [row for row in lock["sources"] if row.get("role") == "main-source"]
        self.assertEqual(len(main), 1)
        self.assertEqual(main[0]["url"], SOURCE_URL)
        self.assertEqual(main[0]["ref"], "25.12")
        self.assertEqual(main[0]["commit"], B_SOURCE)
        self.assertEqual(main[0]["previous_lock"]["commit"], A_SOURCE)
        other = [row for row in lock["sources"] if row.get("role") != "main-source"]
        self.assertEqual(len(other), 38)
        # Canonical rows from accepted A wrapper 2e6371c4; includes all fields.
        self.assertEqual(canonical_hash(other),
                         "a271534a083a1bc35b45b872726b80f549f5c5668e983c57e69738b6f50b0f98")

    def test_build_identity_agrees_without_replacing_original_evidence(self):
        config = dict(line.split("=", 1) for line in
                      (CI.parent / "wrt_core/compilecfg/S20P-WIFI-YES.ini").read_text().splitlines()
                      if "=" in line)
        self.assertEqual(config["REPO_BRANCH"], "25.12")
        self.assertEqual(config["COMMIT_HASH"], B_SOURCE)
        self.assertEqual(config["EXPECTED_KERNEL"], "6.12.112")
        policy = json.loads((CI / "baseline/policy.json").read_text())
        self.assertEqual(policy.pop("upstream_commit"), B_SOURCE)
        self.assertEqual(policy.pop("original_upstream_commit"), A_SOURCE)
        self.assertEqual(canonical_hash(policy),
                         "cc01192b2198ab1773290cee2f4d9a0a5e65199c439673e7dccc5dea6f7bf0c9")
        integrity = json.loads((CI / "baseline/baseline-sha256.json").read_text())
        self.assertEqual(integrity.pop("policy.json"),
                         hashlib.sha256((CI / "baseline/policy.json").read_bytes()).hexdigest())
        self.assertEqual(canonical_hash(integrity),
                         "bdba08e43ede92863f20ea85a9cd3e5f2c2d376f19a0631046ce2180a804d426")
        for name, expected in integrity.items():
            self.assertEqual(hashlib.sha256((CI / "baseline" / name).read_bytes()).hexdigest(), expected)


class ProvenanceTransitionTests(unittest.TestCase):
    def run_provenance(self, source_head=B_SOURCE, locked_head=B_SOURCE):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout, wrapper, commands = (root / name for name in ("checkout", "wrapper", "commands"))
            for path in (checkout, wrapper):
                (path / ".git").mkdir(parents=True)
                (path / "recipe").write_text("public fixture source\n")
            commands.mkdir()
            # Git is the only external boundary stubbed. The CLI's source lock,
            # file manifests, failure paths and evidence output run unchanged.
            fake_git = commands / "git"
            fake_git.write_text("#!" + sys.executable + "\n" + '''import os, pathlib, sys
args = sys.argv[1:]
name = pathlib.Path(args[1]).name
command = args[2:]
if command == ["remote"]:
    print("origin")
elif command == ["remote", "get-url", "origin"]:
    print(os.environ["S20P_TEST_SOURCE_URL"])
elif command == ["rev-parse", "HEAD"]:
    print(os.environ["S20P_TEST_SOURCE_HEAD"] if name == "checkout" else "2" * 40)
elif command == ["rev-parse", "HEAD^{tree}"]:
    print("3" * 40)
elif command == ["ls-tree", "-r", "--full-tree", "HEAD"]:
    print("100644 blob " + "4" * 40 + "\\trecipe")
else:
    raise SystemExit("unexpected Git fixture request: " + repr(command))
''')
            fake_git.chmod(0o755)
            lock = root / "lock.json"
            lock.write_text(json.dumps({"schema_version": 1, "sources": [
                {"url": SOURCE_URL, "ref": "25.12", "commit": locked_head}]}))
            log = root / "clones.jsonl"
            log.write_text("")
            output = root / "evidence/source-lock.json"
            environment = {**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"],
                           "S20P_GIT_LOG": str(log), "S20P_TEST_SOURCE_URL": SOURCE_URL,
                           "S20P_TEST_SOURCE_HEAD": source_head}
            result = subprocess.run([sys.executable, str(CI / "provenance.py"), str(checkout),
                                     "--lock", str(lock), "--wrapper", str(wrapper),
                                     "--output", str(output)], env=environment,
                                    capture_output=True, text=True)
            return result, json.loads(output.read_text()) if output.exists() else None

    def test_b_exact_head_records_truthful_identity(self):
        result, evidence = self.run_provenance()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(evidence["upstream_commit"], B_SOURCE)
        self.assertFalse(evidence["bit_identical_claim"])
        self.assertEqual(evidence["source_lock"]["sources"][0]["commit"], B_SOURCE)

    def test_a_head_is_rejected_even_with_coherent_old_lock(self):
        result, evidence = self.run_provenance(A_SOURCE, A_SOURCE)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("upstream commit drift", result.stderr)
        self.assertIsNone(evidence)

    def test_unapproved_head_is_rejected(self):
        result, evidence = self.run_provenance("f" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("upstream commit drift", result.stderr)
        self.assertIsNone(evidence)

    def test_b_head_with_a_lock_is_rejected(self):
        result, evidence = self.run_provenance(B_SOURCE, A_SOURCE)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prepared checkout source drift", result.stderr)
        self.assertIsNone(evidence)


if __name__ == "__main__":
    unittest.main()
