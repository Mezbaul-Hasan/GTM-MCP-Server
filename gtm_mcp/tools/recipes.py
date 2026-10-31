"""🟡 Recipe: GA4 ecommerce event set (plan with dry_run=true, apply with dry_run=false)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from marketing_mcp_common.errors import ToolError

from .. import config, validation
from ..model import (
    GA4_ECOMMERCE_EVENTS,
    GA4_ECOMMERCE_OPTIONAL_EVENTS,
    build_custom_event_trigger,
    build_ga4_event_tag,
    build_google_tag,
    compact_tag,
    compact_trigger,
    custom_event_names,
    google_tag_ids,
)
from ._shared import WRITE, Deps, audited, applied_result, drop_empty, dry_run_result, load_snapshot, require_scope
from .helpers import coverage_from_snapshot


def register(mcp: MCPServer, deps: Deps) -> None:
    client = deps.client

    @mcp.tool(name="gtm_setup_ga4_ecommerce_events", title="Set up GA4 ecommerce event tags (🟡 recipe)", annotations=WRITE)
    @audited(deps, "gtm_setup_ga4_ecommerce_events")
    async def gtm_setup_ga4_ecommerce_events(
        container: str,
        measurement_id: str,
        events: list[str] | None = None,
        include_optional_events: bool = False,
        add_google_tag_if_missing: bool = False,
        tag_prefix: str = "GA4 - Event - ",
        trigger_prefix: str = "CE - ",
        workspace_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Plan (dry_run=true, default) or apply (dry_run=false) the standard GA4 ecommerce set in a workspace: for every
        missing event (view_item_list … purchase, refund; or the `events` you pass) one Custom Event trigger
        (`dataLayer.push({event: 'purchase', ecommerce: {...}})`) + one GA4 event tag with 'send ecommerce data'.
        Existing triggers/tags are reused, events that only have paused tags are reported (not duplicated).
        measurement_id = G-XXXXXXXX or {{GA4 Measurement ID}}. Nothing goes live until gtm_create_version + gtm_publish_version."""
        mid = validation.measurement_id_or_ref(measurement_id)
        wanted = [validation.ga4_event_name(e) for e in events] if events else list(GA4_ECOMMERCE_EVENTS) + (list(GA4_ECOMMERCE_OPTIONAL_EVENTS) if include_optional_events else [])
        if len(wanted) > config.MAX_RECIPE_EVENTS:
            raise ToolError(f"at most {config.MAX_RECIPE_EVENTS} events per call (quota: 25 requests / 100 s)")
        snap = await load_snapshot(deps, container, workspace_id, built_ins=False)
        target = snap.target
        coverage = coverage_from_snapshot(snap, include_optional=True)
        covered_live = set(coverage["covered_live"])
        paused_only = set(coverage["paused_only"])
        no_flag = set(coverage["active_but_no_ecommerce_data"])
        existing_triggers: dict[str, dict[str, Any]] = {}
        for tr in snap.triggers:
            for name in custom_event_names(tr):
                existing_triggers.setdefault(name, tr)
        existing_names = {str(t.get("name")) for t in snap.tags} | {str(t.get("name")) for t in snap.triggers}

        steps: list[dict[str, Any]] = []
        skipped: dict[str, str] = {}
        warnings: list[str] = []
        scopes = deps.scopes()
        if scopes and config.SCOPE_EDIT_CONTAINERS not in scopes:
            warnings.append(f"this sign-in is {config.profile_for_scopes(scopes)} — applying needs `gtm-mcp auth --with-writes`")
        for event in wanted:
            if event in covered_live:
                skipped[event] = "already has an active GA4 event tag with ecommerce data"
                continue
            if event in paused_only:
                skipped[event] = "existing tag(s) are paused — unpause with gtm_update_tag instead of creating a duplicate"
                continue
            if event in no_flag:
                skipped[event] = "active tag exists but without 'send ecommerce data' — fix with gtm_update_tag parameters {sendEcommerceData: true, getEcommerceDataFrom: 'dataLayer'}"
                continue
            trigger_ref: str
            if event in existing_triggers:
                tr = existing_triggers[event]
                trigger_ref = str(tr.get("triggerId"))
                steps.append({"action": "reuse_trigger", "event": event, "trigger_id": trigger_ref, "trigger_name": tr.get("name")})
            else:
                trig_name = f"{trigger_prefix}{event}"
                if trig_name in existing_names:
                    trig_name = f"{trig_name} (GA4)"
                steps.append({"action": "create_trigger", "event": event, "body": build_custom_event_trigger(name=trig_name, event_name=event, notes="created by gtm-mcp recipe")})
                trigger_ref = f"<trigger created for {event}>"
            tag_name = f"{tag_prefix}{event}"
            if tag_name in existing_names:
                tag_name = f"{tag_name} (2)"
            steps.append({"action": "create_tag", "event": event, "body": build_ga4_event_tag(name=tag_name, event_name=event, measurement_id=mid, firing_trigger_ids=[trigger_ref], send_ecommerce_data=True, notes="created by gtm-mcp recipe")})
        if add_google_tag_if_missing and not mid.startswith("{{"):
            has_google_tag = any(mid in google_tag_ids(t) and not t.get("paused") for t in snap.tags)
            if not has_google_tag:
                steps.append({"action": "create_tag", "event": None, "body": build_google_tag(name=f"Google tag - {mid}", tag_id=mid, notes="created by gtm-mcp recipe")})
                warnings.append(f"no active Google tag for {mid} was found — one on All Pages is included (skip it if the site loads gtag.js another way, e.g. Shopify's Google & YouTube app)")
        api_calls = sum(1 for s in steps if s["action"] in {"create_trigger", "create_tag"})
        plan = {
            "events_requested": wanted,
            "will_create": {"triggers": sum(1 for s in steps if s["action"] == "create_trigger"), "tags": sum(1 for s in steps if s["action"] == "create_tag")},
            "skipped": skipped,
            "steps": steps,
            "api_requests_needed": api_calls,
            "estimated_seconds": round(max(0, api_calls - config.RATE_LIMIT_BURST) / config.RATE_LIMIT_QPS) if api_calls > config.RATE_LIMIT_BURST else 0,
        }
        warnings.append("GTM only forwards data: the website must push each event into the dataLayer with ecommerce.items / value / currency (developer work). Verify with GTM Preview + GA4 DebugView before publishing.")
        if dry_run:
            if not steps:
                return drop_empty({"dry_run": True, "applied": False, "action": "setup_ga4_ecommerce_events", "container": target.describe(), "plan": plan, "warnings": warnings, "message": "nothing to create"}) | {"dry_run": True, "applied": False}
            return dry_run_result(action="setup_ga4_ecommerce_events", method="POST", path=f"{target.workspace_path}/{{triggers,tags}}", body=None, preview=plan, target=target, warnings=warnings)
        if not steps:
            return drop_empty({"dry_run": False, "applied": False, "action": "setup_ga4_ecommerce_events", "container": target.describe(), "plan": plan, "warnings": warnings, "message": "nothing to create"}) | {"dry_run": False, "applied": False}
        require_scope(deps, config.SCOPE_EDIT_CONTAINERS, what="gtm_setup_ga4_ecommerce_events")
        created_triggers: dict[str, str] = {}
        created: list[dict[str, Any]] = []
        failed: dict[str, str] | None = None
        try:
            for step in steps:
                if step["action"] == "reuse_trigger":
                    created_triggers[step["event"]] = step["trigger_id"]
                elif step["action"] == "create_trigger":
                    trig = await client.post(f"{target.workspace_path}/triggers", step["body"])
                    created_triggers[step["event"]] = str(trig.get("triggerId"))
                    created.append({"kind": "trigger", "event": step["event"]} | compact_trigger(trig))
            for step in steps:
                if step["action"] != "create_tag":
                    continue
                body = dict(step["body"])
                if step["event"] is not None:
                    body["firingTriggerId"] = [created_triggers[step["event"]]]
                tag = await client.post(f"{target.workspace_path}/tags", body)
                created.append({"kind": "tag", "event": step["event"]} | compact_tag(tag))
        except ToolError as exc:
            failed = {"error": str(exc)}
        result = {"created": created, "skipped": skipped, "failed": failed}
        if failed:
            out = applied_result(action="setup_ga4_ecommerce_events", target=target, result=result, warnings=warnings, next_step="fix the error and call again — already-created entities are reused, not duplicated")
            out["applied"] = "partial"
            return out
        return applied_result(action="setup_ga4_ecommerce_events", target=target, result=result, warnings=warnings, next_step="test every event in GTM Preview, then gtm_create_version → gtm_publish_version")

    _ = (gtm_setup_ga4_ecommerce_events,)
