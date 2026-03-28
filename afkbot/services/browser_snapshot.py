"""Shared browser snapshot extraction helpers for tools and history context."""

from __future__ import annotations

from typing import Any
import re

_SNAPSHOT_SCRIPT = """
() => {
  const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
  const takeTexts = (selector, limit) => Array.from(document.querySelectorAll(selector))
    .map((node) => normalize(node.innerText || node.textContent || ""))
    .filter(Boolean)
    .slice(0, limit);
  const links = Array.from(document.querySelectorAll("a[href]"))
    .map((node) => ({
      text: normalize(node.innerText || node.textContent || ""),
      href: normalize(node.href || node.getAttribute("href") || ""),
    }))
    .filter((item) => item.text || item.href)
    .slice(0, 12);
  const forms = Array.from(document.forms || [])
    .map((form) => {
      const controls = Array.from(form.elements || [])
        .map((element) => ({
          tag: normalize(element.tagName || "").toLowerCase(),
          type: normalize(element.getAttribute?.("type") || ""),
          name: normalize(element.getAttribute?.("name") || ""),
          placeholder: normalize(element.getAttribute?.("placeholder") || ""),
          label: normalize(element.getAttribute?.("aria-label") || element.getAttribute?.("name") || ""),
        }))
        .filter((item) => item.tag || item.type || item.name || item.placeholder || item.label)
        .slice(0, 8);
      return {
        action: normalize(form.getAttribute("action") || ""),
        method: normalize(form.getAttribute("method") || ""),
        controls,
      };
    })
    .filter((item) => item.action || item.method || item.controls.length)
    .slice(0, 4);
  const images = Array.from(document.querySelectorAll("img"))
    .map((node) => ({
      alt: normalize(node.getAttribute("alt") || ""),
      src: normalize(node.getAttribute("src") || ""),
    }))
    .filter((item) => item.alt || item.src)
    .slice(0, 8);
  const interactives = Array.from(
    document.querySelectorAll(
      "button, a[href], input, textarea, select, [role='button'], [role='link'], "
      + "[role='textbox'], [role='searchbox'], [role='combobox'], [role='checkbox'], "
      + "[role='radio'], [role='option'], [role='menuitem'], [role='tab']"
    )
  )
    .map((node) => ({
      tag: normalize(node.tagName || "").toLowerCase(),
      role: normalize(node.getAttribute("role") || ""),
      type: normalize(node.getAttribute("type") || ""),
      text: normalize(node.innerText || node.textContent || node.getAttribute("value") || ""),
      name: normalize(node.getAttribute("name") || ""),
      label: normalize(node.getAttribute("aria-label") || node.getAttribute("name") || ""),
      placeholder: normalize(node.getAttribute("placeholder") || ""),
      href: normalize(node.href || node.getAttribute("href") || ""),
    }))
    .filter((item) => (
      item.tag || item.role || item.type || item.text || item.name || item.label || item.placeholder || item.href
    ))
    .slice(0, 20);

  return {
    title: normalize(document.title || ""),
    body_text: normalize(document.body?.innerText || ""),
    headings: takeTexts("h1, h2, h3", 12),
    buttons: takeTexts("button, [role='button'], input[type='submit'], input[type='button']", 12),
    links,
    forms,
    images,
    interactives,
  };
}
""".strip()


async def capture_browser_page_snapshot(
    page: Any,
    *,
    max_chars: int,
) -> dict[str, object]:
    """Extract one compact structured page snapshot for LLM-visible browser context."""

    raw: object = {}
    try:
        raw = await page.evaluate(_SNAPSHOT_SCRIPT)
    except Exception:
        raw = {}
    normalized = raw if isinstance(raw, dict) else {}
    title = normalized.get("title")
    if not isinstance(title, str) or not title.strip():
        try:
            title = await page.title()
        except Exception:
            title = ""
    body_text, body_text_truncated = truncate_snapshot_text(
        normalize_snapshot_text(normalized.get("body_text")),
        limit=max_chars,
    )
    return {
        "url": str(getattr(page, "url", "") or ""),
        "title": normalize_snapshot_text(title),
        "body_text": body_text,
        "body_text_truncated": body_text_truncated,
        "headings": normalize_snapshot_string_list(normalized.get("headings"), limit=12),
        "buttons": normalize_snapshot_string_list(normalized.get("buttons"), limit=12),
        "links": normalize_snapshot_link_list(normalized.get("links"), limit=12),
        "forms": normalize_snapshot_form_list(normalized.get("forms"), limit=4),
        "images": normalize_snapshot_image_list(normalized.get("images"), limit=8),
        "interactives": normalize_snapshot_interactive_list(normalized.get("interactives"), limit=20),
    }


def normalize_snapshot_text(value: object) -> str:
    """Collapse whitespace and coerce non-strings to empty text."""

    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def truncate_snapshot_text(value: str, *, limit: int) -> tuple[str, bool]:
    """Clip one text block to a bounded length."""

    cleaned = normalize_snapshot_text(value)
    if len(cleaned) <= limit:
        return cleaned, False
    return cleaned[:limit].rstrip(), True


def normalize_snapshot_string_list(value: object, *, limit: int) -> list[str]:
    """Normalize one list of visible strings."""

    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        cleaned = normalize_snapshot_text(item)
        if not cleaned:
            continue
        items.append(cleaned)
        if len(items) >= limit:
            break
    return items


def normalize_snapshot_link_list(value: object, *, limit: int) -> list[dict[str, str]]:
    """Normalize one list of links."""

    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = normalize_snapshot_text(item.get("text"))
        href = normalize_snapshot_text(item.get("href"))
        if not text and not href:
            continue
        items.append({"text": text, "href": href})
        if len(items) >= limit:
            break
    return items


def normalize_snapshot_form_list(value: object, *, limit: int) -> list[dict[str, object]]:
    """Normalize one list of forms and controls."""

    if not isinstance(value, list):
        return []
    items: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        controls = item.get("controls")
        normalized_controls: list[dict[str, str]] = []
        if isinstance(controls, list):
            for control in controls[:8]:
                if not isinstance(control, dict):
                    continue
                normalized_controls.append(
                    {
                        "tag": normalize_snapshot_text(control.get("tag")),
                        "type": normalize_snapshot_text(control.get("type")),
                        "name": normalize_snapshot_text(control.get("name")),
                        "placeholder": normalize_snapshot_text(control.get("placeholder")),
                        "label": normalize_snapshot_text(control.get("label")),
                    }
                )
        normalized: dict[str, object] = {
            "action": normalize_snapshot_text(item.get("action")),
            "method": normalize_snapshot_text(item.get("method")),
            "controls": normalized_controls,
        }
        if not normalized["action"] and not normalized["method"] and not normalized_controls:
            continue
        items.append(normalized)
        if len(items) >= limit:
            break
    return items


def normalize_snapshot_image_list(value: object, *, limit: int) -> list[dict[str, str]]:
    """Normalize one list of image descriptors."""

    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        alt = normalize_snapshot_text(item.get("alt"))
        src = normalize_snapshot_text(item.get("src"))
        if not alt and not src:
            continue
        items.append({"alt": alt, "src": src})
        if len(items) >= limit:
            break
    return items


def normalize_snapshot_interactive_list(value: object, *, limit: int) -> list[dict[str, str]]:
    """Normalize one list of interactive elements."""

    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized = {
            "tag": normalize_snapshot_text(item.get("tag")),
            "role": normalize_snapshot_text(item.get("role")),
            "type": normalize_snapshot_text(item.get("type")),
            "text": normalize_snapshot_text(item.get("text")),
            "name": normalize_snapshot_text(item.get("name")),
            "label": normalize_snapshot_text(item.get("label")),
            "placeholder": normalize_snapshot_text(item.get("placeholder")),
            "href": normalize_snapshot_text(item.get("href")),
        }
        if not any(normalized.values()):
            continue
        items.append(normalized)
        if len(items) >= limit:
            break
    return items
