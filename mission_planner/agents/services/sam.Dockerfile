FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_RETRIES=0 HF_HUB_OFFLINE=1 TORCHINDUCTOR_COMPILE_THREADS=4 \
    TORCHINDUCTOR_CACHE_DIR=/cache/inductor SNR_SAM_CHECKPOINT=/models/sam3.1_multiplex.pt \
    SNR_SAM_WARMUP=/models/warmup/protocol.json SNR_VISION_SOCKET=/run/snr/vision.sock

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY mission_planner/agents/services/vision-requirements.txt /opt/sam-requirements.txt
RUN python -m pip install --no-cache-dir -r /opt/sam-requirements.txt

ENV TRITON_CACHE_DIR=/cache/triton CUDA_CACHE_PATH=/cache/cuda
RUN groupadd --gid 1003 sam && useradd --no-log-init --uid 1002 --gid 1003 --create-home sam
WORKDIR /app
COPY mission_planner/__init__.py mission_planner/environment.py /app/mission_planner/
COPY mission_planner/agents/__init__.py /app/mission_planner/agents/
COPY mission_planner/agents/services/__init__.py /app/mission_planner/agents/services/
COPY mission_planner/agents/services/sam_video.py mission_planner/agents/services/vision.py /app/mission_planner/agents/services/
USER 1002:1003
ENTRYPOINT ["python", "-m", "mission_planner.agents.services.vision"]
