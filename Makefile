# Common commands. Run `make help` to see them.
# A Makefile is how senior engineers make a project's workflow self-documenting
# and consistent — no more "how did I run this again?"

.PHONY: help install train test lint run docker-build docker-run compose-up compose-down drift clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n", $$1, $$2}'

install: ## Install runtime + dev dependencies
	pip install -r requirements.txt -r requirements-dev.txt

train: ## Train the model artifact
	python training/train.py --n-samples 20000

test: ## Run the test suite
	pytest -v

lint: ## Lint the codebase
	ruff check app/ training/ monitoring/ tests/

run: ## Run the API locally (reload on change)
	uvicorn app.main:app --reload --port 8000

docker-build: ## Build the container image
	docker build --build-arg MODEL_VERSION=local -t churn-api:local .

docker-run: ## Run the container locally
	docker run --rm -p 8000:8000 churn-api:local

compose-up: ## Start API + Prometheus + Grafana
	docker compose up --build

compose-down: ## Stop the local stack
	docker compose down

drift: ## Run drift detection against local logs (set BASELINE and LOGS)
	python monitoring/drift_detection.py --baseline $(BASELINE) --logs $(LOGS)

clean: ## Remove caches and the local model
	rm -rf .pytest_cache .ruff_cache __pycache__ app/ml/model.joblib
	find . -type d -name __pycache__ -exec rm -rf {} +
