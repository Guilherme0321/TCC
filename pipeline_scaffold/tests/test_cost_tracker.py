"""Testes de contabilização de custo e circuit breakers (AD-09, AD-15)."""

import pytest

from config import BATCH_DISCOUNT, CACHE_HIT_INPUT_FACTOR, MODELS
from infra.cost_tracker import (
    CostTracker,
    GlobalBudget,
    GlobalBudgetExceeded,
    RunBudgetExceeded,
    price_call,
)


def test_price_call_standard_no_batch_no_cache():
    # gpt-5-mini: in 0.25, out 2.00 por 1M
    cost = price_call(
        "gpt-5-mini",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        batch=False,
    )
    assert cost == pytest.approx(0.25 + 2.00)


def test_price_call_batch_halves():
    base = price_call("gpt-5-mini", input_tokens=1_000_000, output_tokens=0, batch=False)
    batched = price_call("gpt-5-mini", input_tokens=1_000_000, output_tokens=0, batch=True)
    assert batched == pytest.approx(base * BATCH_DISCOUNT)


def test_price_call_cache_discount_on_input():
    # Todo o input cacheado: custa CACHE_HIT_INPUT_FACTOR do preço base.
    full = price_call("gpt-5-mini", input_tokens=1_000_000, output_tokens=0, batch=False)
    cached = price_call(
        "gpt-5-mini",
        input_tokens=1_000_000,
        output_tokens=0,
        cached_input_tokens=1_000_000,
        batch=False,
    )
    assert cached == pytest.approx(full * CACHE_HIT_INPUT_FACTOR)


def test_cached_tokens_clamped_to_input():
    # cached não pode exceder input
    cost = price_call(
        "gpt-5-mini",
        input_tokens=100,
        output_tokens=0,
        cached_input_tokens=10_000,
        batch=False,
    )
    expected = 100 * MODELS["gpt-5-mini"]["price_in"] * CACHE_HIT_INPUT_FACTOR / 1_000_000
    assert cost == pytest.approx(expected)


def test_run_breaker_trips_over_tier_cap():
    tracker = CostTracker(tier="cost", batch=False)
    # gera custo alto de propósito para estourar o teto do tier cost ($1.00)
    with pytest.raises(RunBudgetExceeded):
        tracker.record("claude-sonnet-4-6", input_tokens=0, output_tokens=1_000_000)
    assert tracker.run_cost > 0  # custo já gasto foi contabilizado (AD-08)


def test_run_breaker_does_not_trip_on_normal_run():
    tracker = CostTracker(tier="cost", batch=True)
    cost = tracker.record("gpt-5-mini", input_tokens=8000, output_tokens=3000)
    assert cost > 0
    assert tracker.run_cost < tracker.run_cap


def test_global_breaker_trips():
    gb = GlobalBudget(spent=0.0, cap=0.001)
    tracker = CostTracker(tier="cost", batch=False, global_budget=gb)
    with pytest.raises(GlobalBudgetExceeded):
        tracker.record("gpt-5-mini", input_tokens=100_000, output_tokens=100_000)
    assert gb.spent > 0


def test_unknown_tier_rejected():
    with pytest.raises(ValueError):
        CostTracker(tier="inexistente")


def test_price_call_cache_write_costs_more_than_fresh_input():
    """Escrita de cache na Anthropic custa 1.25x a entrada base (AD-25)."""
    fresh = price_call("claude-sonnet-5", input_tokens=1_000_000, output_tokens=0, batch=False)
    written = price_call(
        "claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_write_tokens=1_000_000,
        batch=False,
    )
    assert written == pytest.approx(fresh * 1.25)


def test_price_call_cache_write_and_read_are_disjoint_subsets():
    """Leitura, escrita e entrada fresca particionam `input_tokens`, sem dupla contagem."""
    cost = price_call(
        "claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=0,
        cached_input_tokens=600_000,
        cache_write_tokens=300_000,
        batch=False,
    )
    # 100k frescos a 1.0x + 600k lidos a 0.1x + 300k escritos a 1.25x, a $2/M.
    esperado = (100_000 * 1.0 + 600_000 * 0.1 + 300_000 * 1.25) * 2.0 / 1_000_000
    assert cost == pytest.approx(esperado)


def test_price_call_sem_cache_write_nao_muda_provedores_antigos():
    """Regressão: o campo novo não pode alterar o custo já publicado da coleta OpenAI."""
    assert price_call(
        "gpt-5-mini", input_tokens=1_000_000, output_tokens=500_000, batch=True
    ) == price_call(
        "gpt-5-mini",
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_write_tokens=0,
        batch=True,
    )
