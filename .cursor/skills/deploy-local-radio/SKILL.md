---
name: deploy-local-radio
description: >-
  Push the current Local Radio git branch to GitHub, pull it on the Synology NAS
  (Illmatic), and rebuild/restart Docker Compose. Use when the user says deploy,
  push to the server, update the NAS, rebuild on Illmatic, or restart Local Radio
  remotely.
---

# Deploy Local Radio

## Defaults

| Setting | Value |
|---------|-------|
| SSH | `louis@Illmatic` |
| Remote dir | `/volume1/docker/Local-Radio` |
| Compose | `sudo docker compose` |
| Stream impact | Rebuild briefly interrupts on-air audio |

Override with env: `LOCAL_RADIO_SSH`, `LOCAL_RADIO_REMOTE_DIR`.

## When the user says deploy

1. Confirm branch: `git branch --show-current` (do not switch branches unless asked).
2. Commit **only** if the user explicitly asked to commit; otherwise push existing commits.
3. Prefer the repo script:

```bash
./scripts/deploy.sh
```

Or with an explicit branch:

```bash
./scripts/deploy.sh cursor/jump-to-show
```

4. If SSH host/path differ on this machine, set env and retry:

```bash
LOCAL_RADIO_SSH=louis@Illmatic.local LOCAL_RADIO_REMOTE_DIR=/volume1/docker/Local-Radio ./scripts/deploy.sh
```

5. On success, show the remote compose status / last few radio log lines.
6. On `dubious ownership`, the script already sets `safe.directory` for the remote path once; re-run if needed.
7. Never `git push --force` to `main`/`master`. Never change git config locally for this flow.

## Manual fallback (if script missing)

```bash
BRANCH=$(git branch --show-current)
git push -u origin "HEAD:refs/heads/$BRANCH"
ssh louis@Illmatic "git config --global --add safe.directory /volume1/docker/Local-Radio 2>/dev/null; cd /volume1/docker/Local-Radio && git fetch origin && git switch '$BRANCH' && git pull --ff-only && sudo docker compose up -d --build && sudo docker compose ps"
```

## Do not

- Deploy uncommitted work without asking
- Rebuild without telling the user audio will blip
- Assume the remote folder is still named `Local-Radio-new` — default is `Local-Radio`
