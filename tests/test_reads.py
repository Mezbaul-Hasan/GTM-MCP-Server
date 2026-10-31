"""🟢 read tools against a respx-mocked Tag Manager API."""

from __future__ import annotations

import json

import httpx
import pytest

from gtm_mcp import config

from .conftest import (
    A,
    ACCOUNT,
    API,
    BUILT_INS,
    C,
    CONT,
    CONTAINER,
    ENVIRONMENTS,
    FAKE_TOKEN,
    PUBLIC_ID,
    TAGS,
    TRIGGERS,
    VARIABLES,
    VERSION_HEADERS,
    W,
    WORKSPACE,
    WS,
    audit_lines,
    call,
    mock_lookup,
    mock_snapshot,
    mock_workspaces,
    ok,
    version,
)

pytestmark = pytest.mark.anyio


async def test_tool_inventory_and_tiers(client):
    tools = (await client.list_tools()).tools
    names = {t.name for t in tools}
    assert len(tools) == 34
    destructive = {t.name for t in tools if t.annotations and t.annotations.destructive_hint}
    assert destructive == {"gtm_publish_version"}
    read_only = {t.name for t in tools if t.annotations and t.annotations.read_only_hint}
    assert len(read_only) == 23
    assert not any("delete" in n or "permission" in n for n in names)
    # every write tool defaults to dry_run=true
    for t in tools:
        if t.name not in read_only:
            assert t.input_schema["properties"]["dry_run"]["default"] is True, t.name


async def test_list_accounts_and_containers(client, api, token_provider):
    api.get(f"{API}/accounts").mock(return_value=ok({"account": [ACCOUNT]}))
    api.get(f"{API}/accounts/{A}/containers").mock(return_value=ok({"container": [CONTAINER]}))
    err, data = await call(client, "gtm_list_accounts")
    assert not err and data["accounts"][0]["account_id"] == A
    err, data = await call(client, "gtm_list_containers")
    assert not err and data["accounts_scanned"] == 1 and data["containers"][0]["public_id"] == PUBLIC_ID
    err, data = await call(client, "gtm_list_containers", {"account_id": A, "name_contains": "nothing"})
    assert not err and data["count"] == 0
    assert token_provider.calls >= 3
    # bearer header present, never logged
    assert api.calls.last.request.headers["authorization"] == f"Bearer {FAKE_TOKEN}"


async def test_find_container_resolves_public_id_and_caches(client, api):
    lookup = mock_lookup(api)
    workspaces = mock_workspaces(api)
    api.get(f"{WS}/tags").mock(return_value=ok({"tag": TAGS}))
    err, data = await call(client, "gtm_find_container", {"public_id": "gtm-t2qc3r9"})
    assert not err
    assert data["container"]["container_id"] == C and data["default_workspace_id"] == "7"
    assert [w["workspace_id"] for w in data["workspaces"]] == ["8", "7"]
    # second tool call must not repeat lookup / workspace listing
    err, data = await call(client, "gtm_list_tags", {"container": PUBLIC_ID})
    assert not err and data["total"] == len(TAGS)
    assert lookup.call_count == 1
    assert workspaces.call_count == 1
    assert data["requests_used"] == 1


async def test_find_container_by_destination_id_and_path(client, api):
    api.get(f"{API}/accounts/containers:lookup", params={"destinationId": "G-0CV94E19P1"}).mock(return_value=ok(CONTAINER))
    api.get(CONT).mock(return_value=ok(CONTAINER))
    err, data = await call(client, "gtm_get_container", {"container": "G-0CV94E19P1"})
    assert not err and data["container"]["public_id"] == PUBLIC_ID
    err, data = await call(client, "gtm_get_container", {"container": f"accounts/{A}/containers/{C}"})
    assert not err and data["container"]["name"] == "Shop BD — Web"
    err, text = await call(client, "gtm_get_container", {"container": "222"})
    assert err and "bare numeric id" in text
    err, text = await call(client, "gtm_get_container", {"container": "not-an-id"})
    assert err and "not a GTM public id" in text


async def test_list_tags_filters_and_compact_view(client, api):
    mock_snapshot(api)
    err, data = await call(client, "gtm_list_tags", {"container": PUBLIC_ID, "type": "gaawe"})
    assert not err
    assert data["count"] == 3 and data["total"] == 3 and data["paused_total"] == 1
    assert data["tags_by_type"]["gaawe"] == 3
    purchase = next(t for t in data["tags"] if t["name"] == "GA4 - purchase")
    assert purchase["paused"] is True and purchase["key_params"]["eventName"] == "purchase"
    assert purchase["key_params"]["sendEcommerceData"] is True
    assert purchase["type_label"] == "GA4 event"
    err, data = await call(client, "gtm_list_tags", {"container": PUBLIC_ID, "paused": True})
    assert not err and [t["tag_id"] for t in data["tags"]] == ["3"]
    err, data = await call(client, "gtm_list_tags", {"container": PUBLIC_ID, "limit": 2, "offset": 2})
    assert not err and data["count"] == 2 and data["next_offset"] == 4 and data["total"] == len(TAGS)
    err, data = await call(client, "gtm_list_tags", {"container": PUBLIC_ID, "type": "html"})
    html = data["tags"][0]
    assert "html_preview" in html["key_params"] and "fbq" in html["key_params"]["html_preview"]
    assert "html" not in html["key_params"]  # full code only via gtm_get_tag


async def test_get_tag_trigger_variable_full_views(client, api):
    mock_snapshot(api)
    api.get(f"{WS}/tags/4").mock(return_value=ok(TAGS[3]))
    api.get(f"{WS}/triggers/13").mock(return_value=ok(TRIGGERS[3]))
    api.get(f"{WS}/variables/21").mock(return_value=ok(VARIABLES[1]))
    err, data = await call(client, "gtm_get_tag", {"container": PUBLIC_ID, "tag_id": "4"})
    assert not err
    assert data["tag"]["parameters"]["eventName"] == "AddToCart"
    assert data["tag"]["variable_references"] == ["GA4 Measurement ID"]
    assert data["tag"]["fingerprint"] == "fp4"
    err, data = await call(client, "gtm_get_trigger", {"container": PUBLIC_ID, "trigger_id": "13"})
    assert not err and data["trigger"]["filter"] == ["{{Page Path}} contains /checkout"]
    err, data = await call(client, "gtm_get_trigger", {"container": PUBLIC_ID, "trigger_id": config.ALL_PAGES_TRIGGER_ID})
    assert not err and data["trigger"]["built_in"] is True
    err, data = await call(client, "gtm_get_variable", {"container": PUBLIC_ID, "variable_id": "21"})
    assert not err and data["variable"]["parameters"] == {"name": "ecommerce.items", "dataLayerVersion": 2, "setDefaultValue": False}
    assert data["variable"]["summary"] == "Data Layer Variable: ecommerce.items"


async def test_list_triggers_variables_built_ins(client, api):
    mock_snapshot(api)
    err, data = await call(client, "gtm_list_triggers", {"container": PUBLIC_ID})
    assert not err
    ce = next(t for t in data["triggers"] if t["trigger_id"] == "10")
    assert ce["summary"] == "Custom Event: {{_event}} equals purchase"
    assert data["built_in_triggers"][config.ALL_PAGES_TRIGGER_ID].startswith("All Pages")
    err, data = await call(client, "gtm_list_variables", {"container": PUBLIC_ID, "type": "c"})
    assert not err and data["count"] == 2 and data["variables"][0]["summary"] == "Constant: G-0CV94E19P1"
    err, data = await call(client, "gtm_list_built_in_variables", {"container": PUBLIC_ID})
    assert not err and data["built_in_variables"] == [{"type": b["type"], "name": b["name"]} for b in BUILT_INS]


async def test_pagination_follows_next_page_token(client, api):
    mock_lookup(api)
    mock_workspaces(api)
    route = api.get(f"{WS}/tags")
    route.side_effect = [
        ok({"tag": TAGS[:5], "nextPageToken": "p2"}),
        ok({"tag": TAGS[5:]}),
    ]
    err, data = await call(client, "gtm_list_tags", {"container": PUBLIC_ID})
    assert not err and data["total"] == len(TAGS)
    assert route.call_count == 2
    assert route.calls[1].request.url.params["pageToken"] == "p2"


async def test_versions_live_and_environments(client, api):
    mock_lookup(api)
    api.get(f"{CONT}/version_headers").mock(return_value=ok({"containerVersionHeader": VERSION_HEADERS}))
    api.get(f"{CONT}/versions:live").mock(return_value=ok(version("5", "v5 latest")))
    api.get(f"{CONT}/versions/3").mock(return_value=ok(version("3", "v3 purchase fix")))
    api.get(f"{CONT}/environments").mock(return_value=ok({"environment": ENVIRONMENTS}))
    err, data = await call(client, "gtm_list_versions", {"container": PUBLIC_ID})
    assert not err and [v["version_id"] for v in data["versions"]] == ["5", "4", "3"]
    assert data["versions"][0]["num_tags"] == 9
    err, data = await call(client, "gtm_get_live_version", {"container": PUBLIC_ID})
    assert not err and data["live_version"]["version_id"] == "5" and data["live_version"]["counts"]["tags"] == len(TAGS)
    assert "tags" not in data["live_version"]
    err, data = await call(client, "gtm_get_version", {"container": PUBLIC_ID, "version_id": "3", "include_entities": True})
    assert not err and len(data["version"]["tags"]) == len(TAGS)
    err, data = await call(client, "gtm_list_environments", {"container": PUBLIC_ID})
    assert not err
    live = data["environments"][0]
    assert live["container_version_id"] == "5"
    assert "SECRET-CODE" not in json.dumps(data)


async def test_workspace_status(client, api):
    mock_snapshot(api)
    err, data = await call(client, "gtm_get_workspace_status", {"container": PUBLIC_ID})
    assert not err
    assert data["change_count"] == 1 and data["conflict_count"] == 0 and data["ready_to_version"] is True
    assert data["changes"][0] == {"change": "updated", "entity_type": "tag", "id": "3", "name": "GA4 - purchase"}


async def test_explicit_workspace_id_skips_default_lookup(client, api):
    mock_lookup(api)
    workspaces = mock_workspaces(api)
    api.get(f"{CONT}/workspaces/8/tags").mock(return_value=ok({"tag": []}))
    err, data = await call(client, "gtm_list_tags", {"container": PUBLIC_ID, "workspace_id": "8"})
    assert not err and data["total"] == 0
    assert data["container"]["workspace"]["workspace_id"] == "8"
    assert workspaces.call_count == 0


async def test_raw_get_is_get_only_and_path_checked(client, api):
    api.get(f"{WS}/clients").mock(return_value=ok({"client": [{"clientId": "1", "name": "GA4 client"}]}))
    err, data = await call(client, "gtm_raw_get", {"path": f"/tagmanager/v2/accounts/{A}/containers/{C}/workspaces/{W}/clients"})
    assert not err and data["count"] == 1 and data["list_key"] == "client"
    err, text = await call(client, "gtm_raw_get", {"path": "https://evil.example/accounts"})
    assert err and "resource path" in text
    err, text = await call(client, "gtm_raw_get", {"path": "../oauth2/token"})
    assert err


async def test_error_mapping_403_404_429(client, api):
    mock_lookup(api)
    mock_workspaces(api)
    api.get(f"{WS}/tags").mock(return_value=httpx.Response(403, json={"error": {"code": 403, "message": "The caller does not have permission", "status": "PERMISSION_DENIED"}}))
    err, text = await call(client, "gtm_list_tags", {"container": PUBLIC_ID})
    assert err and "GTM role" in text and "auth --with-writes" in text and FAKE_TOKEN not in text
    api.get(f"{WS}/tags/404").mock(return_value=httpx.Response(404, json={"error": {"code": 404, "message": "Not found", "status": "NOT_FOUND"}}))
    err, text = await call(client, "gtm_get_tag", {"container": PUBLIC_ID, "tag_id": "404"})
    assert err and "gtm_find_container" in text
    route = api.get(f"{WS}/triggers")
    route.side_effect = [
        httpx.Response(429, json={"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}}, headers={"Retry-After": "0"}),
        ok({"trigger": TRIGGERS}),
    ]
    err, data = await call(client, "gtm_list_triggers", {"container": PUBLIC_ID})
    assert not err and data["total"] == len(TRIGGERS) and route.call_count == 2


async def test_auth_status_and_audit_log(client, api, config_dir, token_provider):
    err, data = await call(client, "gtm_auth_status")
    assert not err
    assert data["profile"] == "read-only" and data["write_tools_available"] is False
    assert "auth --with-writes" in data["how_to_enable_writes"]
    token_provider.scopes = list(config.SCOPES_WRITES)
    err, data = await call(client, "gtm_auth_status")
    assert data["profile"] == "read+write+publish" and data["publish_available"] is True and "how_to_enable_writes" not in data
    lines = audit_lines(config_dir)
    assert [l["tool"] for l in lines] == ["gtm_auth_status", "gtm_auth_status"]
    assert all(l["status"] == "ok" for l in lines)


async def test_token_bucket_is_wired(deps, client, api):
    from marketing_mcp_common.ratelimit import TokenBucket

    calls = {"n": 0}

    class Spy(TokenBucket):
        async def acquire(self) -> None:
            calls["n"] += 1

    deps.client.http._rate_limiter = Spy(0, 1)
    api.get(f"{API}/accounts").mock(return_value=ok({"account": [ACCOUNT]}))
    err, _ = await call(client, "gtm_list_accounts")
    assert not err and calls["n"] == 1
