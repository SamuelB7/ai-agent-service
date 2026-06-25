from dataclasses import dataclass
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    model: str
    backend_api_url: str
    docs_rag_url: str
    opensearch_url: str
    opensearch_index: str
    database_url: str
    github_token: str
    github_owner: str
    github_repo: str
    history_limit: int
    request_timeout_seconds: float

    @property
    def use_openai(self) -> bool:
        return bool(self.openai_api_key) and self.openai_api_key != "replace-me"

    @property
    def github_configured(self) -> bool:
        return bool(self.github_token and self.github_owner and self.github_repo)


def load_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("AI_AGENT_MODEL", "gpt-4o-mini"),
        backend_api_url=os.getenv("BACKEND_API_URL", "http://localhost:3000"),
        docs_rag_url=os.getenv("DOCS_RAG_URL", "http://localhost:8001"),
        opensearch_url=os.getenv("OPENSEARCH_URL", "http://localhost:9200"),
        opensearch_index=os.getenv("OPENSEARCH_INDEX", "logs"),
        database_url=normalize_database_url(os.getenv("DATABASE_URL", "")),
        github_token=os.getenv("GITHUB_TOKEN", ""),
        github_owner=os.getenv("GITHUB_OWNER", ""),
        github_repo=os.getenv("GITHUB_REPO", ""),
        history_limit=_read_positive_int(os.getenv("AI_AGENT_HISTORY_LIMIT"), 8),
        request_timeout_seconds=float(os.getenv("AI_AGENT_REQUEST_TIMEOUT_SECONDS", "10")),
    )


def normalize_database_url(value: str) -> str:
    if not value:
        return value

    parts = urlsplit(value)
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if key != "schema"]

    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _read_positive_int(value: str | None, fallback: int) -> int:
    if not value:
        return fallback

    try:
        parsed = int(value)
    except ValueError:
        return fallback

    return parsed if parsed > 0 else fallback
