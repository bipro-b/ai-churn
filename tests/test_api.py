"""
Test suite.

Senior note: these tests run in CI BEFORE any image is built or deployed. A
broken model or a broken contract never reaches AWS. We test three layers:
1. The model wrapper produces valid output shapes.
2. The API validates input (rejects garbage with 422).
3. The API serves correct predictions end-to-end (with a real loaded model).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure a model exists before tests run (train a tiny one if missing).
MODEL_PATH = Path("app/ml/model.joblib")


@pytest.fixture(scope="session", autouse=True)
def ensure_model():
    if not MODEL_PATH.exists():
        subprocess.run(
            [sys.executable, "training/train.py", "--n-samples", "2000"],
            check=True,
        )
    yield


@pytest.fixture(scope="session")
def client(ensure_model):
    from app.main import app
    with TestClient(app) as c:  # triggers lifespan -> model load
        yield c


VALID_PAYLOAD = {
    "tenure_months": 3,
    "monthly_charges": 95.5,
    "total_charges": 286.5,
    "num_support_tickets": 4,
    "contract_type": "month-to-month",
    "payment_method": "electronic-check",
    "internet_service": "fiber",
}


def test_liveness(client):
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def test_readiness(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["model_loaded"] is True


def test_predict_valid(client):
    r = client.post("/predict", json=VALID_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert 0.0 <= body["churn_probability"] <= 1.0
    assert body["risk_band"] in {"low", "medium", "high"}
    assert isinstance(body["churn_prediction"], bool)


def test_predict_rejects_bad_enum(client):
    bad = dict(VALID_PAYLOAD, contract_type="forever")
    r = client.post("/predict", json=bad)
    assert r.status_code == 422  # validation error, not a 500


def test_predict_rejects_out_of_range(client):
    bad = dict(VALID_PAYLOAD, tenure_months=-5)
    r = client.post("/predict", json=bad)
    assert r.status_code == 422


def test_batch(client):
    r = client.post("/predict/batch", json={"customers": [VALID_PAYLOAD, VALID_PAYLOAD]})
    assert r.status_code == 200
    assert r.json()["count"] == 2


def test_metrics_exposed(client):
    client.post("/predict", json=VALID_PAYLOAD)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "churn_predictions_total" in r.text


def test_high_risk_signal(client):
    """A month-to-month fiber customer with many tickets and low tenure should
    score higher than a loyal two-year customer. Tests that the model learned signal."""
    high = client.post("/predict", json=VALID_PAYLOAD).json()["churn_probability"]
    loyal = dict(
        VALID_PAYLOAD, tenure_months=60, contract_type="two-year",
        num_support_tickets=0, internet_service="dsl",
        payment_method="credit-card",
    )
    low = client.post("/predict", json=loyal).json()["churn_probability"]
    assert high > low
