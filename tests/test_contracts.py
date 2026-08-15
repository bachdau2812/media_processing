from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.errors import ApiProblem
from app.models.music_artifact import (
    MusicArtifactCreateRequest,
    MusicArtifactResponse,
    MusicMetadata,
)


def test_music_artifact_contract_uses_camel_case_aliases():
    response = MusicArtifactResponse(
        artifactId="artifact-1",
        trackId="1234567890123456789012",
        filename="track.flac",
        sizeBytes=42,
        sha256="hash",
        expiresAt=datetime(2026, 8, 15, tzinfo=UTC),
        metadata=MusicMetadata(albumArtist="Artist"),
    )

    assert response.model_dump(by_alias=True)["artifactId"] == "artifact-1"
    assert response.model_dump(by_alias=True)["metadata"]["albumArtist"] == "Artist"


def test_music_create_contract_requires_spotify_track_id():
    with pytest.raises(ValidationError):
        MusicArtifactCreateRequest(track_id="not-a-spotify-track")


def test_api_problem_uses_request_id_alias():
    problem = ApiProblem(code="FAILED", message="safe message", requestId="request-1")

    assert problem.model_dump(by_alias=True) == {
        "code": "FAILED",
        "message": "safe message",
        "requestId": "request-1",
    }
