FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    YTSC_API_HOST=0.0.0.0 \
    PORT=8787 \
    YTSC_CONFIG_FILE=/data/config.json \
    YTSC_OUTPUT_DIR=/data/output

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements_api.txt ./
RUN python -m pip install --upgrade pip \
    && pip install -r requirements_api.txt

COPY api ./api
COPY config ./config
COPY utils ./utils
COPY clipper_core.py ./
COPY api_server.py ./
COPY version.py ./

RUN mkdir -p /data/output

EXPOSE 8787
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 CMD \
  python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8787/health', timeout=5)"

CMD ["python", "api_server.py"]
