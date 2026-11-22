"""Cliente LLM multi-provedor: Strategy, retry/backoff, caching e contabilização.

- AD-01: sem frameworks de agentes; wrapper próprio, fluxo transparente.
- AD-11: retry/backoff por classe de erro (429: 5–6 tentativas respeitando
  `retry-after`; 503/529: ~4; timeout de rede: ~3; 400/401/422: falha imediata).
- AD-15: prompt caching sempre ativo — o código-fonte vai no PREFIXO estável
  (`cacheable_prefix`), reaproveitado entre arquiteturas, repetições e iterações.
  Tokens reais (incluindo cache hits) são repassados ao cost_tracker.

Batch API (AD-15): cada provedor implementa `submit_batch`/`poll_batch` além do
`complete` síncrono. O payload é montado UMA vez por `_params()` e reutilizado
nos dois caminhos — garantia de que o batch envia exatamente o mesmo prompt,
modelo e temperatura que o síncrono (se divergissem, o tier batch mediria outra
coisa). O agrupamento entre runs é feito pelo `BatchCoalescer` (infra/batch.py),
injetado pelo runner; sem coalescer, o cliente cai no caminho síncrono — usado
pelo smoke test e pelos testes unitários.
"""

from __future__ import annotations

import json
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace

from config import (
    ALL_MODELS,
    NO_SAMPLING_PARAMS,
    REASONING_EFFORT,
    RETRY_MAX_ATTEMPTS,
    TEMPERATURE,
)
from infra.batch import BatchCoalescer, BatchJobFailed
from infra.cost_tracker import CostTracker

logger = logging.getLogger("llm")

# ── Classificação de erros (AD-11) ─────────────────────────────────────────


class TransientError(Exception):
    """Erro transitório, retentável."""

    category: str = "network_timeout"


class RateLimitError(TransientError):
    """429. Respeita `retry_after` quando presente (AD-11)."""

    category = "rate_limit"

    def __init__(self, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class OverloadError(TransientError):
    """503/529 (AD-11)."""

    category = "overload"


class NetworkTimeout(TransientError):
    """Timeout de rede (AD-11)."""

    category = "network_timeout"


class FatalRequestError(Exception):
    """400/401/422: erro de request — falha imediata, sem retry (AD-11)."""


# ── Contratos de request/response ──────────────────────────────────────────


@dataclass
class LLMRequest:
    model: str
    # Prefixo estável e cacheável: código-fonte + instruções fixas (AD-15).
    cacheable_prefix: str
    # Conteúdo dinâmico da iteração (traceback resumido, métricas, feedback…).
    dynamic_suffix: str = ""
    system: str | None = None
    temperature: float = TEMPERATURE
    max_tokens: int = 4096


@dataclass
class LLMResponse:
    """Resposta normalizada de qualquer provedor.

    `output_tokens` é sempre o total FATURADO como output, já incluindo
    raciocínio; `reasoning_tokens` é um subconjunto dele, para instrumentação.
    Cada provider é responsável por normalizar isso — os SDKs divergem: a
    OpenAI já embute o raciocínio em `completion_tokens`, enquanto o Gemini
    reporta `thoughts_token_count` fora de `candidates_token_count`.

    `truncated` indica que a geração parou por ter batido no teto de saída, e
    não por o modelo ter terminado. É a diferença entre "o modelo respondeu
    isto" e "o modelo foi cortado no meio" — sem esse sinal, saída truncada
    chega aos agentes como resposta legítima e vira `ValidationError` ou
    `SyntaxError` sem causa aparente (a falha silenciosa que motivou AD-19).
    Normalizado dos três SDKs: `finish_reason == "length"` (OpenAI),
    `stop_reason == "max_tokens"` (Anthropic), `MAX_TOKENS` (Gemini).
    """

    text: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    # Tokens gravados no cache nesta chamada, cobrados a 1.25x na Anthropic
    # (AD-25). OpenAI e Google não cobram escrita e deixam em 0.
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    truncated: bool = False
    model: str = ""
    raw: object = field(default=None, repr=False)


# ── Política de retry (AD-11) ──────────────────────────────────────────────


class RetryPolicy:
    """Backoff exponencial com jitter, tentativas por categoria de erro (AD-11).

    `sleep` e `rng` são injetáveis para testes determinísticos.
    """

    def __init__(
        self,
        *,
        max_attempts: dict[str, int] = RETRY_MAX_ATTEMPTS,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        sleep=time.sleep,
        rng=random.random,
    ) -> None:
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.sleep = sleep
        self.rng = rng

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after + self.rng() * 0.5  # respeita retry-after + jitter leve
        delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        return delay + self.rng() * delay  # jitter proporcional (full jitter)

    def execute(self, fn):
        """Executa `fn` aplicando a política. Re-levanta ao esgotar/ser fatal."""
        seen: dict[str, int] = {}
        while True:
            try:
                return fn()
            except FatalRequestError:
                raise  # sem retry (AD-11)
            except TransientError as exc:
                cat = exc.category
                seen[cat] = seen.get(cat, 0) + 1
                limit = self.max_attempts.get(cat, 3)
                if seen[cat] >= limit:
                    raise
                retry_after = getattr(exc, "retry_after", None)
                self.sleep(self._backoff(seen[cat], retry_after))


# ── Providers (Strategy) ───────────────────────────────────────────────────


class Provider(ABC):
    """Estratégia de um provedor. Mapeia erros do SDK para a taxonomia AD-11.

    Três operações: `complete` (síncrona) e o par `submit_batch`/`poll_batch`
    da Batch API (AD-15). `poll_batch` devolve `None` enquanto o job processa e,
    ao concluir, uma lista com um resultado por item na ORDEM de submissão —
    cada item é uma `LLMResponse` ou a exceção correspondente (taxonomia AD-11).

    Só `complete` é abstrato: os dois métodos de batch têm implementação padrão
    que falha explicitamente, de modo que um provedor sem Batch API (endpoints
    compatíveis como Ollama) e os dublês de teste síncronos continuem válidos.
    """

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse: ...

    def submit_batch(self, requests: list[LLMRequest]) -> str:
        raise NotImplementedError(f"{type(self).__name__} não implementa Batch API (AD-15)")

    def poll_batch(self, job_id: str, count: int) -> list | None:
        raise NotImplementedError(f"{type(self).__name__} não implementa Batch API (AD-15)")


class AnthropicProvider(Provider):
    def __init__(self, client=None) -> None:
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic()
        return self._client

    def _params(self, request: LLMRequest) -> dict:
        """Payload da mensagem — idêntico no caminho síncrono e no batch."""
        system_blocks = []
        if request.system:
            system_blocks.append({"type": "text", "text": request.system})
        # Prefixo cacheável marcado com cache_control (AD-15).
        system_blocks.append(
            {
                "type": "text",
                "text": request.cacheable_prefix,
                "cache_control": {"type": "ephemeral"},
            }
        )
        params = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": system_blocks,
            "messages": [{"role": "user", "content": request.dynamic_suffix or "."}],
        }
        # AD-18 fixa a temperatura, mas a família Claude 4.7+ removeu os
        # parâmetros de amostragem e devolve 400 para qualquer valor. Omitir é
        # equivalente a enviar 1.0 (o default de todos os provedores em uso),
        # então a constante experimental continua idêntica entre as células —
        # muda só o que trafega no payload. Vale nos dois caminhos, síncrono e
        # batch, porque ambos passam por aqui.
        if request.model not in NO_SAMPLING_PARAMS:
            params["temperature"] = request.temperature
        # Esforço de raciocínio (AD-26). Sem isto o Sonnet 5 consome o teto de
        # saída inteiro pensando e devolve texto vazio.
        esforco = REASONING_EFFORT.get(request.model)
        if esforco:
            params["output_config"] = {"effort": esforco}
        return params

    @staticmethod
    def _to_response(message, model: str) -> LLMResponse:
        """Converte a resposta da Anthropic.

        `reasoning_tokens` fica em 0: com extended thinking a Anthropic embute
        os tokens de raciocínio em `output_tokens` e não os reporta em campo
        separado. O custo, portanto, já está correto — o que falta é apenas a
        sub-contagem instrumental, indisponível neste provedor.
        """
        usage = message.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        input_total = usage.input_tokens + cache_read + cache_creation
        text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        return LLMResponse(
            text=text,
            input_tokens=input_total,
            output_tokens=usage.output_tokens,
            cached_input_tokens=cache_read,
            cache_write_tokens=cache_creation,
            truncated=getattr(message, "stop_reason", None) == "max_tokens",
            model=model,
            raw=message,
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        resp = _anthropic_call(lambda: self.client.messages.create(**self._params(request)))
        return self._to_response(resp, request.model)

    def submit_batch(self, requests: list[LLMRequest]) -> str:
        payload = [
            {"custom_id": _custom_id(i), "params": self._params(r)} for i, r in enumerate(requests)
        ]
        batch = _anthropic_call(lambda: self.client.messages.batches.create(requests=payload))
        return batch.id

    def poll_batch(self, job_id: str, count: int) -> list | None:
        batch = _anthropic_call(lambda: self.client.messages.batches.retrieve(job_id))
        if batch.processing_status != "ended":
            return None

        out: list = [None] * count
        for item in _anthropic_call(lambda: self.client.messages.batches.results(job_id)):
            idx = _custom_index(item.custom_id)
            if not 0 <= idx < count:
                continue
            result = item.result
            if result.type == "succeeded":
                message = result.message
                out[idx] = self._to_response(message, message.model)
            elif result.type == "errored":
                out[idx] = _classify_batch_error(getattr(result, "error", None))
            else:  # canceled | expired
                out[idx] = OverloadError(f"item de batch {result.type} (AD-11)")
        return _fill_missing(out, count)


class OpenAIProvider(Provider):
    """Também atende endpoints compatíveis (Ollama, OpenRouter…) via `base_url`."""

    def __init__(
        self, client=None, *, base_url: str | None = None, api_key: str | None = None
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._api_key = api_key

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI

            kwargs = {}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._api_key:
                kwargs["api_key"] = self._api_key
            self._client = OpenAI(**kwargs)
        return self._client

    def _params(self, request: LLMRequest) -> dict:
        """Payload do chat — idêntico no caminho síncrono e no batch."""
        # OpenAI cacheia automaticamente prefixos estáveis: prefixo primeiro (AD-15).
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "system", "content": request.cacheable_prefix})
        messages.append({"role": "user", "content": request.dynamic_suffix or "."})
        return {
            "model": request.model,
            "temperature": request.temperature,
            "messages": messages,
            # A API oficial da OpenAI exige `max_completion_tokens` (a família
            # GPT-5 rejeita `max_tokens` com 400). Endpoints compatíveis
            # (Ollama, LM Studio…) só entendem `max_tokens` — daí o base_url
            # decidir qual chave usar.
            **(
                {"max_tokens": request.max_tokens}
                if self._base_url
                else {"max_completion_tokens": request.max_tokens}
            ),
        }

    @staticmethod
    def _to_response(body, model: str) -> LLMResponse:
        """Converte a resposta (objeto do SDK ou dict do JSONL de batch).

        `completion_tokens` da OpenAI JÁ inclui os tokens de raciocínio dos
        modelos da família GPT-5; `reasoning_tokens` é extraído à parte apenas
        para instrumentação (quanto do teto de saída virou raciocínio invisível
        em vez de código de teste).
        """
        if isinstance(body, dict):
            usage = body.get("usage") or {}
            details = usage.get("prompt_tokens_details") or {}
            out_details = usage.get("completion_tokens_details") or {}
            choices = body.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content") or ""
            return LLMResponse(
                text=text,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                cached_input_tokens=details.get("cached_tokens", 0) or 0,
                reasoning_tokens=out_details.get("reasoning_tokens", 0) or 0,
                truncated=choices[0].get("finish_reason") == "length",
                model=body.get("model") or model,
                raw=body,
            )
        usage = body.usage
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details else 0
        out_details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(out_details, "reasoning_tokens", 0) if out_details else 0
        return LLMResponse(
            text=body.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cached_input_tokens=cached or 0,
            reasoning_tokens=reasoning or 0,
            truncated=getattr(body.choices[0], "finish_reason", None) == "length",
            model=model,
            raw=body,
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        resp = _openai_call(lambda: self.client.chat.completions.create(**self._params(request)))
        return self._to_response(resp, request.model)

    def submit_batch(self, requests: list[LLMRequest]) -> str:
        """Sobe o JSONL de entrada e cria o job (a OpenAI exige arquivo, AD-15)."""
        lines = [
            json.dumps(
                {
                    "custom_id": _custom_id(i),
                    "method": "POST",
                    "url": _OPENAI_BATCH_ENDPOINT,
                    "body": self._params(r),
                },
                ensure_ascii=False,
            )
            for i, r in enumerate(requests)
        ]
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        upload = _openai_call(
            lambda: self.client.files.create(file=("batch.jsonl", payload), purpose="batch")
        )
        job = _openai_call(
            lambda: self.client.batches.create(
                input_file_id=upload.id,
                endpoint=_OPENAI_BATCH_ENDPOINT,
                completion_window="24h",
            )
        )
        return job.id

    def poll_batch(self, job_id: str, count: int) -> list | None:
        job = _openai_call(lambda: self.client.batches.retrieve(job_id))
        if job.status in ("validating", "in_progress", "finalizing"):
            return None
        if job.status != "completed":
            raise BatchJobFailed(f"job {job_id} terminou como {job.status} (AD-15)")

        out: list = [None] * count
        if job.output_file_id:
            content = _openai_call(lambda: self.client.files.content(job.output_file_id))
            for entry in _iter_jsonl(getattr(content, "text", "") or ""):
                idx = _custom_index(entry.get("custom_id", ""))
                if not 0 <= idx < count:
                    continue
                response = entry.get("response") or {}
                status = response.get("status_code", 0)
                if status == 200:
                    out[idx] = self._to_response(response.get("body") or {}, "")
                else:
                    out[idx] = _classify_status(status, json.dumps(response)[:500])
        if job.error_file_id:
            content = _openai_call(lambda: self.client.files.content(job.error_file_id))
            for entry in _iter_jsonl(getattr(content, "text", "") or ""):
                idx = _custom_index(entry.get("custom_id", ""))
                if 0 <= idx < count and out[idx] is None:
                    out[idx] = FatalRequestError(json.dumps(entry.get("error") or entry)[:500])
        return _fill_missing(out, count)


class GoogleProvider(Provider):
    def __init__(self, client=None) -> None:
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client()
        return self._client

    @staticmethod
    def _params(request: LLMRequest) -> dict:
        """Conteúdo e config — idênticos no caminho síncrono e no batch."""
        from google.genai import types as genai_types

        return {
            "contents": f"{request.cacheable_prefix}\n\n{request.dynamic_suffix}".strip(),
            "config": genai_types.GenerateContentConfig(
                temperature=request.temperature,
                max_output_tokens=request.max_tokens,
                system_instruction=request.system,
            ),
        }

    @staticmethod
    def _to_response(resp, model: str) -> LLMResponse:
        """Converte a resposta do Gemini.

        Diferente da OpenAI, o Gemini reporta `thoughts_token_count` FORA de
        `candidates_token_count`, mas cobra os dois como output. Somar aqui é
        o que mantém `output_tokens` significando "output faturado" — sem isso,
        o custo dos modelos com raciocínio ficaria subestimado.
        """
        meta = resp.usage_metadata
        cached = getattr(meta, "cached_content_token_count", 0) or 0
        thoughts = getattr(meta, "thoughts_token_count", 0) or 0
        candidates = getattr(meta, "candidates_token_count", 0) or 0
        cands = getattr(resp, "candidates", None) or []
        # `finish_reason` do Gemini é enum: comparar pelo nome cobre enum e str.
        reason = getattr(cands[0], "finish_reason", None) if cands else None
        return LLMResponse(
            text=resp.text or "",
            input_tokens=meta.prompt_token_count or 0,
            output_tokens=candidates + thoughts,
            cached_input_tokens=cached,
            reasoning_tokens=thoughts,
            truncated=getattr(reason, "name", reason) == "MAX_TOKENS",
            model=model,
            raw=resp,
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        resp = _google_call(
            lambda: self.client.models.generate_content(
                model=request.model, **self._params(request)
            )
        )
        return self._to_response(resp, request.model)

    def submit_batch(self, requests: list[LLMRequest]) -> str:
        """Job com requisições inline. O Gemini exige um único modelo por job."""
        from google.genai import types as genai_types

        models = {r.model for r in requests}
        if len(models) > 1:
            raise ValueError(f"batch do Gemini exige modelo único; recebido: {sorted(models)}")
        model = requests[0].model
        src = [genai_types.InlinedRequest(model=model, **self._params(r)) for r in requests]
        job = _google_call(lambda: self.client.batches.create(model=model, src=src))
        return job.name

    def poll_batch(self, job_id: str, count: int) -> list | None:
        job = _google_call(lambda: self.client.batches.get(name=job_id))
        state = _google_job_state(job)
        if state in _GOOGLE_PENDING_STATES:
            return None
        if state not in ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"):
            raise BatchJobFailed(f"job {job_id} terminou como {state} (AD-15)")

        # Sem custom_id na API inline: o mapeamento é pela ORDEM de submissão.
        out: list = [None] * count
        responses = getattr(getattr(job, "dest", None), "inlined_responses", None) or []
        for idx, item in enumerate(responses):
            if idx >= count:
                break
            error = getattr(item, "error", None)
            if error is not None:
                out[idx] = _classify_status(
                    getattr(error, "code", 0) or 0, str(getattr(error, "message", error))
                )
            elif getattr(item, "response", None) is not None:
                out[idx] = self._to_response(item.response, "")
        return _fill_missing(out, count)


# ── Mapeamento de erros por SDK (AD-11) ────────────────────────────────────

_OPENAI_BATCH_ENDPOINT = "/v1/chat/completions"
_GOOGLE_PENDING_STATES = frozenset(
    {
        "JOB_STATE_PENDING",
        "JOB_STATE_QUEUED",
        "JOB_STATE_RUNNING",
        "JOB_STATE_UPDATING",
        "JOB_STATE_PAUSED",
        "JOB_STATE_UNSPECIFIED",
    }
)


def _anthropic_call(fn):
    """Executa `fn` traduzindo exceções do SDK Anthropic para a taxonomia AD-11."""
    import anthropic

    try:
        return fn()
    except anthropic.RateLimitError as exc:
        raise RateLimitError(str(exc), _retry_after(exc)) from exc
    except anthropic.APITimeoutError as exc:
        raise NetworkTimeout(str(exc)) from exc
    except anthropic.APIStatusError as exc:
        raise _classify_status(exc.status_code, str(exc)) from exc


def _openai_call(fn):
    """Executa `fn` traduzindo exceções do SDK OpenAI para a taxonomia AD-11."""
    import openai

    try:
        return fn()
    except openai.RateLimitError as exc:
        raise RateLimitError(str(exc), _retry_after(exc)) from exc
    except openai.APITimeoutError as exc:
        raise NetworkTimeout(str(exc)) from exc
    except openai.APIStatusError as exc:
        raise _classify_status(exc.status_code, str(exc)) from exc


def _google_call(fn):
    """Executa `fn` traduzindo exceções do SDK Google para a taxonomia AD-11."""
    from google.genai import errors as genai_errors

    try:
        return fn()
    except genai_errors.APIError as exc:
        raise _classify_status(getattr(exc, "code", 0) or 0, str(exc)) from exc


def _google_job_state(job) -> str:
    """Nome do estado do job, tolerante a enum ou string."""
    state = getattr(job, "state", "")
    return getattr(state, "name", None) or str(state).rsplit(".", 1)[-1]


# ── Utilidades de batch (AD-15) ────────────────────────────────────────────

_CUSTOM_ID_PREFIX = "req-"


def _custom_id(index: int) -> str:
    """Identificador do item no job; a ordem de submissão é a chave do mapeamento."""
    return f"{_CUSTOM_ID_PREFIX}{index}"


def _custom_index(custom_id: str) -> int:
    """Índice de volta a partir do `custom_id`. -1 se irreconhecível."""
    if not custom_id.startswith(_CUSTOM_ID_PREFIX):
        return -1
    try:
        return int(custom_id[len(_CUSTOM_ID_PREFIX) :])
    except ValueError:
        return -1


def _iter_jsonl(text: str):
    """Itera objetos de um corpo JSONL, ignorando linhas inválidas."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _classify_batch_error(error: object) -> Exception:
    """Erro de um item individual do batch → taxonomia AD-11."""
    inner = getattr(error, "error", None)
    kind = str(getattr(inner, "type", "") or getattr(error, "type", "") or "")
    message = str(getattr(inner, "message", "") or error)
    if "rate_limit" in kind:
        return RateLimitError(message)
    if "overloaded" in kind or "api_error" in kind:
        return OverloadError(message)
    if kind in ("invalid_request_error", "authentication_error", "permission_error"):
        return FatalRequestError(message)
    return NetworkTimeout(message)


def _fill_missing(results: list, count: int) -> list:
    """Itens ausentes na resposta do job viram erro retentável (AD-11)."""
    for i in range(count):
        if results[i] is None:
            results[i] = NetworkTimeout(f"item {i} ausente no resultado do batch")
    return results


def _retry_after(exc: object) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers:
        value = headers.get("retry-after")
        if value:
            try:
                return float(value)
            except ValueError:
                return None
    return None


# Erro de request: retentar não muda o resultado, só queima tempo e tentativas
# (AD-11). 403 está aqui porque é o MESMO erro que `_classify_batch_error`
# já trata como fatal pelo corpo (`permission_error`) — sem ele, uma chave sem
# permissão falhava na hora por um caminho e rendia 3 tentativas com backoff
# pelo outro, para a mesma causa.
_FATAL_STATUS = frozenset({400, 401, 403, 422})


def _classify_status(status_code: int, message: str) -> Exception:
    if status_code == 429:
        return RateLimitError(message)
    if status_code in (503, 529):
        return OverloadError(message)
    if status_code in _FATAL_STATUS:
        return FatalRequestError(message)
    if status_code and status_code >= 500:
        return OverloadError(message)
    return NetworkTimeout(message)


# ── Cliente ────────────────────────────────────────────────────────────────

_PROVIDER_FACTORY = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "google": GoogleProvider,
}


class LLMClient:
    """Roteia modelo→provedor, aplica retry e contabiliza tokens no cost_tracker."""

    def __init__(
        self,
        *,
        providers: dict[str, Provider] | None = None,
        retry: RetryPolicy | None = None,
        models: dict = ALL_MODELS,
        coalescer: BatchCoalescer | None = None,
    ) -> None:
        self.models = models
        self.retry = retry or RetryPolicy()
        # Providers são instanciados sob demanda (lazy) por padrão.
        self._providers: dict[str, Provider] = providers or {}
        # Sem coalescer, as chamadas seguem síncronas (smoke test e testes unitários).
        self.coalescer = coalescer

    @staticmethod
    def _provider_key(spec: dict) -> str:
        name = spec["provider"]
        base_url = spec.get("base_url")
        # Endpoint customizado (modelos dev) ganha instância própria de provider.
        return f"{name}@{base_url}" if base_url else name

    def _provider_for(self, spec: dict) -> Provider:
        key = self._provider_key(spec)
        if key not in self._providers:
            kwargs = {}
            if spec.get("base_url"):
                kwargs = {"base_url": spec["base_url"], "api_key": spec.get("api_key")}
            self._providers[key] = _PROVIDER_FACTORY[spec["provider"]](**kwargs)
        return self._providers[key]

    def _use_batch(self, spec: dict) -> bool:
        """Endpoints compatíveis (Ollama, LM Studio…) não têm Batch API."""
        return self.coalescer is not None and not spec.get("base_url")

    def complete(self, request: LLMRequest, *, cost_tracker: CostTracker) -> LLMResponse:
        """Executa a chamada com retry e registra o custo real (incl. cache hits)."""
        spec = self.models.get(request.model)
        if spec is None:
            raise ValueError(f"modelo desconhecido: {request.model!r}")
        provider = self._provider_for(spec)
        # Modelos dev usam um nome diferente no endpoint (`api_model`); o custo
        # continua registrado sob a chave de config.
        config_model = request.model
        api_model = spec.get("api_model", request.model)
        if api_model != request.model:
            request = replace(request, model=api_model)

        if self._use_batch(spec):
            # Grupo por (provedor, modelo): o Gemini exige modelo único por job e
            # a homogeneidade simplifica o mapeamento nos outros dois (AD-15).
            key = f"{self._provider_key(spec)}|{request.model}"
            response = self.retry.execute(lambda: self.coalescer.submit(key, provider, request))
        else:
            response = self.retry.execute(lambda: provider.complete(request))
        if response.truncated:
            # Não é erro recuperável aqui: a resposta JÁ foi paga e o agente
            # chamador é quem decide o que fazer com ela. Mas precisa aparecer,
            # porque saída truncada vira falha sem causa aparente lá na frente.
            logger.warning(
                "saída TRUNCADA no teto de %d tokens (modelo=%s, output=%d, "
                "raciocínio=%d) — o texto está incompleto (AD-19)",
                request.max_tokens,
                config_model,
                response.output_tokens,
                response.reasoning_tokens,
            )
        cost_tracker.record(
            config_model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cached_input_tokens=response.cached_input_tokens,
            cache_write_tokens=response.cache_write_tokens,
            reasoning_tokens=response.reasoning_tokens,
        )
        return response
