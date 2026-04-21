"""Configuration loaded from environment variables."""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Root of the agency-agents repository (one level above company/)
REPO_ROOT: Path = Path(__file__).parent.parent

# Agent category directories to scan
AGENT_DIRS: list[str] = [
    "academic",
    "design",
    "engineering",
    "finance",
    "game-development",
    "marketing",
    "paid-media",
    "product",
    "project-management",
    "sales",
    "spatial-computing",
    "specialized",
    "strategy",
    "support",
    "testing",
]

# ---------------------------------------------------------------------------
# Pollinations API
# ---------------------------------------------------------------------------

POLLINATIONS_BASE_URL: str = os.getenv(
    "POLLINATIONS_BASE_URL", "https://gen.pollinations.ai"
)

# Comma-separated list of sk_ API keys, tried in round-robin with failover.
_raw_keys: str = os.getenv("POLLINATIONS_API_KEYS", "")
POLLINATIONS_API_KEYS: list[str] = [k.strip() for k in _raw_keys.split(",") if k.strip()]

# Request timeout in seconds.
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "120"))

# How long (seconds) to cool down a key that returned a hard error before
# trying it again.
KEY_COOLDOWN_SECONDS: int = int(os.getenv("KEY_COOLDOWN_SECONDS", "120"))

# Explicit free-model allowlist.  Override via env with comma-separated names.
_raw_models: str = os.getenv(
    "MODEL_ALLOWLIST",
    "openai,openai-fast,qwen-coder,mistral,deepseek,claude-fast,nova-fast,nova,glm,minimax,kimi",
)
MODEL_ALLOWLIST: list[str] = [m.strip() for m in _raw_models.split(",") if m.strip()]

# Default model to use when caller does not specify one.
DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "openai")

# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# ---------------------------------------------------------------------------
# Server / security
# ---------------------------------------------------------------------------

# Optional bearer token required on every request to this server.
# Leave empty to disable auth (not recommended for public deployments).
SERVER_API_KEY: str = os.getenv("SERVER_API_KEY", "")

HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))

# Maximum conversation turns stored in memory per session.
MAX_HISTORY_TURNS: int = int(os.getenv("MAX_HISTORY_TURNS", "50"))
