"""
Configuration via environment variables (12-factor app principle III).

Senior note: configuration NEVER lives in code. It comes from the environment.
Same image runs in dev, staging, prod — only the env vars change. pydantic-settings
gives you typed, validated config with sane defaults.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_path: str = "app/ml/model.joblib"
    log_level: str = "INFO"
    # When you wire MLflow registry, this is where the URI would go.
    mlflow_tracking_uri: str = ""

    model_config = {"env_prefix": "", "protected_namespaces": ()}


settings = Settings()
