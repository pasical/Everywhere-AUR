import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
state = importlib.import_module("release_state")
control = importlib.import_module("manage_release")
archive = importlib.import_module("archive_recipes")


class ReleaseControlTests(unittest.TestCase):
    def policy(self):
        return {"pinned_tag": None, "withdrawn_tags": {}}

    def test_pin_allows_only_selected_tag(self):
        policy = state.select_policy(self.policy(), "pin", "v1.2.3")
        self.assertTrue(state.allows(policy, "v1.2.3"))
        self.assertFalse(state.allows(policy, "v2.0.0"))

    def test_withdraw_blocks_old_events_and_retains_reason(self):
        original = self.policy()
        policy = state.select_policy(original, "withdraw", "v1.0.0", "v2.0.0", "Regression")
        self.assertFalse(state.allows(policy, "v2.0.0"))
        self.assertEqual(policy["withdrawn_tags"]["v2.0.0"]["reason"], "Regression")
        self.assertEqual(original, self.policy())

    def test_resume_does_not_resurrect_withdrawn_release(self):
        policy = state.select_policy(self.policy(), "withdraw", "v1.0.0", "v2.0.0")
        policy = state.select_policy(policy, "resume", "v3.0.0")
        self.assertIsNone(policy["pinned_tag"])
        self.assertTrue(state.allows(policy, "v3.0.0"))
        self.assertFalse(state.allows(policy, "v2.0.0"))

    def test_withdraw_cannot_hide_replacement_or_select_withdrawn_version(self):
        with self.assertRaises(ValueError):
            state.select_policy(self.policy(), "withdraw", "v1.0.0", "v1.0.0")
        policy = state.select_policy(self.policy(), "withdraw", "v1.0.0", "v2.0.0")
        with self.assertRaises(ValueError):
            state.select_policy(policy, "pin", "v2.0.0")

    def test_resume_version_order_handles_numeric_canary_and_final(self):
        tags = ["v1.2.3-canary.9", "v1.2.3-canary.10", "v1.2.3", "v1.2.4-canary.1"]
        self.assertEqual(sorted(reversed(tags), key=control.version_key), tags)

    def test_invalid_tag_cannot_be_used_as_archive_path(self):
        with self.assertRaises(ValueError):
            state.select_policy(self.policy(), "pin", "../../escape")

    def finalize(self, publication_tag):
        policy = state.select_policy(self.policy(), "withdraw", "v1.0.0", "v2.0.0")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "published").mkdir()
            (output / "published/publication.json").write_text(json.dumps({"tag": publication_tag}))
            with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/mirror"}), \
                    patch.object(sys, "argv", ["manage_release.py", "--finalize", "--tag", "v1.0.0",
                                              "--withdraw-tag", "v2.0.0", "--output", str(output)]), \
                    patch.object(control, "read_policy", return_value=policy), \
                    patch.object(control, "find_release", return_value={"id": 42}), \
                    patch.object(control, "api") as api:
                control.main()
                return api.call_args

    def test_withdrawal_happens_only_after_confirmed_target_publication(self):
        call = self.finalize("v1.0.0")
        self.assertEqual(call.args, ("repos/owner/mirror/releases/42", "PATCH", {"draft": True}))

    def test_missing_replacement_confirmation_keeps_release_public(self):
        with self.assertRaisesRegex(ValueError, "replacement was not confirmed"):
            self.finalize("v3.0.0")

    def test_generated_recipe_is_committed_before_release_asset_upload(self):
        events = []
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ("PKGBUILD", ".SRCINFO", "mirror.json"):
                (directory / name).write_text("fixture")
            with patch.object(archive, "api", return_value=None), \
                    patch.object(archive, "find_release", return_value={"id": 1}), \
                    patch.object(archive, "commit_files", side_effect=lambda *args: events.append(("git", args[1]))), \
                    patch.object(archive, "run", side_effect=lambda *args: events.append(("release", args))):
                archive.archive("owner/mirror", "v1.0.0", directory)
            self.assertEqual(events[0][0], "git")
            self.assertIn("packages/v1.0.0/generated/.SRCINFO", events[0][1])
            self.assertEqual(events[1][0], "release")

    def test_policy_change_prevents_withdrawal(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/mirror"}), \
                patch.object(sys, "argv", ["manage_release.py", "--finalize", "--tag", "v1.0.0", "--withdraw-tag", "v2.0.0"]), \
                patch.object(control, "read_policy", return_value=self.policy()), patch.object(control, "api") as api:
            with self.assertRaisesRegex(ValueError, "policy changed"):
                control.main()
            api.assert_not_called()
