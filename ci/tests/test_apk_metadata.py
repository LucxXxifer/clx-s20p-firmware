"""Regress apk-tools 3.0.5 package UID versus signed-index identity metadata."""
import json
import unittest

import test_capture


class ApkMetadataTests(unittest.TestCase):
    setUp = test_capture.CaptureTests.setUp
    write_tool = test_capture.CaptureTests.write_tool
    run_capture = test_capture.CaptureTests.run_capture

    def write_package_and_index(self, info_changes=None, record_changes=None, record_omissions=()):
        # b5a31c0 app_mkpkg embeds a 20-byte UID; app_mkndx replaces hashes
        # with the 32-byte ADB identity and adds the complete APK file size.
        info = dict(self.package, hashes="a295bf10289c48e57cee37791cfb2b4315db24d3")
        info.update(info_changes or {})
        package = self.feed / "kernel-6.12.112~test-r1.apk"
        package.write_text(json.dumps({"info": info}))
        record = dict(info, hashes="403ba702cfd42a4fa597eab3276b81db900f631b8e534f8addf37bbb31b7ddd0",
                      **{"file-size": package.stat().st_size})
        record.update(record_changes or {})
        for key in record_omissions:
            record.pop(key)
        (self.feed / "packages.adb").write_text(json.dumps({"packages": [record]}))

    def assert_metadata_rejected(self):
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata", result.stderr.lower())
        self.assertFalse((self.output / "reports/capture.json").exists())

    def test_native_uid_and_signed_index_identity_can_differ(self):
        self.write_package_and_index()
        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads((self.output / "reports/signed-feeds.json").read_text())
        self.assertEqual(report["feeds"][0]["signature"], "verified")
        self.assertEqual(report["feeds"][0]["payload_identity"], "native-fetch-verified")

    def test_index_file_size_must_equal_the_preserved_apk(self):
        self.write_package_and_index(record_changes={"file-size": 1})
        self.assert_metadata_rejected()

    def test_index_only_unknown_metadata_is_rejected(self):
        self.write_package_and_index(record_changes={"future-field": "unmatched"})
        self.assert_metadata_rejected()

    def test_package_only_unknown_metadata_is_rejected(self):
        self.write_package_and_index(info_changes={"future-field": "original"},
                                     record_omissions=("future-field",))
        self.assert_metadata_rejected()

    def test_unknown_metadata_value_must_match(self):
        self.write_package_and_index(info_changes={"future-field": "original"},
                                     record_changes={"future-field": "changed"})
        self.assert_metadata_rejected()

    def test_known_metadata_value_must_match(self):
        self.write_package_and_index(info_changes={"description": "original"},
                                     record_changes={"description": "changed"})
        self.assert_metadata_rejected()

    def test_index_identity_must_be_present(self):
        self.write_package_and_index(record_changes={"hashes": None})
        self.assert_metadata_rejected()

    def test_index_identity_must_be_hexadecimal(self):
        self.write_package_and_index(record_changes={"hashes": "not-an-identity"})
        self.assert_metadata_rejected()

    def test_index_identity_cannot_be_the_package_uid(self):
        self.write_package_and_index(record_changes={"hashes": "a295bf10289c48e57cee37791cfb2b4315db24d3"})
        self.assert_metadata_rejected()

    def test_package_uid_must_have_the_native_schema(self):
        self.write_package_and_index(info_changes={"hashes": "a" * 64})
        self.assert_metadata_rejected()


if __name__ == "__main__":
    unittest.main()
