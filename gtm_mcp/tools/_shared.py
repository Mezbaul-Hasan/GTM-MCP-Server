"""Shared plumbing for tool modules: dependency container, annotations, audit decorator,
container/workspace resolution with a per-process cache, scope gating and dry-run helpers."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from mcp.types import ToolAnnotations

from marketing_mcp_common.audit import AuditLog, Timer
from marketing_mcp_common.errors import ToolError
from marketing_mcp_common.logging import get_logger

from .. import config, validation
from ..client import GTMClient
from ..model import compact_container, compact_workspace

log = get_logger("gtm_mcp")

# Risk tiers (PROJECT.md §5): 🟢 read, 🟡 workspace write (reversible until published), 🔴 publish.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


@dataclass
class Deps:
    client: GTMClient
    audit: AuditLog
    scopes_getter: Callable[[], list[str] | None] = lambda: None
    rate_limiter: Any = None
    cache: dict[str, Any] = field(default_factory=dict)

    def scopes(self) -> list[str] | None:
        try:
            return self.scopes_getter()
        except Exception:  # noqa: BLE001 - unknown is fine; the API will decide
            return None


def _summary(result: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if isinstance(result, dict):
        for key in ("container", "count", "truncated", "dry_run", "applied", "requests_used", "version_id", "workspace_id"):
            value = result.get(key)
            if value is None:
                continue
            if isinstance(value, dict):
                value = value.get("public_id") or value.get("container_id")
            meta[key] = value
    return meta


def audited(deps: Deps, tool_name: str) -> Callable[[F], F]:
    """Wrap a tool: log to stderr + append an audit line; keep the original signature."""

    def decorator(fn: F) -> F:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = sig.bind_partial(*args, **kwargs)
            call_args = {k: v for k, v in bound.arguments.items() if v is not None}
            before = deps.client.requests_made
            with Timer() as t:
                try:
                    result = await fn(*args, **kwargs)
                except ToolError as exc:
                    deps.audit.record(tool=tool_name, args=call_args, status="error", duration_ms=t.ms or 0, error=str(exc))
                    log.info("%s -> error (%s)", tool_name, str(exc)[:120])
                    raise
                except Exception as exc:  # unexpected → still audit, then surface a clean message
                    deps.audit.record(tool=tool_name, args=call_args, status="crash", duration_ms=t.ms or 0, error=f"{type(exc).__name__}: {exc}")
                    log.exception("%s crashed", tool_name)
                    raise ToolError(f"{tool_name} failed unexpectedly ({type(exc).__name__}: {exc}) — try again or simplify the request") from exc
            if isinstance(result, dict) and "requests_used" not in result:
                result["requests_used"] = deps.client.requests_made - before
            deps.audit.record(tool=tool_name, args=call_args, status="ok", duration_ms=t.ms, meta=_summary(result))
            log.info("%s -> ok (%.0f ms, %d API requests)", tool_name, t.ms, deps.client.requests_made - before)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


def drop_empty(obj: Any) -> Any:
    """Recursively remove None / "" / [] / {} values to keep outputs compact."""
    if isinstance(obj, dict):
        cleaned = {k: drop_empty(v) for k, v in obj.items()}
        return {k: v for k, v in cleaned.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        return [drop_empty(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- scopes
def require_scope(deps: Deps, scope: str, *, what: str) -> None:
    scopes = deps.scopes()
    if scopes is None or not scopes:
        return  # unknown (env override without GOOGLE_SCOPES) → let the API decide
    if scope not in scopes:
        raise ToolError(
            f"{what} needs the OAuth scope {scope.rsplit('/', 1)[-1]} but this sign-in is {config.profile_for_scopes(scopes)} — "
            "run `gtm-mcp auth --with-writes` once in a terminal (and the Google account needs the matching GTM role on the container)"
        )


# --------------------------------------------------------------------------- resolution
@dataclass
class Target:
    account_id: str
    container_id: str
    container: dict[str, Any]
    workspace_id: str | None = None
    workspace: dict[str, Any] | None = None

    @property
    def container_path(self) -> str:
        return f"accounts/{self.account_id}/containers/{self.container_id}"

    @property
    def workspace_path(self) -> str:
        if not self.workspace_id:
            raise ToolError("workspace_id is required here")
        return f"{self.container_path}/workspaces/{self.workspace_id}"

    def describe(self) -> dict[str, Any]:
        out = compact_container(self.container)
        if self.workspace is not None:
            out["workspace"] = compact_workspace(self.workspace)
        elif self.workspace_id:
            out["workspace"] = {"workspace_id": self.workspace_id}
        return out


async def resolve_container(deps: Deps, container: str) -> Target:
    """Accept ``GTM-XXXXXXX`` / ``G-…`` / ``AW-…`` (one lookup request, cached) or ``accounts/{a}/containers/{c}``."""
    ref = validation.container_ref(container)
    containers: dict[str, dict[str, Any]] = deps.cache.setdefault("containers", {})
    if isinstance(ref, tuple):
        account_id, container_id = ref
        key = f"{account_id}/{container_id}"
        if key not in containers:
            containers[key] = await deps.client.get(f"accounts/{account_id}/containers/{container_id}")
        data = containers[key]
    else:
        if ref not in containers:
            param = {"tagId": ref} if ref.startswith("GTM-") else {"destinationId": ref}
            data = await deps.client.get("accounts/containers:lookup", param)
            if not data.get("containerId"):
                raise ToolError(f"no container found for {ref} — the Google account may lack access to it; try gtm_list_accounts")
            containers[ref] = data
            containers[f"{data.get('accountId')}/{data.get('containerId')}"] = data
        data = containers[ref]
    return Target(account_id=str(data.get("accountId")), container_id=str(data.get("containerId")), container=data)


async def resolve_workspace(deps: Deps, container: str, workspace_id: str | None) -> Target:
    """Resolve the container and pick the workspace (given id, or the Default Workspace — cached)."""
    target = await resolve_container(deps, container)
    workspaces: dict[str, dict[str, Any]] = deps.cache.setdefault("workspaces", {})
    if workspace_id:
        wid = validation.numeric_id(workspace_id, "workspace")
        target.workspace_id = wid
        target.workspace = workspaces.get(f"{target.container_path}/workspaces/{wid}")
        return target
    cache_key = f"{target.container_path}/default"
    if cache_key not in workspaces:
        items = await deps.client.list_all(f"{target.container_path}/workspaces", "workspace")
        if not items:
            raise ToolError("the container has no workspaces — create one with gtm_create_workspace")
        chosen = next((w for w in items if str(w.get("name", "")).lower() == "default workspace"), items[0])
        for w in items:
            workspaces[str(w.get("path"))] = w
        workspaces[cache_key] = chosen
    chosen = workspaces[cache_key]
    target.workspace_id = str(chosen.get("workspaceId"))
    target.workspace = chosen
    return target


def forget_workspaces(deps: Deps, container_path: str) -> None:
    workspaces: dict[str, dict[str, Any]] = deps.cache.setdefault("workspaces", {})
    for key in [k for k in workspaces if k.startswith(container_path)]:
        workspaces.pop(key, None)


@dataclass
class Snapshot:
    target: Target
    tags: list[dict[str, Any]]
    triggers: list[dict[str, Any]]
    variables: list[dict[str, Any]]
    built_ins: list[dict[str, Any]]

    def trigger_name(self, trigger_id: str) -> str:
        if trigger_id in config.BUILT_IN_TRIGGERS:
            return config.BUILT_IN_TRIGGERS[trigger_id]
        for t in self.triggers:
            if str(t.get("triggerId")) == str(trigger_id):
                return str(t.get("name"))
        return f"<missing trigger {trigger_id}>"


async def load_snapshot(deps: Deps, container: str, workspace_id: str | None, *, built_ins: bool = True) -> Snapshot:
    """Fetch tags, triggers, variables (+ built-ins) of one workspace — 3–4 requests (plus paging)."""
    target = await resolve_workspace(deps, container, workspace_id)
    base = target.workspace_path
    tags = await deps.client.list_all(f"{base}/tags", "tag")
    triggers = await deps.client.list_all(f"{base}/triggers", "trigger")
    variables = await deps.client.list_all(f"{base}/variables", "variable")
    bivs = await deps.client.list_all(f"{base}/built_in_variables", "builtInVariable") if built_ins else []
    return Snapshot(target=target, tags=tags, triggers=triggers, variables=variables, built_ins=bivs)


# --------------------------------------------------------------------------- lists / dry-run
def paged(items: list[dict[str, Any]], *, limit: int, offset: int, key: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    from marketing_mcp_common.pagination import enforce_output_budget

    total = len(items)
    window = items[offset : offset + limit]
    result: dict[str, Any] = {
        "count": len(window),
        "total": total,
        "offset": offset,
        key: window,
    }
    if offset + limit < total:
        result["next_offset"] = offset + limit
    if extra:
        result.update(extra)
    return enforce_output_budget(result, key, budget=config.OUTPUT_BUDGET_BYTES)


def dry_run_result(*, action: str, method: str, path: str, body: Any = None, params: dict[str, Any] | None = None, preview: Any = None, warnings: list[str] | None = None, target: Target | None = None) -> dict[str, Any]:
    """Uniform shape for every write tool when ``dry_run=True`` (the default)."""
    out: dict[str, Any] = {
        "dry_run": True,
        "applied": False,
        "action": action,
        "request": {"method": method, "path": path, "params": params or None, "body": body},
        "preview": preview,
        "warnings": warnings or [],
        "next_step": "Review the request above. Call the same tool again with dry_run=false to apply it in the workspace (nothing goes live until gtm_create_version + gtm_publish_version).",
    }
    if target is not None:
        out["container"] = target.describe()
    return drop_empty(out) | {"dry_run": True, "applied": False}


def applied_result(*, action: str, target: Target | None, result: Any, next_step: str, warnings: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"dry_run": False, "applied": True, "action": action, "result": result, "warnings": warnings or [], "next_step": next_step}
    if target is not None:
        out["container"] = target.describe()
    return drop_empty(out) | {"dry_run": False, "applied": True}
