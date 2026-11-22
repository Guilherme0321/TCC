"""Cliente HTTP fino sobre a API REST do GitHub (v3).

Responsabilidades:
- Autenticação via Personal Access Token (PAT), quando fornecido.
- Respeito a rate limit primário (X-RateLimit-*) e secundário (Retry-After).
- Retries com backoff exponencial em erros transitórios (5xx).
- Endpoints específicos usados pelo pipeline: busca, metadados de repo,
  commit mais recente e data do primeiro commit.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import requests

from . import config

logger = logging.getLogger("benchmark_selector.github_api")


class GitHubAPIError(Exception):
    """Falha persistente ao acessar a API do GitHub após retries."""


class GitHubClient:
    def __init__(self, token: Optional[str] = None, base_url: str = config.GITHUB_API_URL):
        self.base_url = base_url
        self.session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tcc-benchmark-selector",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.session.headers.update(headers)
        self.authenticated = token is not None

    # ----------------------------------------------------------------
    # Núcleo de requisição com rate limiting / retries
    # ----------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        max_retries = 5

        for attempt in range(1, max_retries + 1):
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
            except requests.RequestException as exc:
                wait = min(2 ** attempt, 60)
                logger.warning("Erro de rede (%s). Retry em %ss (tentativa %d/%d).",
                                exc, wait, attempt, max_retries)
                time.sleep(wait)
                continue

            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                wait = self._seconds_until_reset(resp.headers.get("X-RateLimit-Reset"))
                logger.warning("Rate limit primário atingido. Aguardando %.0fs...", wait)
                time.sleep(wait + 1)
                continue

            if resp.status_code in (403, 429) and resp.headers.get("Retry-After"):
                wait = float(resp.headers["Retry-After"])
                logger.warning("Rate limit secundário/abuso. Aguardando %.0fs...", wait)
                time.sleep(wait + 1)
                continue

            if resp.status_code >= 500:
                wait = min(2 ** attempt, 60)
                logger.warning("Erro %s do GitHub. Retry em %ss (tentativa %d/%d).",
                                resp.status_code, wait, attempt, max_retries)
                time.sleep(wait)
                continue

            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining is not None and int(remaining) < 3:
                wait = self._seconds_until_reset(resp.headers.get("X-RateLimit-Reset"))
                logger.info("Rate limit quase esgotado (%s restantes). Aguardando %.0fs.",
                            remaining, wait)
                time.sleep(wait + 1)

            return resp

        raise GitHubAPIError(f"Falha persistente ao acessar {url} após {max_retries} tentativas.")

    @staticmethod
    def _seconds_until_reset(reset_header: Optional[str]) -> float:
        if not reset_header:
            return 60.0
        try:
            reset_ts = int(reset_header)
        except ValueError:
            return 60.0
        return max(reset_ts - time.time(), 1.0)

    # ----------------------------------------------------------------
    # Endpoints usados pelo pipeline
    # ----------------------------------------------------------------

    def search_repositories(self, query: str, per_page: int = 30, page: int = 1) -> list[dict]:
        resp = self._request(
            "GET", "/search/repositories",
            params={"q": query, "per_page": per_page, "page": page, "sort": "updated", "order": "desc"},
        )
        if resp.status_code != 200:
            logger.error("Busca falhou (%s) para query=%r: %s", resp.status_code, query, resp.text[:300])
            return []
        return resp.json().get("items", [])

    def get_repo(self, owner: str, repo: str) -> Optional[dict]:
        resp = self._request("GET", f"/repos/{owner}/{repo}")
        if resp.status_code != 200:
            logger.warning("Não foi possível obter metadados de %s/%s (HTTP %s)", owner, repo, resp.status_code)
            return None
        return resp.json()

    def get_latest_commit(self, owner: str, repo: str, default_branch: str) -> Optional[dict]:
        resp = self._request("GET", f"/repos/{owner}/{repo}/commits/{default_branch}")
        if resp.status_code != 200:
            return None
        return resp.json()

    def get_first_commit_date(self, owner: str, repo: str, default_branch: str) -> Optional[datetime]:
        """Recupera a data do primeiro commit do branch default.

        Usa o truque de paginação do header `Link`: pede 1 commit por
        página e segue para a última página (rel="last"), que contém o
        commit mais antigo do histórico linear do branch.
        """
        resp = self._request(
            "GET", f"/repos/{owner}/{repo}/commits",
            params={"sha": default_branch, "per_page": 1},
        )
        if resp.status_code != 200:
            return None

        last_page = 1
        link = resp.headers.get("Link", "")
        for part in link.split(","):
            if 'rel="last"' in part:
                url_part = part.split(";")[0].strip().strip("<>")
                try:
                    last_page = int(url_part.split("page=")[-1].split("&")[0])
                except (ValueError, IndexError):
                    last_page = 1

        if last_page == 1:
            commits = resp.json()
        else:
            resp2 = self._request(
                "GET", f"/repos/{owner}/{repo}/commits",
                params={"sha": default_branch, "per_page": 1, "page": last_page},
            )
            if resp2.status_code != 200:
                return None
            commits = resp2.json()

        if not commits:
            return None

        date_str = commits[0]["commit"]["committer"]["date"]
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
