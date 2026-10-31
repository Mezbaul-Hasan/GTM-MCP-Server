# GTM MCP Server (`gtm-mcp`)

[![CI](https://github.com/Mezbaul-Hasan/GTM-MCP-Server/actions/workflows/ci.yml/badge.svg)](https://github.com/Mezbaul-Hasan/GTM-MCP-Server/actions/workflows/ci.yml)

[MCP](https://modelcontextprotocol.io) server for **Google Tag Manager**. Runs on **your own computer**, talks to the
[Tag Manager API v2](https://developers.google.com/tag-platform/tag-manager/api/v2) with **your own Google account**.
34 tools: 23 🟢 read/audit tools, 10 🟡 workspace-write tools (every one previews with `dry_run=true` first) and exactly
one 🔴 tool, `gtm_publish_version`. No delete and no user-management tools exist.

Sister project: [GA4-MCP-Server](https://github.com/Mezbaul-Hasan/GA4-MCP-Server) (Google Analytics 4). Both servers share
the small Python library `marketing-mcp-common` (Google OAuth desktop flow, httpx client with retry + rate limiter, error
mapping, redaction, audit log), which lives in that repo under `common/` and is installed automatically from a pinned tag
(see `pyproject.toml`). You can use the **same Google Cloud OAuth client** for both servers. Programme-wide rules and
status: [PROJECT.md](https://github.com/Mezbaul-Hasan/GA4-MCP-Server/blob/main/PROJECT.md).

## 1. Setup (one time)

### 1a. Owner: Google Cloud project
1. Open the Cloud project that holds your OAuth client (the ga4-mcp one works) → **APIs & Services → Library** → enable **Tag Manager API**.
2. **Google Auth Platform → Data Access (Scopes)** → add `https://www.googleapis.com/auth/tagmanager.readonly`.
   Only if people will use the write tools, also add `tagmanager.edit.containers`, `tagmanager.edit.containerversions`, `tagmanager.publish`.
3. The existing **Desktop app** OAuth client id/secret is reused — share it with the team through a password manager, never through chat.

### 1b. Everyone: install `uv`, sign in
```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```
```bash
# read-only profile (default — enough for audits, listing, coverage checks)
uvx --from "git+https://github.com/Mezbaul-Hasan/GTM-MCP-Server" gtm-mcp auth
# read + write + publish profile (only for people who will change containers)
uvx --from "git+https://github.com/Mezbaul-Hasan/GTM-MCP-Server" gtm-mcp auth --with-writes
```
The browser opens → sign in → the refresh token is saved to `~/.gtm-mcp/config.json` (readable only by you).
`gtm-mcp status` shows the profile (`read-only` / `read+write+publish`); `gtm-mcp logout` revokes and deletes it.

Your Google account also needs a GTM role on the container (**Read** for 🟢, **Edit** for workspace writes, **Approve/Publish** for versions/publish) — the OAuth scope alone is not enough.

### 1c. Add to Claude Desktop / Cursor
```json
{
  "mcpServers": {
    "gtm": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Mezbaul-Hasan/GTM-MCP-Server", "gtm-mcp"]
    }
  }
}
```
First prompt to try: *"Find container GTM-XXXXXXX and run a container audit."*

## 2. Tools

| Tool | Tier | What it does |
|---|---|---|
| `gtm_list_accounts` | 🟢 | accounts visible to the signed-in user |
| `gtm_list_containers` | 🟢 | containers of one account, or of all accounts (≤10) |
| `gtm_find_container` | 🟢 | resolve `GTM-XXXXXXX` / `G-…` / `AW-…` to account + container + default workspace (1–2 requests); or search by name |
| `gtm_get_container` | 🟢 | container details (public id, usage context, domains, linked tag ids) |
| `gtm_list_workspaces` | 🟢 | draft workspaces |
| `gtm_get_workspace_status` | 🟢 | unpublished changes + merge conflicts |
| `gtm_list_tags` / `gtm_get_tag` | 🟢 | tags with type labels, paused flag, key parameters, trigger ids; full flattened parameters on get |
| `gtm_list_triggers` / `gtm_get_trigger` | 🟢 | triggers with one-line condition summaries |
| `gtm_list_variables` / `gtm_get_variable` | 🟢 | user-defined variables with summaries |
| `gtm_list_built_in_variables` | 🟢 | enabled built-ins |
| `gtm_list_folders`, `gtm_list_templates` | 🟢 | folders, custom/gallery templates (code not returned) |
| `gtm_list_versions`, `gtm_get_version`, `gtm_get_live_version` | 🟢 | version headers, one version, the live version (counts, tags by type, optional entity lists) |
| `gtm_list_environments` | 🟢 | environments and the version each serves (preview auth codes stripped) |
| `gtm_container_audit` | 🟢 | one-shot health check: UA leftovers, duplicate Google tags, dangling/unused triggers, undefined `{{variables}}`, placeholders, GA4 event naming, Custom HTML pixels, paused tags, unpublished changes — 5–7 requests |
| `gtm_check_ga4_ecommerce_coverage` | 🟢 | matrix of the 11 GA4 ecommerce events: covered live / paused only / missing / no ecommerce data, existing custom-event triggers |
| `gtm_raw_get` | 🟢 | GET any `accounts/…` path (clients, zones, transformations, gtag_config …) |
| `gtm_auth_status` | 🟢 | signed-in profile, requests used this session, rate-limiter state — no API call |
| `gtm_create_workspace` | 🟡 | new draft workspace |
| `gtm_create_tag` | 🟡 | any tag type from a flat `{key: value}` parameter dict |
| `gtm_create_ga4_event_tag` | 🟡 | typed GA4 event tag (`gaawe`); reuses or creates the Custom Event trigger; ecommerce flag on by default for ecommerce events |
| `gtm_update_tag` | 🟡 | pause/unpause, rename, swap triggers, merge parameters — shows a before/after diff, sends the fingerprint |
| `gtm_create_trigger` | 🟡 | Custom Event trigger (equals / regex) or other types with filters |
| `gtm_create_variable` | 🟡 | Data Layer variable, constant, or raw parameters for other types |
| `gtm_enable_built_in_variables` | 🟡 | enable built-ins by API type (`pageUrl`, `clickElement`, …) |
| `gtm_revert_entity` | 🟡 | undo this workspace's change to one entity (live container untouched) |
| `gtm_create_version` | 🟡 | workspace → version (not live); refuses on merge conflicts; warns that the workspace is consumed |
| `gtm_setup_ga4_ecommerce_events` | 🟡 | recipe: plan/apply triggers + GA4 event tags for the missing ecommerce events (optionally the Google tag) |
| `gtm_publish_version` | 🔴 | makes a version live — needs `dry_run=false` **and** `confirm=true`, the publish scope and the Publish role; returns the previous live id for rollback |

Every 🟡/🔴 tool defaults to `dry_run=true` and returns the exact `{method, path, body}` it would send plus warnings; call it again with `dry_run=false` to apply. The read-only profile can run all dry-runs — applying fails with a clear "run `gtm-mcp auth --with-writes`" message.

## 3. Quota — why the server is deliberately slow
Google allows **25 requests per 100 seconds and 10,000 per day per Cloud project** (shared by everyone using the same OAuth client).
gtm-mcp therefore:
- waits in a client-side token bucket (0.25 req/s, burst 20) before every request — `GTM_MCP_RATE_LIMIT_QPS` to tune;
- caches container lookups and the default workspace per process;
- offers audit/coverage tools that read the whole workspace in a handful of requests instead of many single calls;
- follows `nextPageToken` on all lists and retries 429/5xx with back-off.
`gtm_auth_status` shows how many requests the current session used.

## 4. Environment variables
| Variable | Purpose |
|---|---|
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` | use these instead of `~/.gtm-mcp/config.json` (multi-account / CI); add `GOOGLE_SCOPES` so the server knows the profile |
| `GTM_MCP_CONFIG_DIR` | folder for `config.json` + `audit.jsonl` (default `~/.gtm-mcp`) |
| `GTM_MCP_AUDIT_DISABLED=1` | turn off the local audit log |
| `GTM_MCP_RATE_LIMIT_QPS` | client-side rate (default `0.25`; `0` disables waiting — tests only) |
| `MCP_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` — stderr only, never payloads or tokens |

## 5. Development
```bash
git clone https://github.com/Mezbaul-Hasan/GTM-MCP-Server && cd GTM-MCP-Server
pip install -e ".[dev]"     # also installs marketing-mcp-common from its pinned git tag
pytest -q                   # 40 tests, Tag Manager API fully mocked (respx) — no network
python server.py            # or: mcp dev server.py
```
Facts the code relies on (endpoints, list keys, type codes, scopes, quota): [`docs/platform-notes/gtm.md`](docs/platform-notes/gtm.md).
Reference review that shaped the tool set: [`docs/platform-notes/gtm-reference-was-gtm-mcp.md`](docs/platform-notes/gtm-reference-was-gtm-mcp.md).

To work on the shared library at the same time: clone GA4-MCP-Server next to this repo and `pip install -e ../GA4-MCP-Server/common`
(pip keeps the editable copy); bump the `common-vX.Y.Z` tag in `pyproject.toml` when a new library version is needed.

## 6. Safety model
- Reads never modify anything; `gtm_raw_get` is GET-only and limited to `tagmanager.googleapis.com`.
- Writes touch a **workspace** only; the website changes only after `gtm_create_version` → `gtm_publish_version`.
- Publishing is the only 🔴 tool and needs an explicit `confirm=true`; rollback = publish the previous version id (returned in the result).
- Scopes: `auth` = `tagmanager.readonly`; `auth --with-writes` adds `edit.containers`, `edit.containerversions`, `publish`. `delete.containers`, `manage.users`, `manage.accounts` are never requested.
- Audit log: one JSON line per tool call in `~/.gtm-mcp/audit.jsonl` (tool, redacted args, duration, status).

License: MIT.
