"""Runtime configuration, read once from the environment.

Loads `.env` from the repo root with a tiny parser rather than pulling in a
dependency -- there are eleven keys and none of them need interpolation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(ROOT / ".env")


def _bool(key: str, default: bool) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    llm_enabled: bool = _bool("LLM_ENABLED", False)
    llm_base_url: str = os.environ.get("LLM_BASE_URL", "https://router.huggingface.co/v1")
    llm_api_key: str = os.environ.get("LLM_API_KEY", "")
    llm_model: str = os.environ.get("LLM_MODEL", "openai/gpt-oss-120b:fastest")
    llm_timeout_seconds: int = int(os.environ.get("LLM_TIMEOUT_SECONDS", "30"))
    llm_max_tokens: int = int(os.environ.get("LLM_MAX_TOKENS", "700"))
    llm_cache_dir: Path = ROOT / os.environ.get("LLM_CACHE_DIR", "data/llm_cache")
    llm_cache_only: bool = _bool("LLM_CACHE_ONLY", False)

    db_path: Path = ROOT / os.environ.get("RECOVERYOS_DB", "data/recoveryos.db")
    seed: int = int(os.environ.get("RECOVERYOS_SEED", "42"))

    api_host: str = os.environ.get("API_HOST", "127.0.0.1")
    api_port: int = int(os.environ.get("API_PORT", "8001"))

    @property
    def llm_usable(self) -> bool:
        return self.llm_enabled and bool(self.llm_api_key or self.llm_cache_only)


settings = Settings()
