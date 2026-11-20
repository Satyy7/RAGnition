"""Centralised, validated configuration.

Two sources, two purposes:
- Settings (environment / .env): secrets and per-machine values.
- ProvidersConfig (config/providers.yaml): which models to use and in what order.
Both are validated by Pydantic at startup, so a typo fails immediately instead of
mid-request.
"""

from __future__ import annotations

import ssl
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ragnition/core/config.py -> parents[3] is the project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

Role = Literal["generator", "judge", "embedding", "rerank"]


class ProviderSpec(BaseModel):
    kind: Literal["openai_compat", "jina", "cohere", "local"]
    base_url: str = ""
    # Name of the Settings field holding this provider's key. None = no key needed (local).
    api_key_setting: str | None = None

    @model_validator(mode="after")
    def _remote_needs_url_and_key(self) -> ProviderSpec:
        if self.kind != "local" and (not self.base_url or not self.api_key_setting):
            raise ValueError(f"provider kind '{self.kind}' requires base_url and api_key_setting")
        return self


class ModelSpec(BaseModel):
    provider: str
    model: str
    rpm: float | None = Field(default=None, gt=0)
    tpm: float | None = Field(default=None, gt=0)
    timeout_s: float = Field(default=60.0, gt=0)
    dimensions: int | None = Field(default=None, gt=0)  # embeddings only
    batch_size: int = Field(default=32, ge=1)  # embeddings only
    # Cache responses on disk? Worth it for remote calls (quota); not for cheap,
    # deterministic local models whose vectors would just fill the disk.
    cache: bool = True

    @property
    def slot_id(self) -> str:
        return f"{self.provider}:{self.model}"


class ResilienceSpec(BaseModel):
    max_retries_per_model: int = Field(default=2, ge=0)
    backoff_base_s: float = Field(default=0.5, ge=0)
    backoff_max_s: float = Field(default=8.0, ge=0)
    max_inline_wait_s: float = Field(default=5.0, ge=0)
    breaker_failure_threshold: int = Field(default=3, ge=1)
    breaker_cooldown_s: float = Field(default=60.0, gt=0)


class ProvidersConfig(BaseModel):
    providers: dict[str, ProviderSpec]
    chains: dict[Role, list[ModelSpec]]
    resilience: ResilienceSpec = Field(default_factory=ResilienceSpec)

    @model_validator(mode="after")
    def _validate_chains(self) -> ProvidersConfig:
        for role, chain in self.chains.items():
            if not chain:
                raise ValueError(f"chain '{role}' is empty")
            for spec in chain:
                if spec.provider not in self.providers:
                    raise ValueError(f"chain '{role}' references unknown provider '{spec.provider}'")
        if len(self.chains.get("embedding", [])) > 1:
            raise ValueError(
                "embedding chain must contain exactly one model: vectors from different "
                "embedding models are not comparable"
            )
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True
    providers_config_path: Path = PROJECT_ROOT / "config" / "providers.yaml"
    cache_path: Path = PROJECT_ROOT / "data" / "cache" / "model_calls.sqlite3"
    # Corporate networks often re-sign HTTPS with a company root certificate that
    # lives in the OS trust store but not in Python's certifi bundle.
    use_system_certs: bool = False
    # Where local model files (ONNX weights, tokenizers) are downloaded. Machine-
    # specific, so it lives in the environment (e.g. D:\ragnition\models).
    model_cache_dir: Path = PROJECT_ROOT / "data" / "models"

    # SecretStr hides the value in repr()/logs: print(settings) shows '**********'.
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    jina_api_key: SecretStr | None = None
    cohere_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    def secret(self, setting_name: str | None) -> str | None:
        """Return a key's plain value, or None if it is unset, blank, or not needed."""
        if setting_name is None:
            return None
        if setting_name not in type(self).model_fields:
            raise ValueError(f"unknown settings field '{setting_name}'")
        value = getattr(self, setting_name)
        if isinstance(value, SecretStr):
            plain = value.get_secret_value().strip()
            return plain or None
        return None


def tls_verify(settings: Settings) -> ssl.SSLContext | bool:
    """What to pass as httpx's `verify=`.

    True  -> httpx default: verify against the certifi bundle.
    SSLContext from truststore -> verify against the OS trust store (Windows
    certificate store here), which includes corporate root certificates.
    Verification is never disabled.
    """
    if not settings.use_system_certs:
        return True
    import truststore  # imported lazily: only needed when opted in

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def load_providers_config(path: Path) -> ProvidersConfig:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return ProvidersConfig.model_validate(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
