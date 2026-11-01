# GTM MCP Server — project file (source of truth for this repository)

Owner: **Md. Mezbaul Hasan** · GitHub: `Mezbaul-Hasan/GTM-MCP-Server` (public, MIT) · Language in chat: Bengali; code, docs, commits: English.

This file tracks **only `gtm-mcp`**. The programme-wide conventions — risk tiers (🟢/🟡/🔴), tool conventions, auth & secrets
rules, access roles and how the agent must work — live in
[GA4-MCP-Server/PROJECT.md](https://github.com/Mezbaul-Hasan/GA4-MCP-Server/blob/main/PROJECT.md) §3, §5, §6, §8 and apply
here unchanged. Read both files before touching this repository.

## 1. Status
| Server | Platform API | Auth | Main use | Status |
|---|---|---|---|---|
| `gtm-mcp` | Google Tag Manager API v2 (`tagmanager.googleapis.com/tagmanager/v2`) | Google OAuth2 desktop browser flow; `tagmanager.readonly` by default, `gtm-mcp auth --with-writes` adds `edit.containers` + `edit.containerversions` + `publish` | audits (`gtm_container_audit`, `gtm_check_ga4_ecommerce_coverage`), reads, workspace writes with `dry_run` (🟡), `gtm_publish_version` (🔴) | 🟢 **v0.1.0** — 34 tools (23 🟢 / 10 🟡 / 1 🔴), 40 mocked tests, CI green on Ubuntu/Windows/macOS × Python 3.11/3.13 incl. `uvx` install check; **live check by owner pending** |

Status legend: ⬜ not started · 🟡 auth working · 🟢 read tools done · ✅ write tools + tests + docs complete + live-verified.

## 2. Scope decisions (owner, 2026-10-30/31)
- Phase 1 (reads + audits) **and** Phase 2 (workspace writes) in v0.1.0, incl. the GA4-ecommerce recipe `gtm_setup_ga4_ecommerce_events`.
- Exactly **one 🔴 tool**: `gtm_publish_version` (needs `dry_run=false` **and** `confirm=true` **and** the publish scope). **No delete tools, no user-management tools** — ever, without a new owner decision.
- Every write tool: workspace-only, `dry_run=True` by default, returns `{dry_run, applied, request, preview, warnings, next_step}`; `applied=true` only with `dry_run=False`.
- Same Google Cloud project as ga4-mcp (`ga4-mcp-511300`), same Desktop OAuth client; scopes are added per server.
- Quota-aware by design: official quota is **10,000 requests/day and 0.25 QPS per Cloud project** (25 requests per 100 s) → client-side `TokenBucket` (0.25/s, burst 20, env `GTM_MCP_RATE_LIMIT_QPS`), list caching, full pagination, 429 back-off; audit tools fetch a workspace once.

## 3. Layout & distribution
- **Own repository, flat layout** (owner decision 2026-11-01: one GitHub repository per server): `gtm_mcp/` (`cli`, `client`, `config`, `model`, `server`, `validation`, `tools/{reads,writes,recipes,helpers,_shared}`), `tests/`, `docs/platform-notes/gtm.md` (verified API facts) + `gtm-reference-was-gtm-mcp.md` (reference review), `server.py`, `.github/workflows/ci.yml`.
- Shared library **`marketing-mcp-common`** is *not* copied here; it is installed from a pinned tag of the sister repo:
  `marketing-mcp-common @ git+https://github.com/Mezbaul-Hasan/GA4-MCP-Server@common-v0.1.1#subdirectory=common`.
  Upgrade = bump the tag in `pyproject.toml` (+ CHANGELOG line). Option kept open: move `common` to its own repo later (one-line change).
- Teammates: `uvx --from "git+https://github.com/Mezbaul-Hasan/GTM-MCP-Server" gtm-mcp auth` (`--with-writes` only for editors), then host config
  `"gtm": {"command": "uvx", "args": ["--from", "git+https://github.com/Mezbaul-Hasan/GTM-MCP-Server", "gtm-mcp"]}`.
- Config `~/.gtm-mcp/config.json` (0600) + `audit.jsonl`; env `GTM_MCP_CONFIG_DIR`, `GTM_MCP_AUDIT_DISABLED`, `GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN/SCOPES` (multi-account override).

## 4. Platform gotchas (details: `docs/platform-notes/gtm.md`)
- Hierarchy account → container → **workspace** → version; edits live in a workspace until a version is created and **published** (🔴); rollback = republish an older version.
- Quota **25 requests / 100 s and 10,000 / day per Cloud project** — far smaller than other Google APIs; the client-side token bucket in `marketing_mcp_common.ratelimit` is mandatory.
- List responses use **singular keys** (`tag`, `trigger`, `variable`, `account`…); `create_version` **deletes the workspace**; `containers:lookup?tagId=GTM-…` resolves public ids (`GTM-`, `G-`, `AW-`) in one call; updates need the entity `fingerprint`; built-in trigger "All Pages" id `2147479553`; tag type codes `googtag`, `gaawe`, `ua`, `html`, `awct`.

## 5. Definition of done (per release)
- [x] `docs/platform-notes/gtm.md` written before coding
- [x] Every tool annotated (`read_only_hint` / `destructive_hint`), 🟡/🔴 with `dry_run` + preview
- [x] Input validation, pagination, rate-limit back-off, error mapping via `marketing-mcp-common`
- [x] 40 mocked tests (respx) + in-process smoke test; CI on 3 OSes; `uvx` install check from this repo
- [x] README with tool table, scopes, Claude Desktop/Cursor snippet, troubleshooting; CHANGELOG
- [ ] **Live check by the owner** on an own container (see §7)
- [ ] Status row in §1 updated; entry added to §6

## 6. Progress log
| Date | What | Next |
|---|---|---|
| 2026-10-30 | Reference review of `mnsmasum62786/was-gtm-mcp` (19-tool Node GTM MCP, action-multiplexed) → `docs/platform-notes/gtm-reference-was-gtm-mcp.md`: take tool catalogue + id lookup + env override; fix always-all-7-scopes, missing tiers/dry_run/pagination/rate limit, wrong quota claim. Owner chose: Phase 1 + 2 incl. publish 🔴, GA4-ecommerce recipe, Cloud project `ga4-mcp-511300` | Write `docs/platform-notes/gtm.md` → build |
| 2026-10-31 | `docs/platform-notes/gtm.md` written (base URL, singular list keys, custom methods, type codes, scopes, 25/100 s quota, gotchas). **v0.1.0 built:** 34 tools — 23 🟢 (reads, `gtm_find_container`, `gtm_container_audit`, `gtm_check_ga4_ecommerce_coverage`, `gtm_raw_get`, `gtm_auth_status`), 10 🟡 (`dry_run=True` previews, scope-gated: workspace/tag/GA4-event-tag/trigger/variable/built-ins/revert/version + recipe `gtm_setup_ga4_ecommerce_events`), 1 🔴 `gtm_publish_version` (`confirm=true`). Shared library gained `ratelimit.TokenBucket` + optional `HttpClient(rate_limiter=…)` hook. CLI `gtm-mcp auth [--with-writes]/status/logout/serve`. 40 tests green | Push; owner live check |
| 2026-11-01 | First push as a folder of the GA4 repository (that history was later consolidated away at the owner's request); CI green 9/9. Lesson: the GitHub device-flow token needs the `workflow` scope when a commit touches `.github/workflows/` | Owner decision on repository layout |
| 2026-11-01 | **Owner decision: every server gets its own GitHub repository.** Code + GTM platform notes moved here with a fresh history (commit `1d67b1d`), flat layout, own CI; removed from the GA4 repository. Depends on `marketing-mcp-common` tag `common-v0.1.1`. CI run 41237673952 green 9/9 incl. `uvx --from git+…GTM-MCP-Server gtm-mcp help` on 3 OSes | Owner live check (§7) |
| 2026-11-01 | GA4 repository history consolidated into a single commit (owner request: no trace of this server there); tag `common-v0.1.1` re-created on that commit — same tag name, same library version 0.1.1, so `pyproject.toml` here is unchanged | Owner live check (§7) |
| 2026-11-01 | **This `PROJECT.md` added**; all GTM material (status row, scope policy, platform gotchas, log rows) moved out of the GA4 repository's programme file into this repo, per owner instruction | Owner live check (§7) |

## 7. Owner follow-up (before the team uses it)
1. Google Cloud project `ga4-mcp-511300` → **APIs & Services → Library → Tag Manager API → Enable**.
2. **Google Auth Platform → Data Access** → add scope `https://www.googleapis.com/auth/tagmanager.readonly` (add `tagmanager.edit.containers`, `tagmanager.edit.containerversions`, `tagmanager.publish` only if `--with-writes` will be used).
3. On your PC: `uvx --from "git+https://github.com/Mezbaul-Hasan/GTM-MCP-Server" gtm-mcp auth` → browser sign-in → `gtm-mcp status` shows the account and scopes.
4. Add the host config from §3 to Claude Desktop / Cursor, restart, then ask: *"Find container GTM-XXXXXXX and run a container audit."* Report any error text here.

## 8. Decisions
- 2026-10-30/31: Python (MCP SDK v2), local stdio, Google OAuth per person, `uvx` install — same as ga4-mcp; Phase 1 + 2, publish 🔴, no delete/user-management.
- 2026-11-01 (owner): own repository `GTM-MCP-Server`; nothing about this server remains in the GA4 repository; shared library consumed from the GA4 repository via pinned tag.
