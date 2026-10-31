"""Thin Tag Manager API v2 client: paths → URLs, pagination, request counting, error → ToolError."""

from __future__ import annotations

from typing import Any

from marketing_mcp_common.errors import AuthError, PlatformHTTPError, ToolError, google_error_to_tool_error
from marketing_mcp_common.http import HostNotAllowedError, HttpClient

from . import config

GTM_HINTS: dict[int, str] = {
    400: "GTM rejected the request — check ids and the entity body (tag/trigger/variable `type`, parameter keys, trigger ids)",
    401: "the Google token was rejected — run `gtm-mcp auth` again",
    403: (
        "this Google account lacks the GTM role for the container (Read / Edit / Approve / Publish), "
        "or the Tag Manager API is not enabled in the OAuth client's Cloud project, "
        "or you signed in read-only — run `gtm-mcp auth --with-writes` for write tools"
    ),
    404: "account / container / workspace / entity id is wrong — use gtm_find_container, gtm_list_workspaces or the matching gtm_list_* tool",
    409: "the entity changed since it was read (fingerprint mismatch) or the name is already used — read it again and retry",
    412: "the entity changed since it was read (fingerprint mismatch) — read it again and retry",
    429: (
        "Tag Manager API quota hit (25 requests per 100 s and 10,000 per day per Cloud project) — wait 1–2 minutes; "
        "prefer gtm_container_audit / gtm_check_ga4_ecommerce_coverage over many single calls"
    ),
    500: "temporary Tag Manager error — retry in a minute",
    503: "Tag Manager is temporarily unavailable — retry in a minute",
}


def _wrap(exc: Exception) -> ToolError:
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, AuthError):
        return ToolError(f"GTM auth: {exc}")
    if isinstance(exc, HostNotAllowedError):
        return ToolError(f"GTM raw request refused: {exc}")
    if isinstance(exc, PlatformHTTPError):
        return google_error_to_tool_error(exc, platform="GTM", hints=GTM_HINTS)
    return ToolError(f"GTM request failed: {type(exc).__name__}: {exc}")


class GTMClient:
    """All calls go through here so quota usage can be counted and errors mapped once."""

    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self.requests_made = 0

    @staticmethod
    def url(path: str) -> str:
        return f"{config.API_BASE}/{path.lstrip('/')}"

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None) -> dict[str, Any]:
        self.requests_made += 1
        try:
            data = await self.http.request_json(method, self.url(path), params=params, json=body)
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise _wrap(exc) from exc
        return data if isinstance(data, dict) else {}

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, body: Any = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("POST", path, params=params, body=body)

    async def put(self, path: str, body: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("PUT", path, params=params, body=body)

    async def list_all(self, path: str, key: str, params: dict[str, Any] | None = None, *, max_pages: int = config.MAX_LIST_PAGES) -> list[dict[str, Any]]:
        """Follow ``nextPageToken`` until exhausted (GTM lists have no page-size control)."""
        items: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(max_pages):
            query = dict(params or {})
            if token:
                query["pageToken"] = token
            data = await self.get(path, query or None)
            page = data.get(key) or []
            items.extend(x for x in page if isinstance(x, dict))
            token = data.get("nextPageToken") or None
            if not token:
                break
        return items
