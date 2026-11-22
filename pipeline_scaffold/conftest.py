"""Garante que a raiz do repositório esteja no sys.path para os testes do pipeline."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
