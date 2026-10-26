"""Persistent release selection and recipe history, independent of AUR HEAD."""

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

from sync_release import REPOSITORY, package_version, run


DEFAULT_POLICY = {"pinned_tag": None, "withdrawn_tags": {}}


def read_policy(repository):
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid policy repository.")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/contents/state/aur-policy.json?ref=main",
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": "Bearer " + os.environ["GH_TOKEN"]},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content = json.load(response)
    policy = json.loads(base64.b64decode(content["content"]))
    validate_policy(policy)
    return policy


def validate_policy(policy):
    if policy.get("pinned_tag") is not None:
        package_version(policy["pinned_tag"])
    if not isinstance(policy.get("withdrawn_tags"), dict):
        raise ValueError("Invalid withdrawal policy.")
    for tag in policy["withdrawn_tags"]:
        package_version(tag)


def allows(policy, tag):
    package_version(tag)
    return tag not in policy["withdrawn_tags"] and policy["pinned_tag"] in (None, tag)


def select_policy(policy, action, target, withdrawn="", reason=""):
    updated = json.loads(json.dumps(policy))
    package_version(target)
    if target in updated["withdrawn_tags"]:
        raise ValueError("A withdrawn version cannot be selected for AUR.")
    if action == "resume":
        updated["pinned_tag"] = None
    elif action in ("pin", "withdraw"):
        updated["pinned_tag"] = target
    else:
        raise ValueError("Unsupported release action.")
    if action == "withdraw":
        package_version(withdrawn)
        if withdrawn == target:
            raise ValueError("The withdrawn release and replacement must differ.")
        updated["withdrawn_tags"][withdrawn] = {"replacement_tag": target, "reason": reason}
    return updated


def commit_files(repository, files, message, expected_policy=None):
    """Update only named files in a disposable clone, retrying concurrent archives."""
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid archive repository.")
    with tempfile.TemporaryDirectory(prefix="everywhere-history-") as temporary:
        checkout = Path(temporary) / "repository"
        run("git", "clone", "--quiet", "--depth", "1", "--branch", "main",
            f"https://github.com/{repository}.git", str(checkout))
        run("git", "config", "credential.helper", "!gh auth git-credential", cwd=checkout)
        run("git", "config", "user.name", "github-actions[bot]", cwd=checkout)
        run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=checkout)
        for _ in range(3):
            if expected_policy is not None:
                current = json.loads((checkout / "state/aur-policy.json").read_text())
                if current != expected_policy:
                    raise ValueError("Release policy changed concurrently; rerun against the new policy.")
            for name, contents in files.items():
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Archive path must remain inside the repository.")
                path = checkout / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents if isinstance(contents, bytes) else contents.encode())
            run("git", "add", "--", *files, cwd=checkout)
            if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=checkout).returncode == 0:
                return
            run("git", "commit", "--quiet", "-m", message, cwd=checkout)
            result = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=checkout, capture_output=True, text=True)
            if result.returncode == 0:
                return
            run("git", "fetch", "origin", "main", cwd=checkout)
            run("git", "reset", "--hard", "origin/main", cwd=checkout)
        raise RuntimeError("Archive push failed after three attempts.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-tag", required=True)
    args = parser.parse_args()
    policy = read_policy(os.environ["AUR_POLICY_REPOSITORY"])
    if not allows(policy, args.check_tag):
        print(f"AUR publication skipped by release policy: {args.check_tag}; pinned={policy['pinned_tag']}")
        raise SystemExit(3)
    if os.environ.get("AUR_ALLOW_ROLLBACK") == "true" and policy["pinned_tag"] != args.check_tag:
        raise ValueError("A rollback requires an explicit pin to this version.")


if __name__ == "__main__":
    main()
