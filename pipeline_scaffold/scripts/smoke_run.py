"""Smoke test: 1 run completo end-to-end de uma arquitetura (Fase 5).

FAZ CHAMADAS REAIS DE API — por isso é gated por `--yes` e usa por padrão o modelo
mais barato (gpt-5-mini, AD-15). Sem `--yes`, apenas descreve o que faria.

Execução dos testes: por padrão usa `LocalRunner` (sem Docker) para simplicidade do
smoke; passe `--docker` para o caminho de produção AD-04 (exige imagem já construída
via scripts/build_images.py).

Para testar DE GRAÇA (sem gastar orçamento), use um modelo dev (ver DEV_MODELS
em config.py): `--model dev-free` (Ollama local ou qualquer endpoint compatível
com OpenAI via DEV_LLM_BASE_URL/DEV_LLM_MODEL/DEV_LLM_API_KEY) ou
`--model dev-gemini-free` (free tier do Google AI Studio, só GOOGLE_API_KEY).

Uso:
    python scripts/smoke_run.py --arch A1 --project caminho/do/projeto --yes
    python scripts/smoke_run.py --arch A1 --project caminho/do/projeto --model dev-free --yes
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from architectures import PIPELINES  # noqa: E402
from architectures.common import RunContext  # noqa: E402
from config import ALL_MODELS  # noqa: E402
from infra.ast_guard import AstGuard  # noqa: E402
from infra.batch import BatchCoalescer  # noqa: E402
from infra.cost_tracker import CostTracker, GlobalBudget  # noqa: E402
from infra.llm_client import LLMClient  # noqa: E402
from infra.project_loader import load_project  # noqa: E402
from infra.prompt_loader import PromptLoader  # noqa: E402
from infra.test_executor import DockerRunner, LocalRunner, TestExecutor  # noqa: E402

logger = logging.getLogger("smoke")


def _build_executor(project_ctx, *, use_docker: bool) -> TestExecutor:
    if use_docker:
        from scripts.build_images import image_tag

        source_paths = [f"/project/{p}" for p in project_ctx.source_paths]
        return TestExecutor(
            DockerRunner(image_tag(project_ctx.name)),
            cov_target=project_ctx.cov_target,
            mutmut_source_paths=source_paths,
        )
    # LocalRunner: mutação desligada por padrão no smoke (mais rápido/robusto).
    return TestExecutor(
        LocalRunner(project_ctx.root),
        cov_target=project_ctx.cov_target,
        run_mutation=False,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Smoke test de 1 run (Fase 5).")
    p.add_argument("--arch", choices=list(PIPELINES), default="A1")
    p.add_argument(
        "--model",
        default="gpt-5-mini",
        help="padrão: modelo mais barato (AD-15); use dev-free/dev-gemini-free p/ testar grátis",
    )
    p.add_argument("--project", required=True, help="caminho de um projeto Python")
    p.add_argument("--docker", action="store_true", help="usa Docker (AD-04) em vez de LocalRunner")
    p.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="trunca o código-fonte no prompt (APENAS para modelos dev de contexto "
        "pequeno; o experimento oficial nunca trunca por aqui)",
    )
    p.add_argument(
        "--batch",
        action="store_true",
        help="usa a Batch API de verdade (AD-15/AD-17); mais barato, porém sujeito "
        "ao turnaround do job. Padrão: chamada síncrona a preço cheio.",
    )
    p.add_argument("--yes", action="store_true", help="confirma a chamada REAL de API")
    args = p.parse_args(argv)

    if args.model not in ALL_MODELS:
        print(f"modelo desconhecido: {args.model}", file=sys.stderr)
        return 1

    project_ctx = load_project(args.project, max_chars=args.max_chars)
    if project_ctx.truncated:
        print(f"AVISO: código-fonte truncado em {args.max_chars} chars (smoke dev)")
    tier = ALL_MODELS[args.model]["tier"]

    print(f"Arquitetura: {args.arch} | modelo: {args.model} ({tier})")
    print(f"Projeto: {project_ctx.name} | cov_target: {project_ctx.cov_target}")
    print(f"Execução: {'Docker (AD-04)' if args.docker else 'LocalRunner (sem mutação)'}")
    print(f"Chamada: {'Batch API (-50%)' if args.batch else 'síncrona (preço cheio)'}")

    if not args.yes:
        print("\n[DRY-RUN] Faria 1 chamada REAL de API. Reexecute com --yes para confirmar.")
        return 0

    # O desconto de batch no cost_tracker só é honesto se a chamada for MESMO
    # pela Batch API: sem coalescer, `batch=True` reportaria metade do custo real.
    # window_sec=0: o smoke roda UM run, então não há chamadas concorrentes para
    # esperar. Só encurta a espera antes de submeter — não muda o que é enviado.
    coalescer = BatchCoalescer(window_sec=0.0) if args.batch else None

    ctx = RunContext(
        architecture=args.arch,
        project=project_ctx,
        model=args.model,
        tier=tier,
        repetition=1,
        client=LLMClient(coalescer=coalescer),
        prompt_loader=PromptLoader(),
        cost_tracker=CostTracker(tier=tier, batch=args.batch, global_budget=GlobalBudget()),
        ast_guard=AstGuard(),
        executor=_build_executor(project_ctx, use_docker=args.docker),
        started_at=datetime.now(UTC).isoformat(),
    )
    record = PIPELINES[args.arch](ctx)
    record.stamp_finish(datetime.now(UTC).isoformat())

    print("\n── Resultado ─────────────────────────────")
    print(f"status={record.status} converged={record.converged} duração={record.duration_sec}s")
    print(f"cobertura linha={record.line_coverage} branch={record.branch_coverage}")
    print(f"mutation={record.mutation_score} custo=${record.cost_usd:.4f}")
    print(f"repair_iter={record.repair_iterations} reflexion_iter={record.reflexion_iterations}")
    # Sem isto, um smoke que falha não diz POR QUE — e diagnosticar exige gastar
    # outra chamada só para reproduzir a falha.
    if record.failure_reason:
        print(f"failure_reason={record.failure_reason}")
    print(f"tokens={record.tokens.model_dump()}")
    tk = record.tokens
    if tk.output_tokens:
        share = 100.0 * tk.reasoning_tokens / tk.output_tokens
        print(f"raciocínio={tk.reasoning_tokens} de {tk.output_tokens} output ({share:.1f}%)")
    if tk.input_tokens:
        hit = 100.0 * tk.cached_input_tokens / tk.input_tokens
        print(f"cache hit={tk.cached_input_tokens} de {tk.input_tokens} input ({hit:.1f}%)")
    if record.failure_reason:
        print(f"failure_reason={record.failure_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
