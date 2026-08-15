# Sensitive Checker media service

FastAPI service for image moderation and temporary SpotiFLAC music artifacts. The default image runs inference on CPU; the GPU target uses PyTorch CUDA 13.0 wheels. Both targets run one Uvicorn worker as a non-root user and keep generated music in a named Docker volume.

## Start the service

Copy `.env.example` to `.env` if you need to change a default. On the Linux server, run the CPU image with:

```bash
docker compose up -d --build
```

For an NVIDIA server with a compatible driver and NVIDIA Container Toolkit:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build
```

Follow startup and download logs with:

```bash
docker compose logs -f sensitive-checker
```

The service uses host networking and publishes no Docker ports. It binds to `127.0.0.1:8000` by default, so another host-network container can call `http://127.0.0.1:8000`. Check readiness at `GET /health/ready` and liveness at `GET /health/live`.

The music artifact limit is `104857600` bytes (100 MiB). The `sensitive-checker-artifacts` volume retains temporary files across container replacement; startup cleanup and the TTL sweeper remove stale jobs. If `ARTIFACT_ROOT` is changed, update the volume target to the same path.

Do not set `SERVICE_HOST=0.0.0.0` on an Internet-reachable host without a firewall, authentication, rate limiting, or a protected reverse proxy. The music endpoint can start expensive external downloads.

For reproducible promotion, push the built image to your registry and deploy the approved immutable digest, for example `registry.example/sensitive-checker@sha256:<digest>`, through `SENSITIVE_CHECKER_IMAGE`.
