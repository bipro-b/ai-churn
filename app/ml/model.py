"""
Model wrapper.

Senior note: never let your web framework touch the model object directly.
Wrap it. This gives you one place to handle loading, versioning, warm-up, and
the prediction contract. When you later swap GradientBoosting for XGBoost or a
neural net, only this file changes — the API surface stays identical.
"""

import os
from pathlib import Path

import joblib
import pandas as pd

FEATURE_COLUMNS = [
    "tenure_months", "monthly_charges", "total_charges", "num_support_tickets",
    "contract_type", "payment_method", "internet_service",
]


class ChurnModel:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.model = None
        # Version pulled from env (set at build time from git SHA) or defaults.
        self.version = os.getenv("MODEL_VERSION", "dev-local")

    def load(self) -> None:
        path = Path(self.model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Model artifact not found at {path}. "
                f"Run `python training/train.py` first."
            )
        self.model = joblib.load(path)

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    @staticmethod
    def _risk_band(prob: float) -> str:
        if prob < 0.3:
            return "low"
        if prob < 0.6:
            return "medium"
        return "high"

    def predict_one(self, features: dict) -> dict:
        df = pd.DataFrame([features], columns=FEATURE_COLUMNS)
        prob = float(self.model.predict_proba(df)[:, 1][0])
        return {
            "churn_probability": round(prob, 4),
            "churn_prediction": prob >= 0.5,
            "risk_band": self._risk_band(prob),
            "model_version": self.version,
        }

    def predict_batch(self, rows: list[dict]) -> list[dict]:
        df = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
        probs = self.model.predict_proba(df)[:, 1]
        return [
            {
                "churn_probability": round(float(p), 4),
                "churn_prediction": bool(p >= 0.5),
                "risk_band": self._risk_band(float(p)),
                "model_version": self.version,
            }
            for p in probs
        ]
