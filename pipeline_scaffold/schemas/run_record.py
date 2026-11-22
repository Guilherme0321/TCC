"""Schema RunRecord — registro JSONL completo de um run (AD-02, AD-08, AD-10, AD-12).

Um RunRecord é escrito UMA única vez ao final do run (AD-12). Runs falhos NUNCA
são descartados (AD-08): salvam-se `converged=false`, `failure_reason` e as métricas
parciais, pois o custo de runs falhos entra no CCP/CPKM.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from schemas.execution import ExecutionResult

# Status possíveis (AD-12). Apenas os de FINAL_STATUSES bloqueiam reexecução (AD-10);
# "crashed" é reexecutado na retomada.
RunStatus = Literal["completed", "failed", "aborted_budget", "timeout", "crashed"]


def build_run_key(architecture: str, model: str, project: str, repetition: int) -> str:
    """Chave de idempotência (AD-10): {arquitetura}__{modelo}__{projeto}__rep-{n}."""
    return f"{architecture}__{model}__{project}__rep-{repetition}"


class TokenUsage(BaseModel):
    """Tokens consumidos no run, acumulados sobre todas as chamadas de agente.

    Invariante: `output_tokens` é sempre o total FATURADO como output, já
    incluindo tokens de raciocínio. `reasoning_tokens` é um SUBCONJUNTO de
    `output_tokens`, exposto só para instrumentação — quanto do orçamento de
    saída foi gasto em raciocínio invisível em vez de código de teste. Somar os
    dois contaria o mesmo token duas vezes.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0  # subconjunto de input servido do cache (AD-15)
    # Subconjunto de input GRAVADO no cache. Só a Anthropic cobra por isso, a
    # 1.25x o preço base (AD-25); OpenAI e Google reportam 0. Campo novo: runs
    # da coleta de 2026-08 não o têm no JSONL e desserializam com 0, que é o
    # valor correto para eles (default do Pydantic preserva a compatibilidade).
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0  # subconjunto de output gasto em raciocínio
    n_llm_calls: int = 0

    def add(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_input_tokens += cached_input_tokens
        self.cache_write_tokens += cache_write_tokens
        self.reasoning_tokens += reasoning_tokens
        self.n_llm_calls += 1


class RunRecord(BaseModel):
    """Registro completo e serializável de um único run do experimento."""

    # ── Chave de idempotência (AD-10) ────────────────────────────────────
    run_key: str
    architecture: str
    model: str
    tier: str
    project: str
    repetition: int

    # ── Desfecho (AD-08, AD-12) ──────────────────────────────────────────
    status: RunStatus
    converged: bool = False
    failure_reason: str | None = None
    aborted_budget: bool = False
    timed_out: bool = False

    # ── Custo e tokens (AD-09, AD-15) ────────────────────────────────────
    cost_usd: float = 0.0
    tokens: TokenUsage = Field(default_factory=TokenUsage)

    # ── Rastreabilidade de prompts (AD-13) ───────────────────────────────
    # {"generator": "generator.v1|<hash8>", "analyzer": ...}
    prompt_versions: dict[str, str] = Field(default_factory=dict)

    # ── Iterações dos loops (AD-07) ──────────────────────────────────────
    repair_iterations: int = 0
    reflexion_iterations: int = 0

    # ── Ratchet do melhor estado (AD-08) ─────────────────────────────────
    # Quantas suites executáveis o run produziu (1 em A1/A3; até 13 em A4).
    states_evaluated: int = 0
    # True quando o estado registrado NÃO é o último produzido — ou seja, o
    # pipeline piorou e o ratchet reverteu. É métrica do estudo: mede com que
    # frequência os loops de A2/A4 degradam o que já haviam alcançado.
    discarded_regression: bool = False

    # ── Métrica de segurança (AD-05) ─────────────────────────────────────
    ast_rejections: int = 0

    # ── Métricas finais / estado parcial (AD-08) ─────────────────────────
    line_coverage: float | None = None
    branch_coverage: float | None = None
    mutation_score: float | None = None
    final_tests: str = ""
    execution: ExecutionResult | None = None

    # ── Tempos (ISO 8601; injetados pelo runner, nunca via Date.now no código) ─
    started_at: str | None = None
    finished_at: str | None = None
    duration_sec: float | None = None

    def sync_metrics_from_execution(self) -> None:
        """Copia as métricas finais do ExecutionResult para o topo do registro."""
        if self.execution is None:
            return
        self.line_coverage = self.execution.line_coverage
        self.branch_coverage = self.execution.branch_coverage
        self.mutation_score = self.execution.mutation_score

    def stamp_finish(self, finished_at: str) -> None:
        """Carimba o fim do run e deriva `duration_sec` de `started_at` (AD-12).

        O timestamp vem de fora (seam `now_iso` do runner), preservando o
        determinismo dos testes. Timestamps não-ISO deixam `duration_sec`
        em None em vez de derrubar a escrita do registro.
        """
        self.finished_at = finished_at
        if self.started_at is None:
            return
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(finished_at)
        except ValueError:
            return
        self.duration_sec = round((end - start).total_seconds(), 3)
