from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    services = request.app.state.services
    if not services.ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready"},
        )
    device = services.device
    device_name = getattr(device, "type", None)
    return JSONResponse(
        content={"status": "ready", "device": str(device_name or device)}
    )
