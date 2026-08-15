from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]


def compact(path: Path) -> str:
    return "".join(path.read_text(encoding="utf-8").split())


def test_root_cpu_compose_connects_backend_to_healthy_media_service() -> None:
    compose_path = APP_ROOT / "compose.yaml"
    compose = compose_path.read_text(encoding="utf-8")
    normalized = "".join(compose.split())

    assert "social-media:" in compose
    assert "sensitive-checker:" in compose
    assert normalized.count("network_mode:host") == 2
    assert "ports:" not in compose
    assert "MUSIC_FETCH_SERVICE_BASE_URL:http://127.0.0.1:8000" in normalized
    assert normalized.count(
        "MUSIC_ARTIFACT_MAX_SIZE:${MUSIC_ARTIFACT_MAX_SIZE:-104857600}"
    ) == 2
    assert "sensitive-checker:condition:service_healthy" in normalized
    assert "sensitive-checker-artifacts:/var/lib/sensitive-checker/music" in normalized


def test_root_gpu_override_selects_cuda_target_and_all_gpus() -> None:
    compose = compact(APP_ROOT / "compose.gpu.yaml")

    assert "target:runtime-gpu" in compose
    assert "COMPUTE_DEVICE:cuda" in compose
    assert "gpus:all" in compose
