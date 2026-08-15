from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from app.models.music_artifact import (
    MusicArtifactCreateRequest,
    MusicArtifactResponse,
)


router = APIRouter()


class MusicArtifactNotFound(Exception):
    pass


@router.post(
    "/api/v1/music/artifacts",
    response_model=MusicArtifactResponse,
    status_code=201,
)
async def create_artifact(
    request: MusicArtifactCreateRequest, http_request: Request
) -> MusicArtifactResponse:
    return await http_request.app.state.services.music_artifact_service.create(
        request.track_id
    )


@router.get("/api/v1/music/artifacts/{artifact_id}/audio")
async def stream_artifact(artifact_id: UUID, request: Request) -> FileResponse:
    record = await request.app.state.services.artifact_store.get(
        str(artifact_id)
    )
    if record is None:
        raise MusicArtifactNotFound
    return FileResponse(
        path=record.file_path,
        media_type="audio/flac",
        filename=Path(record.filename).name,
        content_disposition_type="attachment",
        headers={"Content-Length": str(record.size_bytes)},
    )


@router.delete(
    "/api/v1/music/artifacts/{artifact_id}", status_code=204
)
async def delete_artifact(artifact_id: UUID, request: Request) -> Response:
    await request.app.state.services.artifact_store.delete(str(artifact_id))
    return Response(status_code=204)
