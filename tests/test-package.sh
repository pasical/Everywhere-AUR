#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/payload" "$fixture/download" "$fixture/build"
printf '#!/bin/sh\necho recipe-fixture\n' > "$fixture/payload/Everywhere"
cp "$fixture/payload/Everywhere" "$fixture/payload/Everywhere.Watchdog"
chmod +x "$fixture/payload/"*
tag=v1.2.3-canary.20261026.1
for arch in x64 arm64; do
    bsdtar -caf "$fixture/download/Everywhere-Linux-${arch}-${tag}.tar.zst" -C "$fixture/payload" .
done
printf 'Fixture license\n' > "$fixture/download/LICENSE"
printf 'Fixture icon\n' > "$fixture/download/Everywhere-icon.png"
python3 - "$repo_root" "$fixture" "$tag" <<'PY'
import importlib.util
from pathlib import Path
import sys
root, fixture, tag = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location('sync_release', root / 'scripts/sync_release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
hashes = {path.name: module.sha256(path) for path in (fixture / 'download').iterdir()}
recipe = module.render_pkgbuild((root / 'packaging/PKGBUILD.in').read_text(), tag,
                               'pasical/Everywhere-AUR', hashes)
recipe = recipe.replace('_base="https://github.com/${_mirror_repository}/releases/download/${_upstream_tag}"',
                        f'_base="file://{fixture}/download"')
(fixture / 'build/PKGBUILD').write_text(recipe)
PY
cd "$fixture/build"
makepkg --printsrcinfo > .SRCINFO
bash -c 'source ./PKGBUILD; pacman -Sp --print-format %n "${depends[@]}" >/dev/null'
grep -q 'arch = x86_64' .SRCINFO
grep -q 'arch = aarch64' .SRCINFO
test "$(vercmp 1.2.3canary.20261026.1 1.2.3)" = -1
test "$(vercmp 1.2.3canary.20261026.2 1.2.3canary.20261026.1)" = 1
test "$(vercmp 1.2.4canary.1 1.2.3)" = 1
makepkg --nodeps --noconfirm --cleanbuild
package=$(find . -maxdepth 1 -name 'everywhere-bin-*.pkg.tar.zst' -print -quit)
bsdtar -tf "$package" | grep -q '^opt/Everywhere/Everywhere.Watchdog$'
bsdtar -xf "$package" usr/share/applications/Everywhere.desktop
desktop-file-validate usr/share/applications/Everywhere.desktop
echo 'Arch package construction, checksums, metadata and version ordering passed.'
