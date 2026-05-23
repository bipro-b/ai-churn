# AIOps Churn Service — Deployment Runbook (Battle-Tested Edition)

> This is the **real** version — every command here is one I actually ran to take this
> service from an empty machine to a live API on AWS, including the fixes for the
> problems that hit along the way (venvs, expired tokens, lost shell variables,
> WSL/Windows quirks). Tutorials hide this stuff. This runbook doesn't.
>
> Environment I deployed from: **Windows + Git Bash for local dev, WSL/Ubuntu for the AWS deploy.**
> Where the two differ, both are shown.

---

## The whole flow at a glance

```
Phase 0  Run locally (venv → train → test → serve)
Phase 1  Containerize + local monitoring stack (Docker, Prometheus, Grafana)
Phase 2  Provision AWS infra (Terraform)
Phase 3  Build → push → deploy the image (manual, once)
Phase 4  Automate with CI/CD (GitHub Actions) [next]
Phase 5  Operate it: monitoring + drift detection [next]
TEARDOWN terraform destroy  ← ALWAYS run when done (stops the billing)
```

The deploy loop you now know by heart:
**`terraform apply` → `docker login` → `build` → `push` → `update-service` → `wait`.**

---

## Prerequisites (one-time)

- **Python 3.12** — *not* 3.13/3.14. The pinned ML packages (scikit-learn 1.6 etc.) have no
  wheels for 3.14, so pip tries to compile from source and fails on Windows. Match the
  Dockerfile's Python (3.12).
  - Install on Windows: `winget install Python.Python.3.12` (or `py install 3.12`)
- **Docker Desktop** — must be running. For WSL: Settings → Resources → WSL Integration →
  toggle on your Ubuntu distro, or Docker isn't reachable from the Linux shell.
- **AWS CLI v2** — `aws --version`
- **Terraform ≥ 1.6** — `terraform --version`
- **An AWS account** with a billing alert (Console → Billing → Budgets → $20 alert). Do this first.

```bash
aws configure                 # access key, secret, region us-east-1, output json
aws sts get-caller-identity   # must print your account ID
```

> **💸 COST DISCIPLINE — the most important habit.** The ALB + Fargate task bill by the hour
> (~$15–35/mo equivalent while running). An idle load balancer alone is ~$16/mo. **Run
> `terraform destroy` every time you finish a session.** ECR image storage persists for pennies.

---

## ⚠️ Two gotchas that will bite you repeatedly — read first

**1. Shell variables vanish between terminals.** `export FOO=bar` only lives in the shell that
ran it. Open a new terminal, switch from Windows to WSL, or sometimes just continue later →
it's gone. Symptom: a `$VARIABLE` command fails with "expected one argument" or behaves oddly.
**First diagnostic move: `echo $VARIABLE`.** Empty = re-set it.

Fix once and for all — make an env file and `source` it whenever variables are empty:

```bash
cat > setenv.sh << 'EOF'
export AWS_REGION=us-east-1
export ECR_URL=023048164234.dkr.ecr.us-east-1.amazonaws.com/churn-api
EOF

source setenv.sh     # loads them into the CURRENT shell. (./setenv.sh does NOT work — sub-shell.)
echo $ECR_URL        # confirm
```
(Replace the account ID with yours. Add `setenv.sh` to `.gitignore`.)

**2. The venv prefix is your "am I in the right Python?" indicator.** If `(.venv)` /
`(.venv-wsl)` is NOT at the start of your prompt, you're on system Python and your installed
packages are missing. Symptom: `ModuleNotFoundError` for something you know you installed.
Fix: reactivate the venv.

---

## Phase 0 — Run it locally

`make` isn't on Windows by default, so use the raw commands the Makefile wraps.

### Windows / Git Bash
```bash
cd /d/0.dev-bipro/ai-engineering/aiops-churn

py -3.12 -m venv .venv               # if "No runtime matches 3.12" → run: py install 3.12
source .venv/Scripts/activate        # WINDOWS path is Scripts/, not bin/
python --version                     # confirm 3.12.x
python -m pip install --upgrade pip  # use `python -m pip`, NOT bare `pip` (avoids self-update error)
python -m pip install -r requirements.txt -r requirements-dev.txt
```

### Train, test, serve
```bash
python training/train.py --n-samples 20000   # → app/ml/model.joblib + metrics block
pytest -v                                     # all tests must pass
uvicorn app.main:app --reload --port 8000     # leave running
```

### Test in a SECOND terminal (activate the venv there too)
```bash
curl http://localhost:8000/health/ready
# {"status":"ready","model_loaded":true,"model_version":"dev-local"}

curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"tenure_months":3,"monthly_charges":95.5,"total_charges":286.5,"num_support_tickets":4,"contract_type":"month-to-month","payment_method":"electronic-check","internet_service":"fiber"}'
# {"churn_probability":0.83...,"risk_band":"high",...}
```

Open **http://localhost:8000/docs** for the interactive API docs. `Ctrl+C` to stop the server.

✅ **Checkpoint:** tests pass, `/predict` returns a sensible probability, `/docs` loads.

---

## Phase 1 — Containerize + local monitoring stack

Docker Desktop must be running.

```bash
docker build --build-arg MODEL_VERSION=local -t churn-api:local .
docker run --rm -p 8000:8000 churn-api:local      # leave running; test in 2nd terminal
```

Verify it works and runs as non-root (a real security check):
```bash
curl http://localhost:8000/health/ready
docker run --rm churn-api:local whoami            # must print "appuser", NOT root
docker images churn-api:local                     # see the image size
```

> **GOTCHA — "port is already allocated".** Means something still holds port 8000 (usually the
> previous `docker run`). Fix:
> ```bash
> docker ps                       # find the container on 0.0.0.0:8000
> docker stop <container-id>      # stop it
> # if a non-docker process holds it: netstat -ano | grep :8000  → taskkill /PID <pid> /F
> ```

Stop the container (`Ctrl+C`), then bring up the full stack (API + Prometheus + Grafana):
```bash
docker compose up --build         # leave running
```

Generate traffic (2nd terminal):
```bash
python scripts/load_test.py http://localhost:8000 --requests 300 --concurrency 10
```

- **Prometheus** → http://localhost:9090 — query `churn_predictions_total`,
  `rate(churn_prediction_latency_seconds_count[1m])`
- **Grafana** → http://localhost:3000

> **GOTCHA — Grafana can't reach Prometheus ("connection refused" on localhost:9090).**
> Inside Docker, `localhost` means the *Grafana container itself*, not your machine. Containers
> reach each other by **service name**. In the Prometheus data-source URL, use:
> ```
> http://prometheus:9090
> ```
> Then **Save & test** → green. (Same idea in the cloud: services find each other by name, never localhost.)

**Build a Grafana panel:** Dashboards → New → New dashboard → **+ Add visualization** → pick
**Prometheus** → in the Queries tab enter `rate(churn_prediction_latency_seconds_count[1m])` →
choose **Time series** → Save. (Dashboards in the container are throwaway on `compose down`;
in production you'd save them as JSON in the repo.)

When done:
```bash
docker compose down
```

✅ **Checkpoint:** container runs as `appuser`; Prometheus + Grafana show your metrics under load.

---

## Phase 2 — Provision AWS infrastructure (Terraform)

```bash
cd infra/terraform
terraform init
terraform plan          # READ this. "Plan: N to add, 0 to change, 0 to destroy" is what you want.
terraform apply         # type "yes"  (ALB takes 3–5 min to provision)
```

> **What to check in the plan (senior habit):** all `+ create`, no surprise `destroy` lines.
> Confirm the task security group only accepts port 8000 **from the ALB security group** (not
> the internet) — that's the least-privilege network design.

Capture outputs:
```bash
terraform output
terraform output -raw ecr_repository_url    # your ECR URL
terraform output -raw api_url               # your public ALB URL
```

> **💸 The meter starts NOW.** From a successful apply, the ALB is billing hourly.

> **EXPECTED: hitting the api_url now returns `503 Service Temporarily Unavailable`.** That is
> NOT a bug. The infra is up but ECR is empty, so there's no task to serve. Phase 3 fixes it.

✅ **Checkpoint:** apply succeeds; `terraform output` prints `ecr_repository_url` and `api_url`.

---

## Phase 3 — Build, push, deploy (manual, once)

> Did the AWS deploy from **WSL/Ubuntu**. Key WSL notes are inline. Run everything from the
> **project root** (`cd /mnt/d/0.dev-bipro/ai-engineering/aiops-churn`), not a sub-folder.

### 3.0 — WSL: make a Linux venv (the Windows .venv does NOT work in WSL)
```bash
# venv module may be missing on fresh Ubuntu:
sudo apt update && sudo apt install -y python3.12-venv

rm -rf .venv-wsl                       # remove any half-made one
python3 -m venv .venv-wsl
source .venv-wsl/bin/activate          # LINUX path is bin/, not Scripts/
```

> **GOTCHA — `externally-managed-environment` error on `pip install` (PEP 668).** Ubuntu
> protects system Python. Even inside the venv on the `/mnt/d` (Windows) drive, `pip` can point
> at system pip (`which pip` → `/usr/bin/pip`). **Fix: call pip through the venv's python:**
> ```bash
> python -m pip install -r requirements.txt
> ```
> Last-resort override (you're in a venv, so low risk): add `--break-system-packages`.

```bash
python -m pip install -r requirements.txt
.venv-wsl/bin/python training/train.py --n-samples 20000   # full path = guaranteed right Python
# → metrics block + "Saved pipeline -> app/ml/model.joblib"
```

> **GOTCHA — `python: command not found` or `ModuleNotFoundError: joblib` in WSL.** The venv
> deactivated (no `(.venv-wsl)` in prompt) OR Ubuntu only has `python3`. Reactivate
> (`source .venv-wsl/bin/activate`) and/or use the full path `.venv-wsl/bin/python`.

### 3.1 — Set variables (or `source setenv.sh`)
```bash
export AWS_REGION=us-east-1
export ECR_URL=$(cd infra/terraform && terraform output -raw ecr_repository_url)
echo $AWS_REGION && echo $ECR_URL      # BOTH must print real values. ECR must end in /churn-api
```

### 3.2 — Authenticate Docker to ECR
```bash
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_URL
# → "Login Succeeded"
```
> The username is the literal word **`AWS`** — NOT your Docker Hub username. The password is a
> temporary token minted on the fly.

### 3.3 — Build (from project root — confirm with `ls` that the Dockerfile is here)
```bash
docker build --build-arg MODEL_VERSION=v1 -t $ECR_URL:v1 -t $ECR_URL:latest .
```
> **GOTCHA — "failed to read dockerfile: no such file or directory".** You're in the wrong
> directory. `docker build .` needs the Dockerfile in your current folder. A `cd infra/terraform
> && ... && cd ..` chain leaves you in `infra/`, not root. Jump straight to root:
> `cd /mnt/d/0.dev-bipro/ai-engineering/aiops-churn`.

### 3.4 — Push
```bash
docker push $ECR_URL:v1
docker push $ECR_URL:latest          # fast — reuses v1's layers. Ends with "digest: sha256:..."
```
> **GOTCHA — "denied: Your authorization token has expired."** ECR tokens last ~12 hours.
> Just re-run the login from 3.2 and push again. (CI/CD in Phase 4 makes this automatic.)

### 3.5 — Deploy + wait
```bash
aws ecs update-service --cluster churn-api-cluster --service churn-api-service --force-new-deployment --no-cli-pager
aws ecs wait services-stable --cluster churn-api-cluster --services churn-api-service
```
> `wait` sits silently 3–5 min while the task starts, passes `/health/ready`, and registers
> healthy with the ALB. That silence is normal.
> **Tip:** add `--no-cli-pager` to any `aws` command, or long JSON output drops you into a
> pager (press `q` to exit it).

### 3.6 — Test the LIVE API
```bash
export API_URL=$(cd infra/terraform && terraform output -raw api_url)
curl $API_URL/health/ready
# {"status":"ready","model_loaded":true,"model_version":"latest"}

curl -X POST $API_URL/predict -H "Content-Type: application/json" \
  -d '{"tenure_months":3,"monthly_charges":95.5,"total_charges":286.5,"num_support_tickets":4,"contract_type":"month-to-month","payment_method":"electronic-check","internet_service":"fiber"}'
# {"churn_probability":0.8312,"churn_prediction":true,"risk_band":"high","model_version":"latest"}
```

✅ **Checkpoint:** a live prediction from a public URL. **Production-grade ML, deployed. Screenshot it.**

> **If still 503 / tasks won't start, debug with:**
> ```bash
> aws ecs describe-services --cluster churn-api-cluster --services churn-api-service \
>   --query "services[0].events[:5]" --no-cli-pager
> aws logs tail /ecs/churn-api --follow
> ```

---

## TEARDOWN — run when done each session 💸

```bash
cd infra/terraform
terraform destroy        # type "yes" — removes ALB, ECS, IAM, log group (stops hourly billing)
```

**Your ECR images persist** (storage is pennies) — so next time you skip rebuild/push if code
is unchanged and go straight: `terraform apply` → re-login → `update-service` → `wait`.

Check / manage kept images:
```bash
aws ecr list-images --repository-name churn-api --region $AWS_REGION
```
(The Terraform lifecycle policy keeps the **last 10 images**; older ones auto-expire.)

Want a permanent offline copy of an image on your laptop?
```bash
docker save $ECR_URL:v1 -o churn-api-v1.tar     # reload later: docker load -i churn-api-v1.tar
```

---

## Re-deploy later (the fast path, now that images exist)

```bash
source setenv.sh                                 # restore AWS_REGION + ECR_URL
cd infra/terraform && terraform apply && cd ..   # rebuild the infra
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_URL
aws ecs update-service --cluster churn-api-cluster --service churn-api-service --force-new-deployment --no-cli-pager
aws ecs wait services-stable --cluster churn-api-cluster --services churn-api-service
curl $(cd infra/terraform && terraform output -raw api_url)/health/ready
```
Only rebuild + push if you changed the code:
`docker build ... && docker push $ECR_URL:v1 && docker push $ECR_URL:latest` before update-service.

---

## Phase 4 — CI/CD with GitHub Actions  *(next milestone)*

**WHY:** makes the whole Phase 3 sequence automatic and test-gated on every `git push` — and
OIDC auth means **no more expired-token re-logins or manual variable juggling.**

One-time AWS setup (keyless OIDC):
1. Create an IAM OIDC provider for `token.actions.githubusercontent.com`.
2. Create an IAM role trusting that provider, scoped to your repo, with ECR push +
   `ecs:UpdateService`/`ecs:DescribeServices` permissions.
3. GitHub repo → Settings → Secrets and variables → Actions → **Variables** →
   add `AWS_ROLE_ARN` = the role ARN.
   (Follow AWS's "Configuring OpenID Connect in AWS" guide for the exact trust-policy JSON.)

Then:
```bash
git init && git add . && git commit -m "Initial AIOps churn service"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```
Watch the **Actions** tab: test → build → push → deploy, automatically.

✅ **Checkpoint:** a `git push` to `main` triggers an automatic, test-gated deploy.

---

## Phase 5 — Operate it: monitoring + drift  *(next milestone)*

**Watch autoscaling under load:**
```bash
python scripts/load_test.py $API_URL --requests 5000 --concurrency 50
# 2nd terminal:
watch -n 5 'aws ecs describe-services --cluster churn-api-cluster \
  --services churn-api-service --query "services[0].runningCount" --no-cli-pager'
```

**Drift detection (the most "MLOps" piece):**
```bash
# baseline = the distribution the model trained on
python -c "from training.train import generate_synthetic_data, FEATURE_COLUMNS; \
  generate_synthetic_data(20000)[FEATURE_COLUMNS].to_csv('baseline.csv', index=False)"

# pull prediction logs from CloudWatch into predictions.log, then:
python monitoring/drift_detection.py --baseline baseline.csv --logs predictions.log
```
PSI thresholds: <0.1 fine, 0.1–0.25 investigate, >0.25 retrain. Exits non-zero on major drift
so you can wire it to an alert. **The loop that IS MLOps:** serve → log → detect drift →
alert → retrain → redeploy.

---

## Where to take it next (toward mastery)

1. **Model in S3, not baked into the image** — decouples model releases from code releases.
   (The friction of training locally on your laptop just to ship is exactly why this exists.)
2. **HTTPS** — ACM cert + HTTPS listener, redirect 80→443.
3. **Own VPC** — private subnets for tasks + NAT, the real network topology.
4. **Blue/green deploys** — CodeDeploy with ECS for instant rollback.
5. **Scheduled drift** — `drift_detection.py` on EventBridge + Lambda, alert to SNS, auto-retrain.

---

## What you can truthfully claim now

- Built and deployed a containerized ML inference service to AWS ECS Fargate behind an
  Application Load Balancer with autoscaling and health checks.
- Implemented infrastructure as code with Terraform (ECR, ECS, ALB, IAM least-privilege, CloudWatch).
- Containerized with a multi-stage, non-root Docker image; managed images in ECR.
- Instrumented with Prometheus/Grafana monitoring and a PSI-based data-drift detector.
- Debugged the real production friction: dependency/Python-version conflicts, venv isolation,
  short-lived cloud credentials, container networking, and cost management.

---

## Quick command index

| Need | Command |
|------|---------|
| Restore env vars | `source setenv.sh` |
| Activate venv (Win) | `source .venv/Scripts/activate` |
| Activate venv (WSL) | `source .venv-wsl/bin/activate` |
| Train model | `python training/train.py --n-samples 20000` |
| Run tests | `pytest -v` |
| Serve locally | `uvicorn app.main:app --reload --port 8000` |
| Local full stack | `docker compose up --build` / `docker compose down` |
| ECR login | `aws ecr get-login-password --region $AWS_REGION \| docker login --username AWS --password-stdin $ECR_URL` |
| Build image | `docker build --build-arg MODEL_VERSION=v1 -t $ECR_URL:v1 -t $ECR_URL:latest .` |
| Push image | `docker push $ECR_URL:v1 && docker push $ECR_URL:latest` |
| Deploy | `aws ecs update-service --cluster churn-api-cluster --service churn-api-service --force-new-deployment --no-cli-pager` |
| Wait healthy | `aws ecs wait services-stable --cluster churn-api-cluster --services churn-api-service` |
| Live health check | `curl $API_URL/health/ready` |
| List ECR images | `aws ecr list-images --repository-name churn-api --region $AWS_REGION` |
| **TEAR DOWN** 💸 | `cd infra/terraform && terraform destroy` |