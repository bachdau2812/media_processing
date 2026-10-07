# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS runtime-base


# ==========================================
# ENVIRONMENT
# ==========================================
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false \
    SERVICE_HOST=127.0.0.1 \
    SERVICE_PORT=8000 \
    LOG_DIR=/srv/app/logs


# ==========================================
# SYSTEM DEPENDENCIES
# ==========================================
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean


# ==========================================
# APPLICATION USER + DIRECTORIES
# ==========================================
RUN groupadd --gid 10001 app \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --create-home \
        --shell /usr/sbin/nologin \
        app \
    && mkdir -p \
        /srv/app \
        /srv/app/logs \
        /srv/app/models \
        /opt/huggingface \
        /var/lib/sensitive-checker/music \
        /tmp/sensitive-checker/images \
    && chown -R app:app \
        /srv/app \
        /opt/huggingface \
        /var/lib/sensitive-checker \
        /tmp/sensitive-checker


WORKDIR /srv/app


FROM runtime-base AS runtime-cpu

ENV COMPUTE_DEVICE=cpu


# ==========================================
# PYTHON DEPENDENCIES
# ==========================================

# requirements-cpu.txt:
#
# --index-url https://download.pytorch.org/whl/cpu
# torch==2.11.0
# torchvision==0.26.0

COPY requirements-cpu.txt ./requirements-cpu.txt
COPY requirements.txt ./requirements.txt

# Torch CPU phải được cài TRƯỚC Ultralytics.
#
# Dùng cùng một RUN để nếu pip có thay đổi/reconcile package,
# image không giữ một layer package cũ riêng phía dưới.
RUN python -m pip install \
        --no-cache-dir \
        -r requirements-cpu.txt \
    && python -m pip install \
        --no-cache-dir \
        -r requirements.txt \
    && python -m pip check \
    && python -c "\
import torch; \
print('Torch version:', torch.__version__); \
print('CUDA version:', torch.version.cuda); \
assert torch.version.cuda is None, 'GPU/CUDA Torch installed in CPU image'; \
print('CPU-only PyTorch verified')"


# ==========================================
# HUGGING FACE MODEL
# ==========================================

# Download model ngay lúc build để runtime có thể chạy offline.
# Chạy bằng user app để cache có đúng ownership ngay từ đầu,
# không phải chmod/chown recursive ở layer sau.
USER app

RUN python -c "\
from transformers import ViTForImageClassification, ViTImageProcessor; \
name='AdamCodd/vit-base-nsfw-detector'; \
print(f'Downloading Hugging Face model: {name}'); \
ViTImageProcessor.from_pretrained(name); \
ViTForImageClassification.from_pretrained(name); \
print('Hugging Face model downloaded successfully')"


# ==========================================
# APPLICATION SOURCE
# ==========================================

USER root

COPY --chown=app:app app ./app
COPY --chown=app:app spotiflac ./spotiflac

COPY --chown=app:app models/erax_nsfw_yolo11m.pt ./models/erax_nsfw_yolo11m.pt


# ==========================================
# RUNTIME
# ==========================================
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    ARTIFACT_ROOT=/var/lib/sensitive-checker/music \
    IMAGE_SCAN_TEMP_ROOT=/tmp/sensitive-checker/images \
    YOLO_MODEL_PATH=/srv/app/models/erax_nsfw_yolo11m.pt \
    VIT_MODEL_NAME=AdamCodd/vit-base-nsfw-detector


USER app

EXPOSE 8000


# ==========================================
# HEALTHCHECK
# ==========================================
HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=60s \
    --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('SERVICE_PORT', '8000') + '/health/ready', timeout=3)"]


# ==========================================
# START APPLICATION
# ==========================================
CMD ["python", "-m", "app"]


FROM runtime-base AS runtime-gpu

ENV COMPUTE_DEVICE=cuda


# ==========================================
# PYTHON DEPENDENCIES
# ==========================================
COPY requirements-gpu.txt ./requirements-gpu.txt
COPY requirements.txt ./requirements.txt

# Keep the CUDA-enabled PyTorch wheel before Ultralytics resolves its dependencies.
RUN python -m pip install \
        --no-cache-dir \
        -r requirements-gpu.txt \
    && python -m pip install \
        --no-cache-dir \
        -r requirements.txt \
    && python -m pip check \
    && python -c "\
import torch; \
print('Torch version:', torch.__version__); \
print('CUDA version:', torch.version.cuda); \
assert torch.version.cuda is not None, 'CPU-only PyTorch installed in GPU image'; \
print('CUDA-enabled PyTorch verified')"


# ==========================================
# HUGGING FACE MODEL
# ==========================================
USER app

RUN python -c "\
from transformers import ViTForImageClassification, ViTImageProcessor; \
name='AdamCodd/vit-base-nsfw-detector'; \
print(f'Downloading Hugging Face model: {name}'); \
ViTImageProcessor.from_pretrained(name); \
ViTForImageClassification.from_pretrained(name); \
print('Hugging Face model downloaded successfully')"


# ==========================================
# APPLICATION SOURCE
# ==========================================
USER root

COPY --chown=app:app app ./app
COPY --chown=app:app spotiflac ./spotiflac
COPY --chown=app:app models/erax_nsfw_yolo11m.pt ./models/erax_nsfw_yolo11m.pt


# ==========================================
# RUNTIME
# ==========================================
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    ARTIFACT_ROOT=/var/lib/sensitive-checker/music \
    IMAGE_SCAN_TEMP_ROOT=/tmp/sensitive-checker/images \
    YOLO_MODEL_PATH=/srv/app/models/erax_nsfw_yolo11m.pt \
    VIT_MODEL_NAME=AdamCodd/vit-base-nsfw-detector

USER app

EXPOSE 8000

HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=60s \
    --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('SERVICE_PORT', '8000') + '/health/ready', timeout=3)"]

CMD ["python", "-m", "app"]
