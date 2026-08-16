# Sensitive Checker media service

FastAPI service for image moderation and temporary SpotiFLAC music artifacts. The Dockerfile provides CPU and GPU runtime targets; both run one Uvicorn worker as a non-root user.

## Build and run locally

Copy `.env.example` to `.env` when overriding runtime defaults.

CPU:

```bash
docker build --target runtime-cpu -t media_processing:local .
docker run --rm \
  --network host \
  --env-file .env \
  -v media_processing_artifacts:/var/lib/sensitive-checker/music \
  media_processing:local
```

GPU requires a compatible NVIDIA driver and NVIDIA Container Toolkit:

```bash
docker build --target runtime-gpu -t media_processing:gpu .
docker run --rm \
  --network host \
  --gpus all \
  --env-file .env \
  -e COMPUTE_DEVICE=cuda \
  -v media_processing_artifacts:/var/lib/sensitive-checker/music \
  media_processing:gpu
```

The safe default binds to `127.0.0.1:8000`. Check `GET /health/live` and `GET /health/ready`. Do not set `SERVICE_HOST=0.0.0.0` on an Internet-reachable host without a firewall or protected authenticated reverse proxy.

The music artifact limit is `104857600` bytes (100 MiB). Startup cleanup and the TTL sweeper remove stale jobs from `ARTIFACT_ROOT`.

## Service logs

All application, Uvicorn, image-scan, and SpotiFLAC events are written to both the console and `./logs/log-<port>.log` by default. Set `LOG_DIR` to use another directory. The active file rolls at local midnight or when it exceeds 100 MiB; archives are gzip-compressed, limited to 30 files, and removed after 14 days.

For a container, mount `/srv/app/logs` when logs must survive recreation:

```bash
docker run --rm --network host \
  -v /data/media-processing/logs:/srv/app/logs \
  media_processing:local
```

The container runs as the non-root `app` user, so the host directory must be writable by UID/GID `10001`.

## Music fetch logs

Running `python -m app` or either Docker runtime target enables INFO logs for the music-fetch lifecycle. Use the response `X-Request-ID` to correlate these stages:

- `music_fetch_started`, `music_capacity_acquired`, `music_job_created`
- `music_process_started`, `music_process_completed` or `music_process_failed`
- `music_download_completed`, `music_metadata_started`, `music_metadata_completed`
- `music_artifact_registered` or `music_provider_failed`

Every SpotiFLAC stdout/stderr record is also streamed at INFO by default as `music_process_output`, including carriage-return progress updates. Records are bounded to 64 KiB and redact access tokens, cookies, authorization and API-key headers, signed query credentials, provider credential fields, and client IP headers. Commands and absolute artifact paths are not logged. ffprobe JSON is captured for metadata parsing but is never streamed.

## CI/CD

A push to `main` runs `.github/workflows/deploy.yml`. It publishes:

- `${DOCKERHUB_USERNAME}/media_processing:latest`
- `${DOCKERHUB_USERNAME}/media_processing:<git-sha>`

The workflow builds `runtime-cpu` by default. Set the GitHub repository variable `MEDIA_PROCESSING_DOCKER_TARGET=runtime-gpu` when the server-side Compose service is already configured with GPU access and `COMPUTE_DEVICE=cuda`.

Required GitHub secrets:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_PASSWORD`
- `SERVER_SSH_KEY`
- `SERVER_HOST`
- `SERVER_PORT`
- `SERVER_USER`

Deployment reuses the operator-owned Compose project at `/data/bachdd/ins_clone-deploy`, the environment file `../environment_variable/.env.backend`, and recreates only the `media_processing` service. This repository intentionally contains no Compose file.
