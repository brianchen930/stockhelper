from __future__ import annotations

from datetime import date, datetime
import json
import logging
import math
import os
import re
from typing import Any, Callable

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_CONTENT_LIMIT = 2000
DISCORD_EMBED_LIMIT = 10
DISCORD_EMBED_DESCRIPTION_LIMIT = 4096
DISCORD_FIELD_NAME_LIMIT = 256
DISCORD_FIELD_VALUE_LIMIT = 1024
DISCORD_EMBED_TOTAL_LIMIT = 6000


def redact_webhook_url(text: Any) -> str:
    value = str(text)
    return re.sub(
        r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/[^/\s]+/[^\s]+",
        "https://discord.com/api/webhooks/***/***",
        value,
        flags=re.IGNORECASE,
    )


def sanitize_for_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value.__class__.__name__ == "NAType":
        return None
    if isinstance(value, dict):
        return {str(key): sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_json(item) for item in value]
    if hasattr(value, "item"):
        try:
            return sanitize_for_json(value.item())
        except (TypeError, ValueError):
            return None
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def split_discord_message(
    message: str,
    max_length: int = DISCORD_CONTENT_LIMIT,
) -> list[str]:
    if len(message) <= max_length:
        return [message]
    parts: list[str] = []
    current = ""
    for paragraph in message.splitlines(keepends=True):
        while len(paragraph) > max_length:
            if current:
                parts.append(current.rstrip())
                current = ""
            cut = paragraph.rfind("。", 0, max_length)
            cut = cut + 1 if cut >= 0 else paragraph.rfind("，", 0, max_length) + 1
            if cut <= 0:
                cut = max_length
            parts.append(paragraph[:cut].rstrip())
            paragraph = paragraph[cut:]
        if len(current) + len(paragraph) > max_length:
            if current:
                parts.append(current.rstrip())
            current = paragraph
        else:
            current += paragraph
    if current:
        parts.append(current.rstrip())
    return [part for part in parts if part]


def _validate_embeds(embeds: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if len(embeds) > DISCORD_EMBED_LIMIT:
        errors.append(f"embed 數量 {len(embeds)} 超過 {DISCORD_EMBED_LIMIT}")
    total = 0
    for index, embed in enumerate(embeds):
        description = str(embed.get("description") or "")
        total += len(str(embed.get("title") or "")) + len(description)
        if len(description) > DISCORD_EMBED_DESCRIPTION_LIMIT:
            errors.append(f"embed[{index}] description 長度 {len(description)} 超限")
        for field_index, field in enumerate(embed.get("fields") or []):
            name = str(field.get("name") or "")
            value = str(field.get("value") or "")
            total += len(name) + len(value)
            if len(name) > DISCORD_FIELD_NAME_LIMIT:
                errors.append(f"embed[{index}].field[{field_index}] name 長度超限")
            if len(value) > DISCORD_FIELD_VALUE_LIMIT:
                errors.append(f"embed[{index}].field[{field_index}] value 長度超限")
    if total > DISCORD_EMBED_TOTAL_LIMIT:
        errors.append(f"embed 總字數 {total} 超過 {DISCORD_EMBED_TOTAL_LIMIT}")
    return errors


def _payload_metrics(payload: dict[str, Any]) -> str:
    content = str(payload.get("content") or "")
    embeds = payload.get("embeds") or []
    field_lengths = [
        (len(str(field.get("name") or "")), len(str(field.get("value") or "")))
        for embed in embeds
        for field in (embed.get("fields") or [])
    ]
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return (
        f"payload_bytes={size} content_length={len(content)} "
        f"embed_count={len(embeds)} field_lengths={field_lengths}"
    )


def send_discord_payload(
    payload: dict[str, Any],
    webhook_url: str | None = None,
    post: Callable[..., Any] = requests.post,
) -> dict[str, Any]:
    url = webhook_url or DISCORD_WEBHOOK_URL
    if not url:
        return {"success": False, "status_code": None, "error": "找不到 DISCORD_WEBHOOK_URL"}
    clean_payload = sanitize_for_json(payload)
    content = str(clean_payload.get("content") or "")
    if len(content) > DISCORD_CONTENT_LIMIT:
        error = f"content 長度 {len(content)} 超過 {DISCORD_CONTENT_LIMIT}"
        logger.error("Discord payload validation failed: %s %s", error, _payload_metrics(clean_payload))
        return {"success": False, "status_code": None, "error": error}
    embed_errors = _validate_embeds(clean_payload.get("embeds") or [])
    if embed_errors:
        error = "；".join(embed_errors)
        logger.error("Discord payload validation failed: %s %s", error, _payload_metrics(clean_payload))
        return {"success": False, "status_code": None, "error": error}
    try:
        json.dumps(clean_payload, allow_nan=False)
        response = post(url, json=clean_payload, timeout=10)
        if not 200 <= response.status_code < 300:
            body = redact_webhook_url(response.text)
            logger.error(
                "Discord webhook failed: status=%s body=%s %s",
                response.status_code, body, _payload_metrics(clean_payload),
            )
            return {"success": False, "status_code": response.status_code, "error": body}
        return {"success": True, "status_code": response.status_code, "error": None}
    except requests.RequestException as error:
        safe_error = redact_webhook_url(error)
        logger.error("Discord webhook request failed: %s %s", safe_error, _payload_metrics(clean_payload))
        return {"success": False, "status_code": None, "error": safe_error}
    except (TypeError, ValueError) as error:
        return {"success": False, "status_code": None, "error": redact_webhook_url(error)}


def send_discord_message(
    message: str,
    webhook_url: str | None = None,
    post: Callable[..., Any] = requests.post,
) -> dict[str, Any]:
    parts = split_discord_message(str(message))
    sent = 0
    last_status_code = None
    for part in parts:
        result = send_discord_payload({"content": part}, webhook_url=webhook_url, post=post)
        if not result["success"]:
            return {
                **result,
                "parts_total": len(parts),
                "parts_sent": sent,
            }
        sent += 1
        last_status_code = result["status_code"]
    return {
        "success": True,
        "status_code": last_status_code,
        "error": None,
        "parts_total": len(parts),
        "parts_sent": sent,
    }


def send_stock_notifications(
    notifications: list[dict[str, str]],
    sender: Callable[[str], dict[str, Any]] = send_discord_message,
) -> dict[str, Any]:
    failed_items = []
    item_results = []
    success_count = 0
    for item in notifications:
        try:
            result = sender(item["message"])
        except Exception as error:
            result = {"success": False, "status_code": None, "error": redact_webhook_url(error)}
        if result["success"]:
            success_count += 1
        else:
            failed_items.append({
                "stock_code": item["stock_code"],
                "stock_name": item.get("stock_name", ""),
                **result,
            })
        item_results.append({
            "stock_code": item["stock_code"],
            "stock_name": item.get("stock_name", ""),
            **result,
        })
    return {
        "matched_count": len(notifications),
        "success_count": success_count,
        "failed_count": len(failed_items),
        "failed_items": failed_items,
        "item_results": item_results,
    }
