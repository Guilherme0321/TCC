"""Configuração de logging compartilhada pelo pipeline.

Escreve simultaneamente no console (nível INFO) e em output/pipeline.log
(nível DEBUG), para permitir auditoria completa da execução sem poluir o
terminal durante uma triagem de 30-40 candidatos.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logger(name: str, log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # evita handlers duplicados em reimportações

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger
