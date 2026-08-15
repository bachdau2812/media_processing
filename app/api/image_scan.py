import asyncio
from uuid import uuid4

import aiofiles
from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.models.image_scan import ImageScanResponse


router = APIRouter()
_CHUNK_SIZE = 1024 * 1024


@router.post("/api/v1/scan", response_model=ImageScanResponse)
async def scan_image(request: Request, file: UploadFile = File(...)):
    settings = request.app.state.settings
    temp_root = settings.image_scan_temp_root.resolve()
    await asyncio.to_thread(temp_root.mkdir, parents=True, exist_ok=True)
    temp_path = temp_root / f"{uuid4()}.upload"

    try:
        size = 0
        async with aiofiles.open(temp_path, "xb") as output:
            while chunk := await file.read(_CHUNK_SIZE):
                size += len(chunk)
                if size > settings.image_upload_max_size:
                    raise HTTPException(
                        status_code=413,
                        detail="Uploaded image is too large",
                    )
                await output.write(chunk)

        result = await asyncio.to_thread(
            request.app.state.services.nsfw_checker.evaluate,
            temp_path,
        )
        return {
            "status": "success",
            "filename": file.filename,
            "data": result,
        }
    finally:
        await file.close()
        await asyncio.to_thread(temp_path.unlink, missing_ok=True)
