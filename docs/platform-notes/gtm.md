# Google Tag Manager — platform notes (written 2026-10-30, before coding `gtm-mcp`)

Sources (official): REST reference https://developers.google.com/tag-platform/tag-manager/api/reference/rest ·
limits & quotas https://developers.google.com/tag-platform/tag-manager/api/v2/limits-quotas ·
OAuth scopes listed on every method page (e.g. containers.lookup).
Reference implementation reviewed: `docs/platform-notes/gtm-reference-was-gtm-mcp.md`.

## 1. API shape
- **Base URL:** `https://tagmanager.googleapis.com/tagmanager/v2/` — JSON, bearer token. Only API version is **v2** (v1 is gone).
- **Hierarchy:** `accounts/{a}` → `containers/{c}` → `workspaces/{w}` → entities (`tags`, `triggers`, `variables`, `built_in_variables`, `folders`, `templates`, `clients`, `zones`, `transformations`, `gtag_config`); container-level: `versions`, `version_headers`, `environments`, `destinations`; account-level: `user_permissions`.
- **Paths:** every resource carries `path` (e.g. `accounts/123/containers/456/workspaces/7/tags/12`) — use it verbatim for get/update/delete/revert.
- **List response keys are singular:** `accounts.list → {account: []}`, `containers.list → {container: []}`, `workspaces.list → {workspace: []}`, `tags.list → {tag: [], nextPageToken}`, `triggers.list → {trigger: []}`, `variables.list → {variable: []}`, `built_in_variables.list → {builtInVariable: []}`, `folders.list → {folder: []}`, `templates.list → {template: []}`, `environments.list → {environment: []}`, `version_headers.list → {containerVersionHeader: [], nextPageToken}`. Lists are paginated with `pageToken` (no `pageSize` control on most) — always loop.
- **Custom methods (colon syntax):**
  - `GET accounts/containers:lookup?tagId=GTM-XXXXXXX` or `?destinationId=G-XXXX|AW-XXXX` → `Container` (resolves a public id to `accountId`/`containerId` in **one** request; readonly scope is enough).
  - `GET …/containers/{c}:snippet` → install snippet.
  - `GET …/containers/{c}/versions:live` → the live `ContainerVersion` (includes `tag[]`, `trigger[]`, `variable[]`, `builtInVariable[]`, `folder[]` …).
  - `GET …/containers/{c}/version_headers:latest`.
  - `POST …/workspaces/{w}:create_version` body `{name, notes}` → `{containerVersion, newWorkspacePath, compilerError, syncStatus}` — **deletes the workspace** and creates a new default one.
  - `GET …/workspaces/{w}:getStatus` → `{workspaceChange[], mergeConflict[]}`; `POST …:sync`, `POST …:quick_preview`, `POST …:resolve_conflict`.
  - `POST …/versions/{v}:publish` → `{containerVersion, compilerError}`; `POST …:set_latest`, `POST …:undelete`.
  - `POST …/tags/{t}:revert` (also triggers, variables, folders, templates…) → `{tag}` — discard workspace changes to that entity.
  - Built-in variables: `POST …/built_in_variables?type=pageUrl&type=event` (enable), `DELETE …/built_in_variables?type=…` (disable), `POST …/built_in_variables:revert?type=…`.
- **Updates:** `PUT <path>` with the full resource; optional query `fingerprint=` must match the stored fingerprint (optimistic lock) — we always GET first and send the fingerprint back.
- **Entities only live in a workspace** until `create_version` → `publish`. Rollback = publish an older version (`versions/{old}:publish`).

## 2. Key resource fields
- **Tag:** `tagId, name, type, parameter[], firingTriggerId[], blockingTriggerId[], paused, parentFolderId, tagFiringOption (unlimited|oncePerEvent|oncePerLoad), priority, notes, consentSettings, setupTag[], teardownTag[], fingerprint, tagManagerUrl`.
  - Common `type` codes: `googtag` (Google tag / GA4 config, param `tagId`), `gaawe` (GA4 event: `measurementIdOverride`, `eventName`, `eventSettingsTable`, `sendEcommerceData` boolean, `getEcommerceDataFrom` = `dataLayer`), `gaawc` (legacy GA4 configuration), `ua` (Universal Analytics — dead product), `html` (custom HTML, param `html`), `img` (custom image), `awct` (Google Ads conversion: `conversionId`, `conversionLabel`), `awrmt` (Ads remarketing), `sp` (Stape/server), `flc`/`fls` (Floodlight), `cvt_<containerId>_<templateId>` (community/custom template), `gclidw` (Conversion Linker), `baut` (Microsoft UET), `hjtc` (Hotjar), `uslt`/`uspt` (custom tag types), `opt`, `pntr` (Pinterest), `twitter_website_tag`, `ts` (TikTok?) etc.
- **Parameter:** `{type: template|integer|boolean|list|map|triggerReference|tagReference, key, value, list[], map[], isWeakReference}`. Variable references inside values look like `{{Variable Name}}`; built-ins appear as `{{Page URL}}`, `{{Event}}`, `{{_event}}` (internal).
- **Trigger:** `triggerId, name, type, customEventFilter[], filter[], autoEventFilter[], waitForTags, checkValidation, …, fingerprint`. Types: `pageview, domReady, windowLoaded, customEvent, triggerGroup, init, consentInit, click, linkClick, formSubmission, jsError, historyChange, timer, scrollDepth, elementVisibility, youTubeVideo, always, serverPageview, firebase*`. A custom-event trigger for `purchase` = `{type: "customEvent", customEventFilter: [{type: "equals", parameter: [{type:"template", key:"arg0", value:"{{_event}}"}, {type:"template", key:"arg1", value:"purchase"}]}]}`. Built-in trigger ids: `2147479553` = All Pages, `2147479572` = Consent Initialization – All Pages, `2147479573` = Initialization – All Pages.
- **Variable:** `variableId, name, type, parameter[], formatValue, fingerprint`. Types: `v` (Data Layer Variable: params `name`, `dataLayerVersion`=2, `setDefaultValue`), `c` (constant: `value`), `jsm` (custom JavaScript: `javascript`), `u` (URL), `k` (1st-party cookie), `j` (JS variable), `smm`/`remm` (lookup tables), `gas` (legacy GA settings), `gtes` (Google tag event settings), `gtcs` (Google tag config settings), `cvt_*` (templates).
- **Built-in variable types** (enum `type`): `pageUrl, pageHostname, pagePath, referrer, event, clickElement, clickClasses, clickId, clickTarget, clickUrl, clickText, formElement, formClasses, formId, formTarget, formUrl, formText, errorMessage, errorUrl, errorLine, newHistoryFragment, oldHistoryFragment, newHistoryState, oldHistoryState, historySource, containerVersion, debugMode, randomNumber, containerId, htmlId, environmentName, scrollDepthThreshold, scrollDepthUnits, scrollDirection, elementVisibilityRatio, elementVisibilityTime, …`.
- **Workspace:** `workspaceId, name, description, fingerprint, tagManagerUrl`. Max 3 workspaces per container on the free tier (GTM UI rule).
- **ContainerVersion / Header:** `containerVersionId, name, description, deleted, numTags, numTriggers, numVariables, numMacros, numRules, numCustomTemplates, numZones, numClients, numTransformations, numGtagConfigs`.
- **Container:** `containerId, accountId, name, publicId (GTM-XXXX), usageContext[] (web|androidSdk5|iosSdk5|server|amp), domainName[], taggingServerUrls[], features{…}, tagIds[], fingerprint`.

## 3. Scopes (OAuth)
| Scope | Needed for |
|---|---|
| `https://www.googleapis.com/auth/tagmanager.readonly` | all reads (Phase 1), `containers:lookup`, `versions:live` |
| `https://www.googleapis.com/auth/tagmanager.edit.containers` | create/update/delete/revert workspace entities, create workspaces |
| `https://www.googleapis.com/auth/tagmanager.edit.containerversions` | `create_version`, version update/set_latest/undelete |
| `https://www.googleapis.com/auth/tagmanager.publish` | `versions:publish` (🔴) |
| `tagmanager.delete.containers`, `tagmanager.manage.users`, `tagmanager.manage.accounts` | **never requested by gtm-mcp** |

`gtm-mcp auth` requests readonly only; `gtm-mcp auth --with-writes` requests readonly + edit.containers + edit.containerversions + publish. The Google account must also hold the matching GTM user role (Read / Edit / Approve / Publish) on the container — otherwise 403 even with the scope.

## 4. Quotas (hard, per Google Cloud project)
- **10,000 requests per project per day** (reset midnight PST).
- **0.25 QPS per project = 25 requests per 100-second sliding window.** Setting a higher per-user limit in the API Console does not raise it.
- Consequences for `gtm-mcp`: client-side token bucket (0.25 req/s, burst 25) in front of every request; full pagination but no speculative calls; audit/coverage helpers fetch each list once per call (4–6 requests per container) and tell the model what they already know so it does not re-list; backoff on 429 honours `Retry-After`.

## 5. Gotchas
- Lists return **singular keys** (`tag`, not `tags`) — easy to miss.
- `create_version` **deletes the workspace** (a fresh default workspace is created; `newWorkspacePath` in the response). Tools must say so.
- `publish` compiles the version: `compilerError: true` means the container did not publish — surface it.
- Tag `type` strings are not documented centrally; keep the mapping above and show the raw type when unknown.
- Workspaces can have **merge conflicts** when the live version moved — `getStatus` before `create_version`; `sync` to pull.
- The UI "Pause" flag is `paused: true` on the tag (not a trigger change).
- `tagManagerUrl` is returned for most entities — include it in outputs so a human can open the exact object.
- Parameter values containing `{{Name}}` reference variables by display name; renaming a variable breaks references silently.
- No sandbox/test environment — tests must be mocked; live writes only against a scratch container chosen by the owner.
