import logging
from pathlib import Path
import time
from typing import Any

from PIL import Image
import torch

from app.config import Settings


logger = logging.getLogger(__name__)


class NsfwChecker:
    BANNED_PARTS = {
        "vagina",
        "penis",
        "anus",
        "nipple",
        "make_love",
        "exposed_breast",
    }

    def __init__(
        self,
        *,
        device: torch.device,
        yolo_model: Any,
        processor: Any,
        classifier: Any,
    ) -> None:
        self.device = device
        self.yolo_model = yolo_model
        self.processor = processor
        self.classifier = classifier

    @classmethod
    def load(cls, settings: Settings, device: torch.device) -> "NsfwChecker":
        from transformers import ViTForImageClassification, ViTImageProcessor
        from ultralytics import YOLO

        logger.info("image_models_load_started device=%s", device)
        logger.info("image_yolo_model_load_started")
        yolo_model = YOLO(str(settings.yolo_model_path))
        logger.info("image_yolo_model_load_completed")
        logger.info(
            "image_vit_model_load_started model=%s device=%s",
            settings.vit_model_name,
            device,
        )
        processor = ViTImageProcessor.from_pretrained(settings.vit_model_name)
        classifier = ViTForImageClassification.from_pretrained(
            settings.vit_model_name
        ).to(device)
        classifier.eval()
        logger.info("image_models_load_completed device=%s", device)
        return cls(
            device=device,
            yolo_model=yolo_model,
            processor=processor,
            classifier=classifier,
        )

    def evaluate(self, image_path: str | Path) -> dict[str, Any]:
        started_at = time.perf_counter()
        logger.info("image_scan_started")
        try:
            with Image.open(image_path) as image:
                original_image = image.convert("RGB")
        except Exception as error:
            logger.error(
                "image_scan_invalid_image error_type=%s",
                type(error).__name__,
            )
            return self._complete(
                {
                    "status": "error",
                    "message": "Kh\u00f4ng th\u1ec3 \u0111\u1ecdc \u1ea3nh",
                },
                started_at,
                reason="invalid_image",
            )

        logger.info("image_vit_scan_started")
        inputs = self.processor(
            images=original_image, return_tensors="pt"
        ).to(self.device)
        with torch.no_grad():
            logits = self.classifier(**inputs).logits
        probabilities = torch.nn.functional.softmax(logits, dim=-1)[0]
        scores = {
            self.classifier.config.id2label[index].lower(): float(
                probabilities[index]
            )
            for index in range(len(probabilities))
        }
        porn_score = scores.get("porn", 0.0)
        hentai_score = scores.get("hentai", 0.0)
        sexy_score = scores.get("sexy", 0.0)
        logger.info(
            "image_vit_scores porn=%.4f hentai=%.4f sexy=%.4f neutral=%.4f",
            porn_score,
            hentai_score,
            sexy_score,
            scores.get("neutral", 0.0),
        )
        if porn_score > 0.60 or hentai_score > 0.60:
            logger.warning(
                "image_vit_hard_block porn=%.4f hentai=%.4f",
                porn_score,
                hentai_score,
            )
            return self._complete(
                self._build_response(
                    True,
                    max(porn_score, hentai_score),
                    "global_porn_or_hentai_detected",
                    [],
                ),
                started_at,
            )

        logger.info("image_yolo_scan_started")
        boxes = self.yolo_model(original_image, verbose=False)[0].boxes
        detections: list[dict[str, Any]] = []
        has_banned_part = False
        highest_yolo_confidence = 0.0
        for box in boxes:
            confidence = float(box.conf[0])
            if confidence < 0.45:
                continue
            class_name = self.yolo_model.names[int(box.cls[0])]
            coordinates = list(map(int, box.xyxy[0].tolist()))
            highest_yolo_confidence = max(highest_yolo_confidence, confidence)
            detections.append(
                {
                    "part": class_name,
                    "bbox": coordinates,
                    "confidence": round(confidence, 4),
                }
            )
            logger.info(
                "image_yolo_detection part=%s confidence=%.4f",
                class_name,
                confidence,
            )
            if class_name in self.BANNED_PARTS:
                has_banned_part = True
                logger.warning(
                    "image_yolo_banned_label part=%s confidence=%.4f",
                    class_name,
                    confidence,
                )
        if not detections:
            logger.info("image_yolo_no_detections")
        if has_banned_part:
            return self._complete(
                self._build_response(
                    True,
                    highest_yolo_confidence,
                    "yolo_detected_banned_parts",
                    detections,
                ),
                started_at,
            )

        reason = (
            "safe_swimwear_or_fitness" if sexy_score > 0.55 else "safe_neutral"
        )
        return self._complete(
            self._build_response(
                False,
                max(porn_score, hentai_score, highest_yolo_confidence),
                reason,
                detections,
            ),
            started_at,
        )

    @staticmethod
    def _complete(
        result: dict[str, Any],
        started_at: float,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        resolved_reason = reason or str(result.get("reason", "unknown"))
        status = str(result.get("status", "success"))
        if status == "success":
            logger.info(
                "image_scan_decision is_nsfw=%s risk_score=%.4f reason=%s",
                result["is_nsfw"],
                result["highest_risk_score"],
                resolved_reason,
            )
        logger.info(
            "image_scan_completed status=%s reason=%s elapsed_ms=%d",
            status,
            resolved_reason,
            round((time.perf_counter() - started_at) * 1000),
        )
        return result

    @staticmethod
    def _build_response(
        is_nsfw: bool,
        score: float,
        reason: str,
        detections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "is_nsfw": is_nsfw,
            "highest_risk_score": round(score, 4),
            "reason": reason,
            "detections": detections,
        }
