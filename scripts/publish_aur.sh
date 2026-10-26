#!/usr/bin/env bash
set -euo pipefail

# Runs as an unprivileged user in the Arch job; credentials remain in ssh-agent.
candidate=$(realpath "${1:-out/PKGBUILD}")
script_dir=$(cd "$(dirname "$0")" && pwd)
candidate_tag=$(bash -c 'source "$1"; printf "%s" "${_upstream_tag:-}"' _ "$candidate")
check_policy() {
    if [[ -n "${AUR_POLICY_REPOSITORY:-}" ]]; then
        if python3 "$script_dir/release_state.py" --check-tag "$candidate_tag"; then
            return 0
        else
            local status=$?
            (( status == 3 )) && exit 0
            exit "$status"
        fi
    fi
}
save_publication() {
    if [[ -n "${AUR_PUBLISHED_DIR:-}" ]]; then
        mkdir -p "$AUR_PUBLISHED_DIR"
        cp PKGBUILD .SRCINFO "$AUR_PUBLISHED_DIR/"
        python3 - "$AUR_PUBLISHED_DIR" "$candidate_tag" "$(git rev-parse HEAD)" <<'PY'
import json, sys
from pathlib import Path
fields = dict(line.strip().split(' = ', 1) for line in Path('.SRCINFO').read_text().splitlines() if ' = ' in line)
epoch = fields.get('epoch', '0')
version = (epoch + ':' if epoch != '0' else '') + fields['pkgver'] + '-' + fields['pkgrel']
Path(sys.argv[1], 'publication.json').write_text(json.dumps({'tag': sys.argv[2], 'version': version, 'aur_commit': sys.argv[3]}, indent=2) + '\n')
PY
    fi
}
check_policy
repo=$(mktemp -d)
trap 'rm -rf "$repo"' EXIT
git clone ssh://aur@aur.archlinux.org/everywhere-bin.git "$repo"
cd "$repo"
new_version=$(bash -c 'source "$1"; printf "%s-%s" "$pkgver" "$pkgrel"' _ "$candidate")
new_pkgver=${new_version%-*}
for attempt in 1 2 3; do
    check_policy
    old_pkgver=
    old_epoch=0
    if [[ -f .SRCINFO ]]; then
        old_version=$(awk '/^[[:space:]]*pkgver = / {v=$3} /^[[:space:]]*pkgrel = / {r=$3} END {print v "-" r}' .SRCINFO)
        old_pkgver=${old_version%-*}
        old_epoch=$(awk '/^[[:space:]]*epoch = / {print $3}' .SRCINFO)
        old_epoch=${old_epoch:-0}
        [[ "$old_epoch" =~ ^[0-9]+$ ]] || { echo 'Invalid AUR epoch' >&2; exit 1; }
        comparison=$(vercmp "$new_pkgver" "$old_pkgver")
        if (( comparison < 0 )); then
            if [[ "${AUR_ALLOW_ROLLBACK:-false}" == true && -n "${AUR_POLICY_REPOSITORY:-}" ]]; then
                old_epoch=$((old_epoch + 1))
            else
                echo "AUR already has $old_version; refusing downgrade to $new_version."
                exit 0
            fi
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
    # An epoch introduced by a deliberate rollback is retained by future releases.
    sed -i '/^epoch=/d' PKGBUILD
    if (( old_epoch > 0 )); then
        sed -i "/^pkgver=/a epoch=$old_epoch" PKGBUILD
    fi
    makepkg --printsrcinfo > .SRCINFO
    if git diff --quiet -- PKGBUILD .SRCINFO; then
        echo "AUR already matches the mirror."
        if [[ "${AUR_ALLOW_ROLLBACK:-false}" == true && -n "${AUR_POLICY_REPOSITORY:-}" ]]; then
            # A no-op pin still needs to invalidate automatic pushes that began
            # before the policy changed. An empty commit provides that Git fence.
            check_policy
            git config user.name "${AUR_USERNAME:?Set AUR_USERNAME}"
            git config user.email "${AUR_EMAIL:?Set AUR_EMAIL}"
            git commit --allow-empty -m "Pin everywhere-bin to $candidate_tag"
            if ! git push origin HEAD:master; then
                git fetch origin master
                git reset --hard origin/master
                continue
            fi
        fi
        save_publication
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
    check_policy
    git config user.name "${AUR_USERNAME:?Set AUR_USERNAME}"
    git config user.email "${AUR_EMAIL:?Set AUR_EMAIL}"
    git add PKGBUILD .SRCINFO
    new_version=$(bash -c 'source ./PKGBUILD; printf "%s-%s" "$pkgver" "$pkgrel"')
    git commit -m "Update everywhere-bin to $new_version"
    if git push origin HEAD:master; then
        save_publication
        exit 0
    fi
    # Another version may have reached AUR while this package was building. Refresh
    # this disposable clone, then recompare versions and regenerate against its HEAD.
    git fetch origin master
    git reset --hard origin/master
done
echo 'AUR push failed after three attempts; retry this workflow.' >&2
exit 1
