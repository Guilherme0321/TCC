"""A3 — analyzer → generator → executor → writer (AD-01, AD-02).

Adiciona a etapa de análise estrutural antes da geração; sem repair. A complexidade
ciclomática individual do radon sobrescreve a estimativa do LLM quando os nomes
casam (AD-02). Falha de parsing da análise é falha de iteração: segue sem análise
(AD-02), não crash.
"""

from __future__ import annotations

from radon.complexity import cc_visit

from agents.analyzer import CodeAnalyzerAgent
from agents.base import AgentOutputError
from agents.generator import TestGeneratorAgent
from architectures.common import PipelineState, RunContext, execute_and_track, run_guarded
from schemas.analysis import CodeAnalysis
from schemas.run_record import RunRecord


def _radon_complexity(source: str) -> dict[str, int]:
    """Mapa nome→complexidade ciclomática do radon (nome simples e qualificado)."""
    try:
        blocks = cc_visit(source)
    except SyntaxError:
        return {}
    out: dict[str, int] = {}
    for b in blocks:
        out[b.name] = b.complexity
        out[b.name.split(".")[-1]] = b.complexity
    return out


def _enrich_with_radon(analysis: CodeAnalysis, source: str) -> None:
    cc = _radon_complexity(source)
    for symbol in analysis.symbols:
        if symbol.name in cc:
            symbol.complexity = cc[symbol.name]


def run_a3(ctx: RunContext) -> RunRecord:
    def flow(ctx: RunContext, state: PipelineState) -> None:
        source = ctx.project.source_code
        analyzer = CodeAnalyzerAgent(
            client=ctx.client,
            prompt_loader=ctx.prompt_loader,
            model=ctx.model,
            cost_tracker=ctx.cost_tracker,
        )
        state.prompt_versions["analyzer"] = analyzer.version_tag
        try:
            state.analysis = analyzer.analyze(source_code=source)
            _enrich_with_radon(state.analysis, source)
        except AgentOutputError:
            state.analysis = None  # falha de iteração: gera sem análise (AD-02)

        generator = TestGeneratorAgent(
            client=ctx.client,
            prompt_loader=ctx.prompt_loader,
            model=ctx.model,
            cost_tracker=ctx.cost_tracker,
        )
        state.prompt_versions["generator"] = generator.version_tag
        execute_and_track(
            ctx, state, generator.generate(source_code=source, analysis=state.analysis)
        )

    return run_guarded(ctx, flow)
