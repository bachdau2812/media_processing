from pydantic import BaseModel


class ImageDetection(BaseModel):
    part: str
    bbox: list[int]
    confidence: float


class ImageScanResult(BaseModel):
    is_nsfw: bool
    highest_risk_score: float
    reason: str
    detections: list[ImageDetection]


class ImageScanResponse(BaseModel):
    status: str
    filename: str | None
    data: ImageScanResult | dict
