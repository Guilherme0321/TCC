"""A2 — A1 + loop de reparo (AD-01, AD-07, AD-14).

Gera, executa e, se a suite não roda, aciona o RepairAgent até rodar (máx
N_REPAIR). Se esgotar sem convergir, salva como falha (AD-08).
"""

from __future__ import annotations

from agents.generator import TestGeneratorAgent
from agents.repair import RepairAgent
from architectures.common import (
    PipelineState,
    RunContext,
    execute_and_track,
    repair_loop,
    run_guarded,
)
from schemas.run_record import RunRecord


def run_a2(ctx: RunContext) -> RunRecord:
    def flow(ctx: RunContext, state: PipelineState) -> None:
        generator = TestGeneratorAgent(
            client=ctx.client,
            prompt_loader=ctx.prompt_loader,
            model=ctx.model,
            cost_tracker=ctx.cost_tracker,
        )
        state.prompt_versions["generator"] = generator.version_tag
        execute_and_track(ctx, state, generator.generate(source_code=ctx.project.source_code))

        if not state.execution.all_passed:
            repair = RepairAgent(
                client=ctx.client,
                prompt_loader=ctx.prompt_loader,
                model=ctx.model,
                cost_tracker=ctx.cost_tracker,
            )
            state.prompt_versions["repair"] = repair.version_tag
            repair_loop(ctx, state, repair, ctx.project.source_code)

    return run_guarded(ctx, flow)
