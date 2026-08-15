from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest
import torch

import app.main as main_module
from app.config import Settings
from app.main import create_app, create_uninitialized_services
from app.services.nsfw_checker import NsfwChecker


class FakeScanner:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def evaluate(self, image_path: str | Path):
        path = Path(image_path)
        assert path.exists()
        self.paths.append(path)
        return {
            "is_nsfw": False,
            "highest_risk_score": 0.1,
            "reason": "safe_neutral",
            "detections": [],
        }


def services(scanner: FakeScanner):
    return SimpleNamespace(ready=True, device="cpu", nsfw_checker=scanner)


async def post_image(app, filename: str, content: bytes):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/v1/scan",
                files={"file": (filename, content, "image/jpeg")},
            )


@pytest.mark.asyncio
async def test_scan_preserves_existing_response_contract(tmp_path: Path):
    scanner = FakeScanner()
    app = create_app(
        Settings(image_scan_temp_root=tmp_path),
        services(scanner),
    )

    response = await post_image(app, "photo.jpg", b"small-image")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "filename": "photo.jpg",
        "data": {
            "is_nsfw": False,
            "highest_risk_score": 0.1,
            "reason": "safe_neutral",
            "detections": [],
        },
    }
    assert len(scanner.paths) == 1
    assert scanner.paths[0].parent == tmp_path.resolve()
    assert not scanner.paths[0].exists()


@pytest.mark.asyncio
async def test_scan_uses_safe_server_filename_for_untrusted_upload_name(tmp_path: Path):
    scanner = FakeScanner()
    app = create_app(
        Settings(image_scan_temp_root=tmp_path),
        services(scanner),
    )

    response = await post_image(app, "../../escape.jpg", b"small-image")

    assert response.status_code == 200
    assert response.json()["filename"] == "../../escape.jpg"
    assert scanner.paths[0].parent == tmp_path.resolve()
    assert scanner.paths[0].name != "escape.jpg"
    assert not (tmp_path.parent / "escape.jpg").exists()


@pytest.mark.asyncio
async def test_scan_rejects_over_limit_upload_and_removes_partial_file(tmp_path: Path):
    scanner = FakeScanner()
    app = create_app(
        Settings(image_scan_temp_root=tmp_path, image_upload_max_size=5),
        services(scanner),
    )

    response = await post_image(app, "large.jpg", b"123456")

    assert response.status_code == 413
    assert scanner.paths == []
    assert list(tmp_path.iterdir()) == []


class FakeBatch(dict):
    def to(self, device):
        return self


class FakeProcessor:
    def __call__(self, **kwargs):
        return FakeBatch()


class FakeClassifier:
    def __init__(self, scores: dict[str, float]) -> None:
        labels = ["porn", "hentai", "sexy", "neutral"]
        self.config = SimpleNamespace(id2label=dict(enumerate(labels)))
        probabilities = torch.tensor([scores[label] for label in labels])
        self.logits = torch.log(probabilities).unsqueeze(0)

    def __call__(self, **kwargs):
        return SimpleNamespace(logits=self.logits)


class FakeBox:
    def __init__(self, class_index: int, confidence: float) -> None:
        self.conf = [confidence]
        self.cls = torch.tensor([class_index])
        self.xyxy = torch.tensor([[1, 2, 30, 40]])


class FakeYolo:
    names = {0: "nipple", 1: "face"}

    def __init__(self, boxes=()) -> None:
        self.boxes = list(boxes)
        self.calls = 0

    def __call__(self, image, verbose: bool):
        self.calls += 1
        return [SimpleNamespace(boxes=self.boxes)]


def checker(scores: dict[str, float], yolo: FakeYolo) -> NsfwChecker:
    return NsfwChecker(
        device=torch.device("cpu"),
        yolo_model=yolo,
        processor=FakeProcessor(),
        classifier=FakeClassifier(scores),
    )


def image_file(tmp_path: Path) -> Path:
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (2, 2)).save(path)
    return path


def test_nsfw_checker_blocks_porn_above_existing_threshold(tmp_path: Path):
    yolo = FakeYolo()
    service = checker(
        {"porn": 0.61, "hentai": 0.01, "sexy": 0.18, "neutral": 0.2}, yolo
    )

    result = service.evaluate(image_file(tmp_path))

    assert result == {
        "is_nsfw": True,
        "highest_risk_score": 0.61,
        "reason": "global_porn_or_hentai_detected",
        "detections": [],
    }
    assert yolo.calls == 0


def test_nsfw_checker_blocks_banned_part_at_existing_confidence(tmp_path: Path):
    service = checker(
        {"porn": 0.1, "hentai": 0.1, "sexy": 0.2, "neutral": 0.6},
        FakeYolo([FakeBox(0, 0.45)]),
    )

    result = service.evaluate(image_file(tmp_path))

    assert result == {
        "is_nsfw": True,
        "highest_risk_score": 0.45,
        "reason": "yolo_detected_banned_parts",
        "detections": [
            {"part": "nipple", "bbox": [1, 2, 30, 40], "confidence": 0.45}
        ],
    }


def test_nsfw_checker_ignores_banned_part_just_below_confidence_threshold(
    tmp_path: Path,
):
    service = checker(
        {"porn": 0.1, "hentai": 0.1, "sexy": 0.2, "neutral": 0.6},
        FakeYolo([FakeBox(0, 0.44996)]),
    )

    result = service.evaluate(image_file(tmp_path))

    assert result == {
        "is_nsfw": False,
        "highest_risk_score": 0.1,
        "reason": "safe_neutral",
        "detections": [],
    }


def test_nsfw_checker_keeps_existing_safe_sexy_decision(tmp_path: Path):
    service = checker(
        {"porn": 0.1, "hentai": 0.1, "sexy": 0.56, "neutral": 0.24},
        FakeYolo([FakeBox(1, 0.9)]),
    )

    result = service.evaluate(image_file(tmp_path))

    assert result == {
        "is_nsfw": False,
        "highest_risk_score": 0.9,
        "reason": "safe_swimwear_or_fitness",
        "detections": [
            {"part": "face", "bbox": [1, 2, 30, 40], "confidence": 0.9}
        ],
    }


def test_nsfw_checker_preserves_invalid_image_result(tmp_path: Path):
    invalid_image = tmp_path / "broken.jpg"
    invalid_image.write_bytes(b"not-an-image")
    service = checker(
        {"porn": 0.1, "hentai": 0.1, "sexy": 0.2, "neutral": 0.6},
        FakeYolo(),
    )

    assert service.evaluate(invalid_image) == {
        "status": "error",
        "message": "Kh\u00f4ng th\u1ec3 \u0111\u1ecdc \u1ea3nh",
    }


@pytest.mark.asyncio
async def test_default_services_load_scanner_once_with_selected_device(
    tmp_path: Path, monkeypatch
):
    scanner = FakeScanner()
    selected_devices: list[str] = []
    load_calls: list[tuple[Settings, torch.device]] = []

    def fake_select_device(requested: str) -> torch.device:
        selected_devices.append(requested)
        return torch.device("cpu")

    def fake_load(settings: Settings, device: torch.device):
        load_calls.append((settings, device))
        return scanner

    monkeypatch.setattr(main_module, "select_device", fake_select_device)
    monkeypatch.setattr(main_module.NsfwChecker, "load", fake_load)
    settings = Settings(
        compute_device="auto",
        image_scan_temp_root=tmp_path / "images",
        artifact_root=tmp_path / "music",
    )
    services = create_uninitialized_services()
    app = create_app(settings, services)

    async with app.router.lifespan_context(app):
        assert services.nsfw_checker is scanner
        assert services.device == torch.device("cpu")
    async with app.router.lifespan_context(app):
        pass

    assert selected_devices == ["auto"]
    assert load_calls == [(settings, torch.device("cpu"))]
