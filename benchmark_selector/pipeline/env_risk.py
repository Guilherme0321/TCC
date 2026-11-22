"""Heurística para o critério de exclusão #1: dependência de
hardware/ambiente não reproduzível em container padrão.

Limitação documentada (registrar nas Ameaças à Validade): esta é uma
busca por palavras-chave nos arquivos de dependência declarados. Não
detecta uso indireto de GPU (ex.: chamada a binário externo via
subprocess) nem garante a ausência de dependências de serviços
proprietários não listadas aqui. Por isso:

- Dependências de GPU conhecidas -> exclusão automática (critério
  objetivo: GPU obrigatória não é reproduzível em container padrão).
- Dependências de serviços proprietários conhecidas -> apenas sinalizadas
  para revisão manual, pois podem ter mock viável (a própria redação do
  critério prevê essa possibilidade, que exige julgamento humano).
"""
from __future__ import annotations

from pathlib import Path

GPU_KEYWORDS = (
    "tensorflow-gpu", "cupy", "pycuda", "nvidia-", "torch+cu",
    "jax[cuda", "onnxruntime-gpu", "deepspeed", "horovod", "cuda-python",
)

PROPRIETARY_SERVICE_KEYWORDS = (
    "boto3", "azure-cognitiveservices", "google-cloud-vision",
    "openai", "anthropic", "stripe", "twilio", "google-cloud-storage",
)

DEP_FILES = ("requirements.txt", "requirements-dev.txt", "requirements_test.txt",
             "pyproject.toml", "setup.py", "Pipfile")


def detect_environment_risk(repo_path: Path) -> tuple[bool, str]:
    """Retorna (exclusao_automatica, detalhe_para_log).

    `exclusao_automatica=True` apenas quando há indício de dependência de
    GPU. Indícios de serviço proprietário aparecem no detalhe mas não
    forçam exclusão automática.
    """
    hits: list[str] = []
    gpu_hit = False

    for fname in DEP_FILES:
        fpath = repo_path / fname
        if not fpath.exists():
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue

        for kw in GPU_KEYWORDS:
            if kw in text:
                hits.append(f"{fname}: possível dependência de GPU ({kw})")
                gpu_hit = True

        for kw in PROPRIETARY_SERVICE_KEYWORDS:
            if kw in text:
                hits.append(f"{fname}: possível serviço proprietário ({kw}) -- revisar mock viável")

    return gpu_hit, "; ".join(hits)
