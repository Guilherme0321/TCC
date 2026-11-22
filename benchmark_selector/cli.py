#!/usr/bin/env python3
"""CLI do pipeline de seleção de benchmark para o TCC.

Subcomandos:
    search    -- gera o pool inicial de candidatos via GitHub Search API
    evaluate  -- roda a triagem completa (metadados, clone, radon, testes,
                 mutmut, contaminação) sobre o pool e grava resultados
                 incrementalmente (suporta retomada após interrupção)
    report    -- regenera relatório CSV/JSON + resumo + shortlist a
                 partir de output/results.jsonl, sem re-rodar a triagem

Exemplos:
    python cli.py search --token SEU_TOKEN -n 40
    python cli.py evaluate --token SEU_TOKEN
    python cli.py evaluate --token SEU_TOKEN --keep-artifacts
    python cli.py report

Veja README.md para instruções de geração do token (PAT) do GitHub.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from pipeline import config
from pipeline.contamination import assess_contamination_risk
from pipeline.criteria import CandidateResult, evaluate_criteria
from pipeline.env_risk import detect_environment_risk
from pipeline.github_api import GitHubClient
from pipeline.mutmut_check import check_mutmut_compatibility
from pipeline.report import print_console_summary, suggest_shortlist, write_csv, write_json
from pipeline.repo_ops import CloneError, cleanup, shallow_clone
from pipeline.search import generate_candidates
from pipeline.static_analysis import RadonError, compute_cyclomatic_complexity, compute_ipynb_ratio, compute_loc
from pipeline.test_validation import create_venv, install_dependencies, measure_baseline_coverage, validate_collection
from pipeline.utils import setup_logger

logger = setup_logger("benchmark_selector", config.OUTPUT_DIR)


def get_token(args) -> str | None:
    return args.token or os.environ.get("GITHUB_TOKEN")


# --------------------------------------------------------------------------
# subcomando: search
# --------------------------------------------------------------------------

def _load_known_repos(out_path: Path) -> tuple[list[dict], set[str]]:
    """Retorna candidatos já salvos e conjunto de full_names conhecidos
    (pool anterior + repos já avaliados em results.jsonl).
    """
    existing: list[dict] = []
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)

    known = {c["full_name"] for c in existing}

    results_path = config.OUTPUT_DIR / "results.jsonl"
    if results_path.exists():
        with open(results_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        known.add(json.loads(line)["full_name"])
                    except (json.JSONDecodeError, KeyError):
                        continue

    return existing, known


def cmd_search(args):
    client = GitHubClient(token=get_token(args))
    if not client.authenticated:
        logger.warning(
            "Rodando sem token: limites de 60 req/h (API) e 10 req/min (Search) "
            "do GitHub. Veja README.md para gerar um PAT gratuito."
        )

    out_path = config.OUTPUT_DIR / "candidatos_raw.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing, known = _load_known_repos(out_path)
    if known:
        logger.info(
            "Excluindo %d repositório(s) já conhecidos (pool anterior + já avaliados).",
            len(known),
        )

    new_candidates = generate_candidates(client, target_n=args.n, exclude=known)
    all_candidates = existing + new_candidates

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_candidates, f, ensure_ascii=False, indent=2)
    logger.info(
        "Pool atualizado: %d novos + %d anteriores = %d total em %s",
        len(new_candidates), len(existing), len(all_candidates), out_path,
    )


# --------------------------------------------------------------------------
# subcomando: evaluate
# --------------------------------------------------------------------------

def _load_input_candidates(args) -> list[dict]:
    path = Path(args.input) if args.input else config.OUTPUT_DIR / "candidatos_raw.json"
    if not path.exists():
        logger.error("Arquivo de candidatos não encontrado: %s. Rode `search` primeiro ou use --input.", path)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Padrões que identificam falsos-negativos causados por bugs de
# INFRAESTRUTURA do próprio pipeline (não do repo candidato), elegíveis para
# reavaliação automática via --rerun-failed. Mantemos APENAS sinais
# inequívocos de infra: o self-upgrade do pip que quebrava o venv e o
# PermissionError no diretório de trabalho.
#
# NÃO incluir aqui erros de mutmut/pytest (ex.: "failed to run pytest with
# args", "badtestexecutioncommandsexception", "paths_to_mutate is
# deprecated"): com o mutmut já fixado em 2.5.x esses passaram a indicar
# reprovação LEGÍTIMA (suíte do repo exige plugins não instalados, ou o repo
# é genuinamente incompatível com mutation testing). Tê-los aqui faria o
# --rerun-failed re-enfileirar infinitamente repos que sempre vão falhar.
_INFRA_BUG_PATTERN = re.compile(
    r"pip\._internal\.cli"
    r"|No module named ['\"]?pip\._internal"
    r"|Permission denied:.*work",
    re.IGNORECASE,
)


def _filter_failed_for_rerun(results_path: Path) -> int:
    """Remove de results.jsonl entradas que foram falso-negativas por bugs
    de infraestrutura do pipeline (self-upgrade do pip no venv ou
    PermissionError no diretório de trabalho). Olha tanto em `erros` quanto
    em `motivo` (este último é onde o handler de Exception genérica grava).
    Cria backup timestampado antes de reescrever. Retorna a contagem de
    linhas removidas.
    """
    if not results_path.exists():
        return 0

    with open(results_path, encoding="utf-8") as f:
        lines = f.readlines()

    keep: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            keep.append(line)
            continue
        haystack = " ".join(row.get("erros") or []) + " " + (row.get("motivo") or "")
        if _INFRA_BUG_PATTERN.search(haystack):
            removed += 1
            continue
        keep.append(line)

    if removed == 0:
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = results_path.with_suffix(results_path.suffix + f".bak.{ts}")
    shutil.copy2(results_path, backup)
    logger.info("Backup criado: %s", backup)

    with open(results_path, "w", encoding="utf-8") as f:
        f.writelines(keep)

    return removed


def _already_processed(results_path: Path) -> set[str]:
    if not results_path.exists():
        return set()
    done = set()
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["full_name"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


class _CandidateBudgetExceeded(Exception):
    """Sinalizador interno: candidato ultrapassou o orçamento de tempo
    configurado em CANDIDATE_TIMEOUT_S e deve ser abortado sem afetar
    os próximos."""


def evaluate_one(client: GitHubClient, candidate: dict, keep_artifacts: bool = False) -> CandidateResult:
    full_name = candidate["full_name"]
    html_url = candidate["html_url"]
    owner, repo = full_name.split("/", 1)

    result = CandidateResult(full_name=full_name, html_url=html_url,
                              stargazers_count=candidate.get("stargazers_count"))
    workdir = config.WORKDIR / full_name.replace("/", "__")

    start = time.monotonic()

    def check_budget(stage: str) -> None:
        elapsed = time.monotonic() - start
        if elapsed > config.CANDIDATE_TIMEOUT_S:
            raise _CandidateBudgetExceeded(
                f"orçamento de {config.CANDIDATE_TIMEOUT_S}s excedido antes de '{stage}' "
                f"(decorrido: {elapsed:.0f}s)"
            )

    try:
        # 2. Metadados ----------------------------------------------------
        meta = client.get_repo(owner, repo)
        if meta is None:
            result.status = "erro"
            result.motivo = "metadados do GitHub indisponíveis (repo removido/privado/renomeado?)"
            return result

        result.license_spdx = (meta.get("license") or {}).get("spdx_id")
        result.created_at = meta.get("created_at")
        result.pushed_at = meta.get("pushed_at")
        default_branch = meta.get("default_branch") or "main"

        latest_commit = client.get_latest_commit(owner, repo, default_branch)
        result.commit_hash = (latest_commit or {}).get("sha")

        first_commit_at = client.get_first_commit_date(owner, repo, default_branch)
        created_dt = (
            datetime.fromisoformat(result.created_at.replace("Z", "+00:00"))
            if result.created_at else None
        )
        risco, detalhe = assess_contamination_risk(created_dt, first_commit_at)
        result.contaminacao_risco = risco
        result.contaminacao_detalhe = detalhe

        # 3. Clone superficial ----------------------------------------------
        check_budget("clone")
        repo_path = shallow_clone(html_url, workdir / "src")

        # 4. Complexidade ciclomática + LOC ----------------------------------
        try:
            cc_media, cc_pico, cc_p90, n_blocos, n_erros_cc = compute_cyclomatic_complexity(repo_path)
            result.cc_media = cc_media
            result.cc_pico = cc_pico
            result.cc_p90 = cc_p90
            result.n_blocos_cc = n_blocos
            if n_erros_cc:
                result.erros.append(f"{n_erros_cc} arquivo(s) com erro de parsing no `radon cc`")
        except RadonError as exc:
            result.erros.append(str(exc))

        try:
            loc = compute_loc(repo_path)
            result.loc_sloc = loc["sloc"] if loc else None
        except RadonError as exc:
            result.erros.append(str(exc))

        # 5. Proporção de notebooks ------------------------------------------
        result.ipynb_ratio = compute_ipynb_ratio(repo_path)

        # 5b. Risco de ambiente/hardware --------------------------------------
        ambiente_risco, ambiente_detalhe = detect_environment_risk(repo_path)
        result.ambiente_risco = ambiente_risco
        result.ambiente_detalhe = ambiente_detalhe

        # 6. venv isolado + instalação + coletabilidade + cobertura ---------
        check_budget("venv/install")
        venv_path = workdir / "venv"
        python_bin = create_venv(venv_path)
        _, install_log = install_dependencies(python_bin, repo_path)
        if install_log:
            result.erros.append(f"instalação: {install_log}")

        check_budget("validate_collection")
        coletavel, collect_log = validate_collection(python_bin, repo_path)
        result.testes_coletaveis = coletavel
        if not coletavel:
            result.erros.append(f"coleta de testes falhou/vazia: {collect_log[-300:]}")
        else:
            check_budget("measure_baseline_coverage")
            result.cobertura_inicial = measure_baseline_coverage(python_bin, repo_path)

        # 7. Dry-run de compatibilidade com mutmut ---------------------------
        check_budget("mutmut_check")
        compat, mutmut_detalhe = check_mutmut_compatibility(python_bin, repo_path)
        result.mutmut_compativel = compat
        result.mutmut_detalhe = mutmut_detalhe

        # 8/9. Aplicação dos critérios + classificação por faixa -------------
        result = evaluate_criteria(result)

    except _CandidateBudgetExceeded as exc:
        logger.warning("Candidato %s abortado: %s", full_name, exc)
        result.status = "erro"
        result.motivo = f"timeout do candidato: {exc}"
    except CloneError as exc:
        result.status = "erro"
        result.motivo = f"clone falhou: {exc}"
    except Exception as exc:  # pragma: no cover - resiliência: nunca aborta o pipeline inteiro
        logger.exception("Erro inesperado processando %s", full_name)
        result.status = "erro"
        result.motivo = f"erro inesperado: {exc}"
    finally:
        if not keep_artifacts:
            cleanup(workdir)

    return result


def cmd_evaluate(args):
    client = GitHubClient(token=get_token(args))
    candidates = _load_input_candidates(args)

    results_path = config.OUTPUT_DIR / "results.jsonl"
    results_path.parent.mkdir(parents=True, exist_ok=True)

    if args.rerun_failed:
        removed = _filter_failed_for_rerun(results_path)
        logger.info(
            "Modo --rerun-failed: %d candidato(s) removido(s) do histórico para reavaliação.",
            removed,
        )

    done = _already_processed(results_path)
    # Lista dos que faltam, preservando a ordem (e a posição absoluta) do pool.
    # A retomada é por full_name: tudo que já está em results.jsonl é pulado,
    # independentemente da posição -- por isso o primeiro pendente pode estar
    # numa posição "no meio" do pool (ex.: candidato perdido quando a execução
    # anterior foi interrompida no meio dele). Isso é o comportamento correto,
    # não um reprocessamento.
    pending = [(i, c) for i, c in enumerate(candidates, start=1)
               if c["full_name"] not in done]
    if done:
        logger.info(
            "Retomando: %d/%d já processado(s) (serão pulados). Faltam %d candidato(s).",
            len(done), len(candidates), len(pending),
        )

    interrupted = False
    try:
        with open(results_path, "a", encoding="utf-8") as out:
            for k, (pos, candidate) in enumerate(pending, start=1):
                full_name = candidate["full_name"]
                logger.info("[pendente %d/%d | pool %d/%d] Avaliando %s",
                            k, len(pending), pos, len(candidates), full_name)
                result = evaluate_one(client, candidate, keep_artifacts=args.keep_artifacts)
                out.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
                out.flush()
                logger.info("  -> %s (%s)", result.status, result.motivo[:120])
    except KeyboardInterrupt:
        # Ctrl+C entre candidatos OU durante subprocess (pip/pytest) que
        # estava bloqueando -- o candidato em andamento perde-se sem ser
        # gravado; basta rodar `evaluate` de novo que ele será reprocessado
        # pelo mecanismo de retomada (_already_processed).
        interrupted = True
        logger.warning(
            "Execução interrompida pelo usuário (Ctrl+C). Progresso salvo em %s. "
            "Rode `evaluate` novamente para retomar de onde parou.",
            results_path,
        )

    _generate_report_from_jsonl(results_path)
    if interrupted:
        sys.exit(130)  # convenção POSIX para "terminated by Ctrl+C"


# --------------------------------------------------------------------------
# subcomando: report
# --------------------------------------------------------------------------

def _load_results_jsonl(path: Path) -> list[dict]:
    results = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def _generate_report_from_jsonl(results_path: Path, out_dir: Path | None = None) -> None:
    if not results_path.exists():
        logger.error("Nenhum resultado encontrado em %s. Rode `evaluate` primeiro.", results_path)
        return
    out_dir = out_dir or config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    results = _load_results_jsonl(results_path)
    write_csv(results, out_dir / "relatorio_benchmark.csv")
    write_json(results, out_dir / "relatorio_benchmark.json")
    print_console_summary(results)
    suggest_shortlist(results)
    logger.info("Relatório gravado em %s/relatorio_benchmark.{csv,json}", out_dir)


def cmd_report(args):
    results_path = Path(args.results) if args.results else config.OUTPUT_DIR / "results.jsonl"
    _generate_report_from_jsonl(results_path)


# --------------------------------------------------------------------------
# subcomando: reclassify (métrica de complexidade alternativa)
# --------------------------------------------------------------------------

_CC_FIELDS = set(CandidateResult.__dataclass_fields__.keys())


def _row_to_result(row: dict) -> CandidateResult:
    return CandidateResult(**{k: v for k, v in row.items() if k in _CC_FIELDS})


def cmd_reclassify(args):
    """Reclassifica as faixas dos aprovados usando uma métrica de CC
    alternativa (pico ou p90) e grava um conjunto de artefatos SEPARADO em
    output/<metrica>/, sem tocar na saída da métrica média em output/.

    Reaproveita output/results.jsonl (dados de aprovação são independentes da
    métrica). Para aprovados que ainda não têm cc_pico/cc_p90 gravados (dados
    de execuções anteriores à métrica), clona o repo e roda apenas o `radon`
    -- barato comparado ao evaluate completo.
    """
    metric = args.cc_metric
    if metric not in ("pico", "p90"):
        logger.error("reclassify aceita apenas --cc-metric pico|p90 (media já está em output/).")
        sys.exit(1)

    src = config.OUTPUT_DIR / "results.jsonl"
    if not src.exists():
        logger.error("%s não encontrado. Rode `evaluate` primeiro.", src)
        sys.exit(1)

    rows = _load_results_jsonl(src)
    field = "cc_pico" if metric == "pico" else "cc_p90"

    # Backfill de cc_pico/cc_p90 nos aprovados que não têm (só esses precisam,
    # pois a faixa só é atribuída a aprovados). É best-effort: falha em um não
    # aborta os demais.
    faltando = [r for r in rows if r.get("status") == "aprovado" and r.get(field) is None]
    if faltando:
        logger.info("Backfill de %s para %d aprovado(s) via radon (clone raso).", field, len(faltando))
        for r in faltando:
            full_name = r["full_name"]
            workdir = config.WORKDIR / ("recl__" + full_name.replace("/", "__"))
            try:
                repo_path = shallow_clone(r["html_url"], workdir / "src")
                cc_media, cc_pico, cc_p90, _n, _e = compute_cyclomatic_complexity(repo_path)
                r["cc_media"] = r.get("cc_media") if r.get("cc_media") is not None else cc_media
                r["cc_pico"] = cc_pico
                r["cc_p90"] = cc_p90
                logger.info("  %s: media=%.1f pico=%.0f p90=%.1f", full_name, cc_media or 0, cc_pico or 0, cc_p90 or 0)
            except Exception as exc:  # resiliência: mantém o repo, só não reclassifica
                logger.warning("  %s: falha ao computar %s (%s); manterá faixa pela média.", full_name, field, exc)
            finally:
                cleanup(workdir)

    # Reclassifica a faixa de cada linha pela métrica escolhida. Se o valor da
    # métrica faltar num aprovado (backfill falhou), cai de volta para a média.
    reclass = []
    for r in rows:
        cr = _row_to_result(r)
        use_metric = metric if getattr(cr, field) is not None else "media"
        out = evaluate_criteria(cr, cc_metric=use_metric)
        reclass.append(asdict(out))

    out_dir = config.OUTPUT_DIR / metric
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "results.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for r in reclass:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info("results.jsonl reclassificado (métrica=%s) gravado em %s", metric, out_path)

    _generate_report_from_jsonl(out_path, out_dir=out_dir)


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Pipeline de seleção de benchmark -- TCC Guilherme (PUC Minas)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="Gera o pool de candidatos via GitHub Search API")
    p_search.add_argument("--token", default=None, help="PAT do GitHub (ou defina a env var GITHUB_TOKEN)")
    p_search.add_argument("-n", type=int, default=config.N_CANDIDATOS_ALVO, help="Número de candidatos desejado")
    p_search.set_defaults(func=cmd_search)

    p_eval = sub.add_parser("evaluate", help="Roda a triagem completa sobre o pool de candidatos")
    p_eval.add_argument("--token", default=None)
    p_eval.add_argument("--input", default=None,
                         help="Caminho para candidatos_raw.json (default: output/candidatos_raw.json)")
    p_eval.add_argument("--keep-artifacts", action="store_true",
                         help="Não remove clones/venvs após cada candidato (útil para debug)")
    p_eval.add_argument("--rerun-failed", action="store_true",
                         help="Remove de results.jsonl os candidatos que falharam pelo bug do "
                              "self-upgrade do pip no venv (cria backup timestampado) e os "
                              "reavalia. Use após corrigir create_venv.")
    p_eval.set_defaults(func=cmd_evaluate)

    p_report = sub.add_parser("report", help="Regenera relatório/shortlist a partir de results.jsonl")
    p_report.add_argument("--results", default=None, help="Caminho alternativo para results.jsonl")
    p_report.set_defaults(func=cmd_report)

    p_recl = sub.add_parser(
        "reclassify",
        help="Reclassifica as faixas por uma métrica de CC alternativa (pico|p90), "
             "gravando em output/<metrica>/ sem alterar a saída da métrica média",
    )
    p_recl.add_argument("--cc-metric", choices=["pico", "p90"], required=True,
                        help="Métrica de complexidade para definir a faixa (pico = máx por bloco; p90 = percentil 90)")
    p_recl.set_defaults(func=cmd_reclassify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
