from __future__ import annotations

import json
from typing import Any


def normalize_todo_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "text": str(item.get("text") or ""),
                "completed": bool(item.get("completed", False)),
            }
        )
    return normalized


def stringify_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if payload is None:
        return ""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)


def extract_claude_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return stringify_payload(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            else:
                parts.append(stringify_payload(block))
        elif isinstance(block, str):
            parts.append(block)
    return "\n\n".join(part for part in parts if part)


def build_unrendered_event_entry(*, order: int, event: dict[str, Any]) -> dict[str, Any]:
    item = event.get("item")
    raw_item_type = None
    if isinstance(item, dict) and item.get("type") is not None:
        raw_item_type = str(item.get("type"))
    return {
        "order": order,
        "kind": "unrendered_event",
        "raw_event_type": str(event.get("type") or "unknown"),
        "raw_item_type": raw_item_type,
    }
