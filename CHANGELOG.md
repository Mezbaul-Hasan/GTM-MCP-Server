# Changelog — gtm-mcp

## 0.1.0 — 2026-10-31
First release (Phase 1 + Phase 2 in one go, decided by the owner on 2026-10-31). Published 2026-11-01 as its own
repository `Mezbaul-Hasan/GTM-MCP-Server` (initially built inside GA4-MCP-Server under `servers/gtm-mcp`).

- 23 🟢 read tools: accounts, containers (`gtm_find_container` resolves `GTM-`/`G-`/`AW-` ids in one request), workspaces,
  workspace status, tags, triggers, variables, built-ins, folders, templates, versions, live version, environments,
  `gtm_raw_get`, `gtm_auth_status`, plus `gtm_container_audit` and `gtm_check_ga4_ecommerce_coverage`.
- 10 🟡 write tools, all `dry_run=true` by default with an exact request preview: create workspace / tag / GA4 event tag /
  trigger / variable, update tag (diff + fingerprint), enable built-ins, revert entity, create version, and the recipe
  `gtm_setup_ga4_ecommerce_events`.
- 1 🔴 tool: `gtm_publish_version` (needs `dry_run=false` + `confirm=true` + publish scope).
- No delete / user-management tools; scopes `tagmanager.readonly` by default, `auth --with-writes` adds
  `edit.containers`, `edit.containerversions`, `publish`.
- Quota-aware: client-side token bucket (0.25 req/s, burst 20) via `marketing_mcp_common.ratelimit`, per-process caches,
  full pagination, retry/back-off on 429/5xx.
- Depends on `marketing-mcp-common` 0.1.1 (installed from the GA4-MCP-Server repo, tag `common-v0.1.1`).
- CLI `gtm-mcp auth [--with-writes] | status | logout | serve | help`; config in `~/.gtm-mcp`.
- 40 mocked tests (respx), no network.
