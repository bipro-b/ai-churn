# syntax=docker/dockerfile:1
#
# Multi-stage build. Senior reasoning:
# - Stage 1 (builder) installs deps into a venv. Build tools stay here and never
#   reach the final image, keeping it small and reducing attack surface.
# - Stage 2 (runtime) copies only the venv + app code. No compilers, no caches.
# - We run as a NON-ROOT user. A container running as root is a security finding
#   in any real review. If the app is compromised, the blast radius is limited.
# - We pin the base image to a specific slim tag for reproducibility.

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Create a virtualenv we can copy wholesale into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    MODEL_PATH=app/ml/model.joblib

# Non-root user.
RUN groupadd --system app && useradd --system --gid app --no-create-home appuser

WORKDIR /app

# Copy the prebuilt venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy application code AND the trained model artifact.
# (The model is baked into the image here for simplicity. The runbook explains
#  the production alternative: pull from S3 / MLflow registry at startup.)
COPY app/ ./app/
COPY training/ ./training/

# Build-time arg lets CI stamp the git SHA as the model/app version.
ARG MODEL_VERSION=dev
ENV MODEL_VERSION=${MODEL_VERSION}

USER appuser

EXPOSE 8000

# Container-level healthcheck (works for docker run / compose; K8s uses its own probes).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health/ready').status==200 else sys.exit(1)" \
        || exit 1

# 2 workers is a reasonable default for a small instance. Tune based on CPU.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
