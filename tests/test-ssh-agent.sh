#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
ssh-keygen -q -t ed25519 -N '' -C agent-test-fixture -f "$fixture/key"
export AUR_SSH_PRIVATE_KEY=$(cat "$fixture/key")
export EXPECTED_SSH_FINGERPRINT=$(ssh-keygen -lf "$fixture/key.pub" | awk '{print $2}')
runuser -u builder -- bash "$repo_root/scripts/with-aur-agent.sh" bash -c '
    set -euo pipefail
    test "$(id -u)" != 0
    test "$HOME" = "$(getent passwd "$(id -u)" | cut -d: -f6)"
    test -z "${AUR_SSH_PRIVATE_KEY+x}"
    ssh-add -l | grep -F "$EXPECTED_SSH_FINGERPRINT"
'
echo 'The unprivileged build user can access its own agent and loaded key.'
