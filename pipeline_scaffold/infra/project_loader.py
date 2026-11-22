"""Extração de código-fonte e metadados de um projeto do benchmark (Fase 1).

Concatena os módulos de produção (excluindo testes) para compor o contexto do
prompt, com o bloco de código no PREFIXO estável para maximizar cache hit (AD-15).
Coleta complexidade ciclomática via radon (input do Analyzer, AD-02) e infere o
alvo de cobertura (`--cov`) e os `source_paths` do mutmut.

Trunca por limite de caracteres (proxy do limite de contexto do modelo), marcando
`truncated=True` — registrável como ameaça à validade.
"""

from __future__ import annotations

import configparser
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from radon.complexity import cc_visit
from radon.raw import analyze as raw_analyze

# Arquivos/diretórios que não são código de produção.
_EXCLUDE_DIRS = {
    "tests",
    "test",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mutmut-cache",
    "mutants",
}
_EXCLUDE_FILES = {"setup.py", "conftest.py"}


def _is_test_file(path: Path) -> bool:
    n = path.name
    return n.startswith("test_") or n.endswith("_test.py")


def _is_production_file(path: Path, root: Path) -> bool:
    if path.suffix != ".py" or _is_test_file(path) or path.name in _EXCLUDE_FILES:
        return False
    return not any(part in _EXCLUDE_DIRS for part in path.relative_to(root).parts)


@dataclass
class ProjectContext:
    name: str
    root: Path
    cov_target: str  # pacote/módulo importável para --cov
    # Destinos (POSIX, relativos ao workdir) entregues ao mutmut em source_paths.
    source_paths: list[str]
    source_code: str  # concatenação dos módulos de produção (prefixo do prompt)
    # Pares (origem relativa ao root do projeto, destino relativo ao workdir).
    # O destino fica na RAIZ do workdir para sombrear o pacote instalado em
    # /project: copiar `src/pkg` para `/work/src/pkg` não põe `pkg` no path de
    # import, e os testes exercitariam o original em vez do mutante (AD-21).
    mutate_targets: list[tuple[str, str]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    total_complexity: int = 0
    avg_complexity: float = 0.0
    n_blocks: int = 0
    loc: int = 0
    truncated: bool = False


def _collect_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if _is_production_file(p, root))


def _read_packaging_metadata(root: Path) -> tuple[list[str], list[str], list[str], str]:
    """Lê o empacotamento declarado: (dirs de busca, pacotes, py-modules, nome).

    Adivinhar o alvo pelo nome da pasta quebrou em 7 dos 22 projetos do
    benchmark: `--cov=claude-agent-sdk-python` não é módulo importável (o pacote
    é `claude_agent_sdk`, sob `src/`), e o run saía convergido com
    `line_coverage=None`. Os metadados dizem a verdade; o nome da pasta, não.
    """
    where: list[str] = []
    packages: list[str] = []
    py_modules: list[str] = []
    name = ""

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            data = {}
        name = str(data.get("project", {}).get("name", "") or "")
        tools = data.get("tool", {})
        st = tools.get("setuptools", {})
        pkg_dir = st.get("package-dir") or st.get("package_dir") or {}
        if isinstance(pkg_dir, dict):
            where += [str(v) for v in pkg_dir.values()]
        find = st.get("packages", {})
        if isinstance(find, dict):
            where += [str(w) for w in find.get("find", {}).get("where", [])]
        elif isinstance(find, list):
            packages += [str(p) for p in find]
        py_modules += [str(m) for m in st.get("py-modules", st.get("py_modules", []))]
        # hatchling declara CAMINHOS ("src/claude_agent_sdk"), não nomes.
        for target in ("wheel", "sdist"):
            hatch = tools.get("hatch", {}).get("build", {}).get("targets", {}).get(target, {})
            packages += [str(p) for p in hatch.get("packages", [])]

    cfg_path = root / "setup.cfg"
    if cfg_path.is_file():
        cfg = configparser.ConfigParser()
        try:
            cfg.read(cfg_path, encoding="utf-8")
        except (configparser.Error, OSError):
            cfg = configparser.ConfigParser()
        if cfg.has_option("options", "package_dir"):
            for line in cfg.get("options", "package_dir").splitlines():
                if "=" in line:
                    where.append(line.split("=", 1)[1].strip())
        if cfg.has_option("options.packages.find", "where"):
            where.append(cfg.get("options.packages.find", "where").strip())
        if cfg.has_option("options", "py_modules"):
            py_modules += cfg.get("options", "py_modules").split()
        if not name and cfg.has_option("metadata", "name"):
            name = cfg.get("metadata", "name").strip()

    where = [w for w in dict.fromkeys(w.strip() for w in where) if w not in ("", ".")]
    return where, packages, py_modules, name


def _infer_cov_target(root: Path, files: list[Path]) -> tuple[str, list[tuple[str, str]]]:
    """Infere o alvo de --cov e os pares (origem, destino) a mutar.

    O destino é sempre um nome de topo, para que a cópia mutada em `/work`
    sombreie o pacote instalado em `/project` (AD-21). Caminhos em POSIX: o
    `setup.cfg` do mutmut é lido dentro do container Linux, e separadores do
    Windows viravam nomes de arquivo sem sentido ("0 files mutated").
    """
    where, declared, py_modules, dist_name = _read_packaging_metadata(root)

    pairs: list[tuple[str, str]] = []
    # 1. Pacotes declarados como CAMINHO (hatchling) — os mais explícitos.
    for decl in declared:
        candidate = root / decl
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            pairs.append((Path(decl).as_posix(), candidate.name))

    # 2. Pacotes encontrados nos diretórios de busca declarados (ou na raiz).
    for base in where or ["."]:
        base_dir = root / base
        if not base_dir.is_dir():
            continue
        for d in sorted(base_dir.iterdir()):
            if d.is_dir() and (d / "__init__.py").exists() and d.name not in _EXCLUDE_DIRS:
                rel = d.relative_to(root).as_posix()
                if not any(rel == s for s, _ in pairs):
                    pairs.append((rel, d.name))

    # 3. Módulos únicos declarados em py-modules (ex.: lanhu_mcp_server.py).
    for mod in py_modules:
        for base in where or ["."]:
            f = root / base / f"{mod}.py"
            if f.is_file():
                pairs.append((f.relative_to(root).as_posix(), f.name))
                break

    if pairs:
        normalized = (dist_name or root.name).replace("-", "_").lower()
        # Preferir o pacote que corresponde ao nome da distribuição.
        pairs.sort(key=lambda p: (Path(p[1]).stem.lower() != normalized, p[1]))
        return Path(pairs[0][1]).stem, pairs

    # 4. Sem metadados nem pacote algum (ex.: Waza: coleção de scripts soltos).
    #    Sem pacote importável não há alvo de --cov nem sombreamento possível;
    #    devolve o fallback histórico para não quebrar, e o smoke de benchmark
    #    (scripts/check_benchmark_targets.py) sinaliza o projeto.
    top_modules = sorted(f.stem for f in files if f.parent == root)
    rel_pairs = [(f.relative_to(root).as_posix(), f.name) for f in files]
    return (top_modules[0] if top_modules else root.name), rel_pairs


def _analyze_metrics(source: str) -> tuple[int, int, int]:
    """(soma_complexidade, nº_blocos, loc) via radon. Robusto a erro de parse."""
    try:
        blocks = cc_visit(source)
        total = sum(b.complexity for b in blocks)
        n_blocks = len(blocks)
    except SyntaxError:
        total, n_blocks = 0, 0
    try:
        loc = raw_analyze(source).loc
    except SyntaxError:
        loc = source.count("\n") + 1
    return total, n_blocks, loc


def load_project(path: str | Path, *, max_chars: int | None = None) -> ProjectContext:
    """Carrega um projeto: concatena fontes, mede complexidade e infere alvos."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"projeto não é um diretório: {root}")

    files = _collect_files(root)
    cov_target, mutate_targets = _infer_cov_target(root, files)

    parts: list[str] = []
    rel_names: list[str] = []
    for f in files:
        rel = f.relative_to(root)
        rel_names.append(str(rel))
        parts.append(f"# === {rel} ===\n{f.read_text(encoding='utf-8', errors='replace')}")
    source_code = "\n\n".join(parts)

    truncated = False
    if max_chars is not None and len(source_code) > max_chars:
        source_code = source_code[:max_chars]
        truncated = True

    total, n_blocks, loc = _analyze_metrics(source_code)
    avg = total / n_blocks if n_blocks else 0.0

    return ProjectContext(
        name=root.name,
        root=root,
        cov_target=cov_target,
        source_paths=[dest for _, dest in mutate_targets],
        mutate_targets=mutate_targets,
        source_code=source_code,
        files=rel_names,
        total_complexity=total,
        avg_complexity=round(avg, 2),
        n_blocks=n_blocks,
        loc=loc,
        truncated=truncated,
    )
