FROM python:3.10.13-slim

ENV PYTHONUNBUFFERED=1
ENV POETRY_REQUESTS_TIMEOUT=120

# System dependencies needed by voiceai's audio/ASR/TTS extras
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libgomp1 \
    ffmpeg \
    curl \
    gcc \
    g++ \
    python3-dev \
    build-essential && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

EXPOSE 5001 8001 8002

# Greenfield liveness: the thin entry serves every module, so the probe hits the
# dependency-free `/api/v1/health/live` (T7 cutover flipped the entry here).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:5001/api/v1/health/live || exit 1

WORKDIR /voiceai

# docker/pyproject.toml + docker/poetry.lock mirror requirements.txt and are
# used only to install deps in the image — voiceai's own packaging (published
# to PyPI) stays on setuptools/requirements.txt, untouched by this.
COPY docker/pyproject.toml docker/poetry.lock ./

RUN --mount=type=cache,target=/root/.cache/pypoetry \
    pip install --no-cache-dir --default-timeout=120 --retries=5 poetry==2.3.3 \
    && poetry config virtualenvs.create false \
    && poetry install --no-root \
    && rm -f pyproject.toml poetry.lock

# Now copy the rest of the code and install voiceai itself from local source
COPY . ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps .

CMD ["uvicorn", "voiceai.app:app", "--host", "0.0.0.0", "--port", "5001"]
