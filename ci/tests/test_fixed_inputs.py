"""Frozen builds must consume the reviewed bytes and fail closed on drift."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import re

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
sys.path.insert(0, str(CI / "tests"))
from component_fixtures import files, inputs
import components


def fixture(root):
    value = inputs()
    archive = b"reviewed\x00bytes\n"
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    archive_sha1 = hashlib.sha1(archive).hexdigest()
    recipe = components.file_bytes(value["ca"]["files"], "Makefile").decode()
    recipe = re.sub(r"(?m)^PKG_HASH:=.*$", "PKG_HASH:=" + archive_sha256, recipe)
    value["ca"]["files"] = files(recipe.encode())
    value["fingerprint"] = components.fingerprint(value)
    component = root / "components.json"
    component.write_text(json.dumps(value))
    archive_path = root / "ca/archive.tar.xz"
    archive_path.parent.mkdir()
    archive_path.write_bytes(archive)
    (root / "payload").write_bytes(b"http payload")
    manifest = {
        "schema_version": 2, "source_run": "41031102553",
        "upstream_commit": "9f397975c7b47ce6986a6e39529d2f72fd5c729d",
        "artifact": {"id": "12978132447", "sha256": "7" * 64},
        "components": {"path": "components.json", "sha256": hashlib.sha256(component.read_bytes()).hexdigest(),
                       "fingerprint": value["fingerprint"]},
        "ca_archive": {"url": "https://snapshot.debian.org/file/" + archive_sha1,
                       "path": "ca/archive.tar.xz", "sha256": archive_sha256},
        "http_inputs": [{"url": "https://example.com/reviewed", "path": "payload",
                         "sha256": hashlib.sha256((root / "payload").read_bytes()).hexdigest()}],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


class FixedInputsTests(unittest.TestCase):
    def test_select_keeps_exact_component_bytes_and_head(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, manifest = fixture(root)
            result = subprocess.run([sys.executable, str(CI / "fixed_inputs.py"), "select", str(path),
                                     "--output", str(root / "selected.json")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["upstream_commit"], manifest["upstream_commit"])
            self.assertEqual((root / "selected.json").read_bytes(), (root / "components.json").read_bytes())

    def test_rejects_tampering_symlinks_and_path_escape(self):
        for attack in ("tamper", "symlink", "escape"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path, manifest = fixture(root)
                if attack == "tamper":
                    (root / "payload").write_bytes(b"new mutable bytes")
                elif attack == "symlink":
                    (root / "payload").rename(root / "outside")
                    (root / "payload").symlink_to(root / "outside")
                else:
                    manifest["http_inputs"][0]["path"] = "../payload"
                    path.write_text(json.dumps(manifest))
                result = subprocess.run([sys.executable, str(CI / "fixed_inputs.py"), "select", str(path),
                                         "--output", str(root / "selected.json")], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "selected.json").exists())

    def test_fixed_ca_archive_is_bound_to_source_hash_and_snapshot_address(self):
        for attack in ("bytes", "address", "source_hash"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path, manifest = fixture(root)
                if attack == "bytes":
                    (root / "ca/archive.tar.xz").write_bytes(b"tampered archive")
                elif attack == "address":
                    manifest["ca_archive"]["url"] = "https://snapshot.debian.org/file/" + "0" * 40
                    path.write_text(json.dumps(manifest))
                else:
                    manifest["ca_archive"]["sha256"] = "0" * 64
                    path.write_text(json.dumps(manifest))
                result = subprocess.run([sys.executable, str(CI / "fixed_inputs.py"), "select", str(path),
                                         "--output", str(root / "selected.json")], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "selected.json").exists())

    def test_http_replay_preserves_file_stdout_and_hashes_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, manifest = fixture(root)
            url = manifest["http_inputs"][0]["url"]
            env = {**os.environ, "S20P_FIXED_INPUTS": str(path), "PATH": "/nonexistent"}
            for args, stdout in ((["curl", "--retry", "3", "--retry-delay", "2", "--retry-all-errors",
                                   "-fsSL", "-o", str(root / "out"), url], b""),
                                 (["wget", "--tries=3", "--waitretry=2", "-qO-", url],
                                  (root / "payload").read_bytes())):
                result = subprocess.run([sys.executable, str(CI / "http_record.py"), str(root / "log"), *args],
                                        env=env, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, stdout)
            self.assertEqual((root / "out").read_bytes(), (root / "payload").read_bytes())
            records = [json.loads(line) for line in (root / "log").read_text().splitlines()]
            self.assertEqual(records[0]["payload_sha256"], manifest["http_inputs"][0]["sha256"])
            self.assertEqual(records[1]["stdout_sha256"], manifest["http_inputs"][0]["sha256"])

    def test_fixed_http_rejects_unknown_url_options_and_tampered_payload(self):
        for attack in ("url", "options", "payload"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path, manifest = fixture(root)
                url = manifest["http_inputs"][0]["url"]
                args = ["wget", "-qO-", url]
                if attack == "url": args[-1] = "https://example.com/new"
                if attack == "options": args.insert(1, "--post-data=changed")
                if attack == "payload": (root / "payload").write_bytes(b"changed")
                result = subprocess.run([sys.executable, str(CI / "http_record.py"), str(root / "log"), *args],
                                        env={**os.environ, "S20P_FIXED_INPUTS": str(path), "PATH": "/nonexistent"},
                                        capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "log").exists())
                self.assertEqual(result.stdout, b"")

    def test_runtime_verify_rejects_component_or_system_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, manifest = fixture(root)
            for head, component in (("f" * 40, root / "components.json"),
                                    (manifest["upstream_commit"], root / "changed.json")):
                (root / "changed.json").write_text("{}")
                result = subprocess.run([sys.executable, str(CI / "fixed_inputs.py"), "verify", str(path),
                                         "--head", head, "--components", str(component)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)

    def test_runtime_capture_is_self_contained_and_http_coverage_is_exact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, manifest = fixture(root)
            result = subprocess.run([sys.executable, str(CI / "fixed_inputs.py"), "verify", str(path),
                                     "--head", manifest["upstream_commit"], "--components", str(root / "components.json"),
                                     "--capture", str(root / "captured")], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "captured/payload").read_bytes(), (root / "payload").read_bytes())
            self.assertEqual((root / "captured/ca/archive.tar.xz").read_bytes(),
                             (root / "ca/archive.tar.xz").read_bytes())
            record = {"urls": [manifest["http_inputs"][0]["url"]], "exit_code": 0,
                      "stdout_sha256": manifest["http_inputs"][0]["sha256"]}
            for rows, success in (([record], True), ([], False), ([record, record], False),
                                  ([{**record, "stdout_sha256": "0" * 64}], False)):
                (root / "http.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
                result = subprocess.run([sys.executable, str(CI / "fixed_inputs.py"), "verify-http", str(path),
                                         "--log", str(root / "http.jsonl")], capture_output=True)
                self.assertEqual(result.returncode == 0, success, result.stderr)


if __name__ == "__main__":
    unittest.main()
