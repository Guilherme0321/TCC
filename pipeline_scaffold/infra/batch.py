"""Coalescência de chamadas concorrentes em jobs da Batch API (AD-15).

AD-15 determina que todas as chamadas de LLM sejam submetidas via Batch API
(−50% em input e output). A Batch API dos três provedores é assíncrona
(submit → poll → resultados), com turnaround de até 24h. Como os loops de
repair/Reflexion são SEQUENCIAIS dentro de um run (A4 encadeia ~10 chamadas),
agrupar chamadas dentro de um run é inviável — seriam ~10 turnarounds em série.

Batch real só existe agrupando chamadas ENTRE runs distintos. Daí o desenho:

    runner (pool de N threads)      →  N runs em voo, cada um em sua thread
    cada run chama client.complete  →  bloqueia, registrando-se no coalescer
    coalescer                       →  junta as chamadas pendentes do mesmo
                                       (provedor, modelo) em UM job de batch
                                       e devolve o resultado a cada thread

Os pipelines (`architectures/`) e os agentes (`agents/`) permanecem funções
lineares síncronas com `while` explícito — AD-01 preservada. O breaker por run
(AD-09) também: cada thread tem seu próprio `CostTracker` e continua abortando
apenas o próprio run.

Protocolo de flush (líder/seguidores, sem thread de fundo): a primeira thread a
enfileirar num grupo torna-se líder e espera até `window_sec` ou até o grupo
atingir `max_size`; então retira o grupo inteiro, submete o job, faz o poll e
distribui os resultados. As demais apenas aguardam seu `Event`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from config import (
    BATCH_MAX_SIZE,
    BATCH_MAX_WAIT_SEC,
    BATCH_POLL_INTERVAL_SEC,
    BATCH_WINDOW_SEC,
)

logger = logging.getLogger("batch")


class BatchTimeout(Exception):
    """Job de batch não concluiu dentro de `BATCH_MAX_WAIT_SEC` (AD-15)."""


class BatchJobFailed(Exception):
    """Job de batch terminou em estado de falha global (não por item)."""


@dataclass
class _Slot:
    """Uma chamada pendente: o pedido, o evento de conclusão e o resultado."""

    request: object
    event: threading.Event = field(default_factory=threading.Event)
    result: object = None


class BatchCoalescer:
    """Agrupa chamadas concorrentes em jobs de Batch API por (provedor, modelo).

    `sleep` e `clock` são injetáveis para testes determinísticos, no mesmo
    padrão da `RetryPolicy` (AD-11).
    """

    def __init__(
        self,
        *,
        max_size: int = BATCH_MAX_SIZE,
        window_sec: float = BATCH_WINDOW_SEC,
        poll_interval_sec: float = BATCH_POLL_INTERVAL_SEC,
        max_wait_sec: float = BATCH_MAX_WAIT_SEC,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_size = max_size
        self.window_sec = window_sec
        self.poll_interval_sec = poll_interval_sec
        self.max_wait_sec = max_wait_sec
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._groups: dict[str, list[_Slot]] = {}

    def submit(self, key: str, provider, request):
        """Enfileira `request` no grupo `key` e bloqueia até o resultado.

        Devolve a `LLMResponse` do item ou re-levanta a exceção associada a ele
        (mantendo a taxonomia de AD-11, para o retry do chamador classificar).
        """
        slot = _Slot(request=request)
        with self._lock:
            group = self._groups.setdefault(key, [])
            group.append(slot)
            is_leader = len(group) == 1

        if is_leader:
            self._lead(key, provider)
        else:
            slot.event.wait()

        if isinstance(slot.result, BaseException):
            raise slot.result
        return slot.result

    # ── Interno ────────────────────────────────────────────────────────────

    def _lead(self, key: str, provider) -> None:
        """Espera a janela de coalescência, submete o grupo e distribui."""
        deadline = self._clock() + self.window_sec
        while True:
            with self._lock:
                size = len(self._groups.get(key, ()))
            remaining = deadline - self._clock()
            if size >= self.max_size or remaining <= 0:
                break
            self._sleep(min(remaining, self.poll_interval_sec))

        with self._lock:
            group = self._groups.pop(key, [])
        if not group:
            return

        logger.info("batch %s: submetendo %d requisição(ões)", key, len(group))
        try:
            results = self._run_job(provider, [s.request for s in group])
        except BaseException as exc:  # noqa: BLE001 — propagado a cada chamador
            results = [exc] * len(group)

        for slot, result in zip(group, results, strict=False):
            slot.result = result
            slot.event.set()

    def _run_job(self, provider, requests: list) -> list:
        """Submete o job e faz poll até concluir. Retorna um resultado por item."""
        job_id = provider.submit_batch(requests)
        deadline = self._clock() + self.max_wait_sec
        while True:
            results = provider.poll_batch(job_id, len(requests))
            if results is not None:
                logger.info("batch job %s concluído (%d itens)", job_id, len(results))
                return results
            if self._clock() >= deadline:
                raise BatchTimeout(f"job {job_id} não concluiu em {self.max_wait_sec}s (AD-15)")
            self._sleep(self.poll_interval_sec)
