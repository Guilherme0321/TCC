"""Testes do cliente LLM: retry/backoff (AD-11) e contabilização (AD-15)."""

from types import SimpleNamespace

import pytest

from infra.cost_tracker import CostTracker
from infra.llm_client import (
    FatalRequestError,
    LLMClient,
    LLMRequest,
    LLMResponse,
    OverloadError,
    Provider,
    RateLimitError,
    RetryPolicy,
    _classify_batch_error,
    _classify_status,
)


class FakeProvider(Provider):
    """Provider controlável: falha `fail_times` vezes com `error`, depois responde."""

    def __init__(self, *, error=None, fail_times=0, response=None):
        self.error = error
        self.fail_times = fail_times
        self.calls = 0
        self.response = response or LLMResponse(
            text="ok", input_tokens=8000, output_tokens=3000, cached_input_tokens=5000
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        self.last_request = request
        if self.calls <= self.fail_times:
            raise self.error
        return self.response


def _no_sleep_policy():
    return RetryPolicy(sleep=lambda _s: None, rng=lambda: 0.0)


def _request(model="gpt-5-mini"):
    return LLMRequest(model=model, cacheable_prefix="codigo fonte", dynamic_suffix="faça X")


# ── RetryPolicy ────────────────────────────────────────────────────────────


def test_retries_then_succeeds_on_rate_limit():
    policy = _no_sleep_policy()
    provider = FakeProvider(error=RateLimitError("429"), fail_times=3)
    result = policy.execute(lambda: provider.complete(_request()))
    assert result.text == "ok"
    assert provider.calls == 4  # 3 falhas + 1 sucesso


def test_gives_up_after_max_attempts():
    policy = _no_sleep_policy()  # rate_limit: 6 tentativas
    provider = FakeProvider(error=RateLimitError("429"), fail_times=99)
    with pytest.raises(RateLimitError):
        policy.execute(lambda: provider.complete(_request()))
    assert provider.calls == 6


def test_overload_uses_its_own_limit():
    policy = _no_sleep_policy()  # overload: 4 tentativas
    provider = FakeProvider(error=OverloadError("503"), fail_times=99)
    with pytest.raises(OverloadError):
        policy.execute(lambda: provider.complete(_request()))
    assert provider.calls == 4


def test_fatal_error_not_retried():
    policy = _no_sleep_policy()
    provider = FakeProvider(error=FatalRequestError("400"), fail_times=99)
    with pytest.raises(FatalRequestError):
        policy.execute(lambda: provider.complete(_request()))
    assert provider.calls == 1  # sem retry (AD-11)


def test_backoff_respects_retry_after():
    slept = []
    policy = RetryPolicy(sleep=lambda s: slept.append(s), rng=lambda: 0.0)
    provider = FakeProvider(error=RateLimitError("429", retry_after=12.0), fail_times=1)
    policy.execute(lambda: provider.complete(_request()))
    assert slept == [12.0]  # respeitou retry-after


def test_classify_status():
    assert isinstance(_classify_status(429, ""), RateLimitError)
    assert isinstance(_classify_status(503, ""), OverloadError)
    assert isinstance(_classify_status(529, ""), OverloadError)
    assert isinstance(_classify_status(400, ""), FatalRequestError)
    assert isinstance(_classify_status(401, ""), FatalRequestError)
    assert isinstance(_classify_status(422, ""), FatalRequestError)


def test_403_is_fatal_not_retryable():
    """403 é chave sem permissão: retentar não conserta, é bug de setup (AD-11)."""
    assert isinstance(_classify_status(403, "permission denied"), FatalRequestError)


def test_permission_error_is_fatal_by_both_paths():
    """Os dois classificadores não podem divergir sobre o MESMO erro (AD-11).

    O caminho síncrono vê o status HTTP; o caminho batch vê o corpo do erro do
    item. Antes desta correção, 403 caía em `NetworkTimeout` num e em
    `FatalRequestError` no outro — 3 tentativas com backoff contra falha
    imediata, para uma causa que nenhuma retentativa resolve.
    """
    by_status = _classify_status(403, "permission denied")
    by_body = _classify_batch_error(
        SimpleNamespace(error=SimpleNamespace(type="permission_error", message="denied"))
    )
    assert type(by_status) is type(by_body) is FatalRequestError


# ── LLMClient ──────────────────────────────────────────────────────────────


def test_client_records_cost_with_cache():
    provider = FakeProvider()
    client = LLMClient(providers={"openai": provider}, retry=_no_sleep_policy())
    tracker = CostTracker(tier="cost", batch=True)

    resp = client.complete(_request("gpt-5-mini"), cost_tracker=tracker)

    assert resp.text == "ok"
    assert provider.calls == 1
    assert tracker.run_cost > 0  # custo real contabilizado


def test_client_unknown_model():
    client = LLMClient(providers={"openai": FakeProvider()})
    tracker = CostTracker(tier="cost")
    with pytest.raises(ValueError):
        client.complete(_request("modelo-inexistente"), cost_tracker=tracker)


def test_dev_model_resolves_api_model_and_costs_zero():
    """Modelos dev (gratuitos): endpoint próprio, `api_model` traduzido, custo $0."""
    dev_models = {
        "dev-free": {
            "provider": "openai",
            "tier": "cost",
            "price_in": 0.0,
            "price_out": 0.0,
            "api_model": "qwen2.5-coder:7b",
            "base_url": "http://fake:11434/v1",
            "api_key": "ollama",
        }
    }
    provider = FakeProvider()
    client = LLMClient(
        providers={"openai@http://fake:11434/v1": provider},
        models=dev_models,
        retry=_no_sleep_policy(),
    )
    tracker = CostTracker(tier="cost", models=dev_models)

    resp = client.complete(_request("dev-free"), cost_tracker=tracker)

    assert resp.text == "ok"
    assert provider.last_request.model == "qwen2.5-coder:7b"  # nome no endpoint
    assert tracker.run_cost == 0.0  # gratuito: nunca dispara breaker
    assert tracker.tokens.input_tokens == 8000  # tokens ainda são registrados


def test_client_routes_by_provider():
    anthropic_provider = FakeProvider(
        response=LLMResponse(text="claude", input_tokens=1, output_tokens=1)
    )
    client = LLMClient(providers={"anthropic": anthropic_provider}, retry=_no_sleep_policy())
    tracker = CostTracker(tier="cost")
    resp = client.complete(_request("claude-sonnet-4-6"), cost_tracker=tracker)
    assert resp.text == "claude"


def test_anthropic_omite_temperature_em_modelos_que_a_rejeitam():
    """Sonnet 5 devolve 400 para `temperature`; o payload precisa omiti-la (AD-25)."""
    from infra.llm_client import AnthropicProvider, LLMRequest

    provider = AnthropicProvider(client=object())
    params = provider._params(
        LLMRequest(model="claude-sonnet-5", cacheable_prefix="x", max_tokens=100)
    )
    assert "temperature" not in params


def test_anthropic_mantem_temperature_onde_a_api_aceita():
    """A omissão é cirúrgica: modelos que aceitam continuam recebendo AD-18."""
    from config import TEMPERATURE
    from infra.llm_client import AnthropicProvider, LLMRequest

    provider = AnthropicProvider(client=object())
    params = provider._params(
        LLMRequest(model="claude-sonnet-4-6", cacheable_prefix="x", max_tokens=100)
    )
    assert params["temperature"] == TEMPERATURE


def test_anthropic_envia_effort_low_no_sonnet5():
    """Sem effort, o Sonnet 5 gasta o teto de saída pensando e devolve vazio (AD-26)."""
    from infra.llm_client import AnthropicProvider, LLMRequest

    provider = AnthropicProvider(client=object())
    params = provider._params(
        LLMRequest(model="claude-sonnet-5", cacheable_prefix="x", max_tokens=100)
    )
    assert params["output_config"] == {"effort": "low"}


def test_anthropic_nao_envia_effort_em_modelo_sem_configuracao():
    """`output_config` só aparece onde AD-26 define um nível — nunca por padrão."""
    from infra.llm_client import AnthropicProvider, LLMRequest

    provider = AnthropicProvider(client=object())
    params = provider._params(
        LLMRequest(model="claude-sonnet-4-6", cacheable_prefix="x", max_tokens=100)
    )
    assert "output_config" not in params
