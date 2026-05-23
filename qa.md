# AIOps Churn Service — Interview Q&A

> Grounded in a project I built and deployed end to end: a churn-prediction ML service
> on AWS ECS Fargate, with Terraform, Docker, a CI/CD design, and monitoring. Every answer
> here maps to something I actually did or debugged — so I can speak from experience, not theory.
>
> **How to use this:** read the answer, then close the file and say it out loud in your own
> words. The "war story" callouts are gold in interviews — real problems you solved.

---

## Part 1 — The 60-second project pitch

**Q: Tell me about a project you're proud of.**

I built a production-grade churn-prediction service and deployed it to AWS the way a real team
would. It's a gradient-boosting model trained with scikit-learn, served as a REST API with
FastAPI, packaged in a multi-stage Docker image, and running on ECS Fargate behind an
Application Load Balancer with autoscaling and health checks. All the infrastructure is defined
as code in Terraform, and I instrumented it with Prometheus/Grafana monitoring plus a data-drift
detector. I deliberately went through the whole MLOps lifecycle — train, version, containerize,
deploy, monitor, detect drift — because my goal was to learn to *operate* a model in production,
not just train one. Along the way I debugged the real stuff: Python version conflicts, container
networking, short-lived cloud credentials, and cost management.

> **Why this works:** it names concrete tech, shows the full lifecycle, and ends on the
> "operate, not just train" distinction that separates MLOps from data science.

---

## Part 2 — Core MLOps concepts

**Q: What's the difference between MLOps and DevOps?**

DevOps is about reliably shipping and operating software. MLOps includes all of that, plus the
problems unique to ML. The big one: a deployed model can silently degrade even when the code
never changes, because the world changes — the input data drifts away from what the model
trained on. So MLOps adds model versioning, data/concept drift monitoring, and a retraining
loop on top of normal deployment. In my project, the piece that's pure MLOps is the drift
detector — nothing about it is a DevOps concern, but it's what keeps the model honest over time.

**Q: What is training-serving skew and how did you prevent it?**

It's when the data preprocessing at training time differs from inference time, so the model sees
differently-shaped inputs in production than it learned on — a silent accuracy killer. I
prevented it by putting the preprocessing and the model into a **single scikit-learn Pipeline**,
saved as one artifact. There's literally no way for serving to apply different preprocessing,
because the same pipeline object does both. One artifact, one code path.

**Q: What is data drift, and how did you detect it?**

Data drift is when the distribution of incoming features shifts away from the training
distribution. The model still returns confident predictions, but they get less accurate. I
detected it with **PSI — Population Stability Index** — which compares the live feature
distribution against a training baseline, bucketing both and measuring divergence. Thresholds
are standard: under 0.1 is fine, 0.1–0.25 means investigate, above 0.25 means major drift,
likely retrain. I chose PSI because it's the telco/finance industry standard, the thresholds are
interpretable, and crucially it needs **no ground-truth labels** — which you usually don't have
at prediction time, since you don't yet know who actually churned.

**Q: The full ML lifecycle — walk me through it.**

Serve → log every prediction → detect drift on those logs → alert when it breaches threshold →
retrain on fresh data → redeploy through the same pipeline → back to serve. That loop is what
"MLOps" actually means in practice. My project implements every stage of it.

**Q: How do you version a model?**

Two layers. The model artifact itself gets a version (I stamped it via a build argument that
flows from the Docker build all the way into the API's responses, so every prediction reports
which model produced it — that's traceability). And experiment tracking — I wired in MLflow so
runs, parameters, and metrics are logged and comparable. "I trained a model" is amateur;
"model v7, AUC 0.91, reproducible from this commit" is production.

---

## Part 3 — The serving layer (FastAPI / API design)

**Q: Why FastAPI?**

Async support, automatic request validation through Pydantic, and auto-generated interactive
docs at `/docs`. The validation matters most: a model that gets garbage input returns confident
garbage. Pydantic rejects bad input at the edge with a clear 422 error before it ever reaches the
model, instead of throwing a 500 deep in the stack.

**Q: You have two health endpoints — `/health/live` and `/health/ready`. Why?**

They answer different questions, and orchestrators use them differently. **Liveness** = "is the
process running at all?" — if it fails, restart the container. **Readiness** = "can it actually
serve traffic right now?" — which requires the model to be loaded; if it fails, stop routing
traffic to this instance but don't restart it. Conflating them causes real bugs: if you only had
one check tied to "process alive," traffic would hit a pod whose model is still loading and get
500s. I mapped readiness to the model being loaded, so the load balancer only sends requests once
the instance can genuinely answer.

> **War story:** when I first deployed, the ALB health check pointed at `/health/ready`, and the
> URL returned 503 until a task was actually healthy. That 503 wasn't a bug — it was the readiness
> design working: the load balancer correctly refused to route to an instance with no model behind it.

**Q: Where do you load the model — and why does it matter?**

Once, at startup, via a lifespan handler — not per request. Loading a model on every request is
the number-one latency killer in junior ML services. Load once, hold it in memory, reuse it.

**Q: How does your service expose metrics?**

A `/metrics` endpoint in Prometheus format. I track prediction count by risk band, latency as a
histogram, error counts, and — important for ML — the distribution of predicted probabilities,
which is itself a drift signal. Health checks answer "is it up?"; metrics answer "is it healthy?"
You need both.

---

## Part 4 — Containerization (Docker)

**Q: Walk me through your Dockerfile decisions.**

Multi-stage build: a builder stage installs dependencies into a virtualenv, and the final runtime
stage copies only that venv plus the app — no compilers or build caches in the shipped image, so
it's smaller and has less attack surface. It runs as a **non-root user** (`appuser`), because a
container running as root is a security finding in any real review — if the app is compromised,
non-root limits the blast radius. And dependencies are pinned to exact versions for
reproducibility.

> **War story:** I verified the non-root part directly with `docker run --rm churn-api:local whoami`
> — it prints `appuser`, not `root`. Small check, but it's exactly what a security scanner flags.

**Q: What's the difference between `docker run` and `docker compose`?**

`docker run` starts one container. `docker compose` orchestrates several together as a system —
in my case the API plus Prometheus plus Grafana — on a shared network where they find each other
by service name, managed as one unit. `run` proved my container works alone; `compose` proved it
works as a *monitored* system. It's the same idea ECS and Kubernetes do in the cloud, just at a
bigger scale.

**Q: Your containers needed to talk to each other. How does that networking work?**

By **service name**, not `localhost`. Inside a container, `localhost` means that container itself,
not the host or another container.

> **War story:** Grafana couldn't reach Prometheus — "connection refused on localhost:9090." The
> fix was pointing Grafana at `http://prometheus:9090`, the Compose service name. Same principle
> applies in the cloud: services discover each other by name through service discovery, never via
> localhost. That one bug taught me container networking better than any doc.

---

## Part 5 — Infrastructure as Code (Terraform)

**Q: Why Terraform instead of clicking around the AWS console?**

Clicking the console creates resources you can't reproduce and forget about — which means surprise
bills and no audit trail. With Terraform the entire stack is one version-controlled, peer-reviewable
definition. `terraform plan` previews exactly what will change before anything happens, and
`terraform destroy` tears it all down cleanly. Reproducible, reviewable, destroyable.

**Q: What does `terraform plan` show, and what do you look for?**

It's a dry-run diff: what will be created, changed, or destroyed. For a first deploy I want to
see all `+ create` and "0 to destroy." The senior habit is **never apply blindly** — if I see
unexpected `destroy` lines, I stop and investigate, because that could mean data loss.

**Q: What infrastructure did your Terraform create?**

About 16 resources: an ECR repo (with a lifecycle policy keeping the last 10 images), an ECS
Fargate cluster + service + task definition, an Application Load Balancer with target group and
listener, two security groups, two IAM roles, a CloudWatch log group, and autoscaling resources.

**Q: Explain your security group design.**

Two groups, least-privilege. The ALB's security group accepts traffic from anywhere on port 80 —
correct for a public API. But the **task** security group only accepts traffic on the app port
**from the ALB's security group** — not from the internet. So the containers are never directly
reachable; all traffic must go through the load balancer. That's the key production pattern.

**Q: Why two IAM roles?**

Separation of concerns. The **execution role** is used by the ECS agent to pull the image from
ECR and write logs to CloudWatch — infrastructure plumbing. The **task role** is the identity my
*application code* runs as. If the app needs to read a model from S3, I'd grant that narrowly on
the task role. Keeping them separate means my app never holds ECR/logging permissions it
shouldn't have.

---

## Part 6 — Deployment (ECS Fargate, ECR)

**Q: Why ECS Fargate over EKS/Kubernetes?**

Fargate is serverless containers — AWS runs the underlying hosts, I just declare "run this image
with this CPU/memory." No cluster to patch, no nodes to babysit. For a single model service
that's exactly right and what most teams actually use. Kubernetes earns its complexity when you
have many services, a platform team, or multi-cloud needs. I included K8s manifests too, but I'm
honest that for this workload it'd be over-engineering — and being able to say *when* to use a
tool is more valuable than always reaching for the heaviest one.

**Q: Walk me through deploying a new version.**

Authenticate Docker to ECR, build the image tagged with a version, push to ECR, then
`aws ecs update-service --force-new-deployment` and `aws ecs wait services-stable`. ECS does a
rolling deploy — brings up new tasks, health-checks them, drains the old ones — so it's
zero-downtime. The wait command polls until the new task is healthy behind the ALB.

**Q: How does autoscaling work in your setup?**

Target-tracking on CPU: keep average CPU near 60%, scale out above it, scale in below, between 1
and 4 tasks. Under load the task count rises automatically; when traffic drops, it scales back to
save cost.

**Q: Why tag images by version/git SHA instead of just `latest`?**

Traceability. "What's running in production right now?" — the SHA answers it exactly and ties the
running container back to precise source code. `latest` is ambiguous; an immutable tag is an
audit trail.

> **War story:** my `docker push` failed with "authorization token has expired." ECR tokens are
> short-lived — about 12 hours — for security: a leaked token expires on its own. I just
> re-ran the login. This is also exactly why CI/CD uses OIDC to fetch a fresh token every run, so
> a human never thinks about token expiry. Feeling that friction manually made the value of the
> automation obvious.

---

## Part 7 — CI/CD

**Q: Describe your CI/CD pipeline.**

On every push to `main`: run the test suite and lint — if that fails, stop. Then build the image,
tag it with the git SHA, push to ECR, and update the ECS service, waiting for it to stabilize. The
golden rule is that nothing reaches production that didn't pass tests. It makes deploys boring and
repeatable, which is exactly what you want production deploys to be.

**Q: How does it authenticate to AWS without storing keys?**

GitHub OIDC. The pipeline assumes an IAM role via a short-lived token instead of long-lived AWS
keys stored in GitHub secrets. No static credentials to leak — that's the modern secure standard.

**Q: Why test *before* building the image?**

Fail fast and cheap. Tests are seconds; building and pushing an image is minutes. No point
spending the time to build an artifact you're going to reject anyway. And it guarantees a broken
model or contract never even becomes a deployable image.

---

## Part 8 — The "tell me about a hard bug" questions (your war stories)

> These are where you shine — every one is real. Interviewers love specific debugging stories.

**Q: Tell me about a time you debugged a tricky environment issue.**

My dependency install failed because I was on Python 3.14, and the pinned scikit-learn had no
prebuilt wheel for that version — so pip tried to compile from source and failed on Windows. I
diagnosed it by reading the build log, which showed it invoking the C compiler. The fix was to
pin the *Python version* too, not just the packages — I matched my local Python to my Dockerfile's
3.12. The deeper lesson: the runtime environment is part of the system, and reproducibility means
controlling the Python version, the packages, and ultimately the whole OS via the container.

**Q: Tell me about a confusing failure with a simple root cause.**

A command kept failing with "expected one argument," and later a `ModuleNotFoundError` for a
package I'd definitely installed. Root cause both times: state didn't carry across shells — an
environment variable was empty in a new terminal, and separately my virtualenv had deactivated.
The lesson was to make the invisible visible: `echo $VAR` to check variables, and watch for the
venv prefix in the prompt. I ended up scripting the environment setup into a file I could `source`
so it was repeatable instead of error-prone.

**Q: Tell me about managing cloud costs.**

Everything in my project is destroyable with `terraform destroy`, and I ran it after every
session, because an idle load balancer alone bills around $16/month whether you use it or not. I
also set a billing alert before deploying anything. Treating teardown as non-negotiable and
knowing which resources bill hourly is part of operating cloud infrastructure responsibly — it's
not a footnote.

**Q: Tell me about a security-conscious decision you made.**

Several, but the clearest: my containers run as a non-root user and are never directly reachable
from the internet — the security group only allows traffic from the load balancer. And credentials
are short-lived throughout: ECR login tokens expire in hours, and the CI/CD design uses OIDC
instead of stored keys. Defense in depth — limit what each component can do and how long any
credential is valid.

---

## Part 9 — System design / scaling-up follow-ups

**Q: How would you improve this for real production?**

Top of my list: stop baking the model into the image and instead pull it from S3 or a model
registry at startup — that decouples model releases from code releases. Then HTTPS with an ACM
certificate, a custom VPC with private subnets for the tasks instead of the default VPC,
blue/green deploys via CodeDeploy for instant rollback, and running the drift detector on a
schedule with EventBridge to auto-trigger retraining.

**Q: How would you handle a much larger model, or GPU inference?**

I'd move off Fargate for the inference itself — either ECS/EKS on GPU instances, or a managed
service like SageMaker endpoints. I'd also separate the model artifact from the image (S3/registry)
since large models bloat images badly, and consider batching requests to use the GPU efficiently.
The serving API and monitoring patterns stay the same; only the compute substrate changes.

**Q: A prediction endpoint is suddenly slow in production. How do you investigate?**

Start with the metrics I already expose: latency histograms and error rates, plus ECS Container
Insights for CPU/memory. Is it one slow instance or all of them? Is CPU pegged (then autoscaling
should kick in — is it?) Check CloudWatch logs for errors. Rule out the model loading per-request
(it shouldn't, I load once at startup). Then look at whether input volume spiked or payloads got
larger. Methodical: metrics first to localize, logs to get specific.

**Q: How do you know when to retrain?**

Drift signal crossing threshold is the leading indicator — that's what the PSI detector is for.
The lagging indicator is actual performance once labels arrive (did the people we flagged actually
churn?). In a mature setup I'd alert on drift and schedule periodic retraining, then gate the new
model behind the same test-and-deploy pipeline so a worse model can't ship.

---

## Part 10 — Rapid-fire definitions (be ready to define crisply)

- **Container** — a lightweight, isolated package of an app plus its dependencies that runs
  identically anywhere.
- **Image vs container** — image is the template; container is a running instance of it.
- **ECR** — AWS's private Docker image registry.
- **ECS** — AWS's container orchestrator; **Fargate** is its serverless mode (no servers to manage).
- **ALB** — Application Load Balancer; routes HTTP traffic to healthy targets, enables zero-downtime deploys.
- **Target group** — the set of backends an ALB routes to, with a health check.
- **Security group** — a virtual firewall controlling inbound/outbound traffic for a resource.
- **IAM role** — an identity with permissions that a service or app assumes (no static credentials).
- **Terraform state** — Terraform's record of what it manages, so it knows what to create/change/destroy.
- **Liveness vs readiness** — "restart me?" vs "send me traffic?"
- **PSI** — Population Stability Index; measures distribution shift for drift detection.
- **OIDC (in CI/CD)** — lets a pipeline assume a cloud role with a short-lived token instead of stored keys.
- **Rolling deploy** — gradually replace old tasks with new, health-checking as you go, for zero downtime.
- **Blue/green deploy** — run old and new side by side, switch traffic at once, instant rollback.

---

## Closing line you can use

"My goal wasn't to build a clever model — it was to learn to operate one in production. So I went
through the entire lifecycle on real cloud infrastructure, and I deliberately did the first deploy
by hand before automating it, because you can't automate a process you don't understand. The most
valuable part was debugging the real friction — environment conflicts, credentials, networking,
cost — because that's the part tutorials skip and the part the job actually is."