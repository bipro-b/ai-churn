"""
FastAPI serving application for the churn model.

Senior-engineer notes baked into this file:
- Lifespan loads the model ONCE at startup, not per-request. Loading a model
  per request is the #1 latency killer in junior ML services.
- /health/live vs /health/ready are distinct on purpose. Kubernetes uses
  liveness to decide "restart this pod" and readiness to decide "send traffic".
  A pod can be alive but not ready (model still loading) — conflating them
  causes traffic to hit pods that 500.
- Prometheus metrics are first-class. "Is it up?" is answered by health checks;
  "is it healthy?" is answered by latency/error/throughput metrics. You need both.
- Every prediction's inputs+output are logged as structured JSON. This log is
  the raw material for drift detection and audit. In regulated industries this
  log is a legal requirement.
"""

import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.config import settings
from app.ml.model import ChurnModel
from app.schemas import (
    BatchPredictionRequest,
    CustomerFeatures,
    HealthResponse,
    PredictionResponse,
)

# Structured JSON logging — parseable by CloudWatch, Loki, Datadog, etc.
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("churn-api")

# --- Prometheus metrics -----------------------------------------------------
PREDICTIONS_TOTAL = Counter(
    "churn_predictions_total", "Total predictions served", ["risk_band"]
)
PREDICTION_LATENCY = Histogram(
    "churn_prediction_latency_seconds", "Prediction latency in seconds",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)
REQUEST_ERRORS = Counter(
    "churn_request_errors_total", "Total request errors", ["endpoint"]
)
# Drift signal: track the mean predicted probability over time. If this shifts
# meaningfully vs the training-time churn rate, your input distribution moved.
PREDICTED_PROBABILITY = Histogram(
    "churn_predicted_probability", "Distribution of predicted churn probabilities",
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

model = ChurnModel(model_path=settings.model_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(json.dumps({"event": "startup", "msg": "loading model"}))
    model.load()
    logger.info(json.dumps({"event": "startup_complete", "version": model.version}))
    yield
    logger.info(json.dumps({"event": "shutdown"}))


app = FastAPI(
    title="Churn Prediction API",
    version="1.0.0",
    description="Production-grade churn scoring service.",
    lifespan=lifespan,
)


def _log_prediction(features: dict, result: dict) -> None:
    """Structured prediction log — the substrate for drift monitoring + audit."""
    logger.info(json.dumps({
        "event": "prediction",
        "features": features,
        "churn_probability": result["churn_probability"],
        "risk_band": result["risk_band"],
        "model_version": result["model_version"],
    }))


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def liveness() -> HealthResponse:
    # Liveness: is the process running at all? Don't depend on the model here.
    return HealthResponse(status="alive", model_loaded=model.is_loaded,
                          model_version=model.version)


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def readiness() -> HealthResponse:
    # Readiness: can we actually serve traffic? Requires a loaded model.
    if not model.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return HealthResponse(status="ready", model_loaded=True,
                          model_version=model.version)


@app.get("/metrics", tags=["monitoring"])
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/predict", response_model=PredictionResponse, tags=["inference"])
def predict(features: CustomerFeatures) -> PredictionResponse:
    if not model.is_loaded:
        REQUEST_ERRORS.labels(endpoint="/predict").inc()
        raise HTTPException(status_code=503, detail="Model not loaded")

    start = time.perf_counter()
    try:
        payload = features.model_dump()
        # Enums serialize to their .value strings, which the model expects.
        payload = {k: (v.value if hasattr(v, "value") else v) for k, v in payload.items()}
        result = model.predict_one(payload)
    except Exception as exc:  # noqa: BLE001 - surface as 500 + metric
        REQUEST_ERRORS.labels(endpoint="/predict").inc()
        logger.error(json.dumps({"event": "prediction_error", "error": str(exc)}))
        raise HTTPException(status_code=500, detail="Prediction failed") from exc

    PREDICTION_LATENCY.observe(time.perf_counter() - start)
    PREDICTIONS_TOTAL.labels(risk_band=result["risk_band"]).inc()
    PREDICTED_PROBABILITY.observe(result["churn_probability"])
    _log_prediction(payload, result)
    return PredictionResponse(**result)


@app.post("/predict/batch", tags=["inference"])
def predict_batch(req: BatchPredictionRequest) -> dict:
    if not model.is_loaded:
        REQUEST_ERRORS.labels(endpoint="/predict/batch").inc()
        raise HTTPException(status_code=503, detail="Model not loaded")

    start = time.perf_counter()
    rows = []
    for c in req.customers:
        d = c.model_dump()
        rows.append({k: (v.value if hasattr(v, "value") else v) for k, v in d.items()})

    try:
        results = model.predict_batch(rows)
    except Exception as exc:  # noqa: BLE001
        REQUEST_ERRORS.labels(endpoint="/predict/batch").inc()
        raise HTTPException(status_code=500, detail="Batch prediction failed") from exc

    PREDICTION_LATENCY.observe(time.perf_counter() - start)
    for r in results:
        PREDICTIONS_TOTAL.labels(risk_band=r["risk_band"]).inc()
        PREDICTED_PROBABILITY.observe(r["churn_probability"])

    return {"predictions": results, "count": len(results)}


@app.get("/", tags=["meta"])
def root() -> dict:
    return {"service": "churn-prediction", "version": "1.0.0", "docs": "/docs"}
