from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def compact(path: Path) -> str:
    return "".join(path.read_text(encoding="utf-8").split())


def test_repository_owns_an_image_workflow_not_compose() -> None:
    assert not (ROOT / "compose.yaml").exists()
    assert not (ROOT / "compose.gpu.yaml").exists()
    assert (ROOT / "Dockerfile").is_file()


def test_deploy_workflow_builds_and_recreates_media_processing() -> None:
    workflow = compact(ROOT / ".github/workflows/deploy.yml")

    assert "branches:-main" in workflow
    assert "docker/login-action@v4" in workflow
    assert "docker/setup-buildx-action@v4" in workflow
    assert "docker/build-push-action@v7" in workflow
    assert "${{vars.MEDIA_PROCESSING_DOCKER_TARGET||'runtime-cpu'}}" in workflow
    assert "${{secrets.DOCKERHUB_USERNAME}}/media_processing:latest" in workflow
    assert "${{secrets.DOCKERHUB_USERNAME}}/media_processing:${{github.sha}}" in workflow
    assert "cd/data/bachdd/ins_clone-deploy" in workflow
    assert "dockercomposepullmedia_processing" in workflow
    assert "--env-file../environment_variable/.env.backend" in workflow
    assert "--force-recreatemedia_processing" in workflow


def test_env_example_contains_runtime_settings_only() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "SENSITIVE_CHECKER_IMAGE" not in env_example
    assert "SERVICE_HOST=127.0.0.1" in env_example
    assert "MUSIC_ARTIFACT_MAX_SIZE=104857600" in env_example
