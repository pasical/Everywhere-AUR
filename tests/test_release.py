import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sync_release", ROOT / "scripts/sync_release.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class ReleaseTests(unittest.TestCase):
    tag = "v0.9.0-canary.20261012.26"

    def assets(self):
        return [{"id": index, "name": f"Everywhere-Linux-{arch}-{self.tag}.tar.zst",
                 "state": "uploaded", "size": 123, "digest": None}
                for index, arch in enumerate(("x64", "arm64"), 1)]

    def test_stable_and_canary_versions(self):
        self.assertEqual(sync.package_version("v1.2.3"), "1.2.3")
        self.assertEqual(sync.package_version(self.tag), "0.9.0canary.20261012.26")
        for tag in ("main", "v1.2.3-beta.1", "v1.2.3;touch x", "v1.2.3/other"):
            with self.assertRaises(ValueError):
                sync.package_version(tag)

    def test_release_without_linux_or_incomplete_upload_is_rejected(self):
        for assets in ([], [{**self.assets()[0], "state": "new"}],
                       [{**self.assets()[0], "size": 0}]):
            with self.assertRaises(ValueError):
                sync.linux_assets({"assets": assets}, self.tag)

    def test_unrelated_assets_are_ignored(self):
        assets = self.assets() + [{"name": "malicious.sh"}]
        self.assertEqual(len(sync.linux_assets({"assets": assets}, self.tag)), 2)

    def test_recipe_uses_mirror_and_hashes_for_available_architectures(self):
        hashes = {"LICENSE": "a" * 64, "Everywhere-icon.png": "b" * 64,
                  self.assets()[0]["name"]: "c" * 64}
        text = sync.render_pkgbuild((ROOT / "packaging/PKGBUILD.in").read_text(), self.tag,
                                   "pasical/Everywhere-AUR", hashes)
        self.assertIn("arch=('x86_64')", text)
        self.assertIn("_mirror_repository='pasical/Everywhere-AUR'", text)
        self.assertIn("sha256sums_x86_64=('" + "c" * 64 + "')", text)
        self.assertNotIn("source_aarch64=", text)
        self.assertNotIn("SKIP", text)

    def test_rejected_source_cannot_dispatch_arbitrary_repository_or_tag(self):
        with self.assertRaises(ValueError):
            sync.mirror("owner/repo;command", "pasical/Everywhere-AUR", self.tag, Path("out"))
        with self.assertRaises(ValueError):
            sync.mirror("pasical/Everywhere", "pasical/Everywhere", self.tag, Path("out"))

    def test_draft_release_is_not_mirrored(self):
        with patch.object(sync, "api", return_value={"draft": True}):
            with self.assertRaisesRegex(ValueError, "Publish"):
                sync.mirror("pasical/Everywhere", "pasical/Everywhere-AUR", self.tag, Path("out"))

    def test_completed_mirror_still_generates_recipe_for_aur_retry(self):
        hashes = {"LICENSE": "a" * 64, "Everywhere-icon.png": "b" * 64,
                  self.assets()[0]["name"]: "c" * 64}
        manifest = {"source_repository": "pasical/Everywhere", "source_release_id": 10,
                    "source_commit": "d" * 40, "tag": self.tag,
                    "upstream_assets": sync.fingerprint(sync.linux_assets({"assets": self.assets()}, self.tag)), "sha256": hashes}
        mirrored = {"id": 20, "draft": False, "assets": [
            {"name": name, "digest": "sha256:" + digest} for name, digest in hashes.items()
        ] + [{"name": "mirror.json"}]}

        def api(path, *args, **kwargs):
            if path.endswith("/commits/" + self.tag):
                return {"sha": "d" * 40}
            if path.startswith("repos/pasical/Everywhere-AUR/"):
                return mirrored
            return {"id": 10, "draft": False, "assets": self.assets()}

        def download(repo, tag, name, directory):
            self.assertEqual(repo, "pasical/Everywhere-AUR")
            self.assertEqual(name, "mirror.json")
            path = directory / name
            path.write_text(json.dumps(manifest))
            return path

        with tempfile.TemporaryDirectory() as directory, patch.object(sync, "api", side_effect=api), \
                patch.object(sync, "download", side_effect=download), \
                patch.object(sync.urllib.request, "urlopen") as head:
            head.return_value.__enter__.return_value.status = 200
            output = Path(directory) / "out"
            sync.mirror("pasical/Everywhere", "pasical/Everywhere-AUR", self.tag, output)
            self.assertTrue((output / "PKGBUILD").is_file())
            self.assertEqual(head.call_count, len(hashes))

    def test_existing_tag_from_different_source_is_rejected(self):
        calls = [{"id": 10, "draft": False, "assets": self.assets()}, {"sha": "d" * 40},
                 {"assets": [{"name": "mirror.json"}]}]

        def download(repo, tag, name, directory):
            path = directory / name
            path.write_text(json.dumps({"source_repository": "another/repo", "source_commit": "d" * 40}))
            return path

        with patch.object(sync, "api", side_effect=calls), patch.object(sync, "download", side_effect=download):
            with self.assertRaisesRegex(ValueError, "different source"):
                sync.mirror("pasical/Everywhere", "pasical/Everywhere-AUR", self.tag, Path("out"))

    def test_interrupted_draft_is_found_in_release_list(self):
        draft = {"tag_name": self.tag, "id": 20, "draft": True}
        with patch.object(sync, "api", return_value=None), \
                patch.object(sync, "run", return_value=json.dumps([[{"tag_name": "other"}], [draft]])):
            self.assertEqual(sync.find_release("pasical/Everywhere-AUR", self.tag), draft)

    def test_first_mirror_uploads_files_before_publication(self):
        events = []
        png = b"\x89PNG\r\n\x1a\nfixture"

        def api(path, method="GET", data=None, optional=False):
            if path == f"repos/pasical/Everywhere/releases/tags/{self.tag}":
                return {"id": 10, "draft": False, "prerelease": True,
                        "html_url": "https://github.com/pasical/Everywhere/releases/tag/" + self.tag,
                        "assets": self.assets()}
            if "/commits/" in path:
                return {"sha": "d" * 40}
            if "/contents/" in path:
                payload = png if "/img/" in path else b"fixture license"
                return {"content": base64.b64encode(payload).decode()}
            if method == "POST":
                events.append("create-draft")
                self.assertTrue(data["draft"])
                return {"id": 20, "draft": True, "assets": []}
            if method == "PATCH":
                events.append("publish")
                self.assertFalse(data["draft"])
                return {}
            raise AssertionError(path)

        def run(*args, **kwargs):
            self.assertEqual(args[:3], ("gh", "release", "upload"))
            events.append("upload")
            return ""

        def download(repo, tag, name, directory):
            path = directory / name
            path.write_bytes(b"fixture binary archive")
            return path

        with tempfile.TemporaryDirectory() as directory, patch.object(sync, "api", side_effect=api), \
                patch.object(sync, "find_release", return_value=None), \
                patch.object(sync, "run", side_effect=run), patch.object(sync, "download", side_effect=download), \
                patch.object(sync.urllib.request, "urlopen") as head:
            head.return_value.__enter__.return_value.status = 200
            output = Path(directory) / "out"
            sync.mirror("pasical/Everywhere", "pasical/Everywhere-AUR", self.tag, output)
            self.assertEqual(events[0], "create-draft")
            self.assertEqual(events[-1], "publish")
            self.assertGreater(events.count("upload"), 2)
            self.assertIn("source_aarch64", (output / "PKGBUILD").read_text())


if __name__ == "__main__":
    unittest.main()
