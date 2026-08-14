import os
import shutil
import torch
import logging
from fastapi import FastAPI, File, UploadFile, HTTPException
from PIL import Image
from ultralytics import YOLO
from transformers import ViTImageProcessor, ViTForImageClassification
import uvicorn
import time

# ==========================================
# CẤU HÌNH LOGGING CHUYÊN NGHIỆP
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ==========================================
# PIPELINE TỐI THƯỢNG: 5-CLASS ViT + YOLOv9
# ==========================================
class UltimateNSFWPipeline:
    def __init__(self, yolo_filename="erax_nsfw_yolo11m.pt"):
        logger.info("--- ĐANG KHỞI ĐỘNG HỆ THỐNG KIỂM DUYỆT ĐA LỚP ---")
        
        # 1. Khởi tạo YOLO (Bắt chi tiết)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        yolo_path = os.path.join(current_dir, "models", yolo_filename)
        logger.info(f"[INIT] Đang tải mô hình YOLO từ: {yolo_path}")
        self.yolo_model = YOLO(yolo_path)
        self.BANNED_PARTS = ['vagina', 'penis', 'anus', 'nipple', 'make_love', 'exposed_breast']
        
        # 2. Khởi tạo ViT 5-Class (Bắt ngữ cảnh toàn cục)
        model_name = "AdamCodd/vit-base-nsfw-detector"
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info(f"[INIT] Đang tải mô hình ViT 5-Class ({model_name}) lên thiết bị: {self.device}")
        
        self.processor = ViTImageProcessor.from_pretrained(model_name)
        self.classifier = ViTForImageClassification.from_pretrained(model_name).to(self.device)
        self.classifier.eval()
        
        logger.info("--- KHỞI TẠO HOÀN TẤT, SẴN SÀNG NHẬN REQUEST ---")

    def evaluate(self, image_path: str):
        logger.info(f"--- BẮT ĐẦU XỬ LÝ ẢNH: {image_path} ---")
        start_time = time.time()
        
        try:
            original_image = Image.open(image_path).convert("RGB")
        except Exception as e:
            logger.error(f"[LỖI] Không thể đọc ảnh: {e}")
            return {"status": "error", "message": "Không thể đọc ảnh"}

        # ---------------------------------------------------------
        # BƯỚC 1: QUÉT NGỮ CẢNH TOÀN CỤC (ViT 5-Class)
        # ---------------------------------------------------------
        logger.info("[BƯỚC 1] Đang quét ngữ cảnh toàn cục (ViT)...")
        inputs = self.processor(images=original_image, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self.classifier(**inputs)
            logits = outputs.logits
        
        probs = torch.nn.functional.softmax(logits, dim=-1)[0]
        scores = {self.classifier.config.id2label[i].lower(): float(probs[i]) for i in range(len(probs))}
        
        porn_score = scores.get("porn", 0.0)
        hentai_score = scores.get("hentai", 0.0)
        sexy_score = scores.get("sexy", 0.0)
        
        logger.info(f"  -> [ViT Scores] Porn: {porn_score:.4f} | Hentai: {hentai_score:.4f} | Sexy: {sexy_score:.4f} | Neutral: {scores.get('neutral', 0.0):.4f}")

        # LUẬT 1: CHẶN CỨNG
        if porn_score > 0.60 or hentai_score > 0.60:
            logger.warning("[QUYẾT ĐỊNH] Kích hoạt LUẬT 1: Chặn cứng do điểm Porn/Hentai vượt ngưỡng (Global Context).")
            return self._build_response(True, max(porn_score, hentai_score), "global_porn_or_hentai_detected", [])

        # ---------------------------------------------------------
        # BƯỚC 2: QUÉT CHI TIẾT (YOLO)
        # ---------------------------------------------------------
        logger.info("[BƯỚC 2] Đang quét chi tiết các bộ phận (YOLO)...")
        yolo_results = self.yolo_model(original_image, verbose=False) # verbose=False để tắt log rác của YOLO
        boxes = yolo_results[0].boxes
        
        detected_parts = []
        has_banned_part = False
        highest_yolo_conf = 0.0

        if len(boxes) == 0:
            logger.info("  -> [YOLO] Không phát hiện đối tượng nào.")
        else:
            for box in boxes:
                conf = float(box.conf[0])
                if conf < 0.45:
                    continue
                    
                class_name = self.yolo_model.names[int(box.cls[0])]
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                
                if conf > highest_yolo_conf:
                    highest_yolo_conf = conf

                detected_parts.append({
                    "part": class_name,
                    "bbox": [x1, y1, x2, y2],
                    "confidence": round(conf, 4)
                })
                
                logger.info(f"  -> [YOLO] Phát hiện: '{class_name}' (Tự tin: {conf:.4f})")

                if class_name in self.BANNED_PARTS:
                    has_banned_part = True
                    logger.warning(f"  -> [YOLO CẢNH BÁO] '{class_name}' là nhãn bị CẤM TUYỆT ĐỐI!")

        # LUẬT 2: BIKINI NHƯNG LỘ HÀNG
        if has_banned_part:
            logger.warning("[QUYẾT ĐỊNH] Kích hoạt LUẬT 2: Chặn do YOLO tìm thấy bộ phận/hành vi nhạy cảm.")
            return self._build_response(True, highest_yolo_conf, "yolo_detected_banned_parts", detected_parts)

        # ---------------------------------------------------------
        # BƯỚC 3: AN TOÀN
        # ---------------------------------------------------------
        if sexy_score > 0.55:
            reason = "safe_swimwear_or_fitness"
            logger.info("[QUYẾT ĐỊNH] LUẬT 3: Hợp lệ. Ảnh Gợi cảm/Đồ bơi nhưng không lộ điểm nhạy cảm.")
        else:
            reason = "safe_neutral"
            logger.info("[QUYẾT ĐỊNH] LUẬT 3: Hợp lệ. Ảnh An toàn/Đời thường.")
            
        process_time = time.time() - start_time
        logger.info(f"--- HOÀN TẤT XỬ LÝ (Mất {process_time:.2f} giây) ---\n")
        
        return self._build_response(False, max(porn_score, hentai_score, highest_yolo_conf), reason, detected_parts)

    def _build_response(self, is_nsfw, score, reason, detections):
        return {
            "is_nsfw": is_nsfw,
            "highest_risk_score": round(score, 4),
            "reason": reason,
            "detections": detections
        }

# ==========================================
# KHỞI TẠO FASTAPI
# ==========================================
app = FastAPI(title="Ultimate NSFW API with Logging")
pipeline_service = UltimateNSFWPipeline()

@app.post("/api/v1/scan")
async def scan_image(file: UploadFile = File(...)):
    logger.info(f"[API] Nhận request kiểm tra file: {file.filename}")
    temp_file = f"temp_{file.filename}"
    try:
        with open(temp_file, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        result = pipeline_service.evaluate(temp_file)
        return {"status": "success", "filename": file.filename, "data": result}
    except Exception as e:
        logger.error(f"[API LỖI] {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
            logger.info(f"[API] Đã xóa file tạm: {temp_file}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)