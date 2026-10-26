#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/seed" "$fixture/bin"
export GIT_CONFIG_GLOBAL="$fixture/gitconfig"
export AUR_USERNAME='Packaging test' AUR_EMAIL='packaging@example.invalid'
cat > "$fixture/candidate" <<'RECIPE'
pkgname=everywhere-bin
pkgver=1.2.3canary.1
pkgrel=1
_upstream_tag=v1.2.3-canary.1
pkgdesc='Fixture package'
arch=('x86_64')
license=('MIT')
package() {
    mkdir -p "$pkgdir/usr/share/applications"
    cat > "$pkgdir/usr/share/applications/Everywhere.desktop" <<'EOF'
[Desktop Entry]
Name=Everywhere
Exec=Everywhere
Icon=Everywhere
Type=Application
Categories=Utility;
EOF
}
RECIPE
sed 's/^pkgver=.*/pkgver=0.0.4/' "$fixture/candidate" > "$fixture/seed/PKGBUILD"
cd "$fixture/seed"
makepkg --printsrcinfo > .SRCINFO
git init -b master -q
git config user.name "$AUR_USERNAME"
git config user.email "$AUR_EMAIL"
git add PKGBUILD .SRCINFO
git commit -qm initial
git clone --bare -q . "$fixture/origin.git"
git config --file "$GIT_CONFIG_GLOBAL" "url.file://$fixture/origin.git.insteadOf" ssh://aur@aur.archlinux.org/everywhere-bin.git
# The package is built by test-package.sh. Here only that expensive build is
# stubbed; Git operations, version comparison, .SRCINFO and recipe edits are real.
real_makepkg=$(command -v makepkg)
cat > "$fixture/bin/makepkg" <<EOF
#!/usr/bin/env bash
if [[ \$1 == --printsrcinfo ]]; then exec "$real_makepkg" "\$@"; fi
EOF
chmod +x "$fixture/bin/makepkg"
export PATH="$fixture/bin:$PATH"
export AUR_PUBLISHED_DIR="$fixture/published"
publish() { bash "$repo_root/scripts/publish_aur.sh" "$fixture/candidate"; }
count() { git --git-dir="$fixture/origin.git" rev-list --count master; }
publish
test "$(count)" = 2
publish
test "$(count)" = 2
sed -i 's/Fixture package/Updated packaging description/' "$fixture/candidate"
publish
test "$(count)" = 3
git --git-dir="$fixture/origin.git" show master:.SRCINFO | grep -q 'pkgrel = 2'
publish
test "$(count)" = 3
sed -i 's/^pkgver=.*/pkgver=0.1.0/' "$fixture/candidate"
sed -i 's/^_upstream_tag=.*/_upstream_tag=v0.1.0/' "$fixture/candidate"
publish
test "$(count)" = 3
real_python=$(command -v python3)
cat > "$fixture/bin/python3" <<EOF
#!/usr/bin/env bash
if [[ \$1 == */release_state.py ]]; then
    [[ \${AUR_TEST_POLICY_ALLOWED:-true} == true ]] && exit 0
    exit 3
fi
exec "$real_python" "\$@"
EOF
chmod +x "$fixture/bin/python3"
export AUR_POLICY_REPOSITORY=test/local-policy
export AUR_TEST_POLICY_ALLOWED=false
publish
test "$(count)" = 3
export AUR_TEST_POLICY_ALLOWED=true AUR_ALLOW_ROLLBACK=true
publish
test "$(count)" = 4
git --git-dir="$fixture/origin.git" show master:.SRCINFO | grep -q 'epoch = 1'
test "$(vercmp 1:0.1.0-1 1.2.3canary.1-2)" = 1
publish
test "$(count)" = 5
sed -i 's/^pkgver=.*/pkgver=2.0.0/' "$fixture/candidate"
sed -i 's/^_upstream_tag=.*/_upstream_tag=v2.0.0/' "$fixture/candidate"
export AUR_ALLOW_ROLLBACK=false
publish
test "$(count)" = 6
git --git-dir="$fixture/origin.git" show master:.SRCINFO | grep -q 'epoch = 1'
python3 - "$fixture/published/publication.json" <<'PY'
import json, sys
assert json.load(open(sys.argv[1]))['version'] == '1:2.0.0-1'
PY
echo 'AUR update, idempotent retry, packaging revision and downgrade checks passed using a local remote.'
