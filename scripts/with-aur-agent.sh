#!/usr/bin/env bash
set -euo pipefail

# OpenSSH checks the connecting UID, not just socket file permissions. Start
# the agent as the same unprivileged user that runs Git and makepkg.
(( EUID != 0 )) || { echo 'Run the AUR SSH agent as the build user, not root.' >&2; exit 1; }
: "${AUR_SSH_PRIVATE_KEY:?Configure AUR_SSH_PRIVATE_KEY}"
agent_environment=$(ssh-agent -s)
eval "$agent_environment"
trap 'ssh-agent -k >/dev/null' EXIT
printf '%s\n' "$AUR_SSH_PRIVATE_KEY" | ssh-add -
unset AUR_SSH_PRIVATE_KEY
ssh-add -l
"$@"
