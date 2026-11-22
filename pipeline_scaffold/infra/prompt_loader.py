"""Carregador de prompts versionados em arquivos externos (AD-13).

Prompts vivem em `prompts/<agente>.vN.txt`; nunca hardcoded (AD-13). O carregador
descobre a maior versão, calcula um hash do conteúdo e expõe `version_tag`
(`generator.v1|<hash8>`) para gravar no RunRecord — garante rastreabilidade e impede
misturar versões de prompt na mesma análise.

Renderização por substituição direcionada de placeholders `{chave}`: NÃO usa
`str.format`, que quebraria com as chaves `{}` presentes no próprio código-fonte
injetado no prompt (AD-15, código no prefixo estável).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from config import PROMPTS_DIR

_VERSION_RE = re.compile(r"^(?P<name>.+)\.v(?P<version>\d+)\.txt$")


def _strip_meta_header(template: str) -> str:
    """Remove o bloco de comentários meta do topo (linhas `#` e vazias iniciais)."""
    lines = template.splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1
    return "\n".join(lines[i:]).lstrip("\n")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    template: str  # já sem o cabeçalho meta
    sha8: str

    @property
    def version_tag(self) -> str:
        """Identificador rastreável para o RunRecord (AD-13)."""
        return f"{self.name}.v{self.version}|{self.sha8}"

    def render(self, **kwargs: object) -> str:
        """Substitui apenas os placeholders conhecidos; preserva chaves do código."""
        text = self.template
        for key, value in kwargs.items():
            text = text.replace("{" + key + "}", str(value))
        return text


class PromptLoader:
    """Descobre e carrega prompts versionados de um diretório (AD-13)."""

    def __init__(self, prompts_dir: str | Path = PROMPTS_DIR) -> None:
        self.dir = Path(prompts_dir)

    def _candidates(self, name: str) -> list[tuple[int, Path]]:
        found: list[tuple[int, Path]] = []
        for path in self.dir.glob(f"{name}.v*.txt"):
            m = _VERSION_RE.match(path.name)
            if m and m.group("name") == name:
                found.append((int(m.group("version")), path))
        return sorted(found)

    def load(self, name: str, *, version: int | None = None) -> Prompt:
        """Carrega o prompt do agente. `version=None` usa a maior versão disponível."""
        candidates = self._candidates(name)
        if not candidates:
            raise FileNotFoundError(f"nenhum prompt '{name}.vN.txt' em {self.dir}")
        if version is None:
            chosen_version, path = candidates[-1]
        else:
            match = [(v, p) for v, p in candidates if v == version]
            if not match:
                raise FileNotFoundError(f"prompt '{name}.v{version}.txt' não encontrado")
            chosen_version, path = match[0]

        raw = path.read_text(encoding="utf-8")
        sha8 = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        return Prompt(
            name=name,
            version=chosen_version,
            template=_strip_meta_header(raw),
            sha8=sha8,
        )
