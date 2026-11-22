"""A1 — baseline linear: loader → generator → executor → writer (AD-01).

Sem análise, sem repair, sem crítica. É o piso de comparação do estudo.
"""

from __future__ import annotations

from agents.generator import TestGeneratorAgent
from architectures.common import PipelineState, RunContext, execute_and_track, run_guarded
from schemas.run_record import RunRecord


def run_a1(ctx: RunContext) -> RunRecord:
    def flow(ctx: RunContext, state: PipelineState) -> None:
        generator = TestGeneratorAgent(
            client=ctx.client,
            prompt_loader=ctx.prompt_loader,
            model=ctx.model,
            cost_tracker=ctx.cost_tracker,
        )
        state.prompt_versions["generator"] = generator.version_tag
        execute_and_track(ctx, state, generator.generate(source_code=ctx.project.source_code))

    return run_guarded(ctx, flow)
