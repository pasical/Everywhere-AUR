# Everywhere-AUR

Event-driven Linux release mirror and maintainer of the existing
[`everywhere-bin`](https://aur.archlinux.org/packages/everywhere-bin) AUR package.
The initial trusted source is **pasical/Everywhere**. Both stable and Canary
releases are supported. No scheduled polling or application rebuild is used.

## Flow

1. The source repository publishes a release, or finishes a Linux build for an
   already-public release, and dispatches `sync-release.yml` with its tag.
2. This repository downloads the named Linux x64/ARM64 tar.zst, DEB and AppImage
   assets. An uploaded, nonempty x64 tar.zst is required. It mirrors the original
   bytes, pins LICENSE and the icon to the source tag's commit, and publishes a
   release with `mirror.json` and SHA256SUMS. Draft source releases are rejected.
3. Only after the mirrored files are publicly downloadable, the workflow renders
   PKGBUILD with per-architecture SHA-256 hashes. An unprivileged Arch build user
   builds the package and commits PKGBUILD and .SRCINFO to AUR's master branch.

Mirror releases retain the upstream tags and prerelease status. The existing
`everywhere-bin` follows both channels: its users will receive Canary upgrades.
For example, `v0.9.0-canary.20261012.26` becomes Arch version
`0.9.0canary.20261012.26`, while download URLs retain the original tag. `vercmp`
prevents older dispatches from downgrading AUR. Same-version packaging changes
increment pkgrel. Completed mirrors still regenerate the recipe on retry, so an
AUR failure does not require re-uploading the release. Existing artifacts with
different bytes are never silently replaced.

## One-time setup

In **this repository** configure:

| Setting | Value |
| --- | --- |
| Variable `UPSTREAM_REPOSITORY` | `pasical/Everywhere` initially |
| Secret `AUR_SSH_PRIVATE_KEY` | Dedicated automation SSH private key; register its public key in your AUR account |
| Optional variables `AUR_USERNAME` / `AUR_EMAIL` | Git author override; defaults to the GitHub Actions bot |
| Variable `AUR_SSH_KNOWN_HOSTS` | Verified `aur.archlinux.org` SSH known_hosts entry |

Use the AUR host entry from a machine on which you have already verified the
server identity (`ssh-keygen -F aur.archlinux.org` extracts it from known_hosts).
This is the server's public host key, not your AUR account key. Secrets from the source fork cannot
be read back or inherited across an independent workflow dispatch; configure
them separately here. An unattended key must be usable by ssh-agent without an
interactive passphrase prompt.

The workflow's built-in GITHUB_TOKEN gets `contents: write` only for this
repository's mirror job. Reading the public source needs no extra token.

In **pasical/Everywhere** configure secret `EVERYWHERE_AUR_DISPATCH_TOKEN`: a
fine-grained PAT restricted to **Everywhere-AUR**, with **Actions: write**.
`aur-publish.yml` uses it to call this repository's workflow_dispatch endpoint.
The fork's AUR SSH secrets are no longer used by that notification workflow.

## Test and retry

After the Linux tag build completes in the fork, publish its draft as a
prerelease. This sends an event automatically. To test mirroring without changing
the AUR package, use **Actions → Mirror Linux release and update AUR → Run
workflow**, enter the exact source tag and uncheck `publish_aur`:

```sh
gh workflow run sync-release.yml --repo pasical/Everywhere-AUR \
  -f tag=v0.9.0-canary.20261026.1 -f publish_aur=false
```

The example tag must first exist as a public release containing Linux assets.
Retry the same tag with `publish_aur=true` after configuring the AUR credentials.
`python3 -m unittest discover -s tests -v` runs offline regression checks.

## Moving to Sylinko/Everywhere

Change `UPSTREAM_REPOSITORY` to `Sylinko/Everywhere`. A release event is local to
its repository: the upstream maintainer must install the same dispatch notifier
and a token permitted to invoke this workflow. Without upstream cooperation,
strictly passive notification needs an external webhook/event relay; otherwise
manual dispatch or polling is necessary. Switching the source variable alone
cannot subscribe to another repository's release events. Existing mirror tags
from a different repository/commit are protected from replacement.
