FROM python:3.13-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PADDLE_PDX_CACHE_HOME=/var/lib/ocr-models \
    PADDLE_PDX_MODEL_SOURCE=BOS \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    HF_HOME=/var/lib/ocr-models/hf \
    MODELSCOPE_CACHE=/var/lib/ocr-models/modelscope \
    OMP_NUM_THREADS=4 \
    HOME=/tmp

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/ocr-models \
    && chown 10001:10001 /var/lib/ocr-models

COPY requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
COPY ocr_service ./ocr_service

FROM base AS test
RUN pip install --no-cache-dir --disable-pip-version-check httpx==0.28.1
COPY tests ./tests
USER 10001:10001
RUN python -m unittest discover -s tests -p 'test_*.py' -v

FROM base AS runtime
LABEL org.opencontainers.image.title="OCR Agent" \
      org.opencontainers.image.description="Self-hosted document OCR and table extraction"
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "ocr_service.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--timeout-keep-alive", "5", "--limit-concurrency", "16"]
