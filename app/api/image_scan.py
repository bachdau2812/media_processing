import asyncio
import logging
from uuid import uuid4

import aiofiles
from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.models.image_scan import ImageScanResponse
from app.logging_utils import sanitize_log_text


router = APIRouter()
_CHUNK_SIZE = 1024 * 1024
_MAX_FILENAME_LOG_BYTES = 512
logger = logging.getLogger(__name__)


@router.post("/api/v1/scan", response_model=ImageScanResponse)
async def scan_image(request: Request, file: UploadFile = File(...)):
    settings = request.app.state.settings
    request_id = getattr(request.state, "request_id", "-")
    filename = sanitize_log_text(
        file.filename or "",
        _MAX_FILENAME_LOG_BYTES,
    )
    logger.info(
        "image_upload_received request_id=%s filename=%r",
        request_id,
        filename,
    )
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

        logger.info(
            "image_upload_completed size_bytes=%d request_id=%s",
            size,
            request_id,
        )

        result = await asyncio.to_thread(
            request.app.state.services.nsfw_checker.evaluate,
            temp_path,
        )
        logger.info(
            "image_api_scan_completed status=%s reason=%s request_id=%s",
            result.get("status", "success"),
            result.get("reason", "invalid_image"),
            request_id,
        )
        return {
            "status": "success",
            "filename": file.filename,
            "data": result,
        }
    except HTTPException as error:
        logger.warning(
            "image_api_scan_rejected status_code=%d request_id=%s",
            error.status_code,
            request_id,
        )
        raise
    except Exception as error:
        logger.error(
            "image_api_scan_failed error_type=%s request_id=%s",
            type(error).__name__,
            request_id,
        )
        raise
    finally:
        await file.close()
        await asyncio.to_thread(temp_path.unlink, missing_ok=True)
        logger.info(
            "image_temp_cleanup_completed request_id=%s",
            request_id,
        )
