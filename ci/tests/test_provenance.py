import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import sys

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
spec = importlib.util.spec_from_file_location("provenance", CI / "provenance.py")
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)


class ProvenanceTests(unittest.TestCase):
    def test_source_tree_records_symlinks_and_excludes_builds_and_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "recipe").write_text("public source")
            (root / "nested").mkdir()
            (root / "link-dir").symlink_to("nested", target_is_directory=True)
            (root / "key-build").write_text("PRIVATE")
            (root / "action_build").mkdir()
            (root / "action_build/secret.key").write_text("PRIVATE")
            rows, _ = provenance.file_manifest(root)
            self.assertEqual({row["path"] for row in rows}, {"recipe", "link-dir"})
            self.assertEqual(next(row for row in rows if row["path"] == "link-dir")["target"], "nested")
            self.assertNotIn("PRIVATE", json.dumps(rows))

    def test_http_recorder_preserves_bytes_and_hashes_output_file(self):
        payload = b"firmware-input\x00\xff\n"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                url = f"http://127.0.0.1:{server.server_port}/asset"
                result = subprocess.run(["python3", str(CI / "http_record.py"), str(root / "http.jsonl"),
                                         "curl", "-fsS", url], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, payload)
                row = json.loads((root / "http.jsonl").read_text())
                self.assertEqual(row["stdout_sha256"], hashlib.sha256(payload).hexdigest())
                result = subprocess.run(["python3", str(CI / "http_record.py"), str(root / "file.jsonl"),
                                         "curl", "-fsS", "-o", str(root / "asset"), url], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertEqual((root / "asset").read_bytes(), payload)
                self.assertEqual(json.loads((root / "file.jsonl").read_text())["payload_sha256"],
                                 hashlib.sha256(payload).hexdigest())
        finally:
            server.shutdown()
            server.server_close()

    def test_source_bin_paths_are_captured_while_root_build_output_is_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            packaged = root / "package/demo/files/usr/bin/helper"
            packaged.parent.mkdir(parents=True)
            packaged.write_text("packaged script")
            (root / "bin").mkdir()
            (root / "bin/generated").write_text("build output")
            rows, _ = provenance.file_manifest(root)
            self.assertEqual([row["path"] for row in rows], ["package/demo/files/usr/bin/helper"])

    def test_packaged_public_keys_are_hashed_but_generated_signing_keys_are_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            packaged = root / "package/keyring/files/trusted.pem"
            packaged.parent.mkdir(parents=True)
            packaged.write_bytes(b"-----BEGIN PUBLIC KEY-----\nPUBLIC\n")
            (root / "private-key.pem").write_bytes(b"PRIVATE GENERATED SIGNING KEY")
            (root / "public-key.pem").write_bytes(b"GENERATED PUBLIC KEY")
            rows, _ = provenance.file_manifest(root)
            self.assertEqual([row["path"] for row in rows], ["package/keyring/files/trusted.pem"])
            self.assertEqual(rows[0]["sha256"], hashlib.sha256(packaged.read_bytes()).hexdigest())

    def test_http_client_failure_is_not_success(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "http.jsonl"
            result = subprocess.run(["python3", str(CI / "http_record.py"), str(log), "curl", "--fail", "--max-time", "1",
                                     "http://127.0.0.1:1/no-server"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotEqual(json.loads(log.read_text())["exit_code"], 0)

    def test_credential_url_rejected_before_request_or_record(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "http.jsonl"
            result = subprocess.run(["python3", str(CI / "http_record.py"), str(log), "curl",
                                     "http://user:password@127.0.0.1:1/"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
