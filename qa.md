# AIOps Churn Service — Interview Q&A (Expanded Edition)

> Grounded in two real projects:
> 1. **Churn ML service** — deployed end to end on AWS ECS Fargate (Terraform, Docker, FastAPI,
>    CI/CD with GitHub Actions, Prometheus/Grafana, PSI drift detection).
> 2. **DevSecOps microservices + Kafka** — Kubernetes on EKS, Jenkins CI with Trivy/OWASP/SonarQube
>    security gates, GitOps delivery via Argo CD, AWS ALB ingress, Helm, Prometheus/Grafana.
>
> Having BOTH lets me answer the most valuable interview questions about *judgment*: when to use
> Fargate vs Kubernetes, push CI/CD vs GitOps. Most candidates know one tool and reach for it
> reflexively — being able to choose the right one per workload is the senior signal.
>
> **How to use this:** read an answer, close the file, say it out loud in your own words. The
> "war story" callouts are real problems I solved — interviewers value those far more than definitions.
>
> ⚠️ **Honesty note:** the K8s/Argo answers below describe the *general* shape of that work.
> Edit them to match exactly what you built, and never claim to have run something you haven't.

---

## Part 1 — The 60-second project pitches

**Q: Tell me about a project you're proud of.**

I built a production-grade churn-prediction service and deployed it end to end on AWS. A
scikit-learn model served via FastAPI, packaged in a multi-stage non-root Docker image, running
on ECS Fargate behind an Application Load Balancer with autoscaling and health checks — all
infrastructure as code in Terraform, with a GitHub Actions CI/CD pipeline, Prometheus/Grafana
monitoring, and a PSI-based data-drift detector. I deliberately walked the whole MLOps lifecycle
because my goal was to learn to *operate* a model, not just train one. And I separately built a
full DevSecOps pipeline for a Kafka microservices system on Amazon EKS — Jenkins CI with Trivy,
OWASP, and SonarQube security gates, GitOps delivery via Argo CD, and Prometheus/Grafana monitoring
— so I've deployed on both ECS and Kubernetes and can speak to when each is the right call.

**Q: Give me the elevator version.**

I take ML models to production on AWS — containerized, infrastructure-as-code, CI/CD, monitored,
with drift detection. I've deployed on both ECS Fargate and Kubernetes/EKS, and I'm comfortable
debugging the messy real-world parts: environment conflicts, credentials, networking, cost.

---

## Part 2 — Core MLOps concepts

**Q: MLOps vs DevOps — what's the difference?**

DevOps is reliably shipping and operating software. MLOps is all of that plus the problems unique
to ML. The defining one: a deployed model can silently degrade even when the code never changes,
because the world changes — input data drifts from the training distribution. So MLOps adds model
versioning, experiment tracking, data/concept drift monitoring, and a retraining loop on top of
normal deployment. In my churn project, the drift detector is the pure-MLOps piece — nothing about
it is a DevOps concern, but it's what keeps the model honest over time.

**Q: What is training-serving skew, and how did you prevent it?**

It's when preprocessing differs between training and inference, so the model sees differently
shaped inputs in production than it learned on — a silent accuracy killer that throws no error. I
prevented it by putting preprocessing and the model into a **single scikit-learn Pipeline**, saved
as one artifact. There's no way for serving to apply different preprocessing, because the same
pipeline object does both. One artifact, one code path, no skew.

**Q: Data drift vs concept drift — distinguish them.**

Data drift (covariate shift) is when the *input* distribution changes — e.g., suddenly more
fiber-internet customers than at training time. Concept drift is when the *relationship* between
inputs and the target changes — e.g., the same customer profile that used to churn now doesn't,
because a competitor changed pricing. Data drift you can detect without labels (compare input
distributions); concept drift usually needs ground-truth labels to catch, because the inputs can
look unchanged while the right answer moved.

**Q: How did you detect drift, and why that method?**

PSI — Population Stability Index. It compares the live feature distribution against a training
baseline by bucketing both and measuring divergence. Thresholds are standard and interpretable:
under 0.1 fine, 0.1–0.25 investigate, over 0.25 major shift. I chose PSI because it's the
telco/finance industry standard, the thresholds are interpretable, and it needs **no ground-truth
labels** — which you usually don't have at prediction time, since you don't yet know who actually
churned. I also track the predicted-probability distribution as a Prometheus histogram, which is a
drift signal in itself.

**Q: Walk me through the full ML lifecycle loop.**

Serve → log every prediction → run drift detection on those logs → alert when it breaches
threshold → retrain on fresh data → redeploy through the same pipeline → back to serve. That loop
*is* MLOps in practice, and my project implements every stage.

**Q: How do you version models?**

Two layers. The artifact gets a version stamp — I pass it as a Docker build arg that flows all the
way into the API's responses, so every prediction reports which model produced it (traceability).
And experiment tracking via MLflow — runs, params, and metrics logged and comparable. "I trained a
model" is amateur; "model v7, AUC 0.91, reproducible from this commit" is production.

**Q: Why use a model registry instead of just saving a .pkl file?**

A registry gives you versioning, stage transitions (staging → production → archived), lineage back
to the training run, and a single source of truth multiple services can pull from. A loose pickle
file has none of that — no idea which data trained it, which code, or whether it's the one actually
serving. The registry is to models what git is to code.

---

## Part 3 — The serving layer (FastAPI / API design)

**Q: Why FastAPI over Flask?**

Async support, automatic request validation via Pydantic, and auto-generated OpenAPI docs. The
validation matters most for ML: a model fed garbage returns confident garbage. Pydantic rejects bad
input at the edge with a clear 422 before it reaches the model, instead of a 500 deep in the stack.
Flask would need that wired up manually.

**Q: Explain your two health endpoints.**

Liveness (`/health/live`) = "is the process running?" — if it fails, restart the container.
Readiness (`/health/ready`) = "can it serve traffic right now?" — which requires the model loaded;
if it fails, stop routing traffic but don't restart. Conflating them causes real bugs: traffic
hitting a pod whose model is still loading returns 500s. I mapped readiness to model-loaded, so the
load balancer only routes once the instance can genuinely answer.

> **War story:** my first AWS deploy returned 503 from the public URL. That wasn't a bug — the ALB
> health check targets `/health/ready`, and there was no healthy task yet (empty ECR). The 503 was
> the readiness design working: the LB correctly refused to route to an instance with no model.

**Q: Where and when do you load the model?**

Once, at startup, via a FastAPI lifespan handler — never per request. Per-request loading is the
number-one latency killer in junior ML services. Load once, hold in memory, reuse.

**Q: How would you handle a model that's too big to fit in memory per replica?**

A few options depending on constraints: serve it from a dedicated inference server (e.g., a
Triton/TorchServe-style setup) that other services call; shard or quantize the model; or move to
GPU instances with enough memory and batch requests. The serving API and monitoring patterns stay;
only the compute substrate changes.

**Q: How do you do zero-downtime model updates?**

Rolling deployment: bring up new tasks/pods with the new model, health-check them against
readiness, drain old ones only once new ones are healthy. The load balancer never routes to an
instance that isn't ready. For instant rollback you'd go blue/green — run old and new side by side,
switch traffic atomically.

---

## Part 4 — Containerization (Docker)

**Q: Walk me through your Dockerfile decisions.**

Multi-stage build: a builder stage installs dependencies into a virtualenv; the runtime stage
copies only that venv plus app code — no compilers or build caches in the shipped image, so it's
smaller with less attack surface. Runs as a **non-root user** because a root container is a security
finding in any review — non-root limits blast radius if the app is compromised. Dependencies pinned
to exact versions for reproducibility. A container-level HEALTHCHECK as a backstop.

> **War story:** I verified non-root directly — `docker run --rm churn-api:local whoami` prints
> `appuser`, not `root`. Small check, exactly what a security scanner flags.

**Q: Why multi-stage builds specifically — what's the payoff?**

Smaller final image and reduced attack surface. Build tools (compilers, dev headers, pip caches)
are needed to *install* dependencies but not to *run* the app. Multi-stage lets you use them in the
builder and leave them behind, shipping only the runtime artifacts. Smaller images also pull faster,
which means faster scaling and deploys.

**Q: docker run vs docker compose?**

`run` starts one container. `compose` orchestrates several as a system — in my case API +
Prometheus + Grafana — on a shared network where they resolve each other by service name, managed
as one unit. `run` proved my container works alone; `compose` proved it works as a *monitored*
system. Same idea ECS and Kubernetes do in the cloud, at larger scale.

**Q: How do containers find each other on a network?**

By service name, not localhost. Inside a container, `localhost` is that container itself.

> **War story:** Grafana couldn't reach Prometheus — "connection refused on localhost:9090." Fix
> was pointing Grafana at `http://prometheus:9090`, the Compose service name. Same principle in the
> cloud: service discovery by name, never localhost. That bug taught me container networking better
> than any doc.

**Q: How do you keep image sizes and vulnerabilities down?**

Slim base images, multi-stage builds, pinned dependencies, non-root, a `.dockerignore` to keep the
build context lean, and image scanning (ECR scans on push). Fewer layers and fewer packages mean
fewer CVEs.

---

## Part 5 — Infrastructure as Code (Terraform)

**Q: Why Terraform over clicking the console?**

Console clicks create resources you can't reproduce and forget about — surprise bills, no audit
trail. Terraform makes the whole stack one version-controlled, reviewable definition. `plan`
previews changes before anything happens; `destroy` tears it down cleanly. Reproducible, reviewable,
destroyable.

**Q: What does `terraform plan` show, and what do you look for?**

A dry-run diff: create / change / destroy. For a first deploy I want all `+ create`, "0 to
destroy." Senior habit: never apply blindly — unexpected `destroy` lines could mean data loss, so I
stop and investigate.

**Q: What's Terraform state, and why does it matter?**

State is Terraform's record of the real resources it manages, mapping config to actual cloud IDs.
It's how `plan` knows what already exists and what to change. In a team you store it remotely (S3 +
DynamoDB lock) so people don't clobber each other and the state isn't on one laptop. Losing or
corrupting state is painful — Terraform no longer knows what it owns.

**Q: Explain your security group design.**

Two groups, least-privilege. The ALB group accepts traffic from anywhere on port 80 — correct for a
public API. The task group accepts the app port **only from the ALB's security group**, not the
internet. So containers are never directly reachable; all traffic flows through the load balancer.

**Q: Why two IAM roles?**

Separation of concerns. The execution role is used by the ECS agent to pull images and write logs —
infrastructure plumbing. The task role is the identity my application code runs as. If the app
needs S3, I grant that narrowly on the task role. Separating them means my app never holds
ECR/logging permissions it shouldn't have.

**Q: How would you manage multiple environments (dev/staging/prod) in Terraform?**

Terraform workspaces or, better for clarity, separate state per environment with shared modules and
per-env variable files. The infrastructure *definition* is one set of modules; the *parameters*
(instance sizes, counts, domains) differ per environment. Same code, different tfvars.

---

## Part 6 — ECS Fargate & deployment

**Q: Why ECS Fargate over EKS for this project?**

Fargate is serverless containers — AWS runs the hosts, I declare "run this image with this
CPU/memory." No cluster to patch, no nodes to babysit. For a single model service that's exactly
right and what most teams use. Kubernetes earns its complexity with many services, a platform team,
or multi-cloud needs. I've run K8s on EKS for a microservices project, so this wasn't avoidance —
it was choosing the simpler tool that fit the problem.

**Q: Walk me through a deploy.**

Authenticate Docker to ECR, build the image tagged with the git SHA, push, then `update-service
--force-new-deployment` and `wait services-stable`. ECS does a rolling deploy — new tasks up,
health-checked, old ones drained — so zero downtime. The wait polls until the new task is healthy
behind the ALB.

**Q: How does your autoscaling work?**

Target-tracking on CPU: hold average near 60%, scale out above, in below, between 1 and 4 tasks.
Under load the task count rises automatically; when it drops, it scales back to save cost.

**Q: Why tag images by git SHA, not just `latest`?**

Traceability. "What's in prod right now?" — the SHA answers exactly and ties the container to
precise source. `latest` is ambiguous; an immutable tag is an audit trail and makes rollback
deterministic.

> **War story:** a `docker push` failed with "authorization token has expired." ECR tokens are
> short-lived (~12h) — a leaked token expires on its own. I re-ran the login. This is exactly why
> CI/CD uses OIDC to fetch a fresh token each run; feeling the manual friction made the automation's
> value obvious.

---

## Part 7 — CI/CD (push-based, GitHub Actions)

**Q: Describe your pipeline.**

On every push to main: lint (ruff) + tests (pytest); if either fails, stop. Then build the image,
tag with git SHA, push to ECR, update the ECS service, wait for stable. The golden rule: nothing
reaches production that didn't pass tests. It makes deploys boring and repeatable.

**Q: How does it auth to AWS without stored keys?**

GitHub OIDC. The pipeline assumes an IAM role via a short-lived token instead of long-lived AWS keys
in GitHub secrets. The IAM trust policy is scoped to my specific repo via the `sub` claim, so only
my repo can assume the role. No static credentials to leak.

**Q: Why lint and test *before* building?**

Fail fast and cheap. Tests are seconds; building and pushing an image is minutes. No point
producing an artifact you'll reject. It guarantees a broken model or contract never even becomes a
deployable image.

> **War story:** my first CI run failed at the lint step — ruff caught unused imports and unsorted
> imports. That's CI working as designed: the safety net stopped a stylistically broken commit before
> it built anything. I'd never run ruff locally, only pytest — which is the whole point of CI:
> enforcing checks consistently that humans forget to run.

**Q: A tradeoff in your current pipeline you'd improve?**

It trains the model inside CI and bakes it into the image, so every deploy reinstalls the ML stack
and retrains (~2–3 min overhead) and couples model releases to code releases. I'd move the model to
S3 or a registry and have the container pull it at startup — decoupling model and code release
cycles, and removing training from the deploy path.

---

## Part 8 — DevSecOps on EKS: Kubernetes, Jenkins, Argo CD, Kafka (the microservices project)

> This was a full DevSecOps pipeline: 4 Node.js microservices + Kafka, built and security-scanned
> in Jenkins, delivered via GitOps with Argo CD onto Amazon EKS, fronted by an AWS ALB, and
> monitored with Prometheus/Grafana. It's the project that shows breadth beyond the ML service.

**Q: Tell me about your Kubernetes / DevSecOps project.**

I built an event-driven microservices system — four Node.js services (user, product, order, and an
API gateway) communicating through Kafka — and deployed it on Amazon EKS through a complete
DevSecOps pipeline. Jenkins ran CI: code checkout, three security scans, a SonarQube quality gate,
then Docker build and push for all four services. CD was GitOps: Jenkins updated the Kubernetes
manifests with the new image tag and committed them to git, and **Argo CD** auto-synced the cluster
to match. Traffic came in through an AWS ALB via the Load Balancer Controller, routing by path to
each service. Prometheus and Grafana ran on-cluster for monitoring. So it covers the full chain —
secure build, GitOps delivery, Kubernetes orchestration, ingress, and observability.

**Q: What makes it "DevSecOps" and not just CI/CD? Walk me through the security gates.**

Security is built into the pipeline, not bolted on after — "shift left." Three scanners run before
anything ships: **Trivy** does a filesystem scan for vulnerabilities, **OWASP Dependency-Check**
scans my dependencies against the CVE database, and **SonarQube** does static analysis for code
quality and bugs with a **quality gate** that fails the build if standards aren't met. Only after
all of those pass does Jenkins build and push the images. So vulnerable or low-quality code never
becomes a deployable artifact — the same "fail fast, fail cheap" principle as testing, applied to
security.

**Q: Explain your CI/CD architecture — the two-pipeline split.**

Two Jenkins pipelines. **CI** handles everything up to producing artifacts: checkout → Trivy → OWASP
→ SonarQube analysis + quality gate → Docker build (4 services) → push to DockerHub → then it
triggers the CD pipeline, passing the Docker tag. **CD** handles delivery the GitOps way: it updates
the Kubernetes manifests with the new tag using `sed`, commits and pushes them to git — and that's
where Jenkins stops. **Argo CD**, watching the repo, detects the change and syncs it to EKS. The
clean separation means CI owns "build a trustworthy artifact" and CD owns "declare the desired
state"; Argo owns "make the cluster match." Email notifications fire on success/failure.

**Q: GitOps (Argo CD) vs push-based CI/CD (GitHub Actions) — you've done both. Compare them.**

I've built both, which is the interesting part. My ML service used **push-based**: GitHub Actions
has credentials and pushes the deploy straight into ECS after tests. My microservices project used
**GitOps/pull-based**: Jenkins only commits manifest changes to git, and Argo CD — running *inside*
the cluster — pulls and reconciles the cluster to match git. GitOps advantages: git is the single
source of truth, so the running state is always auditable from the repo; Argo's **self-heal** means
if someone manually changes the cluster, it reverts to the declared state; rollback is just a git
revert; and no external CI system needs standing credentials into the cluster, which is more secure.
Push-based is simpler and great for a single service. I'd choose GitOps for many services with a
team, push-based when simplicity wins. Knowing when to use which is the point.

**Q: How exactly does Argo CD work in your setup?**

I installed Argo CD into its own namespace on the cluster, connected it to my GitHub repo, and added
the EKS cluster as a target. Then I created an Argo Application pointing at the `kubernetes/` path in
my repo, with sync policy set to **Automatic** plus auto-prune and self-heal. So when the CD pipeline
commits updated manifests, Argo detects the drift between git (desired) and cluster (actual) and
reconciles automatically — pulling the new image tags and rolling out the deployments. Auto-prune
removes resources I deleted from git; self-heal undoes manual cluster changes.

**Q: Why Kafka — what problem did it solve between your services?**

Kafka decouples the services via an event log. Instead of services calling each other synchronously
(brittle — if one is down, the call fails), producers publish events to topics and consumers read at
their own pace. That gives durability (events persist and can be replayed), buffering against load
spikes, and resilience — if the order service is down, its events wait in the topic instead of being
lost. It turns a fragile chain of synchronous HTTP calls into a resilient event-driven system.

**Q: Walk me through standing up the EKS cluster.**

I used `eksctl`. Created the cluster control plane first **without a node group**, then associated an
**OIDC provider** (needed for IRSA), then added a managed node group of two t2.large nodes
separately. Splitting control plane and node group creation is cleaner and lets you manage nodes
independently. Each step takes 15–20 minutes, so it's not quick — which is itself a reason Fargate
made more sense for my single ML service.

**Q: How does traffic get into the cluster? Explain the ALB ingress setup.**

EKS doesn't ship an ingress controller, so I installed the **AWS Load Balancer Controller** via
Helm. It watches Ingress resources and provisions a real AWS ALB to match. My Ingress routes by
path — `/products` → product-service:4001, `/users` → user-service, `/orders` → order-service. The
controller needs AWS permissions, which it gets through **IRSA**: a Kubernetes ServiceAccount
annotated with an IAM role ARN, trusted via the cluster's OIDC provider. So the controller pod
assumes a scoped IAM role with no static keys on the nodes.

**Q: What is IRSA and why does it matter?**

IAM Roles for Service Accounts. It binds a Kubernetes ServiceAccount to an IAM role through the
cluster's OIDC provider, so pods using that ServiceAccount get short-lived, scoped AWS credentials —
no long-lived keys baked into nodes or images. It's the EKS equivalent of the ECS task role:
per-workload least-privilege. I used it for the Load Balancer Controller. It's the same OIDC
federation idea as the GitHub Actions → AWS auth in my ML project — short-lived tokens instead of
stored secrets, just applied to pods instead of a pipeline.

**Q: Map ECS concepts to Kubernetes — show you understand both.**

ECS task ≈ K8s Pod. ECS service ≈ K8s Deployment (desired replicas, rolling updates). ECS
autoscaling ≈ Horizontal Pod Autoscaler. ALB + target group ≈ Ingress + the AWS Load Balancer
Controller. Liveness/readiness probes are the same concept and even the same health endpoints. ECS
task role ≈ Kubernetes ServiceAccount with IRSA. Same ideas; Kubernetes exposes more knobs and needs
more operating — which is exactly the tradeoff that decides Fargate vs EKS per workload.

**Q: When would you NOT use Kubernetes?**

For a single service or a few, where the overhead — cluster upgrades, node management, the
ingress/controller stack, installing your own monitoring, the learning curve — outweighs the
benefit. That's precisely why I put my ML service on Fargate instead. Kubernetes pays off with many
services, a platform team to operate it, multi-cloud needs, or when you want its ecosystem (Argo,
operators, Helm, fine-grained scheduling) — all of which my microservices project genuinely used.

**Q: What's a Jenkins shared library and why use one?**

It's reusable pipeline code — Groovy functions in a `vars/` directory in a separate repo — that
multiple Jenkinsfiles can call. I factored each step (`code_checkout`, `trivy_scan`,
`owasp_dependency`, `sonarqube_analysis`, `docker_build`, `docker_push`) into the library, so my
Jenkinsfiles read like a clean sequence of named stages instead of a wall of shell. It's the DRY
principle for pipelines — fix a step once, every pipeline benefits, and the Jenkinsfile stays
readable.

**Q: War story — a real problem you solved on this project.**

The OWASP dependency scan took 26+ minutes per build because it re-downloaded the entire NVD CVE
database every run and hit rate limits. I fixed it two ways: registered for a free **NVD API key**
to lift the rate limit, and pointed the scan at a **local cached data directory**
(`--data /var/lib/jenkins/owasp-data`) so the database persisted between builds. That dropped scan
time from ~26 minutes to 1–2. Classic CI optimization — cache the expensive, slow-changing thing.

**Q: War story — an AWS permissions issue.**

My ALB wasn't being created — the Ingress `ADDRESS` stayed empty, and the controller logged
`AccessDenied` on `elasticloadbalancing:DescribeListenerAttributes`. Root cause: I'd used an older
version of the IAM policy that predated permissions the newer controller needed. Fix: download the
current policy (v2.11.0), recreate it, and re-attach. Lesson: when a managed component throws
AccessDenied, check that its IAM policy version matches the component version — they evolve together.

**Q: War story — a Jenkins/tooling gotcha.**

A few. `cleanWs()` failed with "No such DSL method" because it needs the Workspace Cleanup plugin —
I used the built-in `deleteDir()` instead. Same with `emailext()` needing the Email Extension plugin
— I fell back to built-in `mail()`. And the shared library failed with "No version specified" until
I set a default branch. The meta-lesson: Jenkins DSL methods often depend on specific plugins, so
when you hit "No such DSL method," the fix is either install the plugin or use the built-in
equivalent.

**Q: How did you monitor the cluster?**

I installed the **kube-prometheus-stack** via Helm — Prometheus plus Grafana plus the standard
exporters in one chart — into a dedicated namespace, then exposed the UIs via NodePort and built
custom Grafana dashboards. Prometheus scrapes cluster and pod metrics; Grafana visualizes them. Same
metrics → Prometheus → Grafana pattern as my ML project, just at cluster scope and installed via Helm
rather than docker-compose.

---

## Part 9 — Monitoring & operations

**Q: How does your service expose metrics, and what do you track?**

A Prometheus-format `/metrics` endpoint. I track prediction count by risk band, latency as a
histogram (so I get p50/p95/p99), error counts, and the predicted-probability distribution (a drift
signal). Health checks answer "is it up?"; metrics answer "is it healthy?" — you need both.

**Q: Prometheus + Grafana — how do they relate?**

Three layers: the app exposes raw metrics → Prometheus scrapes and stores them as time series →
Grafana queries Prometheus and renders dashboards. Industry-standard pattern; the cloud just offers
managed versions of the same tools.

**Q: A prediction endpoint is suddenly slow in prod. How do you investigate?**

Start with the metrics I expose: latency histograms, error rates, plus ECS Container Insights for
CPU/memory. One slow instance or all? CPU pegged (then is autoscaling reacting)? Check CloudWatch
logs for errors. Rule out per-request model loading (I load once at startup). Then check whether
input volume spiked or payloads grew. Metrics to localize, logs to get specific — methodical, not
guessing.

**Q: How do you know when to retrain?**

Leading indicator: drift crossing threshold (the PSI detector). Lagging indicator: actual
performance once labels arrive (did the people we flagged actually churn?). Mature setup: alert on
drift, schedule periodic retraining, and gate the new model behind the same test-and-deploy pipeline
so a worse model can't ship.

---

## Part 10 — War stories (your real debugging — interviewers love these)

**Q: A tricky environment issue you debugged.**

My dependency install failed because I was on Python 3.14 and the pinned scikit-learn had no wheel
for it, so pip tried to compile from source and failed. I diagnosed it from the build log invoking
the C compiler. Fix: pin the *Python version* too, matching my Dockerfile's 3.12. Lesson: the
runtime environment is part of the system; reproducibility means controlling Python version,
packages, and ultimately the whole OS via the container.

**Q: A confusing failure with a simple root cause.**

A command failed with "expected one argument," and later a `ModuleNotFoundError` for a package I'd
installed. Root cause both times: state not carrying across shells — an env var was empty in a new
terminal, and my virtualenv had deactivated. Lesson: make the invisible visible — `echo $VAR`, watch
for the venv prefix in the prompt. I scripted the env setup into a file I could `source` so it was
repeatable.

**Q: A WSL/cross-platform gotcha.**

I built my venv on a Windows-mounted drive inside WSL and pip kept hitting the system Python instead
of the venv's — `which pip` pointed at `/usr/bin/pip`. Fix: call pip through the venv's interpreter
(`python -m pip`) and ideally keep projects on the native Linux filesystem, not the `/mnt` mount,
which has permission/symlink quirks. Taught me how venvs and PATH actually resolve.

**Q: Managing cloud cost.**

Everything's destroyable with `terraform destroy`, which I ran after every session — an idle load
balancer alone is ~$16/month whether used or not. I set a billing alert before deploying anything.
Knowing which resources bill hourly and treating teardown as non-negotiable is part of operating
cloud responsibly.

**Q: A security-conscious decision.**

Layered: containers run non-root and are never directly internet-reachable (security group allows
only the load balancer); credentials are short-lived throughout (ECR tokens expire in hours, CI uses
OIDC not stored keys, and the IAM trust policy is scoped to my exact repo). Defense in depth — limit
what each component can do and how long any credential lives.

---

## Part 11 — System design / scaling-up

**Q: How would you take this to real production?**

Model in S3/registry instead of baked into the image (decouple model and code releases); HTTPS via
ACM with an 80→443 redirect; a custom VPC with private subnets for tasks instead of the default VPC;
blue/green deploys via CodeDeploy for instant rollback; and the drift detector on a schedule
(EventBridge → Lambda) auto-triggering retraining with alerting to SNS.

**Q: How would you serve many models, or A/B test them?**

Route by model version at the API or load-balancer layer — e.g., a percentage of traffic to a
candidate model, compare metrics, promote if better. The version stamp I already return per
prediction makes attribution possible. A registry tracks which versions exist and their stage.

**Q: How would you handle GPU inference at scale?**

Move inference off Fargate to GPU instances (ECS/EKS on GPU nodes, or SageMaker endpoints), separate
the model artifact from the image (large models bloat images), and batch requests to use the GPU
efficiently. Autoscale on GPU utilization. API and monitoring patterns are unchanged.

---

## Part 12 — Rapid-fire definitions

- **Container** — lightweight isolated package of app + dependencies, runs identically anywhere.
- **Image vs container** — image is the template; container is a running instance.
- **ECR** — AWS private Docker image registry.
- **ECS / Fargate** — AWS container orchestrator; Fargate is its serverless mode.
- **EKS** — AWS managed Kubernetes.
- **ALB** — Application Load Balancer; routes HTTP to healthy targets, enables zero-downtime deploys.
- **Target group** — backends an ALB routes to, with a health check.
- **Security group** — virtual firewall for a resource (stateful).
- **IAM role** — an identity with permissions a service/app assumes; no static creds.
- **IRSA** — IAM Roles for Service Accounts; per-pod AWS permissions on EKS.
- **Terraform state** — record of what Terraform manages, mapping config to real resources.
- **Liveness vs readiness** — "restart me?" vs "send me traffic?"
- **HPA** — Horizontal Pod Autoscaler (K8s); scales pod count on metrics.
- **PSI** — Population Stability Index; measures distribution shift for drift detection.
- **OIDC (CI/CD)** — lets a pipeline assume a cloud role with a short-lived token, no stored keys.
- **GitOps** — git is the source of truth; a controller (Argo CD) reconciles the cluster to it (pull-based).
- **Push vs pull deploys** — pipeline pushes changes in vs in-cluster controller pulls and reconciles.
- **Argo CD self-heal** — reverts manual cluster changes back to the git-declared state.
- **Rolling deploy** — gradually replace old with new, health-checking, for zero downtime.
- **Blue/green deploy** — old and new side by side, switch traffic at once, instant rollback.
- **Kafka** — distributed event log; decouples producers/consumers, durable, replayable.
- **Data drift vs concept drift** — input distribution shifts vs input→output relationship shifts.
- **DevSecOps / shift-left** — build security scanning into the pipeline early, not after deploy.
- **Trivy** — scanner for vulnerabilities in filesystems, images, and dependencies.
- **OWASP Dependency-Check** — scans project dependencies against the CVE database.
- **SonarQube** — static analysis for code quality/bugs; a **quality gate** can fail the build.
- **Helm** — Kubernetes package manager; installs charts (e.g., kube-prometheus-stack).
- **Jenkins shared library** — reusable Groovy pipeline functions in a `vars/` repo (DRY pipelines).
- **IRSA** — IAM Roles for Service Accounts; per-pod scoped AWS creds via the cluster OIDC provider.

---

## Closing lines you can use

**On the ML project:** "My goal wasn't a clever model — it was learning to operate one in
production. So I did the whole lifecycle on real cloud infrastructure, deployed by hand first before
automating, because you can't automate a process you don't understand."

**On judgment across both projects:** "I've built a full DevSecOps pipeline on Kubernetes/EKS —
Jenkins CI with Trivy, OWASP, and SonarQube security gates, GitOps delivery via Argo CD, Kafka
microservices, all monitored with Prometheus/Grafana. And I deliberately chose ECS Fargate for my
ML service because Kubernetes would've been over-engineering for a single model. Knowing both — and
choosing the right one per workload — is the part I'm most confident about."

**On security:** "Security wasn't a final step — I shifted it left into the pipeline. Trivy,
OWASP Dependency-Check, and a SonarQube quality gate all run before any image is built, so
vulnerable or low-quality code never becomes a deployable artifact. Same fail-fast principle as
testing, applied to security."