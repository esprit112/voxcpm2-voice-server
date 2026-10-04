# VoxCPM2 Voice Server - container image
#
#   NVIDIA GPU (default):  docker build -t voxcpm2-voice-server .
#   CPU only:              docker build --build-arg TORCH_BACKEND=cpu -t voxcpm2-voice-server:cpu .
#   AMD ROCm:              docker build --build-arg TORCH_BACKEND=rocm6.4 -t voxcpm2-voice-server:rocm .
#
# The PyTorch CUDA wheels bundle the CUDA runtime, so a slim Python base is enough; the host only needs the
# NVIDIA driver + NVIDIA Container Toolkit. The model (~5 GB) is NOT baked in: it lives in the /app/models
# volume and is downloaded on first start. See docker-compose.yml.
FROM python:3.11-slim

ARG TORCH_BACKEND=cu128
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    VOXCPM_HOST=0.0.0.0 \
    VOXCPM_PORT=8808 \
    HF_HOME=/app/models/.hf-cache

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv

WORKDIR /app
COPY requirements.txt .
RUN uv pip install --system --torch-backend "${TORCH_BACKEND}" torch torchaudio \
 && uv pip install --system --torch-backend "${TORCH_BACKEND}" -r requirements.txt

COPY voxcpm_server ./voxcpm_server
COPY web ./web
COPY voices ./voices
COPY scripts ./scripts
COPY integrations ./integrations

RUN useradd --create-home --uid 1000 vox && mkdir -p /app/models /app/data && chown -R vox /app/models /app/data
USER vox
VOLUME ["/app/models", "/app/data"]
EXPOSE 8808

HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8808/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "voxcpm_server", "--no-open"]
