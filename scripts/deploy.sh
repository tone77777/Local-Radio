#!/usr/bin/env bash
# Push current (or given) branch to origin, pull on Synology, rebuild Docker.
set -euo pipefail

SSH_TARGET="${LOCAL_RADIO_SSH:-louis@Illmatic}"
REMOTE_DIR="${LOCAL_RADIO_REMOTE_DIR:-/volume1/docker/Local-Radio}"
BRANCH="${1:-$(git branch --show-current)}"

if [[ -z "$BRANCH" ]]; then
  echo "error: could not determine branch" >&2
  exit 1
fi

echo "==> Pushing $BRANCH to origin"
git push -u origin "HEAD:refs/heads/$BRANCH"

echo "==> Deploying $BRANCH on $SSH_TARGET:$REMOTE_DIR"
# shellcheck disable=SC2029
ssh "$SSH_TARGET" bash -s -- "$REMOTE_DIR" "$BRANCH" <<'REMOTE'
set -euo pipefail
REMOTE_DIR="$1"
BRANCH="$2"

git config --global --add safe.directory "$REMOTE_DIR" 2>/dev/null || true

cd "$REMOTE_DIR"
git fetch origin
git switch "$BRANCH"
git pull --ff-only origin "$BRANCH"
sudo docker compose up -d --build
sudo docker compose ps
echo "==> Recent radio logs"
sudo docker compose logs --tail=40 radio || true
REMOTE

echo "==> Deploy finished ($BRANCH)"
