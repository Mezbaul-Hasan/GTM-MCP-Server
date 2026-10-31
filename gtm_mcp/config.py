"""Static configuration for gtm-mcp (no secrets here — see .env.example)."""

from __future__ import annotations

import os
from pathlib import Path

from . import __version__

APP_NAME = "gtm-mcp"
VERSION = __version__

API_HOST = "tagmanager.googleapis.com"
API_BASE = f"https://{API_HOST}/tagmanager/v2"
ALLOWED_HOSTS = frozenset({API_HOST})

# Scopes (PROJECT.md §6 + docs/platform-notes/gtm.md §3).
SCOPE_READONLY = "https://www.googleapis.com/auth/tagmanager.readonly"
SCOPE_EDIT_CONTAINERS = "https://www.googleapis.com/auth/tagmanager.edit.containers"
SCOPE_EDIT_VERSIONS = "https://www.googleapis.com/auth/tagmanager.edit.containerversions"
SCOPE_PUBLISH = "https://www.googleapis.com/auth/tagmanager.publish"
SCOPES_READONLY = [SCOPE_READONLY]
SCOPES_WRITES = [SCOPE_READONLY, SCOPE_EDIT_CONTAINERS, SCOPE_EDIT_VERSIONS, SCOPE_PUBLISH]
# Never requested: tagmanager.delete.containers, tagmanager.manage.users, tagmanager.manage.accounts.

# Quota (official): 10,000 requests/project/day and 0.25 QPS = 25 requests per 100 s.
RATE_LIMIT_QPS = 0.25
RATE_LIMIT_BURST = 20  # leave head-room under the 25/100 s window for other users of the same project
DAILY_QUOTA = 10_000

# Output / request limits (PROJECT.md §5).
OUTPUT_BUDGET_BYTES = 50_000
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 1_000
MAX_LIST_PAGES = 25
MAX_ACCOUNTS_SCAN = 10
MAX_RECIPE_EVENTS = 20

# Built-in trigger ids that never appear in triggers.list.
BUILT_IN_TRIGGERS = {
    "2147479553": "All Pages (Page View)",
    "2147479572": "Consent Initialization - All Pages",
    "2147479573": "Initialization - All Pages",
}
ALL_PAGES_TRIGGER_ID = "2147479553"

ENV_CONFIG_DIR = "GTM_MCP_CONFIG_DIR"
ENV_AUDIT_DISABLED = "GTM_MCP_AUDIT_DISABLED"
ENV_RATE_LIMIT_QPS = "GTM_MCP_RATE_LIMIT_QPS"


def config_dir() -> Path:
    """``~/.gtm-mcp`` unless ``GTM_MCP_CONFIG_DIR`` points elsewhere."""
    override = os.environ.get(ENV_CONFIG_DIR)
    return Path(override).expanduser() if override else Path.home() / ".gtm-mcp"


def audit_enabled() -> bool:
    return os.environ.get(ENV_AUDIT_DISABLED, "").strip().lower() not in {"1", "true", "yes"}


def rate_limit_qps() -> float:
    raw = os.environ.get(ENV_RATE_LIMIT_QPS, "").strip()
    if not raw:
        return RATE_LIMIT_QPS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return RATE_LIMIT_QPS


def profile_for_scopes(scopes: list[str] | None) -> str:
    """Human label for the signed-in scope set: ``read-only`` / ``read+write`` / ``read+write+publish`` / ``unknown``."""
    if scopes is None:
        return "unknown"
    granted = set(scopes)
    if SCOPE_PUBLISH in granted and SCOPE_EDIT_CONTAINERS in granted:
        return "read+write+publish"
    if SCOPE_EDIT_CONTAINERS in granted:
        return "read+write"
    if SCOPE_READONLY in granted:
        return "read-only"
    return "unknown"
