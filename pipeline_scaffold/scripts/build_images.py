"""Constrói uma imagem Docker base por projeto do benchmark (AD-04, Fase 1).

Uma imagem por projeto, com venv e dependências pré-instaladas, reutilizada entre
as 5 repetições e as 4 arquiteturas (AD-04). A rede fica desligada só no RUN dos
testes; o build precisa de rede para instalar dependências.

Uso:
    python scripts/build_images.py                 # constrói as faltantes
    python scripts/build_images.py --force          # reconstrói todas
    python scripts/build_images.py --only proj-a    # apenas um projeto
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import BENCHMARK_DIR  # noqa: E402

DOCKERFILE = ROOT / "docker" / "Dockerfile.base"
IMAGE_PREFIX = "tcc-"


def image_tag(project: str) -> str:
    return f"{IMAGE_PREFIX}{project}".lower().replace("_", "-")


def image_exists(tag: str, docker_bin: str = "docker") -> bool:
    res = subprocess.run(
        [docker_bin, "image", "inspect", tag],
        capture_output=True,
        text=True,
    )
    return res.returncode == 0


def discover_projects(benchmark_dir: Path) -> list[str]:
    if not benchmark_dir.is_dir():
        return []
    return sorted(p.name for p in benchmark_dir.iterdir() if p.is_dir())


def _import_name(project: str) -> str:
    """Nome importável do pacote sob teste, para o build validar (AD-24).

    Reaproveita a inferência do project_loader — a mesma que alimenta o
    `--cov` — para o Dockerfile provar que o pacote importa de fora de
    /project. Falha de inferência devolve string vazia, que desliga a
    checagem: aqui ela é uma rede de segurança, não um gate novo.
    """
    try:
        from infra.project_loader import load_project

        return load_project(BENCHMARK_DIR / project).cov_target or ""
    except Exception as exc:  # noqa: BLE001 — inferência é best-effort
        print(f"[aviso] não inferi o pacote de {project}: {exc}", file=sys.stderr)
        return ""


def build_image(project: str, *, docker_bin: str = "docker") -> bool:
    """Constrói a imagem de um projeto. Retorna True em sucesso."""
    tag = image_tag(project)
    cmd = [
        docker_bin,
        "build",
        "--build-arg",
        f"PROJECT={project}",
        "--build-arg",
        f"IMPORT_NAME={_import_name(project)}",
        "-f",
        str(DOCKERFILE),
        "-t",
        tag,
        str(ROOT),  # contexto de build contém ./benchmark/<project>
    ]
    print(f"[build] {tag} …", flush=True)
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"[erro] falha ao construir {tag}", file=sys.stderr)
        return False
    print(f"[ok] {tag}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Constrói imagens base do benchmark (AD-04).")
    parser.add_argument("--force", action="store_true", help="reconstrói mesmo se já existir")
    parser.add_argument("--only", help="constrói apenas o projeto indicado")
    parser.add_argument("--docker-bin", default="docker")
    args = parser.parse_args(argv)

    projects = discover_projects(BENCHMARK_DIR)
    if args.only:
        projects = [p for p in projects if p == args.only]
    if not projects:
        print(f"nenhum projeto encontrado em {BENCHMARK_DIR}", file=sys.stderr)
        return 1

    failures = 0
    for project in projects:
        tag = image_tag(project)
        if not args.force and image_exists(tag, args.docker_bin):
            print(f"[skip] {tag} já existe")
            continue
        if not build_image(project, docker_bin=args.docker_bin):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
