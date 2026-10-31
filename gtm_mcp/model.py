"""GTM entity helpers: parameter (de)serialisation, compact views, builders, reference analysis.

Everything here is pure (no I/O) so it is unit-testable and shared by the read, audit,
write and recipe tools. Field names follow docs/platform-notes/gtm.md.
"""

from __future__ import annotations

import re
from typing import Any

from . import config

SCALAR_PARAM_TYPES = {"template", "integer", "boolean", "triggerReference", "tagReference"}
PARAM_TYPES = SCALAR_PARAM_TYPES | {"list", "map"}

TAG_TYPE_LABELS: dict[str, str] = {
    "googtag": "Google tag (GA4 config)",
    "gaawc": "GA4 configuration (legacy)",
    "gaawe": "GA4 event",
    "ua": "Universal Analytics (discontinued)",
    "html": "Custom HTML",
    "img": "Custom image",
    "awct": "Google Ads conversion",
    "awrmt": "Google Ads remarketing",
    "gclidw": "Conversion linker",
    "sp": "Server-side / Stape",
    "flc": "Floodlight counter",
    "fls": "Floodlight sales",
    "baut": "Microsoft Ads UET",
    "hjtc": "Hotjar",
    "pntr": "Pinterest",
    "twitter_website_tag": "X/Twitter pixel",
    "opt": "Google Optimize (discontinued)",
    "cegg": "Crazy Egg",
    "ldas": "LinkedIn Insight",
}
TRIGGER_TYPE_LABELS: dict[str, str] = {
    "pageview": "Page View",
    "domReady": "DOM Ready",
    "windowLoaded": "Window Loaded",
    "customEvent": "Custom Event",
    "triggerGroup": "Trigger Group",
    "init": "Initialization",
    "consentInit": "Consent Initialization",
    "click": "Click - All Elements",
    "linkClick": "Click - Just Links",
    "formSubmission": "Form Submission",
    "jsError": "JavaScript Error",
    "historyChange": "History Change",
    "timer": "Timer",
    "scrollDepth": "Scroll Depth",
    "elementVisibility": "Element Visibility",
    "youTubeVideo": "YouTube Video",
    "always": "Always",
    "serverPageview": "Server Page View",
}
VARIABLE_TYPE_LABELS: dict[str, str] = {
    "v": "Data Layer Variable",
    "c": "Constant",
    "jsm": "Custom JavaScript",
    "j": "JavaScript Variable",
    "u": "URL",
    "k": "1st-Party Cookie",
    "smm": "Lookup Table",
    "remm": "RegEx Table",
    "gas": "Google Analytics Settings (UA, legacy)",
    "gtes": "Google Tag: Event Settings",
    "gtcs": "Google Tag: Configuration Settings",
    "aev": "Auto-Event Variable",
    "d": "DOM Element",
    "e": "Custom Event",
    "f": "HTTP Referrer",
    "r": "Random Number",
    "ctv": "Container Version Number",
    "dbg": "Debug Mode",
    "cid": "Container ID",
    "awec": "User-Provided Data",
}
VARIABLE_TYPE_ALIASES: dict[str, str] = {"datalayer": "v", "data_layer": "v", "dlv": "v", "constant": "c", "const": "c", "javascript": "jsm", "custom_javascript": "jsm", "url": "u", "cookie": "k"}
FILTER_OPS = {"equals", "contains", "startsWith", "endsWith", "matchRegex", "matchRegexIgnoreCase", "greater", "greaterOrEquals", "less", "lessOrEquals", "cssSelector", "urlMatches"}

GA4_ECOMMERCE_EVENTS = [
    "view_item_list",
    "select_item",
    "view_item",
    "add_to_cart",
    "remove_from_cart",
    "view_cart",
    "begin_checkout",
    "add_shipping_info",
    "add_payment_info",
    "purchase",
    "refund",
]
GA4_ECOMMERCE_OPTIONAL_EVENTS = ["view_promotion", "select_promotion", "add_to_wishlist"]
ECOMMERCE_EVENT_SET = set(GA4_ECOMMERCE_EVENTS) | set(GA4_ECOMMERCE_OPTIONAL_EVENTS)

INTERNAL_VARIABLE_NAMES = {"_event", "_url", "_triggers", "_randomNumber", "_containerId", "_containerVersion", "_debugMode", "_eventId"}
_VAR_REF = re.compile(r"\{\{([^{}]+)\}\}")
_PLACEHOLDER = re.compile(r"(?i)(\bx{4,}\b|[A-Z]+-x{4,}|your[_ -]|<[A-Z][A-Z_ -]{2,}>|\bTODO\b|CHANGE ?ME|placeholder|1234567890|0000000000)")
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_VENDOR_SNIPPET = re.compile(r"(googletagmanager\.com/gtag/js|\bgtag\(|\bfbq\(|\bttq\.|\bpintrk\(|snap\.|_linkedin_partner_id|uetq)")


# --------------------------------------------------------------------------- parameters
def flatten_parameter(p: dict[str, Any]) -> Any:
    t = p.get("type")
    if t == "boolean":
        return str(p.get("value", "")).lower() == "true"
    if t == "integer":
        raw = p.get("value")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return raw
    if t in SCALAR_PARAM_TYPES:
        return p.get("value")
    if t == "list":
        return [flatten_parameter(i) for i in p.get("list") or [] if isinstance(i, dict)]
    if t == "map":
        return {i.get("key"): flatten_parameter(i) for i in p.get("map") or [] if isinstance(i, dict)}
    return p.get("value")


def flatten_parameters(params: list[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in params or []:
        if isinstance(p, dict) and p.get("key"):
            out[str(p["key"])] = flatten_parameter(p)
    return out


def to_parameter(key: str | None, value: Any) -> dict[str, Any]:
    """Python value → GTM Parameter. Dicts that already look like a Parameter pass through."""
    param: dict[str, Any]
    if isinstance(value, dict) and value.get("type") in PARAM_TYPES and ("value" in value or "list" in value or "map" in value):
        param = dict(value)
    elif isinstance(value, bool):
        param = {"type": "boolean", "value": "true" if value else "false"}
    elif isinstance(value, int) and not isinstance(value, bool):
        param = {"type": "integer", "value": str(value)}
    elif isinstance(value, float):
        param = {"type": "template", "value": repr(value)}
    elif isinstance(value, str) or value is None:
        param = {"type": "template", "value": "" if value is None else value}
    elif isinstance(value, dict):
        param = {"type": "map", "map": [to_parameter(str(k), v) for k, v in value.items()]}
    elif isinstance(value, (list, tuple)):
        param = {"type": "list", "list": [to_parameter(None, v) for v in value]}
    else:
        param = {"type": "template", "value": str(value)}
    if key is not None:
        param["key"] = key
    elif "key" in param and param["key"] is None:
        del param["key"]
    return param


def to_parameters(flat: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [to_parameter(str(k), v) for k, v in (flat or {}).items()]


def merge_parameters(existing: list[dict[str, Any]], changes: dict[str, Any]) -> list[dict[str, Any]]:
    """Replace/add parameters by key; a ``None`` value removes the key."""
    by_key = {p.get("key"): p for p in existing if isinstance(p, dict) and p.get("key")}
    others = [p for p in existing if not (isinstance(p, dict) and p.get("key"))]
    for key, value in changes.items():
        if value is None:
            by_key.pop(key, None)
        else:
            by_key[key] = to_parameter(key, value)
    return others + list(by_key.values())


# --------------------------------------------------------------------------- references
def iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from iter_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from iter_strings(v)


def variable_references(entity: dict[str, Any]) -> set[str]:
    """Names referenced as ``{{Name}}`` anywhere in parameters / filters of an entity."""
    refs: set[str] = set()
    for field in ("parameter", "customEventFilter", "filter", "autoEventFilter", "priority", "monitoringMetadata", "consentSettings", "formatValue"):
        for s in iter_strings(entity.get(field)):
            refs.update(m.strip() for m in _VAR_REF.findall(s))
    return refs


def has_placeholder(entity: dict[str, Any]) -> list[str]:
    """Parameter values that look unconfigured (XXXX, YOUR_ID, <ID>, AW-XXXXXXXX …); Custom HTML code is skipped."""
    hits: list[str] = []
    for p in entity.get("parameter") or []:
        if not isinstance(p, dict) or p.get("key") in {"html", "javascript"}:
            continue
        for s in iter_strings(p):
            if _PLACEHOLDER.search(s):
                hits.append(s[:80])
    return hits


# --------------------------------------------------------------------------- compact views
def gtm_url(entity: dict[str, Any]) -> str | None:
    return entity.get("tagManagerUrl") or None


def compact_account(a: dict[str, Any]) -> dict[str, Any]:
    return {"account_id": a.get("accountId"), "name": a.get("name"), "path": a.get("path"), "url": gtm_url(a)}


def compact_container(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": c.get("accountId"),
        "container_id": c.get("containerId"),
        "public_id": c.get("publicId"),
        "name": c.get("name"),
        "usage_context": c.get("usageContext"),
        "domain_names": c.get("domainName"),
        "tag_ids": c.get("tagIds"),
        "tagging_server_urls": c.get("taggingServerUrls"),
        "path": c.get("path"),
        "url": gtm_url(c),
    }


def compact_workspace(w: dict[str, Any]) -> dict[str, Any]:
    return {"workspace_id": w.get("workspaceId"), "name": w.get("name"), "description": w.get("description"), "path": w.get("path"), "url": gtm_url(w)}


def tag_type_label(t: str | None) -> str:
    if not t:
        return "unknown"
    if t.startswith("cvt_"):
        return "Custom/community template"
    return TAG_TYPE_LABELS.get(t, t)


def key_params_for_tag(tag: dict[str, Any]) -> dict[str, Any]:
    t = tag.get("type") or ""
    flat = flatten_parameters(tag.get("parameter"))
    keys_by_type = {
        "googtag": ["tagId"],
        "gaawc": ["measurementId"],
        "gaawe": ["eventName", "measurementIdOverride", "sendEcommerceData", "getEcommerceDataFrom", "eventSettingsVariable"],
        "ua": ["trackType", "trackingId", "gaSettings", "eventCategory", "eventAction"],
        "awct": ["conversionId", "conversionLabel", "conversionValue"],
        "awrmt": ["conversionId"],
        "flc": ["advertiserId", "groupTag", "activityTag"],
        "fls": ["advertiserId", "groupTag", "activityTag"],
        "html": [],
        "img": ["url"],
    }
    selected: dict[str, Any] = {}
    if t in keys_by_type:
        for k in keys_by_type[t]:
            if k in flat:
                selected[k] = flat[k]
        if t == "html":
            html = str(flat.get("html") or "")
            selected["html_length"] = len(html)
            selected["html_preview"] = html.strip()[:100]
    else:
        for k, v in list(flat.items())[:4]:
            if isinstance(v, (str, int, bool)):
                selected[k] = v
    return selected


def compact_tag(tag: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "tag_id": tag.get("tagId"),
        "name": tag.get("name"),
        "type": tag.get("type"),
        "type_label": tag_type_label(tag.get("type")),
        "paused": bool(tag.get("paused", False)),
        "firing_trigger_ids": tag.get("firingTriggerId") or [],
        "blocking_trigger_ids": tag.get("blockingTriggerId") or [],
        "folder_id": tag.get("parentFolderId"),
        "key_params": key_params_for_tag(tag),
        "url": gtm_url(tag),
    }
    if full:
        out.update(
            {
                "parameters": flatten_parameters(tag.get("parameter")),
                "tag_firing_option": tag.get("tagFiringOption"),
                "priority": flatten_parameter(tag["priority"]) if isinstance(tag.get("priority"), dict) else None,
                "notes": tag.get("notes"),
                "live_only": tag.get("liveOnly"),
                "setup_tags": tag.get("setupTag"),
                "teardown_tags": tag.get("teardownTag"),
                "consent_settings": tag.get("consentSettings"),
                "schedule_start_ms": tag.get("scheduleStartMs"),
                "schedule_end_ms": tag.get("scheduleEndMs"),
                "fingerprint": tag.get("fingerprint"),
                "path": tag.get("path"),
                "variable_references": sorted(variable_references(tag)),
            }
        )
    return out


def _condition_text(cond: dict[str, Any]) -> str:
    flat = flatten_parameters(cond.get("parameter"))
    op = cond.get("type", "?")
    neg = "not " if str(flat.get("negate", "")).lower() == "true" or flat.get("negate") is True else ""
    return f"{flat.get('arg0', '?')} {neg}{op} {flat.get('arg1', '')}".strip()


def describe_trigger(trigger: dict[str, Any]) -> str:
    conditions = []
    for field in ("customEventFilter", "filter", "autoEventFilter"):
        for cond in trigger.get(field) or []:
            if isinstance(cond, dict):
                conditions.append(_condition_text(cond))
    label = TRIGGER_TYPE_LABELS.get(trigger.get("type") or "", trigger.get("type") or "?")
    return f"{label}: {' AND '.join(conditions)}" if conditions else label


def custom_event_names(trigger: dict[str, Any]) -> list[str]:
    """Event names matched with ``equals`` by a customEvent trigger (empty for regex/other)."""
    if trigger.get("type") != "customEvent":
        return []
    names: list[str] = []
    for cond in trigger.get("customEventFilter") or []:
        if not isinstance(cond, dict):
            continue
        flat = flatten_parameters(cond.get("parameter"))
        if flat.get("arg0") == "{{_event}}" and cond.get("type") == "equals" and flat.get("arg1"):
            names.append(str(flat["arg1"]))
        elif flat.get("arg0") == "{{_event}}" and cond.get("type") in {"matchRegex", "matchRegexIgnoreCase"} and flat.get("arg1"):
            names.extend(part.strip() for part in str(flat["arg1"]).strip("^$()").split("|") if re.fullmatch(r"[A-Za-z0-9_]+", part.strip()))
    return names


def compact_trigger(trigger: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "trigger_id": trigger.get("triggerId"),
        "name": trigger.get("name"),
        "type": trigger.get("type"),
        "summary": describe_trigger(trigger),
        "folder_id": trigger.get("parentFolderId"),
        "url": gtm_url(trigger),
    }
    if full:
        out.update(
            {
                "custom_event_filter": [_condition_text(c) for c in trigger.get("customEventFilter") or [] if isinstance(c, dict)],
                "filter": [_condition_text(c) for c in trigger.get("filter") or [] if isinstance(c, dict)],
                "auto_event_filter": [_condition_text(c) for c in trigger.get("autoEventFilter") or [] if isinstance(c, dict)],
                "parameters": flatten_parameters(trigger.get("parameter")),
                "wait_for_tags": trigger.get("waitForTags"),
                "check_validation": trigger.get("checkValidation"),
                "notes": trigger.get("notes"),
                "fingerprint": trigger.get("fingerprint"),
                "path": trigger.get("path"),
            }
        )
    return out


def describe_variable(variable: dict[str, Any]) -> str:
    t = variable.get("type") or "?"
    flat = flatten_parameters(variable.get("parameter"))
    label = VARIABLE_TYPE_LABELS.get(t, t if not t.startswith("cvt_") else "Custom template variable")
    if t == "v":
        return f"{label}: {flat.get('name', '?')}"
    if t == "c":
        return f"{label}: {flat.get('value', '')}"
    if t == "k":
        return f"{label}: {flat.get('name', '?')}"
    if t == "u":
        return f"{label}: {flat.get('component', '?')}"
    if t == "jsm":
        return f"{label} ({len(str(flat.get('javascript') or ''))} chars)"
    return label


def compact_variable(variable: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "variable_id": variable.get("variableId"),
        "name": variable.get("name"),
        "type": variable.get("type"),
        "summary": describe_variable(variable),
        "folder_id": variable.get("parentFolderId"),
        "url": gtm_url(variable),
    }
    if full:
        out.update(
            {
                "parameters": flatten_parameters(variable.get("parameter")),
                "format_value": variable.get("formatValue"),
                "notes": variable.get("notes"),
                "fingerprint": variable.get("fingerprint"),
                "path": variable.get("path"),
                "variable_references": sorted(variable_references(variable)),
            }
        )
    return out


def compact_version_header(h: dict[str, Any]) -> dict[str, Any]:
    return {
        "version_id": h.get("containerVersionId"),
        "name": h.get("name"),
        "deleted": h.get("deleted"),
        "num_tags": _int(h.get("numTags")),
        "num_triggers": _int(h.get("numTriggers")),
        "num_variables": _int(h.get("numVariables")),
        "num_custom_templates": _int(h.get("numCustomTemplates")),
        "path": h.get("path"),
    }


def compact_version(v: dict[str, Any], *, include_entities: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "version_id": v.get("containerVersionId"),
        "name": v.get("name"),
        "description": v.get("description"),
        "deleted": v.get("deleted"),
        "fingerprint": v.get("fingerprint"),
        "counts": {
            "tags": len(v.get("tag") or []),
            "triggers": len(v.get("trigger") or []),
            "variables": len(v.get("variable") or []),
            "built_in_variables": len(v.get("builtInVariable") or []),
            "folders": len(v.get("folder") or []),
            "custom_templates": len(v.get("customTemplate") or []),
            "clients": len(v.get("client") or []),
            "zones": len(v.get("zone") or []),
        },
        "tags_by_type": count_by_type(v.get("tag") or []),
        "path": v.get("path"),
        "url": gtm_url(v),
    }
    if include_entities:
        out["tags"] = [compact_tag(t) for t in v.get("tag") or []]
        out["triggers"] = [compact_trigger(t) for t in v.get("trigger") or []]
        out["variables"] = [compact_variable(x) for x in v.get("variable") or []]
    return out


def compact_environment(e: dict[str, Any]) -> dict[str, Any]:
    # authorizationCode/url are deliberately dropped (they grant preview access).
    return {
        "environment_id": e.get("environmentId"),
        "type": e.get("type"),
        "name": e.get("name"),
        "description": e.get("description"),
        "container_version_id": e.get("containerVersionId"),
        "enable_debug": e.get("enableDebug"),
        "path": e.get("path"),
    }


def compact_template(t: dict[str, Any]) -> dict[str, Any]:
    gallery = t.get("galleryReference") or {}
    return {
        "template_id": t.get("templateId"),
        "name": t.get("name"),
        "gallery": {"owner": gallery.get("owner"), "repository": gallery.get("repository"), "version": gallery.get("version")} if gallery else None,
        "path": t.get("path"),
        "url": gtm_url(t),
    }


def compact_folder(f: dict[str, Any]) -> dict[str, Any]:
    return {"folder_id": f.get("folderId"), "name": f.get("name"), "notes": f.get("notes"), "path": f.get("path"), "url": gtm_url(f)}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def count_by_type(tags: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in tags:
        key = t.get("type") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------- builders
def param_template(key: str, value: str) -> dict[str, Any]:
    return {"type": "template", "key": key, "value": value}


def param_boolean(key: str, value: bool) -> dict[str, Any]:
    return {"type": "boolean", "key": key, "value": "true" if value else "false"}


def param_integer(key: str, value: int) -> dict[str, Any]:
    return {"type": "integer", "key": key, "value": str(value)}


def settings_table(key: str, entries: dict[str, str], *, name_key: str = "parameter", value_key: str = "parameterValue") -> dict[str, Any]:
    return {
        "type": "list",
        "key": key,
        "list": [{"type": "map", "map": [param_template(name_key, str(k)), param_template(value_key, str(v))]} for k, v in entries.items()],
    }


def build_ga4_event_tag(
    *,
    name: str,
    event_name: str,
    measurement_id: str,
    firing_trigger_ids: list[str],
    event_parameters: dict[str, str] | None = None,
    user_properties: dict[str, str] | None = None,
    send_ecommerce_data: bool = False,
    paused: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    params = [
        param_template("eventName", event_name),
        param_template("measurementIdOverride", measurement_id),
        param_boolean("sendEcommerceData", send_ecommerce_data),
    ]
    if send_ecommerce_data:
        params.append(param_template("getEcommerceDataFrom", "dataLayer"))
    if event_parameters:
        params.append(settings_table("eventSettingsTable", event_parameters))
    if user_properties:
        params.append(settings_table("userProperties", user_properties, name_key="name", value_key="value"))
    body: dict[str, Any] = {
        "name": name,
        "type": "gaawe",
        "parameter": params,
        "firingTriggerId": list(firing_trigger_ids),
        "tagFiringOption": "oncePerEvent",
        "paused": paused,
    }
    if notes:
        body["notes"] = notes
    return body


def build_google_tag(*, name: str, tag_id: str, firing_trigger_ids: list[str] | None = None, config_settings: dict[str, str] | None = None, notes: str | None = None) -> dict[str, Any]:
    params = [param_template("tagId", tag_id)]
    if config_settings:
        params.append(settings_table("configSettingsTable", config_settings))
    body: dict[str, Any] = {
        "name": name,
        "type": "googtag",
        "parameter": params,
        "firingTriggerId": list(firing_trigger_ids or [config.ALL_PAGES_TRIGGER_ID]),
        "tagFiringOption": "oncePerEvent",
    }
    if notes:
        body["notes"] = notes
    return body


def build_custom_event_trigger(*, name: str, event_name: str, use_regex: bool = False, notes: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "type": "customEvent",
        "customEventFilter": [
            {
                "type": "matchRegex" if use_regex else "equals",
                "parameter": [param_template("arg0", "{{_event}}"), param_template("arg1", event_name)],
            }
        ],
    }
    if notes:
        body["notes"] = notes
    return body


def build_filter(variable_ref: str, op: str, value: str, *, negate: bool = False) -> dict[str, Any]:
    params = [param_template("arg0", variable_ref), param_template("arg1", value)]
    if negate:
        params.append(param_boolean("negate", True))
    return {"type": op, "parameter": params}


def build_data_layer_variable(*, name: str, data_layer_name: str, version: int = 2, default_value: str | None = None, notes: str | None = None) -> dict[str, Any]:
    params = [param_template("name", data_layer_name), param_integer("dataLayerVersion", version), param_boolean("setDefaultValue", default_value is not None)]
    if default_value is not None:
        params.append(param_template("defaultValue", default_value))
    body: dict[str, Any] = {"name": name, "type": "v", "parameter": params}
    if notes:
        body["notes"] = notes
    return body


def build_constant_variable(*, name: str, value: str, notes: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "type": "c", "parameter": [param_template("value", value)]}
    if notes:
        body["notes"] = notes
    return body


# --------------------------------------------------------------------------- analysis
def ga4_event_name_of(tag: dict[str, Any]) -> str | None:
    if tag.get("type") != "gaawe":
        return None
    value = flatten_parameters(tag.get("parameter")).get("eventName")
    return str(value) if value else None


def is_snake_case(name: str) -> bool:
    return bool(_SNAKE.match(name))


def html_vendor_snippets(tag: dict[str, Any]) -> list[str]:
    if tag.get("type") != "html":
        return []
    html = str(flatten_parameters(tag.get("parameter")).get("html") or "")
    return sorted({m.group(1) for m in _VENDOR_SNIPPET.finditer(html)})


def google_tag_ids(tag: dict[str, Any]) -> list[str]:
    flat = flatten_parameters(tag.get("parameter"))
    t = tag.get("type")
    if t == "googtag":
        return [str(flat.get("tagId"))] if flat.get("tagId") else []
    if t == "gaawc":
        return [str(flat.get("measurementId"))] if flat.get("measurementId") else []
    return []
