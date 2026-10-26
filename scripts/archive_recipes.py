#!/usr/bin/env python3
"""Keep generated and actually published recipes in Git and as release assets."""

import argparse
import base64
import json
import os
from pathlib import Path

from release_state import commit_files
from sync_release import api, find_release, package_version, run


def archive(repository, tag, directory):
    package_version(tag)
    files = {}
    for name in ("PKGBUILD", ".SRCINFO", "mirror.json"):
        files[f"packages/{tag}/generated/{name}"] = (directory / name).read_bytes()
    published = directory / "published"
    if (published / "publication.json").exists():
        if json.loads((published / "publication.json").read_text())["tag"] != tag:
            raise ValueError("Published snapshot does not match the archive tag.")
        for name in ("PKGBUILD", ".SRCINFO", "publication.json"):
            files[f"packages/{tag}/published/{name}"] = (published / name).read_bytes()
        selected = published
    else:
        selected = directory
        # A mirror-only retry must not replace a previously published recipe
        # with its original epoch/pkgrel=1 template on the release page.
        previous = api(f"repos/{repository}/contents/packages/{tag}/published/PKGBUILD?ref=main", optional=True)
        if previous:
            existing = directory / "previously-published"
            existing.mkdir(exist_ok=True)
            (existing / "PKGBUILD").write_bytes(base64.b64decode(previous["content"]))
            info = api(f"repos/{repository}/contents/packages/{tag}/published/.SRCINFO?ref=main")
            (existing / ".SRCINFO").write_bytes(base64.b64decode(info["content"]))
            selected = existing
    commit_files(repository, files, f"Archive recipes for {tag}")
    release = find_release(repository, tag)
    if release is None:
        raise ValueError("The mirror release no longer exists; Git archive has been retained.")
    # Git remains the public archive even after the release is withdrawn to draft.
    assets = directory / "release-recipes"
    assets.mkdir(exist_ok=True)
    (assets / "PKGBUILD").write_bytes((selected / "PKGBUILD").read_bytes())
    (assets / "SRCINFO").write_bytes((selected / ".SRCINFO").read_bytes())
    run("gh", "release", "upload", tag, str(assets / "PKGBUILD"),
        str(assets / "SRCINFO"), "--repo", repository, "--clobber")
    print(f"Recipes archived: https://github.com/{repository}/tree/main/packages/{tag}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--directory", type=Path, default=Path("out"))
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    archive(repository, args.tag, args.directory)
    withdrawn = args.directory / "withdrawn"
    if withdrawn.exists():
        metadata = json.loads((withdrawn / "mirror.json").read_text())
        archive(repository, metadata["tag"], withdrawn)


if __name__ == "__main__":
    main()
