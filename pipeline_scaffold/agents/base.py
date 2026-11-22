"""Base comum dos agentes: composição de prompt + cliente + parsing (AD-01, AD-02).

Sem framework de agentes (AD-01): um agente é uma classe fina que compõe o
PromptLoader e o LLMClient, monta a requisição com o código-fonte no PREFIXO
cacheável (AD-15) e valida a saída no schema da sua fronteira (AD-02).

Falha de parsing/validação da saída do LLM é `AgentOutputError` — tratada como
falha de ITERAÇÃO pelo pipeline, nunca crash (AD-02).
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from infra.cost_tracker import CostTracker
from infra.llm_client import LLMClient, LLMRequest, LLMResponse
from infra.prompt_loader import Prompt, PromptLoader

_CODE_FENCE = re.compile(r"```(?:python|py)?\s*\n?(.*?)```", re.DOTALL)
_JSON_FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


class AgentOutputError(Exception):
    """Saída do LLM não pôde ser parseada/validada (falha de iteração, AD-02)."""


def strip_code_fences(text: str) -> str:
    """Extrai código Python, removendo cercas markdown se presentes."""
    m = _CODE_FENCE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def extract_json(text: str) -> str:
    """Isola o objeto JSON da resposta (remove cercas e prosa em volta)."""
    t = text.strip()
    m = _JSON_FENCE.search(t)
    if m:
        t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start : end + 1]
    return t


def _source_prefix(source_code: str) -> str:
    """Prefixo estável/cacheável com o código-fonte do projeto (AD-15)."""
    return f"# CÓDIGO-FONTE DO PROJETO (referência estável para cache)\n\n{source_code}"


class BaseAgent:
    """Agente genérico. Subclasses definem `prompt_name` e o método público."""

    __test__ = False  # 'TestGeneratorAgent' não é classe de teste do pytest

    prompt_name: str

    def __init__(
        self,
        *,
        client: LLMClient,
        prompt_loader: PromptLoader,
        model: str,
        cost_tracker: CostTracker,
        prompt_version: int | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.cost_tracker = cost_tracker
        self.prompt: Prompt = prompt_loader.load(self.prompt_name, version=prompt_version)

    @property
    def version_tag(self) -> str:
        return self.prompt.version_tag

    def _complete(self, *, source_code: str, dynamic_suffix: str, max_tokens: int) -> LLMResponse:
        request = LLMRequest(
            model=self.model,
            cacheable_prefix=_source_prefix(source_code),
            dynamic_suffix=dynamic_suffix,
            max_tokens=max_tokens,
        )
        return self.client.complete(request, cost_tracker=self.cost_tracker)

    @staticmethod
    def _validate_json(schema, text: str):
        """Valida a saída no schema; erro vira AgentOutputError (AD-02)."""
        try:
            return schema.model_validate_json(extract_json(text))
        except ValidationError as exc:
            raise AgentOutputError(f"saída inválida para {schema.__name__}: {exc}") from exc
