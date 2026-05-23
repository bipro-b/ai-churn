# AIOps Churn Prediction Service

A production-grade machine learning deployment project: a churn-prediction REST API, containerized, deployed to AWS with infrastructure as code, automated CI/CD, and ML observability.

This repo is built to teach **production MLOps end to end**. Start with [`RUNBOOK.md`](./RUNBOOK.md) — it walks you from your laptop to a live API on AWS, step by step, explaining the senior-engineer reasoning at each stage.

## Architecture

```
                        ┌──────────────┐
   git push main ──────▶│GitHub Actions│  test → build → push → deploy
                        └──────┬───────┘
                               │ (OIDC, no static keys)
                               ▼
                        ┌──────────────┐      ┌─────────────┐
                        │     ECR      │◀─────│   Docker    │ multi-stage,
                        │ (image repo) │      │   image     │ non-root, slim
                        └──────┬───────┘      └─────────────┘
                               │ pull
                               ▼
   Internet ──▶ ALB ──▶ ECS Fargate Service (autoscaled) ──▶ FastAPI app
                 │              │                                  │
            health checks   CloudWatch logs+metrics         /metrics (Prometheus)
            /health/ready                                   /predict, /predict/batch
                                                                   │
                                                            drift_detection.py (PSI)
```

## What's inside

| Path | What it is |
|------|-----------|
| `app/` | FastAPI service: schemas, config, model wrapper, metrics, health checks |
| `training/train.py` | Reproducible training pipeline (sklearn Pipeline + MLflow logging) |
| `tests/` | Pytest suite — runs in CI before any deploy |
| `Dockerfile` | Multi-stage, non-root, healthchecked production image |
| `docker-compose.yml` | Local stack: API + Prometheus + Grafana |
| `infra/terraform/` | AWS infra as code: ECR, ALB, ECS Fargate, IAM, autoscaling, logs |
| `infra/k8s/` | Kubernetes manifests (optional EKS track) |
| `.github/workflows/cicd.yml` | Test-gated CI/CD pipeline with OIDC auth |
| `monitoring/` | Prometheus config + PSI-based data-drift detector |
| `scripts/load_test.py` | Traffic generator for observing metrics and autoscaling |
| `RUNBOOK.md` | **The step-by-step deployment guide — start here** |

## Quick start (local)

```bash
make install    # dependencies
make train      # train the model
make test       # run tests
make run        # serve at http://localhost:8000  (docs at /docs)
```

Then follow [`RUNBOOK.md`](./RUNBOOK.md) to deploy to AWS.

## The model

Predicts customer churn from 7 features (tenure, charges, contract type, support tickets, etc.). Uses synthetic-but-realistic data so the project is fully reproducible with no external dataset. The patterns — train → version → containerize → deploy → monitor → detect drift → retrain — transfer directly to any model type, including LLMs and computer vision.

## Cost

Track A (Fargate) runs roughly $15–35/month while up. Everything is destroyable with `terraform destroy`. Set a billing alert and tear down between sessions.
```
