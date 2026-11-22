"""Garante que a raiz do repositório esteja em sys.path, permitindo
`from pipeline import ...` nos testes independentemente do cwd."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
