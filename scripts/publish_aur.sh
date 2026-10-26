#!/usr/bin/env bash
set -euo pipefail

# Runs as an unprivileged user in the Arch job; credentials remain in ssh-agent.
candidate=$(realpath "${1:-out/PKGBUILD}")
repo=$(mktemp -d)
trap 'rm -rf "$repo"' EXIT
git clone ssh://aur@aur.archlinux.org/everywhere-bin.git "$repo"
cd "$repo"
new_version=$(bash -c 'source "$1"; printf "%s-%s" "$pkgver" "$pkgrel"' _ "$candidate")
new_pkgver=${new_version%-*}
for attempt in 1 2 3; do
    old_pkgver=
    if [[ -f .SRCINFO ]]; then
        old_version=$(awk '/^[[:space:]]*pkgver = / {v=$3} /^[[:space:]]*pkgrel = / {r=$3} END {print v "-" r}' .SRCINFO)
        old_pkgver=${old_version%-*}
        comparison=$(vercmp "$new_pkgver" "$old_pkgver")
        if (( comparison < 0 )); then
            echo "AUR already has $old_version; refusing downgrade to $new_version."
            exit 0
        fi
        # Retain a packaging revision when retrying an already-published software version.
        if [[ "$old_pkgver" == "$new_pkgver" ]]; then
            sed "s/^pkgrel=.*/pkgrel=${old_version##*-}/" "$candidate" > PKGBUILD
        else
            cp "$candidate" PKGBUILD
        fi
    else
        cp "$candidate" PKGBUILD
    fi
    makepkg --printsrcinfo > .SRCINFO
    if git diff --quiet -- PKGBUILD .SRCINFO; then
        echo "AUR already matches the mirror."
        exit 0
    fi
    # A changed recipe for the same software must be visible to existing installations.
    if [[ -n "${old_pkgver:-}" && "$old_pkgver" == "$new_pkgver" ]]; then
        old_pkgrel=${old_version##*-}
        [[ "$old_pkgrel" =~ ^[0-9]+$ ]] || { echo 'Non-integer pkgrel requires manual review.' >&2; exit 1; }
        sed -i "s/^pkgrel=.*/pkgrel=$((old_pkgrel + 1))/" PKGBUILD
        makepkg --printsrcinfo > .SRCINFO
    fi
    makepkg --syncdeps --noconfirm --cleanbuild --force
    sed -n '/^\[Desktop Entry\]/,/^EOF/p' PKGBUILD | sed '$d' > Everywhere.desktop
    desktop-file-validate Everywhere.desktop
    git config user.name "${AUR_USERNAME:?Set AUR_USERNAME}"
    git config user.email "${AUR_EMAIL:?Set AUR_EMAIL}"
    git add PKGBUILD .SRCINFO
    new_version=$(bash -c 'source ./PKGBUILD; printf "%s-%s" "$pkgver" "$pkgrel"')
    git commit -m "Update everywhere-bin to $new_version"
    if git push origin HEAD:master; then
        exit 0
    fi
    # Another version may have reached AUR while this package was building. Refresh
    # this disposable clone, then recompare versions and regenerate against its HEAD.
    git fetch origin master
    git reset --hard origin/master
done
echo 'AUR push failed after three attempts; retry this workflow.' >&2
exit 1
