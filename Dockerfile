# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/huggingface

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

FROM base AS dependencies-common

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

FROM dependencies-common AS dependencies-cpu

COPY requirements-cpu.txt ./requirements-cpu.txt
RUN python -m pip install --no-cache-dir --force-reinstall -r requirements-cpu.txt \
    && python -c "from transformers import ViTForImageClassification, ViTImageProcessor; name='AdamCodd/vit-base-nsfw-detector'; ViTImageProcessor.from_pretrained(name); ViTForImageClassification.from_pretrained(name)"

FROM dependencies-common AS dependencies-gpu

COPY requirements-gpu.txt ./requirements-gpu.txt
RUN python -m pip install --no-cache-dir --force-reinstall -r requirements-gpu.txt \
    && python -c "from transformers import ViTForImageClassification, ViTImageProcessor; name='AdamCodd/vit-base-nsfw-detector'; ViTImageProcessor.from_pretrained(name); ViTForImageClassification.from_pretrained(name)"

FROM dependencies-cpu AS runtime-cpu

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /var/lib/sensitive-checker/music /tmp/sensitive-checker/images \
    && chown -R app:app /var/lib/sensitive-checker /tmp/sensitive-checker \
    && chmod -R a+rX /opt/huggingface
COPY --chown=app:app app ./app
COPY --chown=app:app spotiflac ./spotiflac
COPY --chown=app:app models/erax_nsfw_yolo11m.pt ./models/erax_nsfw_yolo11m.pt

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('SERVICE_PORT', '8000') + '/health/ready', timeout=3)"]
CMD ["python", "-m", "app"]

FROM dependencies-gpu AS runtime-gpu

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /var/lib/sensitive-checker/music /tmp/sensitive-checker/images \
    && chown -R app:app /var/lib/sensitive-checker /tmp/sensitive-checker \
    && chmod -R a+rX /opt/huggingface
COPY --chown=app:app app ./app
COPY --chown=app:app spotiflac ./spotiflac
COPY --chown=app:app models/erax_nsfw_yolo11m.pt ./models/erax_nsfw_yolo11m.pt

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('SERVICE_PORT', '8000') + '/health/ready', timeout=3)"]
CMD ["python", "-m", "app"]
