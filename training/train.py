"""
Training pipeline for the churn prediction model.

Senior-engineer notes:
- We generate synthetic data here so the project is fully reproducible with no
  external dataset dependency. In a real job you'd pull from a feature store /
  data warehouse, but the *structure* (load -> split -> train -> evaluate ->
  register) is identical.
- We log everything to MLflow so every model is versioned and comparable.
  "I trained a model" is amateur; "model v7, AUC 0.91, registered in the
  registry, reproducible from commit abc123" is production.
- The trained model + the preprocessing live together in one sklearn Pipeline.
  This is the single most common production bug source: training-serving skew,
  where preprocessing differs between training and inference. One Pipeline = one
  artifact = no skew.
"""

import argparse
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

# MLflow is optional at runtime: if it's not configured we still produce the
# model artifact, so the pipeline never hard-fails in a fresh environment.
try:
    import mlflow
    import mlflow.sklearn
    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False


NUMERIC_FEATURES = ["tenure_months", "monthly_charges", "total_charges", "num_support_tickets"]
CATEGORICAL_FEATURES = ["contract_type", "payment_method", "internet_service"]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES


def generate_synthetic_data(n: int = 20_000, seed: int = 42) -> pd.DataFrame:
    """Create a realistic-looking churn dataset with signal the model can learn."""
    rng = np.random.default_rng(seed)

    tenure = rng.integers(0, 72, size=n)
    monthly = rng.normal(70, 30, size=n).clip(15, 150)
    total = (monthly * tenure * rng.uniform(0.8, 1.1, size=n)).clip(0)
    tickets = rng.poisson(1.5, size=n)

    contract = rng.choice(["month-to-month", "one-year", "two-year"], size=n, p=[0.55, 0.25, 0.20])
    payment = rng.choice(["electronic-check", "mailed-check", "bank-transfer", "credit-card"], size=n)
    internet = rng.choice(["dsl", "fiber", "none"], size=n, p=[0.35, 0.45, 0.20])

    # Build a churn probability with real, learnable structure.
    logit = (
        -1.0
        + 1.4 * (contract == "month-to-month")
        - 0.9 * (contract == "two-year")
        + 0.04 * tickets
        - 0.03 * tenure
        + 0.012 * (monthly - 70)
        + 0.6 * (internet == "fiber")          # fiber customers churn more (price sensitivity)
        + 0.5 * (payment == "electronic-check")
    )
    prob = 1 / (1 + np.exp(-logit))
    churn = rng.binomial(1, prob)

    return pd.DataFrame({
        "tenure_months": tenure,
        "monthly_charges": monthly.round(2),
        "total_charges": total.round(2),
        "num_support_tickets": tickets,
        "contract_type": contract,
        "payment_method": payment,
        "internet_service": internet,
        "churned": churn,
    })


def build_pipeline() -> Pipeline:
    """Preprocessing + model in a single artifact to prevent train/serve skew."""
    preprocessor = ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), NUMERIC_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ]
    )
    model = GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
    )
    return Pipeline([("preprocess", preprocessor), ("model", model)])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="app/ml/model.joblib",
                        help="Where to write the trained pipeline artifact.")
    parser.add_argument("--n-samples", type=int, default=20_000)
    parser.add_argument("--experiment", default="churn-prediction")
    args = parser.parse_args()

    df = generate_synthetic_data(n=args.n_samples)
    X = df[FEATURE_COLUMNS]
    y = df["churned"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    pipeline = build_pipeline()

    if MLFLOW_AVAILABLE and os.getenv("MLFLOW_TRACKING_URI"):
        mlflow.set_experiment(args.experiment)
        run_ctx = mlflow.start_run()
    else:
        run_ctx = None
        if not MLFLOW_AVAILABLE:
            print("[warn] mlflow not installed; training without experiment tracking.")
        else:
            print("[info] MLFLOW_TRACKING_URI not set; training without tracking.")

    pipeline.fit(X_train, y_train)

    proba = pipeline.predict_proba(X_test)[:, 1]
    preds = (proba >= 0.5).astype(int)
    metrics = {
        "roc_auc": float(roc_auc_score(y_test, proba)),
        "pr_auc": float(average_precision_score(y_test, proba)),
        "f1": float(f1_score(y_test, preds)),
        "churn_rate": float(y.mean()),
    }
    print("Evaluation metrics:")
    for k, v in metrics.items():
        print(f"  {k:12s}: {v:.4f}")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, out)
    print(f"Saved pipeline -> {out}")

    if run_ctx is not None:
        mlflow.log_params({
            "n_samples": args.n_samples,
            "model": "GradientBoostingClassifier",
            "n_estimators": 200,
            "max_depth": 3,
        })
        mlflow.log_metrics(metrics)
        mlflow.sklearn.log_model(pipeline, artifact_path="model",
                                 registered_model_name="churn-predictor")
        mlflow.end_run()
        print("Logged run + registered model in MLflow.")


if __name__ == "__main__":
    main()
