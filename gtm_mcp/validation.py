"""Input validation → ToolError with a next step (never a stack trace)."""

from __future__ import annotations

import re

from marketing_mcp_common.errors import ToolError

_NUMERIC = re.compile(r"^\d{1,20}$")
_PUBLIC_ID = re.compile(r"^(GTM|G|AW|DC|GT)-[A-Z0-9]{4,20}$")
_CONTAINER_PATH = re.compile(r"^accounts/(\d+)/containers/(\d+)$")
_MEASUREMENT_ID = re.compile(r"^G-[A-Z0-9]{4,20}$")
_EVENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-. ]{0,99}$")
_GA4_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_VARIABLE_REF = re.compile(r"^\{\{[^{}]+\}\}$")
_BUILT_IN_TYPE = re.compile(r"^[a-zA-Z]{2,60}$")
_RAW_PATH = re.compile(r"^accounts(/[A-Za-z0-9_\-:.]+)*$")


def numeric_id(value: str | int, what: str) -> str:
    text = str(value).strip()
    if text.startswith(f"{what}s/"):
        text = text.split("/", 1)[1]
    if not _NUMERIC.match(text):
        raise ToolError(f"{what}_id '{value}' is not a numeric GTM id — use the id from the matching gtm_list_* tool")
    return text


def public_id(value: str) -> str:
    text = str(value).strip().upper()
    if not _PUBLIC_ID.match(text):
        raise ToolError(f"'{value}' is not a GTM public id (GTM-XXXXXXX) or destination id (G-XXXX / AW-XXXX)")
    return text


def container_ref(value: str) -> tuple[str, str] | str:
    """Return ``(account_id, container_id)`` for an API path, or the public id string."""
    text = str(value).strip()
    m = _CONTAINER_PATH.match(text)
    if m:
        return m.group(1), m.group(2)
    if _NUMERIC.match(text):
        raise ToolError(
            f"container '{value}' is a bare numeric id — pass the public id (GTM-XXXXXXX) or the path accounts/<account_id>/containers/{text}"
        )
    return public_id(text)


def measurement_id_or_ref(value: str) -> str:
    text = str(value).strip()
    if _VARIABLE_REF.match(text):
        return text
    text = text.upper()
    if not _MEASUREMENT_ID.match(text):
        raise ToolError(f"'{value}' is not a GA4 measurement id (G-XXXXXXXX) or a GTM variable reference like {{{{GA4 Measurement ID}}}}")
    return text


def entity_name(value: str, what: str = "name") -> str:
    text = str(value).strip()
    if not text or len(text) > 255:
        raise ToolError(f"{what} must be 1–255 characters")
    return text


def event_name(value: str) -> str:
    text = str(value).strip()
    if not _EVENT_NAME.match(text):
        raise ToolError(f"event name '{value}' is invalid — letters, digits, underscore (GA4 recommends snake_case like add_to_cart)")
    return text


def ga4_event_name(value: str) -> str:
    text = event_name(value)
    if not _GA4_EVENT_NAME.match(text):
        raise ToolError(f"GA4 event name '{value}' should be snake_case (lowercase letters, digits, underscore, ≤40 chars), e.g. add_to_cart")
    return text


def built_in_types(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for v in values:
        text = str(v).strip()
        if not _BUILT_IN_TYPE.match(text):
            raise ToolError(f"built-in variable type '{v}' is invalid — use camelCase API names like pageUrl, clickElement, event")
        if text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        raise ToolError("types must contain at least one built-in variable type (e.g. pageUrl, event, clickElement)")
    return cleaned


def raw_path(value: str) -> str:
    text = str(value).strip().lstrip("/")
    if text.startswith("tagmanager/v2/"):
        text = text[len("tagmanager/v2/") :]
    if not _RAW_PATH.match(text):
        raise ToolError("path must be a Tag Manager v2 resource path starting with 'accounts', e.g. accounts/123/containers/456/workspaces/7/tags")
    return text


def limit_and_offset(limit: int | None, offset: int | None, *, default: int, maximum: int) -> tuple[int, int]:
    lim = default if limit is None else int(limit)
    off = 0 if offset is None else int(offset)
    if lim < 1 or lim > maximum:
        raise ToolError(f"limit must be between 1 and {maximum}")
    if off < 0:
        raise ToolError("offset must be >= 0")
    return lim, off
