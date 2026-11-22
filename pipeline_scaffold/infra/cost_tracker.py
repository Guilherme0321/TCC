"""Contabilização tokens→USD e circuit breaker financeiro (AD-09, AD-15).

Modelo de custo (simplificação declarada do experimento):
- input cacheado custa `CACHE_HIT_INPUT_FACTOR` (10%) do preço base (AD-15);
- input não-cacheado e output custam o preço base;
- se Batch API ativa, aplica-se `BATCH_DISCOUNT` (0.5) sobre o total (AD-15).

Breakers (AD-09): ao ultrapassar o teto do tier por run, `RunBudgetExceeded`;
ao ultrapassar o teto global, `GlobalBudgetExceeded`. O pipeline captura o primeiro
e salva estado parcial com `aborted_budget=true` (AD-12); o runner captura o segundo
e pausa (AD-09).
"""

from __future__ import annotations

import threading

from config import (
    ALL_MODELS,
    BATCH_DISCOUNT,
    CACHE_HIT_INPUT_FACTOR,
    CACHE_WRITE_INPUT_FACTOR,
    MAX_COST_GLOBAL_USD,
    MAX_COST_PER_RUN_USD,
)
from schemas.run_record import TokenUsage


class RunBudgetExceeded(Exception):
    """Teto de custo por run (do tier) ultrapassado (AD-09)."""


class GlobalBudgetExceeded(Exception):
    """Teto de custo global do experimento ultrapassado (AD-09)."""


def price_call(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = True,
    models: dict = ALL_MODELS,
) -> float:
    """Custo em USD de uma chamada, aplicando fatores de cache e batch (AD-15).

    `input_tokens` é o total de entrada; `cached_input_tokens` (leitura) e
    `cache_write_tokens` (escrita) são subconjuntos dele, cobrados a
    `CACHE_HIT_INPUT_FACTOR` e `CACHE_WRITE_INPUT_FACTOR` do preço base. O que
    sobra é entrada fresca, ao preço cheio. Provedores sem cobrança de escrita
    reportam zero e o termo desaparece (AD-25).
    """
    spec = models[model]
    price_in = spec["price_in"]
    price_out = spec["price_out"]

    cached = min(cached_input_tokens, input_tokens)
    written = min(cache_write_tokens, input_tokens - cached)
    fresh_in = input_tokens - cached - written

    input_cost = (
        fresh_in * price_in
        + cached * price_in * CACHE_HIT_INPUT_FACTOR
        + written * price_in * CACHE_WRITE_INPUT_FACTOR
    ) / 1_000_000
    output_cost = output_tokens * price_out / 1_000_000
    cost = input_cost + output_cost
    if batch:
        cost *= BATCH_DISCOUNT
    return cost


class GlobalBudget:
    """Acumulador global compartilhado entre runs (AD-09).

    Sob a Batch API (AD-15) há vários runs em voo em threads distintas, então o
    acumulador é protegido por lock: sem ele, incrementos concorrentes se
    perderiam e o teto global poderia ser ultrapassado sem disparar.
    """

    def __init__(self, *, spent: float = 0.0, cap: float = MAX_COST_GLOBAL_USD) -> None:
        self.spent = spent
        self.cap = cap
        self._lock = threading.Lock()

    def add(self, cost: float) -> None:
        with self._lock:
            self.spent += cost

    @property
    def exceeded(self) -> bool:
        with self._lock:
            return self.spent >= self.cap


class CostTracker:
    """Rastreia o custo de UM run e checa os breakers por run e global (AD-09)."""

    def __init__(
        self,
        *,
        tier: str,
        batch: bool = True,
        models: dict = ALL_MODELS,
        per_run_caps: dict = MAX_COST_PER_RUN_USD,
        global_budget: GlobalBudget | None = None,
    ) -> None:
        if tier not in per_run_caps:
            raise ValueError(f"tier desconhecido: {tier!r}")
        self.tier = tier
        self.batch = batch
        self.models = models
        self.run_cap = per_run_caps[tier]
        self.global_budget = global_budget
        self.run_cost = 0.0
        # Acumula os tokens reais do run para gravar no RunRecord (AD-12).
        self.tokens = TokenUsage()

    def record(
        self,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> float:
        """Contabiliza uma chamada e dispara os breakers. Retorna o custo da chamada.

        A chamada em si SEMPRE é contabilizada antes de checar (o custo já foi
        gasto). Estourar o teto levanta exceção que o chamador captura para salvar
        estado parcial — nunca perde o custo já consumido (AD-08, AD-12).

        `reasoning_tokens` NÃO entra no cálculo: é subconjunto de `output_tokens`,
        já precificado ali. Serve só para instrumentação no RunRecord.
        """
        cost = price_call(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=cache_write_tokens,
            batch=self.batch,
            models=self.models,
        )
        self.run_cost += cost
        self.tokens.add(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        if self.global_budget is not None:
            self.global_budget.add(cost)

        if self.global_budget is not None and self.global_budget.exceeded:
            raise GlobalBudgetExceeded(
                f"custo global ${self.global_budget.spent:.2f} ≥ teto ${self.global_budget.cap:.2f}"
            )
        if self.run_cost >= self.run_cap:
            raise RunBudgetExceeded(
                f"custo do run ${self.run_cost:.2f} ≥ teto do tier {self.tier} ${self.run_cap:.2f}"
            )
        return cost
