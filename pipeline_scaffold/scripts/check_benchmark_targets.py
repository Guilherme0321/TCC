"""Valida os alvos de cobertura e mutação dos 22 projetos do benchmark (AD-21).

Sem API e sem LLM: para cada projeto, monta uma suite trivial que importa o
`cov_target` inferido e roda pytest-cov no MESMO container de produção, com os
fontes já copiados para `/work` como na fase de mutação. Duas asserções:

1. `--cov=<cov_target>` produz cobertura não-nula — pega o caso em que o alvo
   inferido não é importável (`--cov=claude-agent-sdk-python`), que fazia o run
   convergir com `line_coverage=None` e sem erro nenhum.
2. O pacote importado vem de `/work`, não de `/project` — pega o caso em que a
   cópia não sombreia o instalado, no qual o mutmut mutaria arquivos que os
   testes não exercitam e TODOS os mutantes sobreviveriam, silenciosamente.

Motivo de existir: a correção de AD-21 foi validada num único projeto de pacote
na raiz e passou; 7 dos 22 quebravam por layout (`src/`, `package-dir`, scripts
soltos), e isso só apareceu depois de US$ 0,42 de coleta. Rodar este script é
pré-requisito de qualquer disparo.

Uso: python scripts/check_benchmark_targets.py [--project NOME]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import BENCHMARK_DIR  # noqa: E402
from infra.project_loader import ProjectContext, load_project  # noqa: E402
from scripts.build_images import image_tag  # noqa: E402

SUITE = """\
import {mod}


def test_importa_e_vem_do_workdir():
    origem = getattr({mod}, "__file__", "") or ""
    assert origem.startswith("/work"), f"importado de {{origem}}, nao de /work"
"""


def check(ctx: ProjectContext) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for origem, destino in ctx.mutate_targets:
            src = ctx.root / origem
            if not src.exists():
                return False, f"origem inexistente: {origem}"
            dest = work / destino
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest)

        (work / "test_generated.py").write_text(SUITE.format(mod=ctx.cov_target), encoding="utf-8")
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--cpus=2",
            "--memory=2g",
            "--read-only",
            "--tmpfs=/tmp",
            "-v",
            f"{work}:/work:rw",
            "-w",
            "/work",
            "-u",
            "1000:1000",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "HOME=/tmp",
            image_tag(ctx.name),
            "python",
            "-m",
            "pytest",
            "test_generated.py",
            f"--cov={ctx.cov_target}",
            "--cov-branch",
            "--cov-report=json:cov.json",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-q",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return False, "timeout no container"

        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-4:]
            return False, "pytest falhou: " + " | ".join(tail)

        cov_file = work / "cov.json"
        if not cov_file.exists():
            return False, "cov.json não gerado"
        totals = json.loads(cov_file.read_text(encoding="utf-8")).get("totals", {})
        n = totals.get("num_statements", 0)
        if not n:
            return False, f"--cov={ctx.cov_target} mediu 0 statements (alvo não importável?)"
        return True, f"ok — {n} statements sob medição"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", help="verifica só este projeto")
    args = ap.parse_args()

    roots = sorted(p for p in BENCHMARK_DIR.iterdir() if p.is_dir())
    if args.project:
        roots = [p for p in roots if p.name == args.project] or roots
    falhas = []
    for root in roots:
        ctx = load_project(root)
        ok, msg = check(ctx)
        print(f"{'PASS' if ok else 'FALHA'}  {ctx.name:28s} cov={ctx.cov_target:24s} {msg}")
        if not ok:
            falhas.append(ctx.name)

    print(f"\n{len(roots) - len(falhas)}/{len(roots)} projetos aptos.")
    if falhas:
        print("FALHARAM:", ", ".join(falhas))
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
