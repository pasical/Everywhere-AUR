#!/usr/bin/env python3
"""Mirror one explicitly requested public release; never poll or rebuild it."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request


TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)(?:-canary\.([0-9]+(?:\.[0-9]+)*))?\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def api(path, method="GET", data=None, optional=False):
    args = ["gh", "api", path, "--method", method]
    if data is not None:
        args += ["--input", "-"]
    try:
        result = run(*args, input=json.dumps(data) if data is not None else None)
        return json.loads(result) if result.strip() else None
    except subprocess.CalledProcessError as error:
        if optional and "HTTP 404" in error.stderr:
            return None
        raise RuntimeError(error.stderr.strip()) from error


def package_version(tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError("Tag must be vX.Y.Z or vX.Y.Z-canary.<numeric qualifiers>.")
    version = ".".join(match.group(1, 2, 3))
    return version + ("canary." + match[4] if match[4] else "")


def find_release(repository, tag):
    release = api(f"repos/{repository}/releases/tags/{tag}", optional=True)
    if release is not None:
        return release
    # The tag endpoint can omit drafts. Search the authenticated release list
    # to resume an interrupted draft instead of creating another one.
    pages = json.loads(run("gh", "api", f"repos/{repository}/releases?per_page=100", "--paginate", "--slurp"))
    return next((release for page in pages for release in page if release["tag_name"] == tag), None)


def linux_assets(release, tag):
    expected = {
        f"Everywhere-Linux-{arch}-{tag}.{extension}"
        for arch in ("x64", "arm64")
        for extension in ("tar.zst", "deb", "AppImage")
    }
    assets = sorted(
        (asset for asset in release["assets"] if asset["name"] in expected),
        key=lambda asset: asset["name"],
    )
    required = f"Everywhere-Linux-x64-{tag}.tar.zst"
    if not any(asset["name"] == required for asset in assets):
        raise ValueError(f"Release does not yet contain the required asset: {required}")
    if any(asset["state"] != "uploaded" or asset["size"] <= 0 for asset in assets):
        raise ValueError("Linux assets are still uploading or empty; retry after the build completes.")
    return assets


def fingerprint(assets):
    return [{key: asset.get(key) for key in ("id", "name", "size", "digest")} for asset in assets]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(repository, tag, name, directory):
    run("gh", "release", "download", tag, "--repo", repository, "--pattern", name,
        "--dir", str(directory), "--clobber")
    return directory / name


def remote_hash(repository, tag, asset, directory):
    digest = asset.get("digest", "") or ""
    if re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        return digest.removeprefix("sha256:")
    return sha256(download(repository, tag, asset["name"], directory))


def render_pkgbuild(template, tag, repository, hashes):
    version = package_version(tag)
    architectures = [arch for arch, rid in (("x86_64", "x64"), ("aarch64", "arm64"))
                     if f"Everywhere-Linux-{rid}-{tag}.tar.zst" in hashes]
    archive_lines = []
    for arch in architectures:
        rid = "x64" if arch == "x86_64" else "arm64"
        name = f"Everywhere-Linux-{rid}-{tag}.tar.zst"
        archive_lines += [f'source_{arch}=("${{_base}}/{name}")',
                          f"sha256sums_{arch}=('{hashes[name]}')"]
    values = {
        "PKGVER": version,
        "TAG": tag,
        "REPOSITORY": repository,
        "ARCHES": " ".join(f"'{arch}'" for arch in architectures),
        "LICENSE_SHA256": hashes["LICENSE"],
        "ICON_SHA256": hashes["Everywhere-icon.png"],
        "ARCH_SOURCES": "\n".join(archive_lines),
    }
    for key, value in values.items():
        template = template.replace("@" + key + "@", value)
    if re.search(r"@[A-Z_]+@", template) or "SKIP" in template:
        raise ValueError("Unresolved template values or missing checksums.")
    return template


def mirror(source, destination, tag, output):
    for repository in (source, destination):
        if not REPOSITORY.fullmatch(repository):
            raise ValueError("Invalid repository name.")
    if source.lower() == destination.lower():
        raise ValueError("Source and mirror repositories must differ.")
    package_version(tag)
    upstream = api(f"repos/{source}/releases/tags/{tag}")
    if upstream["draft"]:
        raise ValueError("Publish the upstream release before mirroring it.")
    assets = linux_assets(upstream, tag)
    commit = api(f"repos/{source}/commits/{tag}")["sha"]
    identity = {"source_repository": source, "source_release_id": upstream["id"],
                "source_commit": commit, "tag": tag, "upstream_assets": fingerprint(assets)}
    destination_release = find_release(destination, tag)
    with tempfile.TemporaryDirectory(prefix="everywhere-mirror-") as temporary:
        directory = Path(temporary)
        old_manifest = None
        existing = {asset["name"]: asset for asset in (destination_release or {}).get("assets", [])}
        if "mirror.json" in existing:
            old_manifest = json.loads(download(destination, tag, "mirror.json", directory).read_text())
            if old_manifest["source_repository"].lower() != source.lower() or old_manifest["source_commit"] != commit:
                raise ValueError("The mirror tag belongs to a different source repository or commit.")
        hashes = old_manifest.get("sha256", {}) if old_manifest else {}
        unchanged = old_manifest and all(old_manifest.get(key) == value for key, value in identity.items())
        complete = unchanged and all(
            name in existing and remote_hash(destination, tag, existing[name], directory) == digest
            for name, digest in hashes.items()
        )
        if not complete:
            hashes = {}
            for asset in assets:
                path = download(source, tag, asset["name"], directory)
                digest = sha256(path)
                if asset.get("digest") and asset["digest"] != "sha256:" + digest:
                    raise ValueError(f"Source checksum mismatch: {asset['name']}")
                hashes[path.name] = digest
            for name, path in (("LICENSE", "LICENSE"), ("Everywhere-icon.png", "img/Everywhere-icon.png")):
                content = api(f"repos/{source}/contents/{path}?ref={commit}")
                data = base64.b64decode(content["content"])
                if name.endswith(".png") and data.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    url = f"https://media.githubusercontent.com/media/{source}/{commit}/{path}"
                    with urllib.request.urlopen(url, timeout=60) as response:
                        data = response.read()
                if name.endswith(".png") and not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("The release icon is not a PNG.")
                (directory / name).write_bytes(data)
                hashes[name] = sha256(directory / name)
            if destination_release is None:
                destination_release = api(f"repos/{destination}/releases", "POST", {
                    "tag_name": tag, "target_commitish": os.environ.get("GITHUB_SHA", "main"),
                    "name": tag + " — Linux mirror", "draft": True,
                    "prerelease": upstream["prerelease"],
                    "body": f"Unmodified Linux binaries from {upstream['html_url']}\n\nSource commit: `{commit}`.\nSee mirror.json and SHA256SUMS for provenance and checksums.",
                })
            for name, digest in hashes.items():
                if name in existing:
                    if remote_hash(destination, tag, existing[name], directory) != digest:
                        raise ValueError(f"Refusing to replace an existing artifact with different bytes: {name}")
                else:
                    run("gh", "release", "upload", tag, str(directory / name), "--repo", destination)
            manifest = {**identity, "sha256": hashes}
            (directory / "mirror.json").write_text(json.dumps(manifest, indent=2) + "\n")
            (directory / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())))
            run("gh", "release", "upload", tag, str(directory / "mirror.json"),
                str(directory / "SHA256SUMS"), "--repo", destination, "--clobber")
        if destination_release["draft"]:
            api(f"repos/{destination}/releases/{destination_release['id']}", "PATCH",
                {"draft": False, "prerelease": upstream["prerelease"], "make_latest": "false"})
        # Do not update AUR until every generated source URL is publicly downloadable.
        for name in hashes:
            url = f"https://github.com/{destination}/releases/download/{tag}/{name}"
            request = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status != 200:
                    raise ValueError(f"Mirror asset is not publicly available: {name}")
        output.mkdir(parents=True, exist_ok=True)
        template = Path(__file__).resolve().parents[1] / "packaging/PKGBUILD.in"
        (output / "PKGBUILD").write_text(render_pkgbuild(template.read_text(), tag, destination, hashes))
        (output / "mirror.json").write_text(json.dumps({**identity, "sha256": hashes}, indent=2) + "\n")
        print(f"Mirror ready: https://github.com/{destination}/releases/tag/{tag}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.environ.get("UPSTREAM_REPOSITORY", "pasical/Everywhere"))
    parser.add_argument("--destination", default=os.environ.get("GITHUB_REPOSITORY", "pasical/Everywhere-AUR"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, default=Path("out"))
    args = parser.parse_args()
    from release_state import read_policy
    if args.tag in read_policy(args.destination)["withdrawn_tags"]:
        print(f"Mirror skipped: {args.tag} was withdrawn. Its recipes remain archived.")
        return
    mirror(args.source, args.destination, args.tag, args.output)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("ready=true\n")


if __name__ == "__main__":
    main()
