#!/usr/bin/env python3
"""Pin AUR, resume automatic selection, or withdraw a mirror after replacement."""

import argparse
import json
import os
from pathlib import Path

from release_state import commit_files, read_policy, select_policy
from sync_release import TAG, api, download, find_release, package_version, render_pkgbuild, run


def version_key(tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError("Unsupported version tag.")
    return (*map(int, match.group(1, 2, 3)), 0 if match[4] else 1,
            tuple(map(int, match[4].split("."))) if match[4] else ())


def prepare(repository, tag, directory, require_public=True):
    package_version(tag)
    release = find_release(repository, tag)
    if release is None or (require_public and release["draft"]):
        raise ValueError("The selected replacement must have a public mirror release.")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(download(repository, tag, "mirror.json", directory).read_text())
    if manifest["tag"] != tag:
        raise ValueError("Mirror provenance does not match the selected tag.")
    template = Path(__file__).resolve().parents[1] / "packaging/PKGBUILD.in"
    # A retained recipe can be deliberately maintained independently of upstream.
    archived = api(f"repos/{repository}/contents/packages/{tag}/generated/PKGBUILD?ref=main", optional=True)
    if archived:
        import base64
        recipe = base64.b64decode(archived["content"]).decode()
    else:
        recipe = render_pkgbuild(template.read_text(), tag, repository, manifest["sha256"])
    (directory / "PKGBUILD").write_text(recipe)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("pin", "withdraw", "resume"))
    parser.add_argument("--tag", default="")
    parser.add_argument("--withdraw-tag", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("out"))
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    policy = read_policy(repository)
    if args.finalize:
        entry = policy["withdrawn_tags"].get(args.withdraw_tag)
        if not entry or entry["replacement_tag"] != args.tag or policy["pinned_tag"] != args.tag:
            raise ValueError("Withdrawal policy changed; the release will remain public.")
        publication = json.loads((args.output / "published/publication.json").read_text())
        if publication["tag"] != args.tag:
            raise ValueError("AUR replacement was not confirmed; the release will remain public.")
        release = find_release(repository, args.withdraw_tag)
        if release:
            api(f"repos/{repository}/releases/{release['id']}", "PATCH", {"draft": True})
        print(f"Withdrawn {args.withdraw_tag}; AUR is pinned to {args.tag}.")
        return
    tag = args.tag
    if args.action == "resume":
        pages = json.loads(run("gh", "api", f"repos/{repository}/releases?per_page=100", "--paginate", "--slurp"))
        tags = [release["tag_name"] for page in pages for release in page
                if not release["draft"] and TAG.fullmatch(release["tag_name"])
                and release["tag_name"] not in policy["withdrawn_tags"]]
        if not tags:
            raise ValueError("No eligible public mirrors are available.")
        tag = max(tags, key=version_key)
    prepare(repository, tag, args.output)
    if args.action == "withdraw":
        prepare(repository, args.withdraw_tag, args.output / "withdrawn", require_public=False)
    updated = select_policy(policy, args.action, tag, args.withdraw_tag, args.reason)
    commit_files(repository, {"state/aur-policy.json": json.dumps(updated, indent=2) + "\n"},
                 f"Release policy: {args.action} {tag}", expected_policy=policy)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"tag={tag}\nrollback={'true' if args.action != 'resume' else 'false'}\n")


if __name__ == "__main__":
    main()
