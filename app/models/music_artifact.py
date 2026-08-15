from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


def _to_camel_case(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.title() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel_case)


class MusicArtifactCreateRequest(ApiModel):
    track_id: str = Field(pattern=r"^[A-Za-z0-9]{22}$")


class MusicMetadata(ApiModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    composer: str | None = None
    genre: str | None = None
    lyrics: str | None = None


class MusicArtifactResponse(ApiModel):
    artifact_id: str
    track_id: str
    filename: str
    content_type: str = "audio/flac"
    size_bytes: int
    sha256: str
    expires_at: datetime
    metadata: MusicMetadata
