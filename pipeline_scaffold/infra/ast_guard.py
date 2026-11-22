"""Análise estática de AST antes de executar qualquer teste gerado (AD-05).

Primeira das três camadas de proteção. Rejeita imports/uso de `os.system`,
`subprocess`, `shutil.rmtree`, `socket`, `eval`, `exec` e escrita em caminho
absoluto fora do tempdir. A contagem de rejeições é métrica do estudo (AD-05):
o `AstGuard` acumula `rejections` ao longo de um run.

Erro de sintaxe no código gerado NÃO é rejeição de segurança — é falha de
execução, tratada pelo RepairAgent (AD-14). Reportamos como `syntax_error`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from config import AST_FORBIDDEN

# Modos de abertura de arquivo que escrevem.
_WRITE_MODES = ("w", "a", "x", "+")
# Prefixos onde escrever é aceitável (tmpfs do container efêmero, AD-04).
_ALLOWED_WRITE_PREFIXES = ("/tmp", "/work", "/var/tmp")

# Deriva as categorias de proibição a partir de config.AST_FORBIDDEN.
_BUILTIN_CALL_BANS = {n for n in AST_FORBIDDEN if n in {"eval", "exec"}}
_MODULE_BANS = {
    n for n in AST_FORBIDDEN if "." not in n and n not in _BUILTIN_CALL_BANS
}  # ex.: subprocess, socket
_ATTR_CALL_BANS: dict[str, set[str]] = {}
for _name in AST_FORBIDDEN:
    if "." in _name:
        _root, _attr = _name.split(".", 1)
        _ATTR_CALL_BANS.setdefault(_root, set()).add(_attr)  # ex.: os->{system}


@dataclass
class GuardResult:
    """Veredito da análise estática de uma suite de testes."""

    allowed: bool
    violations: list[str] = field(default_factory=list)
    syntax_error: str | None = None


def _string_value(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _writes_outside_tempdir(call: ast.Call) -> str | None:
    """Detecta open(path, mode-de-escrita) com caminho absoluto fora do tempdir."""
    func = call.func
    is_open = (isinstance(func, ast.Name) and func.id == "open") or (
        isinstance(func, ast.Attribute) and func.attr == "open"
    )
    if not is_open:
        return None

    mode = None
    if len(call.args) >= 2:
        mode = _string_value(call.args[1])
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = _string_value(kw.value)
    if mode is None or not any(m in mode for m in _WRITE_MODES):
        return None

    path = _string_value(call.args[0]) if call.args else None
    if path is None:
        # Caminho dinâmico com modo de escrita: não conseguimos provar segurança.
        return "open() em modo de escrita com caminho dinâmico"
    if path.startswith("/") and not path.startswith(_ALLOWED_WRITE_PREFIXES):
        return f"escrita em caminho absoluto fora do tempdir: {path!r}"
    return None


def _scan(tree: ast.AST) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _MODULE_BANS:
                    violations.append(f"import proibido: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _MODULE_BANS:
                violations.append(f"import proibido: from {node.module}")
            banned_attrs = _ATTR_CALL_BANS.get(root, set())
            for alias in node.names:
                if alias.name in banned_attrs:
                    violations.append(f"import proibido: from {root} import {alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in _BUILTIN_CALL_BANS:
                    violations.append(f"chamada proibida: {func.id}()")
                if func.id == "__import__" and node.args:
                    mod = _string_value(node.args[0])
                    if mod and mod.split(".")[0] in _MODULE_BANS:
                        violations.append(f"__import__ proibido: {mod}")
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                base, attr = func.value.id, func.attr
                if attr in _ATTR_CALL_BANS.get(base, set()):
                    violations.append(f"chamada proibida: {base}.{attr}()")
            write_violation = _writes_outside_tempdir(node)
            if write_violation:
                violations.append(write_violation)
    return violations


def analyze(test_code: str) -> GuardResult:
    """Analisa uma suite. Não muta contador; use `AstGuard.check` no pipeline."""
    try:
        tree = ast.parse(test_code)
    except SyntaxError as exc:
        return GuardResult(allowed=True, syntax_error=f"{exc.msg} (linha {exc.lineno})")
    violations = _scan(tree)
    return GuardResult(allowed=not violations, violations=violations)


class AstGuard:
    """Guard com contador de rejeições por run (métrica AD-05)."""

    def __init__(self) -> None:
        self.rejections = 0
        self.checked = 0

    def check(self, test_code: str) -> GuardResult:
        self.checked += 1
        result = analyze(test_code)
        if not result.allowed:
            self.rejections += 1
        return result
