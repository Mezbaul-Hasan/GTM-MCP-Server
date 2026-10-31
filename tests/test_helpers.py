"""Audit / coverage helpers and the pure model functions."""

from __future__ import annotations

import pytest

from gtm_mcp import model

from .conftest import PUBLIC_ID, WS, call, mock_snapshot, ok, tpl

pytestmark = pytest.mark.anyio


def test_parameter_round_trip():
    flat = {"eventName": "purchase", "sendEcommerceData": True, "dataLayerVersion": 2, "eventSettingsTable": [{"parameter": "currency", "parameterValue": "BDT"}], "nested": {"a": "b"}}
    params = model.to_parameters(flat)
    assert params[0] == {"type": "template", "key": "eventName", "value": "purchase"}
    assert params[1] == {"type": "boolean", "key": "sendEcommerceData", "value": "true"}
    assert params[2] == {"type": "integer", "key": "dataLayerVersion", "value": "2"}
    assert params[3]["type"] == "list" and params[3]["list"][0]["type"] == "map" and "key" not in params[3]["list"][0]
    assert model.flatten_parameters(params) == flat
    # raw Parameter objects pass through untouched
    raw = {"type": "template", "key": "x", "value": "{{Page URL}}"}
    assert model.to_parameter("x", raw) == raw


def test_merge_parameters_and_references():
    existing = [tpl("eventName", "AddToCart"), tpl("measurementIdOverride", "{{GA4 Measurement ID}}")]
    merged = model.merge_parameters(existing, {"eventName": "add_to_cart", "sendEcommerceData": True, "measurementIdOverride": None})
    assert model.flatten_parameters(merged) == {"eventName": "add_to_cart", "sendEcommerceData": True}
    assert model.variable_references({"parameter": existing}) == {"GA4 Measurement ID"}


def test_custom_event_names_and_trigger_summary():
    eq = model.build_custom_event_trigger(name="CE - purchase", event_name="purchase")
    assert model.custom_event_names(eq) == ["purchase"]
    rx = model.build_custom_event_trigger(name="CE - cart", event_name="add_to_cart|remove_from_cart", use_regex=True)
    assert model.custom_event_names(rx) == ["add_to_cart", "remove_from_cart"]
    assert model.describe_trigger(rx) == "Custom Event: {{_event}} matchRegex add_to_cart|remove_from_cart"
    assert model.custom_event_names({"type": "pageview"}) == []


def test_builders_match_gtm_shapes():
    tag = model.build_ga4_event_tag(name="GA4 - Event - purchase", event_name="purchase", measurement_id="G-ABC12345", firing_trigger_ids=["10"], event_parameters={"currency": "BDT"}, send_ecommerce_data=True)
    flat = model.flatten_parameters(tag["parameter"])
    assert tag["type"] == "gaawe" and tag["tagFiringOption"] == "oncePerEvent"
    assert flat["eventName"] == "purchase" and flat["measurementIdOverride"] == "G-ABC12345"
    assert flat["sendEcommerceData"] is True and flat["getEcommerceDataFrom"] == "dataLayer"
    assert flat["eventSettingsTable"] == [{"parameter": "currency", "parameterValue": "BDT"}]
    gt = model.build_google_tag(name="Google tag", tag_id="G-ABC12345")
    assert gt["type"] == "googtag" and gt["firingTriggerId"] == ["2147479553"] and model.flatten_parameters(gt["parameter"]) == {"tagId": "G-ABC12345"}
    dlv = model.build_data_layer_variable(name="DLV - ecommerce", data_layer_name="ecommerce", default_value="")
    assert model.flatten_parameters(dlv["parameter"]) == {"name": "ecommerce", "dataLayerVersion": 2, "setDefaultValue": True, "defaultValue": ""}
    f = model.build_filter("{{Page Path}}", "contains", "/checkout", negate=True)
    assert model.flatten_parameters(f["parameter"]) == {"arg0": "{{Page Path}}", "arg1": "/checkout", "negate": True}


async def test_container_audit_findings(client, api):
    mock_snapshot(api)
    err, data = await call(client, "gtm_container_audit", {"container": PUBLIC_ID})
    assert not err, data
    assert data["counts"] == {"tags": 9, "tags_active": 8, "tags_paused": 1, "triggers": 4, "variables": 3, "built_in_variables": 2}
    assert data["requests_used"] == 7  # lookup + workspaces + tags + triggers + variables + built-ins + getStatus
    findings = {f["code"]: f for f in data["findings"]}
    assert findings["legacy_ua_tags"]["severity"] == "high"
    assert findings["duplicate_google_tags"]["items"][0]["measurement_id"] == "G-0CV94E19P1"
    assert findings["dangling_trigger_references"]["items"][0]["missing_trigger_ids"] == ["999"]
    assert findings["tags_without_firing_trigger"]["items"] == ["Orphan HTML"]
    # {{Page Path}} is referenced by a trigger but the pagePath built-in is not enabled → flagged too
    assert findings["undefined_variable_references"]["items"] == [
        {"entity": "tag", "name": "Orphan HTML", "undefined": ["Missing Var"]},
        {"entity": "trigger", "name": "Unused - checkout page", "undefined": ["Page Path"]},
    ]
    assert findings["unused_variables"]["items"] == ["DLV - ecommerce.items", "Unused constant"]
    assert findings["unused_triggers"]["items"] == ["Unused - checkout page"]
    assert findings["placeholder_values"]["items"][0]["name"] == "Ads conversion - broken"
    assert findings["non_snake_case_ga4_events"]["items"][0]["event_name"] == "AddToCart"
    assert findings["custom_html_tags"]["severity"] == "medium"
    assert findings["custom_html_tags"]["items"][0]["snippets"] == ["fbq("]
    assert findings["paused_tags"]["items"] == ["GA4 - purchase"]
    assert findings["unpublished_changes"]["severity"] == "info"
    assert data["summary"]["high"] >= 4
    assert data["workspace_status"] == {"unpublished_changes": 1, "merge_conflicts": 0}
    # findings are ordered by severity
    sev = [f["severity"] for f in data["findings"]]
    assert sev == sorted(sev, key=lambda s: {"high": 0, "medium": 1, "low": 2, "info": 3}[s])


async def test_audit_flags_merge_conflicts(client, api):
    mock_snapshot(api)
    api.get(f"{WS}:getStatus").mock(return_value=ok({"workspaceChange": [], "mergeConflict": [{"entityInWorkspace": {"tag": {"tagId": "1", "name": "x"}}}]}))
    err, data = await call(client, "gtm_container_audit", {"container": PUBLIC_ID})
    assert not err and data["findings"][0]["code"] == "workspace_merge_conflicts"


async def test_ga4_ecommerce_coverage(client, api):
    mock_snapshot(api)
    err, data = await call(client, "gtm_check_ga4_ecommerce_coverage", {"container": PUBLIC_ID})
    assert not err, data
    assert data["requests_used"] == 5  # no built-ins, no status
    assert data["covered_live"] == ["view_item"]
    assert data["paused_only"] == ["purchase"]
    assert "add_to_cart" in data["missing"] and "purchase" not in data["missing"]
    assert data["score"].startswith("1/11")
    assert data["details"]["purchase"][0]["triggers"] == ["CE - purchase"]
    assert data["existing_custom_event_triggers_for_ecommerce_events"] == {"purchase": [{"trigger_id": "10", "name": "CE - purchase"}], "view_item": [{"trigger_id": "12", "name": "CE - view_item"}]}
    assert data["google_tags"][0]["ids"] == ["G-0CV94E19P1"]
    assert "other_ga4_event_names" in data and data["other_ga4_event_names"] == {"AddToCart": 1}
    assert "gtm_setup_ga4_ecommerce_events" in data["next_step"]
    err, data = await call(client, "gtm_check_ga4_ecommerce_coverage", {"container": PUBLIC_ID, "include_optional_events": True})
    assert not err and len(data["events_checked"]) == 14
