import ssl

import pytest
from pydantic import SecretStr, ValidationError

from ragnition.core.config import PROJECT_ROOT, ProvidersConfig, Settings, load_providers_config, tls_verify


def test_shipped_providers_yaml_is_valid():
    config = load_providers_config(PROJECT_ROOT / "config" / "providers.yaml")
    assert {"generator", "judge", "embedding", "rerank"} <= set(config.chains)
    for spec in (s for chain in config.chains.values() for s in chain):
        setting = config.providers[spec.provider].api_key_setting
        assert setting is None or setting in Settings.model_fields


def test_remote_provider_requires_url_and_key():
    raw = {"providers": {"p": {"kind": "openai_compat"}}, "chains": {}}
    with pytest.raises(ValidationError, match="requires base_url and api_key_setting"):
        ProvidersConfig.model_validate(raw)


def _config(**chains):
    return {
        "providers": {"p": {"kind": "openai_compat", "base_url": "http://x", "api_key_setting": "groq_api_key"}},
        "chains": chains,
    }


def test_embedding_chain_must_have_one_model():
    two = [{"provider": "p", "model": "a"}, {"provider": "p", "model": "b"}]
    with pytest.raises(ValidationError, match="exactly one model"):
        ProvidersConfig.model_validate(_config(embedding=two))


def test_unknown_provider_rejected():
    with pytest.raises(ValidationError, match="unknown provider"):
        ProvidersConfig.model_validate(_config(generator=[{"provider": "nope", "model": "a"}]))


def test_tls_verify_defaults_to_certifi_and_never_disables():
    assert tls_verify(Settings(_env_file=None, use_system_certs=False)) is True
    ctx = tls_verify(Settings(_env_file=None, use_system_certs=True))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_secret_blank_is_none_and_value_is_hidden():
    s = Settings(_env_file=None, groq_api_key=SecretStr("  "), gemini_api_key=SecretStr("zz-s3cr3t-zz"))
    assert s.secret("groq_api_key") is None
    assert s.secret("gemini_api_key") == "zz-s3cr3t-zz"
    assert "zz-s3cr3t-zz" not in repr(s)
