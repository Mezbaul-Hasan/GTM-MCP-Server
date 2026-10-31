"""🟢 Helper tools: resolve a container by public id, one-shot container audit, GA4 ecommerce coverage."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from marketing_mcp_common.errors import ToolError
from marketing_mcp_common.pagination import enforce_output_budget

from .. import config
from ..model import (
    ECOMMERCE_EVENT_SET,
    GA4_ECOMMERCE_EVENTS,
    GA4_ECOMMERCE_OPTIONAL_EVENTS,
    INTERNAL_VARIABLE_NAMES,
    compact_container,
    count_by_type,
    custom_event_names,
    flatten_parameters,
    ga4_event_name_of,
    google_tag_ids,
    has_placeholder,
    html_vendor_snippets,
    is_snake_case,
    tag_type_label,
    variable_references,
)
from ._shared import READ_ONLY, Deps, Snapshot, audited, drop_empty, load_snapshot, resolve_container

MAX_ITEMS_PER_FINDING = 40


def _names(tags: list[dict[str, Any]], key: str = "name") -> list[str]:
    return [str(t.get(key)) for t in tags[:MAX_ITEMS_PER_FINDING]]


def _finding(code: str, severity: str, title: str, detail: str, items: list[Any] | None = None, *, fix: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "severity": severity, "title": title, "detail": detail}
    if items:
        out["items"] = items[:MAX_ITEMS_PER_FINDING]
        if len(items) > MAX_ITEMS_PER_FINDING:
            out["items_truncated"] = len(items) - MAX_ITEMS_PER_FINDING
    if fix:
        out["fix"] = fix
    return out


def audit_snapshot(snap: Snapshot) -> dict[str, Any]:
    """Pure analysis of a workspace snapshot → findings (unit-tested without network)."""
    tags, triggers, variables, bivs = snap.tags, snap.triggers, snap.variables, snap.built_ins
    active = [t for t in tags if not t.get("paused")]
    paused = [t for t in tags if t.get("paused")]
    trigger_ids = {str(t.get("triggerId")) for t in triggers}
    findings: list[dict[str, Any]] = []

    # 1. Universal Analytics tags (dead product)
    ua = [t for t in tags if t.get("type") == "ua"]
    if ua:
        ua_active = [t for t in ua if not t.get("paused")]
        findings.append(
            _finding(
                "legacy_ua_tags",
                "high" if ua_active else "info",
                "Universal Analytics tags still in the container",
                f"{len(ua)} UA tag(s), {len(ua_active)} active. UA stopped processing data in 2023/2024 — active ones waste page time and can confuse audits.",
                [{"tag_id": t.get("tagId"), "name": t.get("name"), "paused": bool(t.get("paused"))} for t in ua],
                fix="pause/remove them in GTM (gtm_update_tag paused=true) and publish",
            )
        )

    # 2. Duplicate Google tags (double page_view risk)
    by_measurement: dict[str, list[dict[str, Any]]] = {}
    for t in active:
        for mid in google_tag_ids(t):
            by_measurement.setdefault(mid, []).append(t)
    dups = {mid: ts for mid, ts in by_measurement.items() if len(ts) > 1}
    if dups:
        findings.append(
            _finding(
                "duplicate_google_tags",
                "high",
                "Same GA4 measurement id configured by more than one active tag",
                "Each active Google tag / GA4 configuration tag with the same id sends its own page_view → inflated sessions/pageviews.",
                [{"measurement_id": mid, "tags": _names(ts)} for mid, ts in dups.items()],
                fix="keep one Google tag per measurement id on All Pages; pause the rest",
            )
        )

    # 3. Dangling trigger references
    dangling = []
    for t in tags:
        missing = [tid for tid in (t.get("firingTriggerId") or []) + (t.get("blockingTriggerId") or []) if str(tid) not in trigger_ids and str(tid) not in config.BUILT_IN_TRIGGERS]
        if missing:
            dangling.append({"tag_id": t.get("tagId"), "name": t.get("name"), "missing_trigger_ids": missing})
    if dangling:
        findings.append(_finding("dangling_trigger_references", "high", "Tags reference triggers that do not exist", "These tags can never fire (or never be blocked) as intended.", dangling, fix="re-attach a valid trigger with gtm_update_tag"))

    # 4. Active tags with no firing trigger (and not used as setup/teardown)
    setup_names = {s.get("tagName") for t in tags for s in (t.get("setupTag") or []) + (t.get("teardownTag") or []) if isinstance(s, dict)}
    orphan = [t for t in active if not t.get("firingTriggerId") and t.get("name") not in setup_names]
    if orphan:
        findings.append(_finding("tags_without_firing_trigger", "medium", "Active tags without any firing trigger", "They are dead weight unless fired as setup/teardown tags.", _names(orphan), fix="attach a trigger or pause the tag"))

    # 5. Variable references
    user_var_names = {str(v.get("name")) for v in variables}
    built_in_names = {str(b.get("name")) for b in bivs}
    known = user_var_names | built_in_names | INTERNAL_VARIABLE_NAMES
    referenced: set[str] = set()
    undefined: list[dict[str, Any]] = []
    for kind, entities in (("tag", tags), ("trigger", triggers), ("variable", variables)):
        for e in entities:
            refs = variable_references(e)
            referenced.update(refs)
            missing_refs = sorted(r for r in refs if r not in known and not r.startswith("_"))
            if missing_refs:
                undefined.append({"entity": kind, "name": e.get("name"), "undefined": missing_refs})
    if undefined:
        findings.append(
            _finding(
                "undefined_variable_references",
                "high",
                "{{Variable}} references that do not resolve",
                "The variable is neither user-defined nor an enabled built-in → the value is undefined at runtime (tags send empty data or triggers never match).",
                undefined,
                fix="create the variable (gtm_create_variable) or enable the built-in (gtm_enable_built_in_variables)",
            )
        )
    unused_vars = sorted(n for n in user_var_names if n not in referenced)
    if unused_vars:
        findings.append(_finding("unused_variables", "low", "User-defined variables not referenced anywhere", f"{len(unused_vars)} variable(s) are unused (clutter, not a bug).", unused_vars))

    # 6. Unused triggers
    used_triggers = {str(tid) for t in tags for tid in (t.get("firingTriggerId") or []) + (t.get("blockingTriggerId") or [])}
    unused_triggers = [t for t in triggers if str(t.get("triggerId")) not in used_triggers]
    if unused_triggers:
        findings.append(_finding("unused_triggers", "low", "Triggers not attached to any tag", f"{len(unused_triggers)} trigger(s) are unused (clutter, not a bug).", _names(unused_triggers)))

    # 7. Placeholder-looking values
    placeholders = [{"tag_id": t.get("tagId"), "name": t.get("name"), "values": hits} for t in active if (hits := has_placeholder(t))]
    if placeholders:
        findings.append(_finding("placeholder_values", "high", "Active tags with placeholder-looking values", "Values like XXXX, YOUR_ID, G-XXXXXXX or 1234567890 mean the tag was never configured.", placeholders, fix="set the real ids with gtm_update_tag"))

    # 8. GA4 event naming / ecommerce flags
    bad_names = []
    no_ecom_flag = []
    for t in tags:
        ev = ga4_event_name_of(t)
        if not ev or ev.startswith("{{"):
            continue
        if not is_snake_case(ev):
            bad_names.append({"tag_id": t.get("tagId"), "name": t.get("name"), "event_name": ev, "paused": bool(t.get("paused"))})
        if ev in ECOMMERCE_EVENT_SET and not t.get("paused"):
            flat = flatten_parameters(t.get("parameter"))
            if flat.get("sendEcommerceData") is not True:
                no_ecom_flag.append({"tag_id": t.get("tagId"), "name": t.get("name"), "event_name": ev})
    if bad_names:
        findings.append(_finding("non_snake_case_ga4_events", "medium", "GA4 event tags with non-standard event names", "GA4 ecommerce/recommended reports only recognise snake_case names (add_to_cart, not AddToCart).", bad_names, fix="rename the event in the tag (gtm_update_tag parameters.eventName)"))
    if no_ecom_flag:
        findings.append(_finding("ga4_ecommerce_tags_without_ecommerce_data", "medium", "GA4 ecommerce event tags that do not send ecommerce data", "'Send Ecommerce data' is off, so items/value never reach GA4 for these events.", no_ecom_flag, fix="set parameters.sendEcommerceData=true and getEcommerceDataFrom=dataLayer"))

    # 9. Custom HTML
    html_tags = [t for t in tags if t.get("type") == "html"]
    if html_tags:
        vendor = [{"tag_id": t.get("tagId"), "name": t.get("name"), "snippets": sn, "paused": bool(t.get("paused"))} for t in html_tags if (sn := html_vendor_snippets(t))]
        findings.append(
            _finding(
                "custom_html_tags",
                "medium" if any(not v["paused"] for v in vendor) else "info",
                "Custom HTML tags",
                f"{len(html_tags)} Custom HTML tag(s) ({sum(1 for t in html_tags if not t.get('paused'))} active). {len(vendor)} contain vendor pixel code (gtag/fbq/ttq/pintrk/…) that native templates handle better and that can double-fire.",
                vendor or _names(html_tags),
            )
        )

    # 10. Paused tags (informational)
    if paused:
        findings.append(_finding("paused_tags", "info", "Paused tags", f"{len(paused)} of {len(tags)} tags are paused — review whether they should be deleted or re-enabled.", _names(paused)))

    severity_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: severity_rank.get(f["severity"], 9))
    return {
        "counts": {
            "tags": len(tags),
            "tags_active": len(active),
            "tags_paused": len(paused),
            "triggers": len(triggers),
            "variables": len(variables),
            "built_in_variables": len(bivs),
        },
        "tags_by_type": {k: {"count": v, "label": tag_type_label(k)} for k, v in count_by_type(tags).items()},
        "google_tag_measurement_ids": sorted(by_measurement),
        "findings": findings,
        "summary": {"high": sum(1 for f in findings if f["severity"] == "high"), "medium": sum(1 for f in findings if f["severity"] == "medium"), "low": sum(1 for f in findings if f["severity"] == "low"), "info": sum(1 for f in findings if f["severity"] == "info")},
    }


def coverage_from_snapshot(snap: Snapshot, *, include_optional: bool = False) -> dict[str, Any]:
    events = list(GA4_ECOMMERCE_EVENTS) + (list(GA4_ECOMMERCE_OPTIONAL_EVENTS) if include_optional else [])
    covered: dict[str, list[dict[str, Any]]] = {e: [] for e in events}
    dynamic: list[dict[str, Any]] = []
    other_ga4_events: dict[str, int] = {}
    for t in snap.tags:
        ev = ga4_event_name_of(t)
        if not ev:
            continue
        if ev.startswith("{{"):
            dynamic.append({"tag_id": t.get("tagId"), "name": t.get("name"), "event_name": ev, "paused": bool(t.get("paused"))})
            continue
        flat = flatten_parameters(t.get("parameter"))
        entry = {
            "tag_id": t.get("tagId"),
            "name": t.get("name"),
            "paused": bool(t.get("paused")),
            "send_ecommerce_data": flat.get("sendEcommerceData") is True,
            "measurement_id": flat.get("measurementIdOverride"),
            "triggers": [snap.trigger_name(str(x)) for x in t.get("firingTriggerId") or []],
        }
        if ev in covered:
            covered[ev].append(entry)
        else:
            other_ga4_events[ev] = other_ga4_events.get(ev, 0) + 1
    live_ok = [e for e in events if any(not x["paused"] and x["send_ecommerce_data"] for x in covered[e])]
    paused_only = [e for e in events if covered[e] and all(x["paused"] for x in covered[e])]
    missing = [e for e in events if not covered[e]]
    no_flag = [e for e in events if covered[e] and any(not x["paused"] for x in covered[e]) and e not in live_ok]
    ce_triggers = {}
    for tr in snap.triggers:
        for name in custom_event_names(tr):
            ce_triggers.setdefault(name, []).append({"trigger_id": tr.get("triggerId"), "name": tr.get("name")})
    google_tags = [{"tag_id": t.get("tagId"), "name": t.get("name"), "ids": google_tag_ids(t), "paused": bool(t.get("paused"))} for t in snap.tags if google_tag_ids(t)]
    return {
        "events_checked": events,
        "score": f"{len(live_ok)}/{len(events)} events have an active GA4 event tag with ecommerce data",
        "covered_live": live_ok,
        "missing": missing,
        "paused_only": paused_only,
        "active_but_no_ecommerce_data": no_flag,
        "details": {e: covered[e] for e in events if covered[e]},
        "existing_custom_event_triggers_for_ecommerce_events": {e: ce_triggers[e] for e in events if e in ce_triggers},
        "dynamic_event_tags": dynamic,
        "other_ga4_event_names": other_ga4_events,
        "google_tags": google_tags,
        "caveats": [
            "Static analysis of the GTM workspace only — it cannot see whether the website's dataLayer actually pushes these events with ecommerce.items (verify in GTM Preview / GA4 DebugView).",
            "Ecommerce events sent outside GTM (e.g. Shopify's Google & YouTube app, hard-coded gtag) are not visible here.",
        ],
    }


def register(mcp: MCPServer, deps: Deps) -> None:
    client = deps.client

    @mcp.tool(name="gtm_find_container", title="Find container by GTM-/G-/AW- id or name", annotations=READ_ONLY)
    @audited(deps, "gtm_find_container")
    async def gtm_find_container(public_id: str | None = None, name_contains: str | None = None) -> dict[str, Any]:
        """Resolve a public id (GTM-XXXXXXX from the website's source, or a linked G-/AW- destination id) to the account,
        container id and default workspace — 1–2 requests. Or search container names across all accounts (1 request per account)."""
        if public_id:
            target = await resolve_container(deps, public_id)
            workspaces = await client.list_all(f"{target.container_path}/workspaces", "workspace")
            for w in workspaces:
                deps.cache.setdefault("workspaces", {})[str(w.get("path"))] = w
            default = next((w for w in workspaces if str(w.get("name", "")).lower() == "default workspace"), workspaces[0] if workspaces else None)
            if default is not None:
                deps.cache["workspaces"][f"{target.container_path}/default"] = default
            return drop_empty(
                {
                    "container": compact_container(target.container),
                    "workspaces": [{"workspace_id": w.get("workspaceId"), "name": w.get("name")} for w in workspaces],
                    "default_workspace_id": default.get("workspaceId") if default else None,
                    "hint": "pass the public id as `container` to every other tool; workspace_id can be omitted for the default workspace",
                }
            )
        if not name_contains:
            raise ToolError("pass public_id (GTM-XXXXXXX) or name_contains")
        needle = name_contains.lower()
        accounts = await client.list_all("accounts", "account")
        matches: list[dict[str, Any]] = []
        for a in accounts[: config.MAX_ACCOUNTS_SCAN]:
            for c in await client.list_all(f"accounts/{a.get('accountId')}/containers", "container"):
                if needle in str(c.get("name", "")).lower() or needle in str(c.get("publicId", "")).lower():
                    matches.append(compact_container(c) | {"account_name": a.get("name")})
        return {"count": len(matches), "containers": matches, "accounts_scanned": min(len(accounts), config.MAX_ACCOUNTS_SCAN)}

    @mcp.tool(name="gtm_container_audit", title="Audit a container workspace", annotations=READ_ONLY)
    @audited(deps, "gtm_container_audit")
    async def gtm_container_audit(container: str, workspace_id: str | None = None, include_workspace_status: bool = True) -> dict[str, Any]:
        """One-shot health check of a workspace (4–6 requests total — prefer this over many single list calls):
        UA leftovers, duplicate Google tags, dangling/unused triggers, undefined {{variables}}, placeholder values,
        GA4 event naming, Custom HTML pixels, paused tags, unpublished changes. Container: GTM-XXXXXXX or accounts/<a>/containers/<c>."""
        snap = await load_snapshot(deps, container, workspace_id)
        report = audit_snapshot(snap)
        result: dict[str, Any] = {"container": snap.target.describe()}
        if include_workspace_status:
            status = await client.get(f"{snap.target.workspace_path}:getStatus")
            changes = status.get("workspaceChange") or []
            conflicts = status.get("mergeConflict") or []
            result["workspace_status"] = {"unpublished_changes": len(changes), "merge_conflicts": len(conflicts)}
            if conflicts:
                report["findings"].insert(0, _finding("workspace_merge_conflicts", "high", "Workspace has merge conflicts", f"{len(conflicts)} conflict(s) block versioning.", fix="resolve in the GTM UI or sync the workspace"))
            elif changes:
                report["findings"].append(_finding("unpublished_changes", "info", "Workspace has unpublished changes", f"{len(changes)} change(s) are not live yet.", fix="gtm_get_workspace_status → gtm_create_version → gtm_publish_version"))
        result.update(report)
        result["next_steps"] = [
            "Use gtm_check_ga4_ecommerce_coverage for the ecommerce event matrix.",
            "Fix items with gtm_update_tag / gtm_create_* (dry_run first), then gtm_create_version and gtm_publish_version.",
        ]
        return enforce_output_budget(drop_empty(result), "findings", budget=config.OUTPUT_BUDGET_BYTES)

    @mcp.tool(name="gtm_check_ga4_ecommerce_coverage", title="GA4 ecommerce event coverage", annotations=READ_ONLY)
    @audited(deps, "gtm_check_ga4_ecommerce_coverage")
    async def gtm_check_ga4_ecommerce_coverage(container: str, workspace_id: str | None = None, include_optional_events: bool = False) -> dict[str, Any]:
        """Which of the 11 GA4 ecommerce events (view_item_list … purchase, refund) have an active GA4 event tag with
        'send ecommerce data', which are missing or paused, and which custom-event triggers already exist (3 requests).
        include_optional_events adds view_promotion, select_promotion, add_to_wishlist."""
        snap = await load_snapshot(deps, container, workspace_id, built_ins=False)
        result = {"container": snap.target.describe()} | coverage_from_snapshot(snap, include_optional=include_optional_events)
        result["next_step"] = "gtm_setup_ga4_ecommerce_events (dry_run=true) proposes the missing triggers + tags." if result["missing"] else "coverage complete — verify the dataLayer in GTM Preview."
        return enforce_output_budget(drop_empty(result), "details", budget=config.OUTPUT_BUDGET_BYTES)

    _ = (gtm_find_container, gtm_container_audit, gtm_check_ga4_ecommerce_coverage)
