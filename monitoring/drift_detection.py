"""
Data drift detection.

THIS is the file that makes recruiters take you seriously. Anyone can deploy a
model. Operating one means knowing WHEN it's silently rotting.

The world changes. The data your model sees in production drifts away from what
it trained on (new pricing, new customer mix, seasonality). The model's accuracy
quietly degrades while it keeps returning confident answers. Drift detection is
your early-warning system.

Method here: Population Stability Index (PSI), the industry-standard drift metric
in finance/telco. We compare the distribution of a feature (or the predicted
probability) in production vs a training-time baseline.

  PSI < 0.1  -> no significant shift
  0.1 - 0.25 -> moderate shift, investigate
  PSI > 0.25 -> major shift, model likely needs retraining

Run this on a schedule (cron / Airflow / EventBridge) against your prediction
logs. When PSI breaches threshold, alert + trigger retraining.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def population_stability_index(expected: np.ndarray, actual: np.ndarray,
                               bins: int = 10) -> float:
    """PSI between a baseline (expected) and a live (actual) distribution."""
    # Build bin edges from the baseline so both are bucketed identically.
    quantiles = np.linspace(0, 1, bins + 1)
    edges = np.quantile(expected, quantiles)
    edges[0], edges[-1] = -np.inf, np.inf  # capture tails

    exp_counts, _ = np.histogram(expected, bins=edges)
    act_counts, _ = np.histogram(actual, bins=edges)

    exp_pct = np.clip(exp_counts / exp_counts.sum(), 1e-6, None)
    act_pct = np.clip(act_counts / act_counts.sum(), 1e-6, None)

    return float(np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct)))


def load_predictions_from_logs(log_path: str) -> pd.DataFrame:
    """Parse the structured JSON prediction logs the API emits."""
    records = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "prediction":
                flat = dict(obj.get("features", {}))
                flat["churn_probability"] = obj.get("churn_probability")
                records.append(flat)
    return pd.DataFrame(records)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True,
                        help="CSV of training-time feature distribution.")
    parser.add_argument("--logs", required=True,
                        help="Path to the API's JSON prediction log file.")
    parser.add_argument("--threshold", type=float, default=0.25)
    args = parser.parse_args()

    baseline = pd.read_csv(args.baseline)
    live = load_predictions_from_logs(args.logs)

    if live.empty:
        print("No prediction records found in logs. Nothing to check.")
        return

    numeric_features = ["tenure_months", "monthly_charges", "total_charges",
                        "num_support_tickets", "churn_probability"]

    print(f"{'feature':22s} {'PSI':>8s}   status")
    print("-" * 48)
    breached = []
    for feat in numeric_features:
        if feat not in baseline.columns or feat not in live.columns:
            continue
        psi = population_stability_index(
            baseline[feat].dropna().values, live[feat].dropna().values
        )
        if psi > args.threshold:
            status, flag = "MAJOR DRIFT", True
        elif psi > 0.1:
            status, flag = "moderate", False
        else:
            status, flag = "ok", False
        if flag:
            breached.append(feat)
        print(f"{feat:22s} {psi:8.4f}   {status}")

    print("-" * 48)
    if breached:
        print(f"\n[ALERT] Drift threshold breached on: {', '.join(breached)}")
        print("Action: investigate input changes and consider retraining.")
        raise SystemExit(1)  # non-zero exit so a scheduler can alert on it
    print("\nNo major drift detected.")


if __name__ == "__main__":
    main()
