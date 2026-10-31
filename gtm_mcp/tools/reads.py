"""🟢 Read-only tools: accounts → containers → workspaces → entities, versions, environments, raw GET."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from marketing_mcp_common.errors import ToolError
from marketing_mcp_common.pagination import enforce_output_budget

from .. import config, validation
from ..model import (
    compact_account,
    compact_container,
    compact_environment,
    compact_folder,
    compact_tag,
    compact_template,
    compact_trigger,
    compact_variable,
    compact_version,
    compact_version_header,
    compact_workspace,
    count_by_type,
)
from ._shared import READ_ONLY, Deps, audited, drop_empty, paged, resolve_container, resolve_workspace

CONTAINER_DOC = "Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>."
WORKSPACE_DOC = "Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."


def _filtered(items: list[dict[str, Any]], *, name_contains: str | None, type_: str | None = None) -> list[dict[str, Any]]:
    out = items
    if name_contains:
        needle = name_contains.lower()
        out = [x for x in out if needle in str(x.get("name", "")).lower()]
    if type_:
        out = [x for x in out if str(x.get("type", "")) == type_]
    return out


def register(mcp: MCPServer, deps: Deps) -> None:
    client = deps.client

    @mcp.tool(name="gtm_list_accounts", title="List GTM accounts", annotations=READ_ONLY)
    @audited(deps, "gtm_list_accounts")
    async def gtm_list_accounts() -> dict[str, Any]:
        """List every Google Tag Manager account the signed-in Google user can see (1 request)."""
        items = await client.list_all("accounts", "account")
        return paged([compact_account(a) for a in items], limit=config.MAX_LIST_LIMIT, offset=0, key="accounts")

    @mcp.tool(name="gtm_list_containers", title="List GTM containers", annotations=READ_ONLY)
    @audited(deps, "gtm_list_containers")
    async def gtm_list_containers(account_id: str | None = None, name_contains: str | None = None) -> dict[str, Any]:
        """List containers of one account (1 request), or of all accounts when account_id is omitted
        (1 request per account, capped at 10 accounts — quota is 25 requests / 100 s)."""
        if account_id:
            aid = validation.numeric_id(account_id, "account")
            items = await client.list_all(f"accounts/{aid}/containers", "container")
            extra: dict[str, Any] = {"account_id": aid}
        else:
            accounts = await client.list_all("accounts", "account")
            items = []
            scanned = accounts[: config.MAX_ACCOUNTS_SCAN]
            for a in scanned:
                items.extend(await client.list_all(f"accounts/{a.get('accountId')}/containers", "container"))
            extra = {"accounts_scanned": len(scanned)}
            if len(accounts) > len(scanned):
                extra["accounts_skipped"] = len(accounts) - len(scanned)
                extra["hint"] = "pass account_id to list the remaining accounts"
        items = _filtered(items, name_contains=name_contains)
        return paged([compact_container(c) for c in items], limit=config.MAX_LIST_LIMIT, offset=0, key="containers", extra=extra)

    @mcp.tool(name="gtm_get_container", title="Get GTM container", annotations=READ_ONLY)
    @audited(deps, "gtm_get_container")
    async def gtm_get_container(container: str) -> dict[str, Any]:
        """Container details (name, public id, usage context, domains, linked tag ids). Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>."""
        target = await resolve_container(deps, container)
        return drop_empty({"container": compact_container(target.container), "features": target.container.get("features")})

    @mcp.tool(name="gtm_list_workspaces", title="List workspaces", annotations=READ_ONLY)
    @audited(deps, "gtm_list_workspaces")
    async def gtm_list_workspaces(container: str) -> dict[str, Any]:
        """Workspaces of a container (unpublished drafts; free tier allows 3). Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>."""
        target = await resolve_container(deps, container)
        items = await client.list_all(f"{target.container_path}/workspaces", "workspace")
        for w in items:
            deps.cache.setdefault("workspaces", {})[str(w.get("path"))] = w
        return paged([compact_workspace(w) for w in items], limit=config.MAX_LIST_LIMIT, offset=0, key="workspaces", extra={"container": compact_container(target.container)})

    @mcp.tool(name="gtm_get_workspace_status", title="Workspace status (changes & conflicts)", annotations=READ_ONLY)
    @audited(deps, "gtm_get_workspace_status")
    async def gtm_get_workspace_status(container: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Unpublished changes and merge conflicts of a workspace — run before gtm_create_version. Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>. Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."""
        target = await resolve_workspace(deps, container, workspace_id)
        status = await client.get(f"{target.workspace_path}:getStatus")
        changes = []
        for ch in status.get("workspaceChange") or []:
            kind, entity = next(((k, v) for k, v in ch.items() if k != "changeStatus" and isinstance(v, dict)), ("?", {}))
            changes.append({"change": ch.get("changeStatus"), "entity_type": kind, "id": entity.get(f"{kind}Id") or entity.get("type"), "name": entity.get("name")})
        conflicts = []
        for mc in status.get("mergeConflict") or []:
            ent = mc.get("entityInWorkspace") or {}
            kind, entity = next(((k, v) for k, v in ent.items() if k != "changeStatus" and isinstance(v, dict)), ("?", {}))
            conflicts.append({"entity_type": kind, "id": entity.get(f"{kind}Id"), "name": entity.get("name"), "change": ent.get("changeStatus")})
        result = {
            "container": target.describe(),
            "change_count": len(changes),
            "conflict_count": len(conflicts),
            "changes": changes,
            "conflicts": conflicts,
            "ready_to_version": not conflicts,
        }
        return enforce_output_budget(result, "changes", budget=config.OUTPUT_BUDGET_BYTES)

    @mcp.tool(name="gtm_list_tags", title="List tags", annotations=READ_ONLY)
    @audited(deps, "gtm_list_tags")
    async def gtm_list_tags(
        container: str,
        workspace_id: str | None = None,
        name_contains: str | None = None,
        type: str | None = None,
        paused: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """Tags of a workspace with type label, paused flag, trigger ids and key parameters (all pages fetched once;
        filters/offset are applied locally). type examples: gaawe (GA4 event), googtag, html, awct, ua. Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>. Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."""
        lim, off = validation.limit_and_offset(limit, offset, default=config.DEFAULT_LIST_LIMIT, maximum=config.MAX_LIST_LIMIT)
        target = await resolve_workspace(deps, container, workspace_id)
        items = await client.list_all(f"{target.workspace_path}/tags", "tag")
        filtered = _filtered(items, name_contains=name_contains, type_=type)
        if paused is not None:
            filtered = [t for t in filtered if bool(t.get("paused", False)) == paused]
        extra = {"container": target.describe(), "tags_by_type": count_by_type(items), "paused_total": sum(1 for t in items if t.get("paused"))}
        return paged([drop_empty(compact_tag(t)) for t in filtered], limit=lim, offset=off, key="tags", extra=extra)

    @mcp.tool(name="gtm_get_tag", title="Get tag", annotations=READ_ONLY)
    @audited(deps, "gtm_get_tag")
    async def gtm_get_tag(container: str, tag_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Full tag: flattened parameters, triggers, consent, notes, fingerprint and the variables it references. Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>. Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."""
        target = await resolve_workspace(deps, container, workspace_id)
        tag = await client.get(f"{target.workspace_path}/tags/{validation.numeric_id(tag_id, 'tag')}")
        return enforce_output_budget(drop_empty({"container": target.describe(), "tag": compact_tag(tag, full=True)}), "tag", budget=config.OUTPUT_BUDGET_BYTES)

    @mcp.tool(name="gtm_list_triggers", title="List triggers", annotations=READ_ONLY)
    @audited(deps, "gtm_list_triggers")
    async def gtm_list_triggers(container: str, workspace_id: str | None = None, name_contains: str | None = None, type: str | None = None, limit: int | None = None, offset: int | None = None) -> dict[str, Any]:
        """Triggers with a one-line condition summary (e.g. 'Custom Event: {{_event}} equals purchase'). Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>. Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."""
        lim, off = validation.limit_and_offset(limit, offset, default=config.DEFAULT_LIST_LIMIT, maximum=config.MAX_LIST_LIMIT)
        target = await resolve_workspace(deps, container, workspace_id)
        items = await client.list_all(f"{target.workspace_path}/triggers", "trigger")
        filtered = _filtered(items, name_contains=name_contains, type_=type)
        extra = {"container": target.describe(), "built_in_triggers": config.BUILT_IN_TRIGGERS}
        return paged([drop_empty(compact_trigger(t)) for t in filtered], limit=lim, offset=off, key="triggers", extra=extra)

    @mcp.tool(name="gtm_get_trigger", title="Get trigger", annotations=READ_ONLY)
    @audited(deps, "gtm_get_trigger")
    async def gtm_get_trigger(container: str, trigger_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Full trigger with all filter conditions and fingerprint. Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>. Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."""
        target = await resolve_workspace(deps, container, workspace_id)
        tid = validation.numeric_id(trigger_id, "trigger")
        if tid in config.BUILT_IN_TRIGGERS:
            return {"container": target.describe(), "trigger": {"trigger_id": tid, "name": config.BUILT_IN_TRIGGERS[tid], "built_in": True}}
        trig = await client.get(f"{target.workspace_path}/triggers/{tid}")
        return drop_empty({"container": target.describe(), "trigger": compact_trigger(trig, full=True)})

    @mcp.tool(name="gtm_list_variables", title="List variables", annotations=READ_ONLY)
    @audited(deps, "gtm_list_variables")
    async def gtm_list_variables(container: str, workspace_id: str | None = None, name_contains: str | None = None, type: str | None = None, limit: int | None = None, offset: int | None = None) -> dict[str, Any]:
        """User-defined variables with a summary (type codes: v = Data Layer, c = Constant, jsm = Custom JS, u = URL, k = Cookie). Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>. Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."""
        lim, off = validation.limit_and_offset(limit, offset, default=config.DEFAULT_LIST_LIMIT, maximum=config.MAX_LIST_LIMIT)
        target = await resolve_workspace(deps, container, workspace_id)
        items = await client.list_all(f"{target.workspace_path}/variables", "variable")
        filtered = _filtered(items, name_contains=name_contains, type_=type)
        return paged([drop_empty(compact_variable(v)) for v in filtered], limit=lim, offset=off, key="variables", extra={"container": target.describe()})

    @mcp.tool(name="gtm_get_variable", title="Get variable", annotations=READ_ONLY)
    @audited(deps, "gtm_get_variable")
    async def gtm_get_variable(container: str, variable_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Full variable with flattened parameters (e.g. Data Layer name, JS code) and fingerprint. Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>. Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."""
        target = await resolve_workspace(deps, container, workspace_id)
        var = await client.get(f"{target.workspace_path}/variables/{validation.numeric_id(variable_id, 'variable')}")
        return enforce_output_budget(drop_empty({"container": target.describe(), "variable": compact_variable(var, full=True)}), "variable", budget=config.OUTPUT_BUDGET_BYTES)

    @mcp.tool(name="gtm_list_built_in_variables", title="List enabled built-in variables", annotations=READ_ONLY)
    @audited(deps, "gtm_list_built_in_variables")
    async def gtm_list_built_in_variables(container: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Built-in variables currently enabled in the workspace (type + display name, e.g. pageUrl → 'Page URL'). Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>. Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."""
        target = await resolve_workspace(deps, container, workspace_id)
        items = await client.list_all(f"{target.workspace_path}/built_in_variables", "builtInVariable")
        return paged([{"type": b.get("type"), "name": b.get("name")} for b in items], limit=config.MAX_LIST_LIMIT, offset=0, key="built_in_variables", extra={"container": target.describe()})

    @mcp.tool(name="gtm_list_folders", title="List folders", annotations=READ_ONLY)
    @audited(deps, "gtm_list_folders")
    async def gtm_list_folders(container: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Folders of a workspace (tags/triggers/variables reference them via folder_id). Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>. Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."""
        target = await resolve_workspace(deps, container, workspace_id)
        items = await client.list_all(f"{target.workspace_path}/folders", "folder")
        return paged([drop_empty(compact_folder(f)) for f in items], limit=config.MAX_LIST_LIMIT, offset=0, key="folders", extra={"container": target.describe()})

    @mcp.tool(name="gtm_list_templates", title="List custom templates", annotations=READ_ONLY)
    @audited(deps, "gtm_list_templates")
    async def gtm_list_templates(container: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Custom / community-gallery templates installed in the workspace (template code is not returned). Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>. Workspace id (numeric). Omit to use the 'Default Workspace' (looked up once and cached)."""
        target = await resolve_workspace(deps, container, workspace_id)
        items = await client.list_all(f"{target.workspace_path}/templates", "template")
        return paged([drop_empty(compact_template(t)) for t in items], limit=config.MAX_LIST_LIMIT, offset=0, key="templates", extra={"container": target.describe()})

    @mcp.tool(name="gtm_list_versions", title="List container versions", annotations=READ_ONLY)
    @audited(deps, "gtm_list_versions")
    async def gtm_list_versions(container: str, include_deleted: bool = False, limit: int | None = None, offset: int | None = None) -> dict[str, Any]:
        """Version headers (id, name, entity counts), newest first, plus the latest version id. Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>."""
        lim, off = validation.limit_and_offset(limit, offset, default=50, maximum=config.MAX_LIST_LIMIT)
        target = await resolve_container(deps, container)
        items = await client.list_all(f"{target.container_path}/version_headers", "containerVersionHeader", {"includeDeleted": "true"} if include_deleted else None)
        items.sort(key=lambda h: int(h.get("containerVersionId") or 0), reverse=True)
        return paged([drop_empty(compact_version_header(h)) for h in items], limit=lim, offset=off, key="versions", extra={"container": compact_container(target.container)})

    @mcp.tool(name="gtm_get_version", title="Get container version", annotations=READ_ONLY)
    @audited(deps, "gtm_get_version")
    async def gtm_get_version(container: str, version_id: str, include_entities: bool = False) -> dict[str, Any]:
        """One version: counts and tags-by-type; include_entities=true adds compact tag/trigger/variable lists. Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>."""
        target = await resolve_container(deps, container)
        v = await client.get(f"{target.container_path}/versions/{validation.numeric_id(version_id, 'version')}")
        result = drop_empty({"container": compact_container(target.container), "version": compact_version(v, include_entities=include_entities)})
        return enforce_output_budget(result, "version", budget=config.OUTPUT_BUDGET_BYTES)

    @mcp.tool(name="gtm_get_live_version", title="Get live (published) version", annotations=READ_ONLY)
    @audited(deps, "gtm_get_live_version")
    async def gtm_get_live_version(container: str, include_entities: bool = False) -> dict[str, Any]:
        """The version currently published on the website — what visitors actually get. Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>."""
        target = await resolve_container(deps, container)
        v = await client.get(f"{target.container_path}/versions:live")
        result = drop_empty({"container": compact_container(target.container), "live_version": compact_version(v, include_entities=include_entities)})
        return enforce_output_budget(result, "live_version", budget=config.OUTPUT_BUDGET_BYTES)

    @mcp.tool(name="gtm_list_environments", title="List environments", annotations=READ_ONLY)
    @audited(deps, "gtm_list_environments")
    async def gtm_list_environments(container: str) -> dict[str, Any]:
        """Environments (Live, Latest, custom) with the version each one serves. Preview auth codes are never returned. Container as public id (GTM-XXXXXXX, or a linked G-/AW- destination id) or API path accounts/<a>/containers/<c>."""
        target = await resolve_container(deps, container)
        items = await client.list_all(f"{target.container_path}/environments", "environment")
        return paged([drop_empty(compact_environment(e)) for e in items], limit=config.MAX_LIST_LIMIT, offset=0, key="environments", extra={"container": compact_container(target.container)})

    @mcp.tool(name="gtm_raw_get", title="Raw GET (escape hatch)", annotations=READ_ONLY)
    @audited(deps, "gtm_raw_get")
    async def gtm_raw_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """GET any Tag Manager v2 resource path (starts with 'accounts/…', e.g. accounts/1/containers/2/workspaces/3/clients)
        for things no dedicated tool covers. Read-only; output is truncated at 50 KB."""
        clean = validation.raw_path(path)
        data = await client.get(clean, params or None)
        list_key = next((k for k, v in data.items() if isinstance(v, list)), None)
        result: dict[str, Any] = {"path": clean, "data": data}
        if list_key:
            result["count"] = len(data[list_key])
            return enforce_output_budget({"path": clean, "count": len(data[list_key]), "list_key": list_key, list_key: data[list_key], "next_page_token": data.get("nextPageToken")}, list_key, budget=config.OUTPUT_BUDGET_BYTES)
        return enforce_output_budget(result, "data", budget=config.OUTPUT_BUDGET_BYTES)

    @mcp.tool(name="gtm_auth_status", title="Sign-in profile & quota usage", annotations=READ_ONLY)
    @audited(deps, "gtm_auth_status")
    async def gtm_auth_status() -> dict[str, Any]:
        """Which scope profile is signed in (read-only vs read+write+publish), API requests made in this session and
        the client-side rate limiter state. Makes no API call."""
        scopes = deps.scopes()
        limiter = deps.rate_limiter.status() if deps.rate_limiter is not None and hasattr(deps.rate_limiter, "status") else None
        profile = config.profile_for_scopes(scopes)
        return drop_empty(
            {
                "profile": profile,
                "scopes": scopes,
                "write_tools_available": profile in {"read+write", "read+write+publish"} or profile == "unknown",
                "publish_available": profile in {"read+write+publish"} or profile == "unknown",
                "requests_made_this_session": client.requests_made,
                "rate_limiter": limiter,
                "quota": {"per_100_seconds": 25, "per_day": config.DAILY_QUOTA, "note": "per Google Cloud project, shared by everyone using the same OAuth client"},
                "how_to_enable_writes": None if profile.startswith("read+write") else "run `gtm-mcp auth --with-writes` in a terminal",
                "requests_used": 0,
            }
        )

    # keep a reference so linters do not flag unused closures
    _ = (gtm_list_accounts, gtm_get_container, gtm_get_tag, gtm_get_trigger, gtm_get_variable, gtm_raw_get, gtm_auth_status)
    if False:  # pragma: no cover
        raise ToolError("unreachable")
