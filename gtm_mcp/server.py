"""Build the gtm-mcp MCPServer: wires auth → rate limiter → http → client → tools."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from marketing_mcp_common.audit import AuditLog
from marketing_mcp_common.errors import AuthError
from marketing_mcp_common.google_auth import GoogleCredentialStore, GoogleTokenProvider
from marketing_mcp_common.http import HttpClient, TokenProvider
from marketing_mcp_common.logging import get_logger
from marketing_mcp_common.ratelimit import TokenBucket

from . import config
from .client import GTMClient
from .tools import helpers, reads, recipes, writes
from .tools._shared import Deps

log = get_logger("gtm_mcp")

INSTRUCTIONS = """Google Tag Manager (GTM) server — reads, audits and careful workspace edits, running on the user's own computer.
Workflow: (1) gtm_find_container with the GTM-XXXXXXX id from the website (or gtm_list_accounts → gtm_list_containers);
(2) gtm_container_audit and gtm_check_ga4_ecommerce_coverage give the whole picture in 3–6 API requests — prefer them over
many single list calls because the GTM API quota is only 25 requests per 100 seconds and 10,000 per day for everyone sharing
the Cloud project; (3) all write tools default to dry_run=true and return the exact request as a preview — show it to the
user, then call again with dry_run=false; (4) writes only change a workspace; the website changes only after
gtm_create_version followed by gtm_publish_version (🔴, needs confirm=true and the owner's explicit approval).
There are no delete or user-management tools. If a tool answers 'not signed in', the user must run `gtm-mcp auth`
(read-only) or `gtm-mcp auth --with-writes` once in a terminal. gtm_auth_status shows the current profile."""


def build_deps(
    *,
    token_provider: TokenProvider,
    config_dir: Path | None = None,
    audit_enabled: bool | None = None,
    http: HttpClient | None = None,
    scopes_getter: Callable[[], list[str] | None] | None = None,
    rate_limiter: TokenBucket | None = None,
) -> Deps:
    cfg_dir = config_dir or config.config_dir()
    limiter = rate_limiter if rate_limiter is not None else TokenBucket(config.rate_limit_qps(), config.RATE_LIMIT_BURST)
    http_client = http or HttpClient(
        token_provider=token_provider,
        allowed_hosts=config.ALLOWED_HOSTS,
        user_agent=f"{config.APP_NAME}/{config.VERSION}",
        logger_name="gtm_mcp.http",
        rate_limiter=limiter,
    )
    audit = AuditLog(cfg_dir / "audit.jsonl", enabled=config.audit_enabled() if audit_enabled is None else audit_enabled)
    return Deps(client=GTMClient(http_client), audit=audit, scopes_getter=scopes_getter or (lambda: None), rate_limiter=limiter)


def _scopes_from_provider(provider: GoogleTokenProvider) -> Callable[[], list[str] | None]:
    def getter() -> list[str] | None:
        try:
            scopes = provider.credentials().scopes
        except AuthError:
            return None
        return list(scopes) if scopes else None

    return getter


def create_server(deps: Deps | None = None) -> MCPServer:
    """Return a ready MCPServer. ``deps`` can be injected for tests."""
    if deps is None:
        store = GoogleCredentialStore(config.config_dir(), env_prefix="GOOGLE")
        provider = GoogleTokenProvider(store, app_name=config.APP_NAME)
        deps = build_deps(token_provider=provider, scopes_getter=_scopes_from_provider(provider))
    assert deps is not None

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[Deps]:
        try:
            yield deps
        finally:
            await deps.client.http.aclose()

    mcp = MCPServer(
        name=config.APP_NAME,
        title="Google Tag Manager",
        version=config.VERSION,
        instructions=INSTRUCTIONS,
        website_url="https://developers.google.com/tag-platform/tag-manager/api/v2",
        lifespan=lifespan,
    )
    reads.register(mcp, deps)
    helpers.register(mcp, deps)
    writes.register(mcp, deps)
    recipes.register(mcp, deps)
    return mcp


def run_stdio() -> None:
    server = create_server()
    log.info("%s %s starting on stdio (config dir %s, rate limit %.2f rps)", config.APP_NAME, config.VERSION, config.config_dir(), config.rate_limit_qps())
    server.run(transport="stdio")
