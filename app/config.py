from pathlib import Path
from typing import Literal

from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_host: str = "127.0.0.1"
    service_port: PositiveInt = 8000
    compute_device: Literal["cpu", "cuda", "auto"] = "cpu"
    artifact_root: Path = Path("/var/lib/sensitive-checker/music")
    music_artifact_max_size: PositiveInt = 100 * 1024 * 1024
    music_artifact_ttl_seconds: PositiveInt = 900
    music_process_timeout_seconds: PositiveInt = 300
    music_max_concurrent_downloads: PositiveInt = 2
    music_capacity_wait_seconds: PositiveInt = 1
    music_cleanup_interval_seconds: PositiveInt = 60
    image_upload_max_size: PositiveInt = 100 * 1024 * 1024
    image_scan_temp_root: Path = Path("/tmp/sensitive-checker/images")
    yolo_model_path: Path = Path("models/erax_nsfw_yolo11m.pt")
    vit_model_name: str = "AdamCodd/vit-base-nsfw-detector"
