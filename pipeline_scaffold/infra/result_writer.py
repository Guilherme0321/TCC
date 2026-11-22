"""Escrita JSONL incremental e leitura para retomada idempotente (AD-10, AD-12).

Cada run é escrito UMA única vez, ao final (AD-12). `finalize_run` é um context
manager que garante essa escrita única e, em crash não-tratado, grava o registro
com `status="crashed"` preservando o custo parcial já acumulado, e re-levanta.

Para retomada (AD-10), `completed_keys` devolve as chaves com status definitivo
(FINAL_STATUSES); runs `crashed` não bloqueiam reexecução.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from config import FINAL_STATUSES
from schemas.run_record import RunRecord


class ResultWriter:
    """Leitura/escrita do arquivo JSONL de resultados do experimento."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # Runs concorrentes (AD-15) gravam no mesmo JSONL: uma linha por vez.
        self._lock = threading.Lock()

    def completed_keys(self) -> set[str]:
        """Chaves de runs com status definitivo (AD-10). Linhas inválidas são puladas."""
        if not self.path.exists():
            return set()
        keys: set[str] = set()
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("status") in FINAL_STATUSES and "run_key" in obj:
                    keys.add(obj["run_key"])
        return keys

    def read_all(self) -> list[dict]:
        """Todos os registros já gravados (para inspeção/análise)."""
        if not self.path.exists():
            return []
        records: list[dict] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def append(self, record: RunRecord) -> None:
        """Anexa um registro como uma linha JSON. Cria o diretório pai se preciso."""
        line = record.model_dump_json()
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


@contextmanager
def finalize_run(writer: ResultWriter, record: RunRecord) -> Iterator[RunRecord]:
    """Garante escrita única do `record` ao final do run (AD-12).

    O corpo do `with` muta `record` até o status final. Em saída normal, grava.
    Em crash não-tratado, marca `crashed` (se ainda não for definitivo) preservando
    o custo parcial e re-levanta a exceção.
    """
    try:
        yield record
    except BaseException as exc:
        if record.status not in FINAL_STATUSES:
            record.status = "crashed"
            if record.failure_reason is None:
                record.failure_reason = f"{type(exc).__name__}: {exc}"
        writer.append(record)
        raise
    else:
        writer.append(record)
