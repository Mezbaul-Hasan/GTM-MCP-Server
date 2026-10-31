"""Shared fixtures: in-process MCP client, fake token provider, respx-mocked Tag Manager API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp.client import Client

from gtm_mcp import config
from gtm_mcp.server import build_deps, create_server
from marketing_mcp_common.http import HttpClient
from marketing_mcp_common.ratelimit import TokenBucket

API = config.API_BASE
FAKE_TOKEN = "ya29.test-access-token-should-never-appear-in-logs"

A, C, W = "111", "222", "7"
PUBLIC_ID = "GTM-T2QC3R9"
WS = f"{API}/accounts/{A}/containers/{C}/workspaces/{W}"
CONT = f"{API}/accounts/{A}/containers/{C}"


class FakeTokenProvider:
    def __init__(self, scopes: list[str] | None = None) -> None:
        self.calls = 0
        self.scopes = scopes if scopes is not None else list(config.SCOPES_READONLY)

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        self.calls += 1
        return FAKE_TOKEN


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def token_provider() -> FakeTokenProvider:
    return FakeTokenProvider()


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(config.ENV_CONFIG_DIR, str(tmp_path))
    return tmp_path


@pytest.fixture
def limiter() -> TokenBucket:
    return TokenBucket(0, 1)  # disabled in tests (rate 0 = never waits)


@pytest.fixture
def deps(token_provider: FakeTokenProvider, config_dir: Path, limiter: TokenBucket):
    http = HttpClient(
        token_provider=token_provider,
        allowed_hosts=config.ALLOWED_HOSTS,
        backoff_base=0.0,
        backoff_cap=0.0,
        user_agent="gtm-mcp-tests",
        rate_limiter=limiter,
    )
    return build_deps(
        token_provider=token_provider,
        config_dir=config_dir,
        audit_enabled=True,
        http=http,
        scopes_getter=lambda: token_provider.scopes,
        rate_limiter=limiter,
    )


@pytest.fixture
def server(deps):
    return create_server(deps)


@pytest.fixture
async def client(server) -> AsyncIterator[Client]:
    async with Client(server) as c:
        yield c


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


async def call(client: Client, name: str, args: dict[str, Any] | None = None) -> tuple[bool, Any]:
    """Call a tool; return (is_error, parsed JSON or error text)."""
    result = await client.call_tool(name, args or {})
    text = result.content[0].text if result.content else ""
    if result.is_error:
        return True, text
    try:
        return False, json.loads(text)
    except ValueError:
        return False, text


def audit_lines(config_dir: Path) -> list[dict[str, Any]]:
    path = config_dir / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ok(body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


# ----------------------------------------------------------------------------- API shapes
def tpl(key: str, value: str) -> dict[str, str]:
    return {"type": "template", "key": key, "value": value}


def boolean(key: str, value: bool) -> dict[str, str]:
    return {"type": "boolean", "key": key, "value": "true" if value else "false"}


def ce_filter(event: str, op: str = "equals") -> list[dict[str, Any]]:
    return [{"type": op, "parameter": [tpl("arg0", "{{_event}}"), tpl("arg1", event)]}]


ACCOUNT = {"path": f"accounts/{A}", "accountId": A, "name": "Agency Account", "tagManagerUrl": "https://tagmanager.google.com/#/admin/accounts/111"}
CONTAINER = {
    "path": f"accounts/{A}/containers/{C}",
    "accountId": A,
    "containerId": C,
    "name": "Shop BD — Web",
    "publicId": PUBLIC_ID,
    "usageContext": ["web"],
    "domainName": ["shop.example"],
    "tagIds": [PUBLIC_ID, "G-0CV94E19P1"],
    "fingerprint": "100",
    "tagManagerUrl": "https://tagmanager.google.com/#/container/accounts/111/containers/222",
}
WORKSPACE = {"path": f"accounts/{A}/containers/{C}/workspaces/{W}", "accountId": A, "containerId": C, "workspaceId": W, "name": "Default Workspace", "fingerprint": "1"}
WORKSPACE_2 = {"path": f"accounts/{A}/containers/{C}/workspaces/8", "accountId": A, "containerId": C, "workspaceId": "8", "name": "Feature X", "fingerprint": "1"}


def tag(tag_id: str, name: str, type_: str, params: list[dict[str, Any]], *, firing: list[str] | None = None, blocking: list[str] | None = None, paused: bool = False, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "path": f"accounts/{A}/containers/{C}/workspaces/{W}/tags/{tag_id}",
        "accountId": A,
        "containerId": C,
        "workspaceId": W,
        "tagId": tag_id,
        "name": name,
        "type": type_,
        "parameter": params,
        "fingerprint": f"fp{tag_id}",
        "firingTriggerId": firing if firing is not None else [config.ALL_PAGES_TRIGGER_ID],
        "tagFiringOption": "oncePerEvent",
        "tagManagerUrl": f"https://tagmanager.google.com/#/container/accounts/111/containers/222/workspaces/7/tags/{tag_id}",
    }
    if blocking:
        body["blockingTriggerId"] = blocking
    if paused:
        body["paused"] = True
    body.update(extra)
    return body


TAGS = [
    tag("1", "Google tag - G-0CV94E19P1", "googtag", [tpl("tagId", "G-0CV94E19P1")]),
    tag("2", "GA4 Config (old)", "googtag", [tpl("tagId", "G-0CV94E19P1")]),  # duplicate → double page_view
    tag("3", "GA4 - purchase", "gaawe", [tpl("eventName", "purchase"), tpl("measurementIdOverride", "G-0CV94E19P1"), boolean("sendEcommerceData", True), tpl("getEcommerceDataFrom", "dataLayer")], firing=["10"], paused=True),
    tag("4", "GA4 - AddToCart", "gaawe", [tpl("eventName", "AddToCart"), tpl("measurementIdOverride", "{{GA4 Measurement ID}}"), boolean("sendEcommerceData", False)], firing=["11"]),
    tag("5", "UA - Pageview", "ua", [tpl("trackType", "TRACK_PAGEVIEW"), tpl("trackingId", "UA-1234-1")]),
    tag("6", "Meta Pixel (HTML)", "html", [tpl("html", "<script>fbq('init','3344865008907604');fbq('track','PageView');</script>")]),
    tag("7", "Ads conversion - broken", "awct", [tpl("conversionId", "AW-XXXXXXXXX"), tpl("conversionLabel", "YOUR_LABEL")], firing=["999"]),
    tag("8", "GA4 - view_item", "gaawe", [tpl("eventName", "view_item"), tpl("measurementIdOverride", "G-0CV94E19P1"), boolean("sendEcommerceData", True), tpl("getEcommerceDataFrom", "dataLayer")], firing=["12"]),
    tag("9", "Orphan HTML", "html", [tpl("html", "<script>console.log({{Missing Var}})</script>")], firing=[]),
]


def trigger(trigger_id: str, name: str, type_: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "path": f"accounts/{A}/containers/{C}/workspaces/{W}/triggers/{trigger_id}",
        "accountId": A,
        "containerId": C,
        "workspaceId": W,
        "triggerId": trigger_id,
        "name": name,
        "type": type_,
        "fingerprint": f"fp{trigger_id}",
        "tagManagerUrl": f"https://tagmanager.google.com/#/container/accounts/111/containers/222/workspaces/7/triggers/{trigger_id}",
    }
    body.update(extra)
    return body


TRIGGERS = [
    trigger("10", "CE - purchase", "customEvent", customEventFilter=ce_filter("purchase")),
    trigger("11", "CE - AddToCart", "customEvent", customEventFilter=ce_filter("AddToCart")),
    trigger("12", "CE - view_item", "customEvent", customEventFilter=ce_filter("view_item")),
    trigger("13", "Unused - checkout page", "pageview", filter=[{"type": "contains", "parameter": [tpl("arg0", "{{Page Path}}"), tpl("arg1", "/checkout")]}]),
]


def variable(variable_id: str, name: str, type_: str, params: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "path": f"accounts/{A}/containers/{C}/workspaces/{W}/variables/{variable_id}",
        "accountId": A,
        "containerId": C,
        "workspaceId": W,
        "variableId": variable_id,
        "name": name,
        "type": type_,
        "parameter": params,
        "fingerprint": f"fp{variable_id}",
        "tagManagerUrl": f"https://tagmanager.google.com/#/container/accounts/111/containers/222/workspaces/7/variables/{variable_id}",
    }


VARIABLES = [
    variable("20", "GA4 Measurement ID", "c", [tpl("value", "G-0CV94E19P1")]),
    variable("21", "DLV - ecommerce.items", "v", [tpl("name", "ecommerce.items"), {"type": "integer", "key": "dataLayerVersion", "value": "2"}, boolean("setDefaultValue", False)]),
    variable("22", "Unused constant", "c", [tpl("value", "abc")]),
]

BUILT_INS = [{"type": "pageUrl", "name": "Page URL", "workspaceId": W}, {"type": "event", "name": "Event", "workspaceId": W}]

VERSION_HEADERS = [
    {"path": f"accounts/{A}/containers/{C}/versions/3", "containerVersionId": "3", "name": "v3 purchase fix", "numTags": "9", "numTriggers": "4", "numVariables": "3"},
    {"path": f"accounts/{A}/containers/{C}/versions/5", "containerVersionId": "5", "name": "v5 latest", "numTags": "9", "numTriggers": "4", "numVariables": "3"},
    {"path": f"accounts/{A}/containers/{C}/versions/4", "containerVersionId": "4", "name": "v4", "deleted": True, "numTags": "8"},
]


def version(version_id: str, name: str = "v") -> dict[str, Any]:
    return {
        "path": f"accounts/{A}/containers/{C}/versions/{version_id}",
        "accountId": A,
        "containerId": C,
        "containerVersionId": version_id,
        "name": name,
        "fingerprint": "f",
        "tag": TAGS,
        "trigger": TRIGGERS,
        "variable": VARIABLES,
        "builtInVariable": BUILT_INS,
        "tagManagerUrl": f"https://tagmanager.google.com/#/versions/accounts/111/containers/222/versions/{version_id}",
    }


ENVIRONMENTS = [
    {"path": f"accounts/{A}/containers/{C}/environments/1", "environmentId": "1", "type": "live", "name": "Live", "containerVersionId": "5", "authorizationCode": "SECRET-CODE", "url": "https://tagmanager.google.com/?authuser=0#/preview?code=SECRET-CODE"},
    {"path": f"accounts/{A}/containers/{C}/environments/2", "environmentId": "2", "type": "latest", "name": "Latest", "containerVersionId": "5"},
]


def mock_lookup(api: respx.MockRouter) -> respx.Route:
    return api.get(f"{API}/accounts/containers:lookup", params={"tagId": PUBLIC_ID}).mock(return_value=ok(CONTAINER))


def mock_workspaces(api: respx.MockRouter) -> respx.Route:
    return api.get(f"{CONT}/workspaces").mock(return_value=ok({"workspace": [WORKSPACE_2, WORKSPACE]}))


def mock_snapshot(api: respx.MockRouter) -> None:
    mock_lookup(api)
    mock_workspaces(api)
    api.get(f"{WS}/tags").mock(return_value=ok({"tag": TAGS}))
    api.get(f"{WS}/triggers").mock(return_value=ok({"trigger": TRIGGERS}))
    api.get(f"{WS}/variables").mock(return_value=ok({"variable": VARIABLES}))
    api.get(f"{WS}/built_in_variables").mock(return_value=ok({"builtInVariable": BUILT_INS}))
    api.get(f"{WS}:getStatus").mock(return_value=ok({"workspaceChange": [{"tag": TAGS[2], "changeStatus": "updated"}], "mergeConflict": []}))
