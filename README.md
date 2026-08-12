# Local Radio

Dockerized scripts for running on a Synology NAS.

## First build (local)

```bash
cp .env.example .env   # if you don't already have .env
docker compose up --build
```

Stop with `Ctrl+C`, or run detached with `docker compose up --build -d`.

## Layout

- `Dockerfile` — base image and package installs
- `docker-compose.yml` — how the container runs (local + Synology)
- `scripts/` — shell scripts executed inside the container
- `data/` — mounted volume for persistent files
- `.env` — local/NAS config (not committed)
