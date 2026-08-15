from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def compact(value: str) -> str:
    return "".join(value.split())


def test_dockerfile_provides_cpu_and_gpu_runtime_contracts() -> None:
    dockerfile = read("Dockerfile")
    normalized = compact(dockerfile)

    assert "python:3.12" in dockerfile
    assert "AS runtime-cpu" in dockerfile
    assert "AS runtime-gpu" in dockerfile
    assert "ffmpeg" in dockerfile
    assert "requirements.txt" in dockerfile
    assert "requirements-cpu.txt" in dockerfile
    assert "requirements-gpu.txt" in dockerfile
    assert "AdamCodd/vit-base-nsfw-detector" in dockerfile
    assert re.search(r"COPY(?:\s+--chown=\S+)?\s+app\s+\./app", dockerfile)
    assert re.search(
        r"COPY(?:\s+--chown=\S+)?\s+spotiflac\s+\./spotiflac", dockerfile
    )
    assert re.search(
        r"COPY(?:\s+--chown=\S+)?\s+models/erax_nsfw_yolo11m\.pt\s+"
        r"\./models/erax_nsfw_yolo11m\.pt",
        dockerfile,
    )
    assert "USER app" in dockerfile
    assert '["python","-m","app"]' in normalized
    assert "urllib.request" in dockerfile
    assert "curl" not in dockerfile.lower()


def test_device_requirements_pin_official_torch_wheels() -> None:
    cpu = read("requirements-cpu.txt")
    gpu = read("requirements-gpu.txt")
    common = read("requirements.txt")

    assert "--index-url https://download.pytorch.org/whl/cpu" in cpu
    assert "torch==2.11.0" in cpu
    assert "torchvision==0.26.0" in cpu
    assert "--index-url https://download.pytorch.org/whl/cu130" in gpu
    assert "torch==2.11.0" in gpu
    assert "torchvision==0.26.0" in gpu
    assert "spotiflac==1.6.0" in common.lower()


def test_entrypoint_uses_configured_host_and_one_worker() -> None:
    entrypoint = read("app/__main__.py")

    assert "host=settings.service_host" in compact(entrypoint)
    assert "workers=1" in compact(entrypoint)


def test_cpu_compose_uses_host_network_without_port_publication() -> None:
    compose = read("compose.yaml")
    normalized = compact(compose)

    assert "target:runtime-cpu" in normalized
    assert "network_mode:host" in normalized
    assert "ports:" not in compose
    assert "SERVICE_HOST:${SERVICE_HOST:-127.0.0.1}" in normalized
    assert "MUSIC_ARTIFACT_MAX_SIZE:${MUSIC_ARTIFACT_MAX_SIZE:-104857600}" in normalized
    assert "COMPUTE_DEVICE:${COMPUTE_DEVICE:-cpu}" in normalized
    assert "restart:unless-stopped" in normalized
    assert "volumes:" in compose
    assert "/var/lib/sensitive-checker/music" in compose
    assert "/health/ready" in compose


def test_gpu_compose_selects_cuda_runtime_and_all_gpus() -> None:
    compose = compact(read("compose.gpu.yaml"))

    assert "target:runtime-gpu" in compose
    assert "COMPUTE_DEVICE:cuda" in compose
    assert "gpus:all" in compose
