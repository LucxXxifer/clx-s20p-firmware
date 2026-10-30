"""Daily tracking must cover the whole configured source inventory, safely."""
import copy
import json
from pathlib import Path
import sys
import unittest

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
import plugin_sources as plugins


class PluginSourceTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((CI / "sources.lock.json").read_text())
        self.by_repo = {plugins.repository(row["url"]): row for row in plugins.selected(self.lock)}

    def fetch(self, path):
        repo, _, suffix = path.removeprefix("repos/").partition("/git/ref/heads/")
        if not suffix:
            return {"default_branch": "main"}
        return {"ref": "refs/heads/" + suffix,
                "object": {"type": "commit", "sha": self.by_repo[repo]["commit"]}}

    def test_istore_openclash_docker_and_other_feeds_are_included(self):
        rows = plugins.resolve(self.lock, fetch=self.fetch)
        repos = {row["repository"] for row in rows}
        self.assertTrue({"linkease/istore", "kenzok8/small-package", "immortalwrt/packages",
                         "lisaac/luci-app-dockerman", "lisaac/luci-lib-docker", "immortalwrt/luci"} <= repos)
        self.assertFalse(set(plugins.EXCLUDED) & repos)
        self.assertEqual(plugins.update_lock(self.lock, rows), self.lock)

    def test_missing_or_duplicate_source_cannot_silently_escape_tracking(self):
        rows = plugins.resolve(self.lock, fetch=self.fetch)
        for value in (rows[:-1], rows[:-1] + rows[:1]):
            with self.subTest(value=value[-1]["repository"]), self.assertRaises(ValueError):
                plugins.validate(value, self.lock)

    def test_forward_update_changes_only_selected_pins(self):
        rows = plugins.resolve(self.lock, fetch=self.fetch)
        target = next(row for row in rows if row["repository"] == "linkease/istore")
        target["head_commit"] = "a" * 40
        target["comparison"]["compare_status"] = "ahead"
        updated = plugins.update_lock(self.lock, rows)
        changed = [(a, b) for a, b in zip(self.lock["sources"], updated["sources"]) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0][1]["commit"], "a" * 40)

    def test_rewind_divergence_and_wrong_base_are_rejected(self):
        rows = plugins.resolve(self.lock, fetch=self.fetch)
        for field, value in (("compare_status", "behind"), ("compare_status", "diverged"),
                             ("merge_base", "0" * 40), ("base_commit", "0" * 40)):
            mutated = copy.deepcopy(rows)
            mutated[0]["head_commit"] = "a" * 40
            mutated[0]["comparison"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                plugins.validate(mutated, self.lock)

    def test_previous_source_rewind_is_rejected_even_if_still_ahead_of_original(self):
        rows = plugins.resolve(self.lock, fetch=self.fetch)
        rows[0]["head_commit"] = "a" * 40
        rows[0]["comparison"]["compare_status"] = "ahead"
        rows[0]["previous_comparison"] = {"base_commit": "b" * 40,
                                            "merge_base": "a" * 40, "compare_status": "behind"}
        with self.assertRaisesRegex(ValueError, "forward source update"):
            plugins.validate(rows, self.lock)

    def test_previous_comparison_does_not_create_a_spurious_new_identity(self):
        rows = plugins.resolve(self.lock, fetch=self.fetch)
        original = plugins.identity(rows)
        rows[0]["previous_comparison"] = dict(rows[0]["comparison"])
        self.assertEqual(plugins.identity(rows), original)

    def test_different_repository_or_branch_cannot_replace_an_approved_source(self):
        rows = plugins.resolve(self.lock, fetch=self.fetch)
        for key, value in (("repository", "attacker/istore"), ("ref", "other"),
                           ("resolved_ref", "other")):
            mutated = copy.deepcopy(rows)
            mutated[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                plugins.validate(mutated, self.lock)


if __name__ == "__main__":
    unittest.main()
