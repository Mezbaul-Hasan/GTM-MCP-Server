"""Recipe tool (plan/apply) and CLI behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_mcp import cli, config

from .conftest import PUBLIC_ID, TAGS, TRIGGERS, WS, call, mock_snapshot, ok

pytestmark = pytest.mark.anyio


async def test_recipe_plan_then_apply(client, api, token_provider):
    mock_snapshot(api)
    err, plan = await call(client, "gtm_setup_ga4_ecommerce_events", {"container": PUBLIC_ID, "measurement_id": "G-0CV94E19P1"})
    assert not err, plan
    assert plan["dry_run"] is True and plan["applied"] is False
    p = plan["preview"]
    assert p["skipped"]["view_item"].startswith("already has")
    assert p["skipped"]["purchase"].startswith("existing tag(s) are paused")
    assert "add_to_cart" not in p["skipped"]  # AddToCart (camel case) does not count
    # 11 events − view_item − purchase = 9 tags; 9 triggers (none of the remaining events has a trigger)
    assert p["will_create"] == {"triggers": 9, "tags": 9}
    assert p["api_requests_needed"] == 18
    assert all(s["action"] in {"create_trigger", "create_tag"} for s in p["steps"])
    first_tag = next(s for s in p["steps"] if s["action"] == "create_tag")
    assert first_tag["body"]["name"].startswith("GA4 - Event - ") and first_tag["body"]["type"] == "gaawe"
    assert any("developer work" in w for w in plan["warnings"])
    assert not [c for c in api.calls if c.request.method == "POST"]

    # apply a subset with the write profile
    token_provider.scopes = list(config.SCOPES_WRITES)
    counter = {"t": 100}

    def make_trigger(request):
        counter["t"] += 1
        body = json.loads(request.content)
        return ok(TRIGGERS[0] | {"triggerId": str(counter["t"]), "name": body["name"]})

    def make_tag(request):
        body = json.loads(request.content)
        return ok(TAGS[2] | {"tagId": "9" + body["firingTriggerId"][0], "name": body["name"], "firingTriggerId": body["firingTriggerId"], "paused": False})

    trig_route = api.post(f"{WS}/triggers").mock(side_effect=make_trigger)
    tag_route = api.post(f"{WS}/tags").mock(side_effect=make_tag)
    err, data = await call(client, "gtm_setup_ga4_ecommerce_events", {"container": PUBLIC_ID, "measurement_id": "G-0CV94E19P1", "events": ["begin_checkout", "purchase", "view_item"], "dry_run": False})
    assert not err, data
    assert data["applied"] is True
    created = data["result"]["created"]
    assert [c["kind"] for c in created] == ["trigger", "tag"]
    assert created[1]["firing_trigger_ids"] == [created[0]["trigger_id"]]
    assert data["result"]["skipped"]["purchase"].startswith("existing tag(s) are paused")
    assert trig_route.call_count == 1 and tag_route.call_count == 1
    sent = json.loads(tag_route.calls.last.request.content)
    assert sent["notes"] == "created by gtm-mcp recipe" and sent["firingTriggerId"] == ["101"]


async def test_recipe_requires_scope_and_limits(client, api, token_provider):
    mock_snapshot(api)
    err, text = await call(client, "gtm_setup_ga4_ecommerce_events", {"container": PUBLIC_ID, "measurement_id": "G-0CV94E19P1", "dry_run": False})
    assert err and "auth --with-writes" in text
    err, text = await call(client, "gtm_setup_ga4_ecommerce_events", {"container": PUBLIC_ID, "measurement_id": "G-0CV94E19P1", "events": [f"e{i}" for i in range(25)]})
    assert err and "at most" in text
    err, data = await call(client, "gtm_setup_ga4_ecommerce_events", {"container": PUBLIC_ID, "measurement_id": "G-0CV94E19P1", "events": ["view_item"]})
    assert not err and data["message"] == "nothing to create"


async def test_recipe_can_add_google_tag(client, api):
    mock_snapshot(api)
    err, plan = await call(client, "gtm_setup_ga4_ecommerce_events", {"container": PUBLIC_ID, "measurement_id": "G-NEWNEW123", "events": ["refund"], "add_google_tag_if_missing": True})
    assert not err, plan
    kinds = [(s["action"], s["body"]["type"]) for s in plan["preview"]["steps"] if "body" in s]
    assert ("create_tag", "googtag") in kinds
    assert any("Google tag" in w for w in plan["warnings"])


# ------------------------------------------------------------------------------- CLI
def test_cli_help_and_unknown(capsys):
    assert cli.main(["help"]) == 0
    assert "auth --with-writes" in capsys.readouterr().err
    assert cli.main(["bogus"]) == 2


def test_cli_status_not_signed_in(config_dir: Path, capsys, monkeypatch):
    for var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert cli.main(["status"]) == 1
    info = json.loads(capsys.readouterr().out)
    assert info["signed_in"] is False and info["config_dir"] == str(config_dir) and info["profile"] is None


def test_cli_auth_with_writes_requests_write_scopes(config_dir: Path, monkeypatch, capsys):
    captured: dict[str, object] = {}

    class FakeFlow:
        def __init__(self, **kw):
            captured.update(kw)

        def run(self, echo):
            from marketing_mcp_common.google_auth import GoogleCredentials

            return GoogleCredentials(client_id="id", client_secret="secret", refresh_token="rt", scopes=list(captured["scopes"]))

    class FakeProvider:
        def __init__(self, *a, **k):
            pass

        def get_access_token_sync(self, *, force_refresh=False):
            return "token"

    monkeypatch.setattr(cli, "GoogleOAuthDesktopFlow", FakeFlow)
    monkeypatch.setattr(cli, "GoogleTokenProvider", FakeProvider)
    assert cli.main(["auth", "--with-writes", "--client-id", "id", "--client-secret", "secret", "--no-browser"]) == 0
    assert captured["scopes"] == config.SCOPES_WRITES
    assert "read+write+publish" in capsys.readouterr().err
    saved = json.loads((config_dir / "config.json").read_text())
    assert config.SCOPE_PUBLISH in saved["scopes"]
    assert cli.main(["auth", "--client-id", "id", "--client-secret", "secret", "--no-browser"]) == 0
    assert captured["scopes"] == config.SCOPES_READONLY


def test_profile_labels():
    assert config.profile_for_scopes(None) == "unknown"
    assert config.profile_for_scopes([config.SCOPE_READONLY]) == "read-only"
    assert config.profile_for_scopes([config.SCOPE_READONLY, config.SCOPE_EDIT_CONTAINERS]) == "read+write"
    assert config.profile_for_scopes(config.SCOPES_WRITES) == "read+write+publish"
    assert config.SCOPES_WRITES == [config.SCOPE_READONLY, config.SCOPE_EDIT_CONTAINERS, config.SCOPE_EDIT_VERSIONS, config.SCOPE_PUBLISH]
    assert not any(s.endswith(("delete.containers", "manage.users", "manage.accounts")) for s in config.SCOPES_WRITES)
