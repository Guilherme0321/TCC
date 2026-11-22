"""A4 — A3 + loop de reparo + loop Reflexion (AD-01, AD-03, AD-07, AD-14).

Fluxo (AD-14):
    Generator → executor → [erro exec?] → Repair → executor        (loop até rodar)
                        → [roda mas qualidade baixa] → Critic → Generator → executor
                                                                     (loop Reflexion)

Memória do Reflexion (AD-03): resumo filtrado das últimas N_REFLEXION_MEMORY_WINDOW
iterações (linhas não cobertas, mutantes sobreviventes) — nunca traceback bruto nem
histórico integral.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.analyzer import CodeAnalyzerAgent
from agents.base import AgentOutputError
from agents.critic import CriticAgent
from agents.generator import TestGeneratorAgent
from agents.repair import RepairAgent
from architectures.a3 import _enrich_with_radon
from architectures.common import (
    PipelineState,
    RunContext,
    execute_and_track,
    repair_loop,
    run_guarded,
)
from config import N_REFLEXION, REFLEXION_MEMORY_WINDOW
from schemas.execution import ExecutionResult
from schemas.run_record import RunRecord


@dataclass
class _MemoryEntry:
    iteration: int
    n_uncovered: int
    n_survivors: int
    sample_uncovered: list[int]
    sample_survivors: list[str]


@dataclass
class ReflexionMemory:
    """Resumo filtrado das últimas `window` iterações (AD-03)."""

    window: int = REFLEXION_MEMORY_WINDOW
    entries: list[_MemoryEntry] = field(default_factory=list)

    def add(self, iteration: int, execution: ExecutionResult) -> None:
        self.entries.append(
            _MemoryEntry(
                iteration=iteration,
                n_uncovered=len(execution.uncovered_lines),
                n_survivors=len(execution.surviving_mutants),
                sample_uncovered=execution.uncovered_lines[:10],
                sample_survivors=execution.surviving_mutants[:10],
            )
        )

    def render(self) -> str:
        recent = self.entries[-self.window :]
        if not recent:
            return ""
        return " | ".join(
            f"iter {e.iteration}: {e.n_uncovered} linhas não cobertas (ex.: {e.sample_uncovered}), "
            f"{e.n_survivors} mutantes sobreviventes (ex.: {e.sample_survivors})"
            for e in recent
        )


def _quality_ok(execution: ExecutionResult) -> bool:
    """Qualidade suficiente: nada a melhorar (sem linhas descobertas nem sobreviventes)."""
    return not execution.uncovered_lines and not execution.surviving_mutants


def run_a4(ctx: RunContext) -> RunRecord:
    def flow(ctx: RunContext, state: PipelineState) -> None:
        source = ctx.project.source_code
        kw = {
            "client": ctx.client,
            "prompt_loader": ctx.prompt_loader,
            "model": ctx.model,
            "cost_tracker": ctx.cost_tracker,
        }
        analyzer = CodeAnalyzerAgent(**kw)
        generator = TestGeneratorAgent(**kw)
        repair = RepairAgent(**kw)
        critic = CriticAgent(**kw)
        state.prompt_versions.update(
            analyzer=analyzer.version_tag,
            generator=generator.version_tag,
            repair=repair.version_tag,
            critic=critic.version_tag,
        )

        try:
            state.analysis = analyzer.analyze(source_code=source)
            _enrich_with_radon(state.analysis, source)
        except AgentOutputError:
            state.analysis = None  # falha de iteração (AD-02)

        memory = ReflexionMemory()
        feedback = None
        while True:
            execute_and_track(
                ctx,
                state,
                generator.generate(source_code=source, analysis=state.analysis, feedback=feedback),
            )

            # Loop de reparo até rodar (AD-14).
            repair_loop(ctx, state, repair, source)
            if not state.execution.all_passed:
                break  # não roda após esgotar o repair → falha (AD-08)

            # Roda e passa: avalia qualidade (AD-14).
            if _quality_ok(state.execution) or state.reflexion_iterations >= N_REFLEXION:
                break

            # Loop Reflexion: Critic gera feedback textual que realimenta o Generator.
            try:
                feedback = critic.critique(
                    source_code=source,
                    current_tests=state.tests.test_code,
                    uncovered_lines=state.execution.uncovered_lines,
                    surviving_mutants=state.execution.surviving_mutants,
                    history_summary=memory.render(),
                )
            except AgentOutputError:
                break  # feedback inválido: encerra o Reflexion mantendo a suite atual
            memory.add(state.reflexion_iterations + 1, state.execution)
            state.reflexion_iterations += 1

    return run_guarded(ctx, flow)
