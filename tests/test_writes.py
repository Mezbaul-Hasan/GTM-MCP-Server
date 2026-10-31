"""🟡 write tools (dry-run default, scope gating, previews) and the 🔴 publish tool."""

from __future__ import annotations

import json

import httpx
import pytest

from gtm_mcp import config

from .conftest import CONT, PUBLIC_ID, TAGS, TRIGGERS, WS, call, mock_lookup, mock_snapshot, mock_workspaces, ok, version

pytestmark = pytest.mark.anyio


def grant_writes(token_provider) -> None:
    token_provider.scopes = list(config.SCOPES_WRITES)


async def test_create_tag_dry_run_makes_no_write(client, api):
    mock_lookup(api)
    mock_workspaces(api)
    err, data = await call(
        client,
        "gtm_create_tag",
        {"container": PUBLIC_ID, "name": "Google tag", "type": "googtag", "parameters": {"tagId": "G-NEW12345"}, "firing_trigger_ids": [config.ALL_PAGES_TRIGGER_ID]},
    )
    assert not err, data
    assert data["dry_run"] is True and data["applied"] is False
    assert data["request"]["method"] == "POST" and data["request"]["path"] == f"accounts/111/containers/222/workspaces/7/tags"
    assert data["request"]["body"]["parameter"] == [{"type": "template", "key": "tagId", "value": "G-NEW12345"}]
    assert data["preview"]["type_label"].startswith("Google tag")
    assert "auth --with-writes" in data["warnings"][0]  # read-only sign-in warned up front
    assert not [c for c in api.calls if c.request.method == "POST"]


async def test_create_tag_requires_write_scope_then_posts(client, api, token_provider):
    mock_lookup(api)
    mock_workspaces(api)
    args = {"container": PUBLIC_ID, "name": "Google tag", "type": "googtag", "parameters": {"tagId": "G-NEW12345"}, "firing_trigger_ids": [config.ALL_PAGES_TRIGGER_ID], "dry_run": False}
    err, text = await call(client, "gtm_create_tag", args)
    assert err and "gtm-mcp auth --with-writes" in text and "read-only" in text
    grant_writes(token_provider)
    route = api.post(f"{WS}/tags").mock(return_value=ok(TAGS[0] | {"tagId": "50", "name": "Google tag"}))
    err, data = await call(client, "gtm_create_tag", args)
    assert not err, data
    assert data["applied"] is True and data["result"]["tag_id"] == "50"
    sent = json.loads(route.calls.last.request.content)
    assert sent["type"] == "googtag" and sent["firingTriggerId"] == ["2147479553"] and sent["paused"] is False
    err, text = await call(client, "gtm_create_tag", args | {"firing_trigger_ids": []})
    assert err and "never fires" in text


async def test_create_ga4_event_tag_reuses_or_creates_trigger(client, api, token_provider):
    mock_snapshot(api)
    grant_writes(token_provider)
    # purchase already has a trigger → reuse, single POST
    err, data = await call(client, "gtm_create_ga4_event_tag", {"container": PUBLIC_ID, "event_name": "purchase", "measurement_id": "g-0cv94e19p1"})
    assert not err, data
    assert data["dry_run"] is True
    assert len(data["request"]["body"]["steps"]) == 1
    body = data["request"]["body"]["steps"][0]["body"]
    assert body["firingTriggerId"] == ["10"] and body["name"] == "GA4 - Event - purchase"
    assert any("reusing existing trigger" in w for w in data["warnings"])
    assert any("dataLayer" in w for w in data["warnings"])
    # add_to_cart has no trigger → two steps; apply posts trigger then tag with the new id
    trig_route = api.post(f"{WS}/triggers").mock(return_value=ok(TRIGGERS[0] | {"triggerId": "77", "name": "CE - add_to_cart"}))
    tag_route = api.post(f"{WS}/tags").mock(return_value=ok(TAGS[2] | {"tagId": "78", "name": "GA4 - Event - add_to_cart", "firingTriggerId": ["77"]}))
    err, data = await call(client, "gtm_create_ga4_event_tag", {"container": PUBLIC_ID, "event_name": "add_to_cart", "measurement_id": "{{GA4 Measurement ID}}", "event_parameters": {"currency": "BDT"}, "dry_run": False})
    assert not err, data
    assert data["applied"] is True and data["result"]["trigger"]["trigger_id"] == "77" and data["result"]["tag"]["tag_id"] == "78"
    sent_tag = json.loads(tag_route.calls.last.request.content)
    assert sent_tag["firingTriggerId"] == ["77"]
    flat = {p["key"]: p.get("value") for p in sent_tag["parameter"] if "value" in p}
    assert flat["measurementIdOverride"] == "{{GA4 Measurement ID}}" and flat["sendEcommerceData"] == "true"
    sent_trig = json.loads(trig_route.calls.last.request.content)
    assert sent_trig["customEventFilter"][0]["parameter"][1]["value"] == "add_to_cart"
    err, text = await call(client, "gtm_create_ga4_event_tag", {"container": PUBLIC_ID, "event_name": "AddToCart", "measurement_id": "G-1"})
    assert err and "snake_case" in text


async def test_update_tag_diff_and_fingerprint(client, api, token_provider):
    mock_lookup(api)
    mock_workspaces(api)
    api.get(f"{WS}/tags/4").mock(return_value=ok(TAGS[3]))
    err, data = await call(client, "gtm_update_tag", {"container": PUBLIC_ID, "tag_id": "4", "paused": True, "parameters": {"eventName": "add_to_cart", "sendEcommerceData": True}})
    assert not err, data
    assert data["dry_run"] is True and data["request"]["method"] == "PUT" and data["request"]["params"] == {"fingerprint": "fp4"}
    assert data["preview"]["diff"]["paused"] == {"before": False, "after": True}
    assert data["preview"]["diff"]["parameters"]["eventName"] == {"before": "AddToCart", "after": "add_to_cart"}
    assert data["preview"]["diff"]["parameters"]["sendEcommerceData"] == {"before": False, "after": True}
    assert "fingerprint" not in data["request"]["body"] and "tagManagerUrl" not in data["request"]["body"]
    # no-op
    err, data = await call(client, "gtm_update_tag", {"container": PUBLIC_ID, "tag_id": "4", "paused": False})
    assert not err and data["applied"] is False and "no changes" in data["message"]
    grant_writes(token_provider)
    route = api.put(f"{WS}/tags/4").mock(return_value=ok(TAGS[3] | {"paused": True, "fingerprint": "fp4b"}))
    err, data = await call(client, "gtm_update_tag", {"container": PUBLIC_ID, "tag_id": "4", "paused": True, "dry_run": False})
    assert not err, data
    assert data["applied"] is True and route.calls.last.request.url.params["fingerprint"] == "fp4"
    assert json.loads(route.calls.last.request.content)["paused"] is True


async def test_create_trigger_variable_built_ins(client, api, token_provider):
    mock_snapshot(api)
    grant_writes(token_provider)
    err, data = await call(client, "gtm_create_trigger", {"container": PUBLIC_ID, "name": "CE - purchase 2", "custom_event_name": "purchase"})
    assert not err and any("already exists" in w for w in data["warnings"])
    err, data = await call(client, "gtm_create_trigger", {"container": PUBLIC_ID, "name": "PV - checkout", "type": "pageview", "filters": [{"variable": "{{Page Path}}", "op": "startsWith", "value": "/checkout"}]})
    assert not err and data["request"]["body"]["filter"][0]["type"] == "startsWith"
    err, text = await call(client, "gtm_create_trigger", {"container": PUBLIC_ID, "name": "bad", "type": "pageview", "filters": [{"variable": "{{Page Path}}", "op": "like", "value": "x"}]})
    assert err and "filter op" in text
    err, text = await call(client, "gtm_create_trigger", {"container": PUBLIC_ID, "name": "bad"})
    assert err and "custom_event_name" in text
    route = api.post(f"{WS}/variables").mock(return_value=ok({"variableId": "60", "name": "DLV - ecommerce.transaction_id", "type": "v", "parameter": []}))
    err, data = await call(client, "gtm_create_variable", {"container": PUBLIC_ID, "name": "DLV - ecommerce.transaction_id", "data_layer_name": "ecommerce.transaction_id", "dry_run": False})
    assert not err and data["applied"] is True and data["next_step"].endswith("{{DLV - ecommerce.transaction_id}}")
    sent = json.loads(route.calls.last.request.content)
    assert sent["type"] == "v" and sent["parameter"][0] == {"type": "template", "key": "name", "value": "ecommerce.transaction_id"}
    err, data = await call(client, "gtm_create_variable", {"container": PUBLIC_ID, "name": "Const", "type": "constant", "value": "x"})
    assert not err and data["request"]["body"]["type"] == "c"
    err, text = await call(client, "gtm_create_variable", {"container": PUBLIC_ID, "name": "JS", "type": "jsm"})
    assert err and "parameters are required" in text
    biv = api.post(f"{WS}/built_in_variables").mock(return_value=ok({"builtInVariable": [{"type": "clickElement", "name": "Click Element"}, {"type": "pagePath", "name": "Page Path"}]}))
    err, data = await call(client, "gtm_enable_built_in_variables", {"container": PUBLIC_ID, "types": ["clickElement", "pagePath"], "dry_run": False})
    assert not err and data["result"]["enabled"][0]["name"] == "Click Element"
    assert biv.calls.last.request.url.params.get_list("type") == ["clickElement", "pagePath"]
    err, text = await call(client, "gtm_enable_built_in_variables", {"container": PUBLIC_ID, "types": ["Page URL"]})
    assert err and "camelCase" in text


async def test_revert_entity(client, api, token_provider):
    mock_lookup(api)
    mock_workspaces(api)
    api.get(f"{WS}/tags/3").mock(return_value=ok(TAGS[2]))
    err, data = await call(client, "gtm_revert_entity", {"container": PUBLIC_ID, "entity_type": "tag", "entity_id": "3"})
    assert not err and data["dry_run"] is True and data["request"]["path"].endswith("/tags/3:revert")
    grant_writes(token_provider)
    route = api.post(f"{WS}/tags/3:revert").mock(return_value=ok({"tag": TAGS[2] | {"paused": False}}))
    err, data = await call(client, "gtm_revert_entity", {"container": PUBLIC_ID, "entity_type": "tag", "entity_id": "3", "dry_run": False})
    assert not err and data["result"]["state"] == "restored_to_live_version"
    assert route.calls.last.request.url.params["fingerprint"] == "fp3"
    err, text = await call(client, "gtm_revert_entity", {"container": PUBLIC_ID, "entity_type": "container", "entity_id": "3"})
    assert err and "entity_type must be one of" in text


async def test_create_workspace_and_version(client, api, token_provider):
    mock_snapshot(api)
    grant_writes(token_provider)
    ws_route = api.post(f"{CONT}/workspaces").mock(return_value=ok({"workspaceId": "9", "name": "gtm-mcp changes", "path": f"accounts/111/containers/222/workspaces/9"}))
    err, data = await call(client, "gtm_create_workspace", {"container": PUBLIC_ID, "name": "gtm-mcp changes", "dry_run": False})
    assert not err and data["result"]["workspace_id"] == "9" and ws_route.called
    # version: dry run shows pending changes + warnings
    err, data = await call(client, "gtm_create_version", {"container": PUBLIC_ID, "name": "purchase fix"})
    assert not err and data["dry_run"] is True and data["preview"] == {"pending_changes": 1, "merge_conflicts": 0}
    assert any("consumed" in w for w in data["warnings"]) and any("does not publish" in w for w in data["warnings"])
    route = api.post(f"{WS}:create_version").mock(return_value=ok({"containerVersion": version("6", "purchase fix"), "newWorkspacePath": "accounts/111/containers/222/workspaces/10", "syncStatus": {"mergeConflict": False, "syncError": False}}))
    err, data = await call(client, "gtm_create_version", {"container": PUBLIC_ID, "name": "purchase fix", "notes": "via gtm-mcp", "dry_run": False})
    assert not err, data
    assert data["result"]["version_id"] == "6" and data["result"]["new_workspace_path"].endswith("/workspaces/10")
    assert json.loads(route.calls.last.request.content) == {"name": "purchase fix", "notes": "via gtm-mcp"}
    assert "gtm_publish_version" in data["next_step"]
    # conflicts block the real call
    api.get(f"{WS}:getStatus").mock(return_value=ok({"workspaceChange": [], "mergeConflict": [{"entityInWorkspace": {"tag": {"tagId": "1"}}}]}))
    err, text = await call(client, "gtm_create_version", {"container": PUBLIC_ID, "name": "x", "dry_run": False})
    assert err and "merge conflict" in text
    # compiler error surfaces
    api.get(f"{WS}:getStatus").mock(return_value=ok({"workspaceChange": [{"tag": TAGS[0]}], "mergeConflict": []}))
    route.mock(return_value=ok({"compilerError": True}))
    err, text = await call(client, "gtm_create_version", {"container": PUBLIC_ID, "name": "x", "dry_run": False})
    assert err and "compiler error" in text


async def test_create_version_needs_version_scope(client, api, token_provider):
    mock_snapshot(api)
    token_provider.scopes = [config.SCOPE_READONLY, config.SCOPE_EDIT_CONTAINERS]
    err, text = await call(client, "gtm_create_version", {"container": PUBLIC_ID, "name": "x", "dry_run": False})
    assert err and "edit.containerversions" in text


async def test_publish_is_guarded(client, api, token_provider):
    mock_lookup(api)
    api.get(f"{CONT}/versions/6").mock(return_value=ok(version("6", "purchase fix")))
    api.get(f"{CONT}/versions:live").mock(return_value=ok(version("5", "v5 latest")))
    publish = api.post(f"{CONT}/versions/6:publish").mock(return_value=ok({"containerVersion": version("6", "purchase fix")}))
    # 1. dry run (default) → preview only
    err, data = await call(client, "gtm_publish_version", {"container": PUBLIC_ID, "version_id": "6"})
    assert not err and data["dry_run"] is True
    assert data["preview"]["to_publish"]["version_id"] == "6" and data["preview"]["currently_live"]["version_id"] == "5"
    assert "LIVE" in data["next_step"]
    # 2. dry_run=false but no confirm → refused
    err, text = await call(client, "gtm_publish_version", {"container": PUBLIC_ID, "version_id": "6", "dry_run": False})
    assert err and "confirm=true" in text and not publish.called
    # 3. confirm but read-only → scope error
    err, text = await call(client, "gtm_publish_version", {"container": PUBLIC_ID, "version_id": "6", "dry_run": False, "confirm": True})
    assert err and "tagmanager.publish" in text and not publish.called
    # 4. write profile without publish scope → still refused
    token_provider.scopes = [config.SCOPE_READONLY, config.SCOPE_EDIT_CONTAINERS, config.SCOPE_EDIT_VERSIONS]
    err, text = await call(client, "gtm_publish_version", {"container": PUBLIC_ID, "version_id": "6", "dry_run": False, "confirm": True})
    assert err and "publish" in text and not publish.called
    # 5. full profile → publishes, reports rollback id
    grant_writes(token_provider)
    err, data = await call(client, "gtm_publish_version", {"container": PUBLIC_ID, "version_id": "6", "dry_run": False, "confirm": True})
    assert not err, data
    assert data["applied"] is True and data["result"]["version_id"] == "6" and data["result"]["previous_live_version_id"] == "5"
    assert "Rollback" in data["next_step"] and publish.call_count == 1
    # 6. compiler error → not published
    publish.mock(return_value=ok({"compilerError": True}))
    err, text = await call(client, "gtm_publish_version", {"container": PUBLIC_ID, "version_id": "6", "dry_run": False, "confirm": True})
    assert err and "NOT published" in text


async def test_publish_tool_is_annotated_destructive(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert tools["gtm_publish_version"].annotations.destructive_hint is True
    assert tools["gtm_create_tag"].annotations.destructive_hint is False
    assert tools["gtm_create_tag"].annotations.read_only_hint is False
    assert tools["gtm_publish_version"].input_schema["properties"]["confirm"]["default"] is False


async def test_write_errors_are_mapped(client, api, token_provider):
    mock_lookup(api)
    mock_workspaces(api)
    grant_writes(token_provider)
    api.post(f"{WS}/tags").mock(return_value=httpx.Response(400, json={"error": {"code": 400, "message": "Tag name already exists", "status": "INVALID_ARGUMENT"}}))
    err, text = await call(client, "gtm_create_tag", {"container": PUBLIC_ID, "name": "dup", "type": "html", "parameters": {"html": "<b/>"}, "firing_trigger_ids": ["2147479553"], "dry_run": False})
    assert err and "already exists" in text and "GTM rejected" in text
