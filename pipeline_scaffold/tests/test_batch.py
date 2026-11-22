"""Testes da Batch API e do coalescer (AD-15).

Sem rede: providers são dublês que registram o que receberiam. O ponto central
verificado aqui é que o caminho batch envia EXATAMENTE o mesmo payload do
caminho síncrono — se divergissem, o tier rodado em batch mediria outra coisa.
"""

import json
import threading

import pytest

from config import ALL_MODELS
from infra.batch import BatchCoalescer, BatchTimeout
from infra.cost_tracker import CostTracker
from infra.llm_client import (
    AnthropicProvider,
    FatalRequestError,
    GoogleProvider,
    LLMClient,
    LLMRequest,
    LLMResponse,
    OpenAIProvider,
    RateLimitError,
    RetryPolicy,
    _custom_id,
    _custom_index,
)


def _request(model="gpt-5-mini", suffix="s") -> LLMRequest:
    return LLMRequest(model=model, cacheable_prefix="CODIGO", dynamic_suffix=suffix)


def _response(text="ok") -> LLMResponse:
    return LLMResponse(text=text, input_tokens=10, output_tokens=5)


class _FakeBatchProvider:
    """Provider de mentira: um job, resultados fixos, contagem de submissões."""

    def __init__(self, results=None, *, pending_polls=0):
        self.results = results
        self.pending_polls = pending_polls
        self.submitted: list[list] = []
        self.polls = 0

    def submit_batch(self, requests):
        self.submitted.append(list(requests))
        return f"job-{len(self.submitted)}"

    def poll_batch(self, job_id, count):
        self.polls += 1
        if self.polls <= self.pending_polls:
            return None
        if self.results is not None:
            return list(self.results)
        return [_response(f"r{i}") for i in range(count)]


# ── Coalescer ──────────────────────────────────────────────────────────────


def test_coalescer_groups_concurrent_calls_into_one_job():
    # O ponto de AD-15: chamadas de runs distintos viram UM job de batch.
    provider = _FakeBatchProvider()
    coalescer = BatchCoalescer(max_size=4, window_sec=5.0, sleep=lambda s: None)
    barrier = threading.Barrier(4)
    out: dict[int, object] = {}

    def call(i):
        barrier.wait()  # garante que as 4 threads cheguem juntas à janela
        out[i] = coalescer.submit("openai|gpt-5-mini", provider, _request(suffix=str(i)))

    threads = [threading.Thread(target=call, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(provider.submitted) == 1  # um único job para as 4 chamadas
    assert len(provider.submitted[0]) == 4
    assert len(out) == 4


def test_coalescer_flushes_on_window_when_group_not_full():
    provider = _FakeBatchProvider()
    ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0])
    coalescer = BatchCoalescer(
        max_size=32, window_sec=1.0, sleep=lambda s: None, clock=lambda: next(ticks)
    )
    result = coalescer.submit("k", provider, _request())
    assert isinstance(result, LLMResponse)
    assert len(provider.submitted[0]) == 1


def test_coalescer_maps_results_by_position():
    provider = _FakeBatchProvider(results=[_response("a"), _response("b")])
    coalescer = BatchCoalescer(max_size=2, window_sec=5.0, sleep=lambda s: None)
    barrier = threading.Barrier(2)
    got: dict[int, object] = {}

    def call(i):
        barrier.wait()
        got[i] = coalescer.submit("k", provider, _request(suffix=str(i)))

    threads = [threading.Thread(target=call, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    texts = sorted(r.text for r in got.values())
    assert texts == ["a", "b"]


def test_coalescer_raises_item_exception_in_its_own_caller():
    # Erro de UM item não contamina os demais (AD-11 classifica no chamador).
    provider = _FakeBatchProvider(results=[RateLimitError("429"), _response("ok")])
    coalescer = BatchCoalescer(max_size=1, window_sec=0.0, sleep=lambda s: None)
    with pytest.raises(RateLimitError):
        coalescer.submit("k", provider, _request())


def test_coalescer_times_out_when_job_never_finishes():
    provider = _FakeBatchProvider(pending_polls=10_000)
    ticks = iter([0.0, 0.0, 0.0] + [999.0] * 20)
    coalescer = BatchCoalescer(
        max_size=1,
        window_sec=0.0,
        max_wait_sec=10.0,
        sleep=lambda s: None,
        clock=lambda: next(ticks),
    )
    with pytest.raises(BatchTimeout):
        coalescer.submit("k", provider, _request())


def test_coalescer_poll_returns_none_until_ready():
    provider = _FakeBatchProvider(pending_polls=3)
    coalescer = BatchCoalescer(max_size=1, window_sec=0.0, sleep=lambda s: None)
    assert isinstance(coalescer.submit("k", provider, _request()), LLMResponse)
    assert provider.polls == 4


# ── Integração com o LLMClient ─────────────────────────────────────────────


def test_client_uses_coalescer_when_present():
    provider = _FakeBatchProvider()
    coalescer = BatchCoalescer(max_size=1, window_sec=0.0, sleep=lambda s: None)
    client = LLMClient(providers={"openai": provider}, coalescer=coalescer)
    tracker = CostTracker(tier="cost", batch=True)
    client.complete(_request(), cost_tracker=tracker)
    assert len(provider.submitted) == 1  # foi pela Batch API, não pelo síncrono


def test_client_stays_sync_without_coalescer():
    calls = []

    class _Sync(_FakeBatchProvider):
        def complete(self, request):
            calls.append(request)
            return _response()

    provider = _Sync()
    client = LLMClient(providers={"openai": provider})
    client.complete(_request(), cost_tracker=CostTracker(tier="cost", batch=True))
    assert len(calls) == 1
    assert provider.submitted == []


def test_dev_model_with_base_url_never_uses_batch():
    # Endpoints compatíveis (Ollama, LM Studio) não têm Batch API.
    calls = []

    class _Sync(_FakeBatchProvider):
        def complete(self, request):
            calls.append(request)
            return _response()

    provider = _Sync()
    coalescer = BatchCoalescer(max_size=1, window_sec=0.0, sleep=lambda s: None)
    # A chave do provider depende do base_url configurado por ambiente: derive-a
    # do próprio config em vez de fixar o endpoint padrão.
    key = LLMClient._provider_key(ALL_MODELS["dev-free"])
    client = LLMClient(providers={key: provider}, coalescer=coalescer)
    client.complete(_request(model="dev-free"), cost_tracker=CostTracker(tier="cost", batch=True))
    assert len(calls) == 1
    assert provider.submitted == []


def test_client_retry_applies_to_batch_path():
    # AD-11: erro transitório vindo do batch é retentado pelo chamador.
    provider = _FakeBatchProvider(results=[RateLimitError("429")])
    attempts = {"n": 0}

    class _Flaky(_FakeBatchProvider):
        def poll_batch(self, job_id, count):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return [RateLimitError("429")]
            return [_response("ok")]

    provider = _Flaky()
    coalescer = BatchCoalescer(max_size=1, window_sec=0.0, sleep=lambda s: None)
    client = LLMClient(
        providers={"openai": provider},
        coalescer=coalescer,
        retry=RetryPolicy(sleep=lambda s: None, rng=lambda: 0.0),
    )
    resp = client.complete(_request(), cost_tracker=CostTracker(tier="cost", batch=True))
    assert resp.text == "ok"
    assert len(provider.submitted) == 2  # reenviado num novo job


# ── Paridade de payload síncrono × batch ───────────────────────────────────


def test_anthropic_batch_payload_matches_sync():
    req = _request(model="claude-sonnet-4-6")
    provider = AnthropicProvider(client=object())
    params = provider._params(req)
    assert params["temperature"] == req.temperature
    assert params["max_tokens"] == req.max_tokens
    # Prefixo cacheável marcado para caching (AD-15), em ambos os caminhos.
    assert params["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert params["system"][-1]["text"].endswith("CODIGO")


def test_openai_batch_payload_matches_sync():
    req = _request()
    provider = OpenAIProvider(client=object())
    params = provider._params(req)
    assert params["model"] == "gpt-5-mini"
    assert params["temperature"] == req.temperature
    # Prefixo estável primeiro, para a OpenAI cachear automaticamente (AD-15).
    assert params["messages"][0]["content"] == "CODIGO"


def test_openai_batch_jsonl_is_wellformed():
    captured = {}

    class _Files:
        def create(self, *, file, purpose):
            captured["payload"] = file[1]
            captured["purpose"] = purpose
            return type("F", (), {"id": "file-1"})()

    class _Batches:
        def create(self, *, input_file_id, endpoint, completion_window):
            captured["endpoint"] = endpoint
            captured["window"] = completion_window
            return type("B", (), {"id": "batch-1"})()

    class _Client:
        files = _Files()
        batches = _Batches()

    provider = OpenAIProvider(client=_Client())
    job_id = provider.submit_batch([_request(suffix="a"), _request(suffix="b")])

    assert job_id == "batch-1"
    assert captured["purpose"] == "batch"
    assert captured["window"] == "24h"
    lines = [json.loads(x) for x in captured["payload"].decode().strip().split("\n")]
    assert [x["custom_id"] for x in lines] == ["req-0", "req-1"]
    assert all(x["url"] == "/v1/chat/completions" for x in lines)
    assert lines[0]["body"]["messages"][-1]["content"] == "a"


def test_openai_poll_parses_output_jsonl():
    body = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
        "choices": [{"message": {"content": "gerado"}}],
        "model": "gpt-5-mini",
    }
    output = "\n".join(
        [
            json.dumps({"custom_id": "req-1", "response": {"status_code": 200, "body": body}}),
            json.dumps({"custom_id": "req-0", "response": {"status_code": 200, "body": body}}),
        ]
    )

    class _Client:
        class batches:
            @staticmethod
            def retrieve(job_id):
                return type(
                    "B",
                    (),
                    {"status": "completed", "output_file_id": "out", "error_file_id": None},
                )()

        class files:
            @staticmethod
            def content(fid):
                return type("C", (), {"text": output})()

    results = OpenAIProvider(client=_Client()).poll_batch("batch-1", 2)
    assert len(results) == 2
    # Ordem restaurada pelo custom_id, não pela ordem do arquivo.
    assert all(isinstance(r, LLMResponse) for r in results)
    assert results[0].cached_input_tokens == 80
    assert results[0].input_tokens == 100


def test_openai_poll_returns_none_while_processing():
    class _Client:
        class batches:
            @staticmethod
            def retrieve(job_id):
                return type("B", (), {"status": "in_progress"})()

    assert OpenAIProvider(client=_Client()).poll_batch("b", 1) is None


def test_openai_poll_marks_error_file_items_fatal():
    err = json.dumps({"custom_id": "req-0", "error": {"message": "bad request"}})

    class _Client:
        class batches:
            @staticmethod
            def retrieve(job_id):
                return type(
                    "B",
                    (),
                    {"status": "completed", "output_file_id": None, "error_file_id": "err"},
                )()

        class files:
            @staticmethod
            def content(fid):
                return type("C", (), {"text": err})()

    results = OpenAIProvider(client=_Client()).poll_batch("b", 1)
    assert isinstance(results[0], FatalRequestError)


def test_google_batch_requires_single_model():
    provider = GoogleProvider(client=object())
    with pytest.raises(ValueError, match="modelo único"):
        provider.submit_batch([_request(model="gemini-3.5-flash"), _request(model="gpt-5-mini")])


def test_missing_items_become_retryable_errors():
    class _Client:
        class batches:
            @staticmethod
            def retrieve(job_id):
                return type(
                    "B",
                    (),
                    {"status": "completed", "output_file_id": None, "error_file_id": None},
                )()

    results = OpenAIProvider(client=_Client()).poll_batch("b", 3)
    assert len(results) == 3
    assert all(isinstance(r, Exception) for r in results)


# ── custom_id ──────────────────────────────────────────────────────────────


def test_custom_id_roundtrip():
    assert _custom_index(_custom_id(7)) == 7
    assert _custom_index("lixo") == -1
    assert _custom_index("req-abc") == -1


# ── Tokens de raciocínio ───────────────────────────────────────────────────


def test_openai_sync_extracts_reasoning_tokens():
    # `completion_tokens` da OpenAI JÁ inclui o raciocínio; reasoning é subconjunto.
    usage = type(
        "U",
        (),
        {
            "prompt_tokens": 100,
            "completion_tokens": 2000,
            "prompt_tokens_details": None,
            "completion_tokens_details": type("D", (), {"reasoning_tokens": 1800})(),
        },
    )()
    body = type(
        "B",
        (),
        {
            "usage": usage,
            "choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})()],
        },
    )()
    resp = OpenAIProvider._to_response(body, "gpt-5-mini")
    assert resp.output_tokens == 2000
    assert resp.reasoning_tokens == 1800


def test_openai_batch_body_extracts_reasoning_tokens():
    body = {
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 500,
            "completion_tokens_details": {"reasoning_tokens": 400},
        },
        "choices": [{"message": {"content": "x"}}],
    }
    resp = OpenAIProvider._to_response(body, "gpt-5-mini")
    assert resp.output_tokens == 500
    assert resp.reasoning_tokens == 400


def test_gemini_adds_thoughts_to_billed_output():
    # Gemini reporta thoughts FORA de candidates, mas cobra como output.
    meta = type(
        "M",
        (),
        {
            "prompt_token_count": 50,
            "candidates_token_count": 200,
            "thoughts_token_count": 300,
            "cached_content_token_count": 0,
        },
    )()
    resp = GoogleProvider._to_response(type("R", (), {"usage_metadata": meta, "text": "t"})(), "g")
    assert resp.output_tokens == 500  # 200 visíveis + 300 de raciocínio
    assert resp.reasoning_tokens == 300


# ── Detecção de truncagem (AD-19) ──────────────────────────────────────────


def _openai_body(finish_reason: str) -> dict:
    return {
        "usage": {"prompt_tokens": 10, "completion_tokens": 16384},
        "choices": [{"message": {"content": "def test_x("}, "finish_reason": finish_reason}],
    }


@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [("length", True), ("stop", False)],
)
def test_openai_batch_body_flags_truncation(finish_reason, expected):
    assert (
        OpenAIProvider._to_response(_openai_body(finish_reason), "gpt-5-mini").truncated is expected
    )


@pytest.mark.parametrize(("reason", "expected"), [("length", True), ("stop", False)])
def test_openai_sync_flags_truncation(reason, expected):
    usage = type(
        "U",
        (),
        {
            "prompt_tokens": 10,
            "completion_tokens": 16384,
            "prompt_tokens_details": None,
            "completion_tokens_details": None,
        },
    )()
    choice = type(
        "C", (), {"message": type("M", (), {"content": "x"})(), "finish_reason": reason}
    )()
    body = type("B", (), {"usage": usage, "choices": [choice]})()
    assert OpenAIProvider._to_response(body, "gpt-5-mini").truncated is expected


@pytest.mark.parametrize(("stop_reason", "expected"), [("max_tokens", True), ("end_turn", False)])
def test_anthropic_flags_truncation(stop_reason, expected):
    usage = type("U", (), {"input_tokens": 10, "output_tokens": 8192})()
    message = type(
        "M",
        (),
        {
            "usage": usage,
            "content": [type("T", (), {"type": "text", "text": "x"})()],
            "stop_reason": stop_reason,
        },
    )()
    assert AnthropicProvider._to_response(message, "claude").truncated is expected


@pytest.mark.parametrize(("reason", "expected"), [("MAX_TOKENS", True), ("STOP", False)])
def test_gemini_flags_truncation(reason, expected):
    # O SDK entrega enum; comparar pelo `.name` cobre enum e string crua.
    meta = type(
        "M",
        (),
        {
            "prompt_token_count": 10,
            "candidates_token_count": 8192,
            "thoughts_token_count": 0,
            "cached_content_token_count": 0,
        },
    )()
    enum_like = type("E", (), {"name": reason})()
    resp = type(
        "R",
        (),
        {
            "usage_metadata": meta,
            "text": "x",
            "candidates": [type("C", (), {"finish_reason": enum_like})()],
        },
    )()
    assert GoogleProvider._to_response(resp, "gemini").truncated is expected


def test_truncated_defaults_false_when_provider_omits_reason():
    # Dublês e endpoints compatíveis podem não expor o campo: não pode explodir.
    body = {"usage": {"prompt_tokens": 1, "completion_tokens": 2}, "choices": [{"message": {}}]}
    assert OpenAIProvider._to_response(body, "m").truncated is False


def test_reasoning_tokens_do_not_change_cost():
    # São subconjunto de output_tokens: precificar de novo seria contar em dobro.
    from infra.cost_tracker import price_call

    tracker = CostTracker(tier="cost", batch=False)
    cost = tracker.record("gpt-5-mini", input_tokens=100, output_tokens=2000, reasoning_tokens=1800)
    assert cost == pytest.approx(
        price_call("gpt-5-mini", input_tokens=100, output_tokens=2000, batch=False)
    )
    assert tracker.tokens.reasoning_tokens == 1800
    assert tracker.tokens.output_tokens == 2000
