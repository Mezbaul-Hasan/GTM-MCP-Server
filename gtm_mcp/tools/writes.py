"""🟡 Workspace write tools (dry_run=True by default, nothing goes live) and the single 🔴 publish tool.

Rules (PROJECT.md §5/§6): every write previews the exact request first; writes stay inside a
workspace until gtm_create_version + gtm_publish_version; no delete / user-management tools.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from marketing_mcp_common.errors import ToolError

from .. import config, validation
from ..model import (
    ECOMMERCE_EVENT_SET,
    FILTER_OPS,
    VARIABLE_TYPE_ALIASES,
    build_constant_variable,
    build_custom_event_trigger,
    build_data_layer_variable,
    build_filter,
    build_ga4_event_tag,
    compact_tag,
    compact_trigger,
    compact_variable,
    compact_version,
    compact_workspace,
    custom_event_names,
    flatten_parameters,
    merge_parameters,
    to_parameters,
)
from ._shared import DESTRUCTIVE, WRITE, Deps, Target, audited, applied_result, drop_empty, dry_run_result, forget_workspaces, require_scope, resolve_container, resolve_workspace

ENTITY_COLLECTIONS = {"tag": "tags", "trigger": "triggers", "variable": "variables", "folder": "folders", "template": "templates"}


def _scope_warning(deps: Deps, scope: str) -> list[str]:
    scopes = deps.scopes()
    if scopes and scope not in scopes:
        return [f"this sign-in is {config.profile_for_scopes(scopes)} — applying will fail until you run `gtm-mcp auth --with-writes`"]
    return []


def _trigger_ids(values: list[str] | None, what: str) -> list[str]:
    return [validation.numeric_id(v, "trigger") for v in (values or [])] if values else []


async def _find_custom_event_trigger(deps: Deps, target: Target, event: str) -> dict[str, Any] | None:
    triggers = await deps.client.list_all(f"{target.workspace_path}/triggers", "trigger")
    for tr in triggers:
        if event in custom_event_names(tr):
            return tr
    return None


def register(mcp: MCPServer, deps: Deps) -> None:
    client = deps.client

    # ------------------------------------------------------------------ workspaces
    @mcp.tool(name="gtm_create_workspace", title="Create workspace (🟡 write)", annotations=WRITE)
    @audited(deps, "gtm_create_workspace")
    async def gtm_create_workspace(container: str, name: str, description: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Create a new empty workspace (a draft; nothing is published). Container: GTM-XXXXXXX or accounts/<a>/containers/<c>.
        dry_run=true (default) only previews the request."""
        target = await resolve_container(deps, container)
        body = drop_empty({"name": validation.entity_name(name), "description": description})
        path = f"{target.container_path}/workspaces"
        if dry_run:
            return dry_run_result(action="create_workspace", method="POST", path=path, body=body, target=target, warnings=_scope_warning(deps, config.SCOPE_EDIT_CONTAINERS) + ["free GTM allows at most 3 workspaces per container"])
        require_scope(deps, config.SCOPE_EDIT_CONTAINERS, what="gtm_create_workspace")
        created = await client.post(path, body)
        forget_workspaces(deps, target.container_path)
        return applied_result(action="create_workspace", target=target, result=compact_workspace(created), next_step=f"pass workspace_id={created.get('workspaceId')} to the write tools")

    # ------------------------------------------------------------------ tags
    @mcp.tool(name="gtm_create_tag", title="Create tag (🟡 write)", annotations=WRITE)
    @audited(deps, "gtm_create_tag")
    async def gtm_create_tag(
        container: str,
        name: str,
        type: str,
        parameters: dict[str, Any],
        firing_trigger_ids: list[str],
        blocking_trigger_ids: list[str] | None = None,
        paused: bool = False,
        notes: str | None = None,
        tag_firing_option: str | None = None,
        workspace_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Create any tag type in the workspace. `type` is the GTM code (gaawe, googtag, html, awct, img, …; see gtm_list_tags
        output of similar tags). `parameters` is a flat dict {key: value} — strings become template parameters, bools boolean,
        ints integer, lists/dicts list/map (or pass raw GTM Parameter objects). Use trigger id 2147479553 for All Pages.
        For GA4 event tags prefer gtm_create_ga4_event_tag. dry_run=true (default) previews only."""
        target = await resolve_workspace(deps, container, workspace_id)
        if not firing_trigger_ids:
            raise ToolError("firing_trigger_ids must contain at least one trigger id (2147479553 = All Pages) — a tag without triggers never fires")
        body = drop_empty(
            {
                "name": validation.entity_name(name),
                "type": validation.entity_name(type, "type"),
                "parameter": to_parameters(parameters),
                "firingTriggerId": _trigger_ids(firing_trigger_ids, "firing"),
                "blockingTriggerId": _trigger_ids(blocking_trigger_ids, "blocking"),
                "paused": paused,
                "notes": notes,
                "tagFiringOption": tag_firing_option,
            }
        )
        path = f"{target.workspace_path}/tags"
        if dry_run:
            return dry_run_result(action="create_tag", method="POST", path=path, body=body, preview=compact_tag(body), target=target, warnings=_scope_warning(deps, config.SCOPE_EDIT_CONTAINERS))
        require_scope(deps, config.SCOPE_EDIT_CONTAINERS, what="gtm_create_tag")
        created = await client.post(path, body)
        return applied_result(action="create_tag", target=target, result=compact_tag(created, full=True), next_step="test in GTM Preview, then gtm_create_version → gtm_publish_version")

    @mcp.tool(name="gtm_create_ga4_event_tag", title="Create GA4 event tag (🟡 write)", annotations=WRITE)
    @audited(deps, "gtm_create_ga4_event_tag")
    async def gtm_create_ga4_event_tag(
        container: str,
        event_name: str,
        measurement_id: str,
        firing_trigger_ids: list[str] | None = None,
        event_parameters: dict[str, str] | None = None,
        send_ecommerce_data: bool | None = None,
        name: str | None = None,
        trigger_name: str | None = None,
        workspace_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Typed helper for a GA4 event tag (type gaawe). measurement_id = G-XXXXXXXX or a variable reference like
        {{GA4 Measurement ID}}. If firing_trigger_ids is omitted, a Custom Event trigger for `event_name` is reused when it
        exists or created (dataLayer.push({event: '<event_name>'})). send_ecommerce_data defaults to true for the GA4
        ecommerce events. dry_run=true (default) previews the request(s)."""
        target = await resolve_workspace(deps, container, workspace_id)
        event = validation.ga4_event_name(event_name)
        mid = validation.measurement_id_or_ref(measurement_id)
        ecommerce = (event in ECOMMERCE_EVENT_SET) if send_ecommerce_data is None else bool(send_ecommerce_data)
        tag_name = validation.entity_name(name or f"GA4 - Event - {event}")
        steps: list[dict[str, Any]] = []
        warnings = _scope_warning(deps, config.SCOPE_EDIT_CONTAINERS)
        trigger_ids = _trigger_ids(firing_trigger_ids, "firing")
        trigger_body: dict[str, Any] | None = None
        if not trigger_ids:
            existing = await _find_custom_event_trigger(deps, target, event)
            if existing is not None:
                trigger_ids = [str(existing.get("triggerId"))]
                warnings.append(f"reusing existing trigger '{existing.get('name')}' (id {existing.get('triggerId')}) for event '{event}'")
            else:
                trigger_body = build_custom_event_trigger(name=validation.entity_name(trigger_name or f"CE - {event}"), event_name=event)
                steps.append({"step": 1, "method": "POST", "path": f"{target.workspace_path}/triggers", "body": trigger_body})
        tag_body = build_ga4_event_tag(name=tag_name, event_name=event, measurement_id=mid, firing_trigger_ids=trigger_ids or ["<id of step 1>"], event_parameters=event_parameters, send_ecommerce_data=ecommerce)
        steps.append({"step": len(steps) + 1, "method": "POST", "path": f"{target.workspace_path}/tags", "body": tag_body})
        if ecommerce:
            warnings.append("the website's dataLayer must push this event with an `ecommerce` object (items, value, currency) — that is developer work, GTM only forwards it")
        if dry_run:
            return dry_run_result(action="create_ga4_event_tag", method="POST", path=f"{target.workspace_path}/tags", body={"steps": steps}, preview=compact_tag(tag_body), target=target, warnings=warnings)
        require_scope(deps, config.SCOPE_EDIT_CONTAINERS, what="gtm_create_ga4_event_tag")
        created: dict[str, Any] = {}
        if trigger_body is not None:
            trig = await client.post(f"{target.workspace_path}/triggers", trigger_body)
            created["trigger"] = compact_trigger(trig)
            tag_body["firingTriggerId"] = [str(trig.get("triggerId"))]
        tag = await client.post(f"{target.workspace_path}/tags", tag_body)
        created["tag"] = compact_tag(tag, full=True)
        return applied_result(action="create_ga4_event_tag", target=target, result=created, warnings=warnings, next_step="test in GTM Preview (push the event in the console), then gtm_create_version → gtm_publish_version")

    @mcp.tool(name="gtm_update_tag", title="Update tag (🟡 write)", annotations=WRITE)
    @audited(deps, "gtm_update_tag")
    async def gtm_update_tag(
        container: str,
        tag_id: str,
        name: str | None = None,
        paused: bool | None = None,
        firing_trigger_ids: list[str] | None = None,
        blocking_trigger_ids: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
        notes: str | None = None,
        workspace_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Change selected fields of an existing tag (pause/unpause, rename, swap triggers, set parameters). `parameters`
        merges by key — pass null for a key to remove it. The tag is read first and updated with its fingerprint (safe
        against concurrent edits). dry_run=true (default) shows a before/after diff only."""
        target = await resolve_workspace(deps, container, workspace_id)
        tid = validation.numeric_id(tag_id, "tag")
        path = f"{target.workspace_path}/tags/{tid}"
        current = await client.get(path)
        updated = {k: v for k, v in current.items() if k not in {"fingerprint", "tagManagerUrl", "path", "accountId", "containerId", "workspaceId"}}
        diff: dict[str, Any] = {}
        if name is not None and name != current.get("name"):
            updated["name"] = validation.entity_name(name)
            diff["name"] = {"before": current.get("name"), "after": updated["name"]}
        if paused is not None and bool(paused) != bool(current.get("paused", False)):
            updated["paused"] = bool(paused)
            diff["paused"] = {"before": bool(current.get("paused", False)), "after": bool(paused)}
        if firing_trigger_ids is not None:
            new_ids = _trigger_ids(firing_trigger_ids, "firing")
            if new_ids != list(current.get("firingTriggerId") or []):
                updated["firingTriggerId"] = new_ids
                diff["firing_trigger_ids"] = {"before": current.get("firingTriggerId") or [], "after": new_ids}
        if blocking_trigger_ids is not None:
            new_ids = _trigger_ids(blocking_trigger_ids, "blocking")
            if new_ids != list(current.get("blockingTriggerId") or []):
                updated["blockingTriggerId"] = new_ids
                diff["blocking_trigger_ids"] = {"before": current.get("blockingTriggerId") or [], "after": new_ids}
        if parameters:
            before = flatten_parameters(current.get("parameter"))
            updated["parameter"] = merge_parameters(list(current.get("parameter") or []), parameters)
            after = flatten_parameters(updated["parameter"])
            changed = {k: {"before": before.get(k), "after": after.get(k)} for k in parameters if before.get(k) != after.get(k)}
            if changed:
                diff["parameters"] = changed
        if notes is not None and notes != current.get("notes"):
            updated["notes"] = notes
            diff["notes"] = {"before": current.get("notes"), "after": notes}
        if not diff:
            return {"dry_run": dry_run, "applied": False, "action": "update_tag", "container": target.describe(), "tag": compact_tag(current), "message": "no changes — the tag already matches the requested values"}
        params = {"fingerprint": current.get("fingerprint")} if current.get("fingerprint") else None
        if dry_run:
            return dry_run_result(action="update_tag", method="PUT", path=path, params=params, body=updated, preview={"tag": compact_tag(current), "diff": diff}, target=target, warnings=_scope_warning(deps, config.SCOPE_EDIT_CONTAINERS))
        require_scope(deps, config.SCOPE_EDIT_CONTAINERS, what="gtm_update_tag")
        saved = await client.put(path, updated, params)
        return applied_result(action="update_tag", target=target, result={"diff": diff, "tag": compact_tag(saved, full=True)}, next_step="gtm_create_version → gtm_publish_version to make it live")

    # ------------------------------------------------------------------ triggers / variables / built-ins
    @mcp.tool(name="gtm_create_trigger", title="Create trigger (🟡 write)", annotations=WRITE)
    @audited(deps, "gtm_create_trigger")
    async def gtm_create_trigger(
        container: str,
        name: str,
        type: str = "customEvent",
        custom_event_name: str | None = None,
        use_regex: bool = False,
        filters: list[dict[str, Any]] | None = None,
        workspace_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Create a trigger. type=customEvent needs custom_event_name (the dataLayer event; use_regex=true for 'a|b').
        Other types (pageview, domReady, windowLoaded, click, linkClick, historyChange, scrollDepth…) take optional
        filters: [{"variable": "{{Page Path}}", "op": "equals|contains|startsWith|endsWith|matchRegex", "value": "/checkout", "negate": false}].
        dry_run=true (default) previews only."""
        target = await resolve_workspace(deps, container, workspace_id)
        trig_name = validation.entity_name(name)
        if type == "customEvent":
            if not custom_event_name:
                raise ToolError("custom_event_name is required for a customEvent trigger")
            body = build_custom_event_trigger(name=trig_name, event_name=validation.event_name(custom_event_name), use_regex=use_regex)
        else:
            body = {"name": trig_name, "type": validation.entity_name(type, "type")}
        conds = []
        for f in filters or []:
            op = str(f.get("op") or "equals")
            if op not in FILTER_OPS:
                raise ToolError(f"filter op '{op}' is not valid — use one of {', '.join(sorted(FILTER_OPS))}")
            if not f.get("variable") or f.get("value") is None:
                raise ToolError("each filter needs 'variable' (e.g. {{Page Path}}), 'op' and 'value'")
            conds.append(build_filter(str(f["variable"]), op, str(f["value"]), negate=bool(f.get("negate", False))))
        if conds:
            body["filter"] = conds
        path = f"{target.workspace_path}/triggers"
        warnings = _scope_warning(deps, config.SCOPE_EDIT_CONTAINERS)
        if type == "customEvent" and not use_regex and custom_event_name:
            existing = await _find_custom_event_trigger(deps, target, custom_event_name)
            if existing is not None:
                warnings.append(f"a trigger for event '{custom_event_name}' already exists: '{existing.get('name')}' (id {existing.get('triggerId')}) — consider reusing it")
        if dry_run:
            return dry_run_result(action="create_trigger", method="POST", path=path, body=body, preview=compact_trigger(body), target=target, warnings=warnings)
        require_scope(deps, config.SCOPE_EDIT_CONTAINERS, what="gtm_create_trigger")
        created = await client.post(path, body)
        return applied_result(action="create_trigger", target=target, result=compact_trigger(created, full=True), warnings=warnings, next_step=f"attach it to tags via firing_trigger_ids=['{created.get('triggerId')}']")

    @mcp.tool(name="gtm_create_variable", title="Create variable (🟡 write)", annotations=WRITE)
    @audited(deps, "gtm_create_variable")
    async def gtm_create_variable(
        container: str,
        name: str,
        type: str = "dataLayer",
        data_layer_name: str | None = None,
        value: str | None = None,
        default_value: str | None = None,
        parameters: dict[str, Any] | None = None,
        workspace_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Create a user-defined variable. type=dataLayer (code v) needs data_layer_name (e.g. ecommerce.items);
        type=constant (code c) needs value; any other GTM type code (jsm, u, k, …) takes raw `parameters`.
        dry_run=true (default) previews only."""
        target = await resolve_workspace(deps, container, workspace_id)
        var_name = validation.entity_name(name)
        code = VARIABLE_TYPE_ALIASES.get(type.strip().lower(), type.strip())
        if code == "v":
            if not data_layer_name:
                raise ToolError("data_layer_name is required for a Data Layer variable (e.g. ecommerce.transaction_id)")
            body = build_data_layer_variable(name=var_name, data_layer_name=data_layer_name.strip(), default_value=default_value)
        elif code == "c":
            if value is None:
                raise ToolError("value is required for a constant variable")
            body = build_constant_variable(name=var_name, value=value)
        else:
            if not parameters:
                raise ToolError(f"parameters are required for variable type '{code}' (e.g. jsm → {{'javascript': 'function(){{…}}'}})")
            body = {"name": var_name, "type": validation.entity_name(code, "type"), "parameter": to_parameters(parameters)}
        path = f"{target.workspace_path}/variables"
        if dry_run:
            return dry_run_result(action="create_variable", method="POST", path=path, body=body, preview=compact_variable(body), target=target, warnings=_scope_warning(deps, config.SCOPE_EDIT_CONTAINERS))
        require_scope(deps, config.SCOPE_EDIT_CONTAINERS, what="gtm_create_variable")
        created = await client.post(path, body)
        return applied_result(action="create_variable", target=target, result=compact_variable(created, full=True), next_step=f"reference it in tags as {{{{{created.get('name')}}}}}")

    @mcp.tool(name="gtm_enable_built_in_variables", title="Enable built-in variables (🟡 write)", annotations=WRITE)
    @audited(deps, "gtm_enable_built_in_variables")
    async def gtm_enable_built_in_variables(container: str, types: list[str], workspace_id: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Enable built-in variables by API type (pageUrl, pagePath, pageHostname, referrer, event, clickElement, clickClasses,
        clickId, clickUrl, clickText, formId, scrollDepthThreshold, …). Already-enabled types are harmless. dry_run=true previews."""
        target = await resolve_workspace(deps, container, workspace_id)
        kinds = validation.built_in_types(types)
        path = f"{target.workspace_path}/built_in_variables"
        if dry_run:
            return dry_run_result(action="enable_built_in_variables", method="POST", path=path, params={"type": kinds}, target=target, warnings=_scope_warning(deps, config.SCOPE_EDIT_CONTAINERS))
        require_scope(deps, config.SCOPE_EDIT_CONTAINERS, what="gtm_enable_built_in_variables")
        data = await client.post(path, None, {"type": kinds})
        enabled = [{"type": b.get("type"), "name": b.get("name")} for b in data.get("builtInVariable") or []]
        return applied_result(action="enable_built_in_variables", target=target, result={"enabled": enabled}, next_step="reference them as {{Page URL}}, {{Click Element}}, … in tags and triggers")

    @mcp.tool(name="gtm_revert_entity", title="Revert workspace changes to an entity (🟡 undo)", annotations=WRITE)
    @audited(deps, "gtm_revert_entity")
    async def gtm_revert_entity(container: str, entity_type: str, entity_id: str, workspace_id: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Undo this workspace's changes to one tag / trigger / variable / folder: restores the live version's copy, or removes
        the entity from the workspace if it was created here (the live container is never touched). dry_run=true previews."""
        target = await resolve_workspace(deps, container, workspace_id)
        kind = entity_type.strip().lower()
        if kind not in ENTITY_COLLECTIONS:
            raise ToolError(f"entity_type must be one of {', '.join(ENTITY_COLLECTIONS)}")
        eid = validation.numeric_id(entity_id, kind)
        path = f"{target.workspace_path}/{ENTITY_COLLECTIONS[kind]}/{eid}"
        current = await client.get(path)
        params = {"fingerprint": current.get("fingerprint")} if current.get("fingerprint") else None
        warnings = _scope_warning(deps, config.SCOPE_EDIT_CONTAINERS) + ["if this entity does not exist in the live version yet, revert removes it from the workspace"]
        if dry_run:
            return dry_run_result(action="revert_entity", method="POST", path=f"{path}:revert", params=params, preview={"entity_type": kind, "id": eid, "name": current.get("name"), "type": current.get("type")}, target=target, warnings=warnings)
        require_scope(deps, config.SCOPE_EDIT_CONTAINERS, what="gtm_revert_entity")
        data = await client.post(f"{path}:revert", None, params)
        restored = data.get(kind)
        return applied_result(action="revert_entity", target=target, result={"entity_type": kind, "id": eid, "state": "restored_to_live_version" if restored else "removed_from_workspace", "entity": restored}, warnings=warnings, next_step="gtm_get_workspace_status to confirm")

    # ------------------------------------------------------------------ versions
    @mcp.tool(name="gtm_create_version", title="Create version from workspace (🟡 write)", annotations=WRITE)
    @audited(deps, "gtm_create_version")
    async def gtm_create_version(container: str, name: str, notes: str | None = None, workspace_id: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Snapshot the workspace into a container version (NOT published yet). GTM deletes the workspace afterwards and
        creates a fresh default one. Merge conflicts block it — dry_run=true (default) shows the pending changes/conflicts."""
        target = await resolve_workspace(deps, container, workspace_id)
        status = await client.get(f"{target.workspace_path}:getStatus")
        changes = status.get("workspaceChange") or []
        conflicts = status.get("mergeConflict") or []
        body = drop_empty({"name": validation.entity_name(name), "notes": notes})
        path = f"{target.workspace_path}:create_version"
        warnings = _scope_warning(deps, config.SCOPE_EDIT_VERSIONS) + ["the workspace is consumed (deleted) by this call; GTM creates a new default workspace", "this does not publish — call gtm_publish_version next"]
        if conflicts:
            warnings.append(f"{len(conflicts)} merge conflict(s) — resolve in the GTM UI first")
        if not changes:
            warnings.append("the workspace has no unpublished changes — the new version would equal the latest one")
        preview = {"pending_changes": len(changes), "merge_conflicts": len(conflicts)}
        if dry_run:
            return dry_run_result(action="create_version", method="POST", path=path, body=body, preview=preview, target=target, warnings=warnings)
        if conflicts:
            raise ToolError(f"{len(conflicts)} merge conflict(s) in the workspace — resolve them in the GTM UI, then retry")
        require_scope(deps, config.SCOPE_EDIT_VERSIONS, what="gtm_create_version")
        data = await client.post(path, body)
        forget_workspaces(deps, target.container_path)
        version = data.get("containerVersion") or {}
        if data.get("compilerError"):
            raise ToolError("GTM reported a compiler error while creating the version — open the workspace in the GTM UI to see the error")
        return applied_result(
            action="create_version",
            target=target,
            result={"version": compact_version(version), "version_id": version.get("containerVersionId"), "new_workspace_path": data.get("newWorkspacePath"), "sync_status": data.get("syncStatus")},
            warnings=warnings,
            next_step=f"gtm_publish_version(container, version_id='{version.get('containerVersionId')}', confirm=true, dry_run=false) when ready to go live",
        )

    @mcp.tool(name="gtm_publish_version", title="Publish version (🔴 goes live)", annotations=DESTRUCTIVE)
    @audited(deps, "gtm_publish_version")
    async def gtm_publish_version(container: str, version_id: str, confirm: bool = False, dry_run: bool = True) -> dict[str, Any]:
        """🔴 Make a container version LIVE for every visitor of the website. Requires `gtm-mcp auth --with-writes`, the
        Publish role in GTM, dry_run=false AND confirm=true. dry_run (default) shows the version vs the current live
        version. Rollback = publish the previous version id."""
        target = await resolve_container(deps, container)
        vid = validation.numeric_id(version_id, "version")
        version = await client.get(f"{target.container_path}/versions/{vid}")
        live = await client.get(f"{target.container_path}/versions:live")
        preview = {"to_publish": compact_version(version), "currently_live": compact_version(live)}
        warnings = _scope_warning(deps, config.SCOPE_PUBLISH)
        if str(live.get("containerVersionId")) == vid:
            warnings.append("this version is already live")
        if version.get("deleted"):
            warnings.append("this version is marked deleted — GTM may refuse to publish it")
        path = f"{target.container_path}/versions/{vid}:publish"
        if dry_run:
            out = dry_run_result(action="publish_version", method="POST", path=path, preview=preview, target=target, warnings=warnings)
            out["next_step"] = "This goes LIVE for all website visitors. Only when the owner has approved: call again with dry_run=false and confirm=true."
            return out
        if not confirm:
            raise ToolError("publishing needs confirm=true (and the owner's explicit approval) — nothing was published")
        require_scope(deps, config.SCOPE_PUBLISH, what="gtm_publish_version")
        data = await client.post(path)
        published = data.get("containerVersion") or version
        if data.get("compilerError"):
            raise ToolError("GTM reported a compiler error — the version was NOT published; open it in the GTM UI to see the error")
        return applied_result(
            action="publish_version",
            target=target,
            result={"version_id": published.get("containerVersionId"), "live_version": compact_version(published), "previous_live_version_id": live.get("containerVersionId")},
            warnings=warnings,
            next_step=f"verify on the website (GTM Preview / GA4 DebugView). Rollback: gtm_publish_version(version_id='{live.get('containerVersionId')}')",
        )

    _ = (gtm_create_workspace, gtm_create_tag, gtm_create_ga4_event_tag, gtm_update_tag, gtm_create_trigger, gtm_create_variable, gtm_enable_built_in_variables, gtm_revert_entity, gtm_create_version, gtm_publish_version)
