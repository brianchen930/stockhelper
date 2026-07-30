import json

import pandas as pd

from app.notifier import (
    sanitize_for_json,
    send_discord_message,
    send_discord_payload,
    send_stock_notifications,
    split_discord_message,
)

WEBHOOK = "https://discord.com/api/webhooks/" + "123456/test-token"


class Response:
    def __init__(self, status_code=204, text=""):
        self.status_code = status_code
        self.text = text


def test_long_content_is_split_within_discord_limit():
    parts = split_discord_message(("第一段內容。" * 300) + "\n" + ("第二段內容。" * 300))

    assert len(parts) > 1
    assert all(len(part) <= 2000 for part in parts)


def test_oversized_embed_field_is_rejected_before_sending():
    called = False

    def post(*args, **kwargs):
        nonlocal called
        called = True
        return Response()

    result = send_discord_payload(
        {"embeds": [{"fields": [{"name": "名稱", "value": "x" * 1025}]}]},
        webhook_url=WEBHOOK,
        post=post,
    )

    assert result["success"] is False
    assert "value 長度超限" in result["error"]
    assert called is False


def test_nan_and_pandas_na_are_removed_from_payload():
    clean = sanitize_for_json({
        "nan": float("nan"),
        "infinity": float("inf"),
        "negative_infinity": float("-inf"),
        "pandas_na": pd.NA,
    })

    assert clean == {
        "nan": None,
        "infinity": None,
        "negative_infinity": None,
        "pandas_na": None,
    }
    json.dumps(clean, allow_nan=False)


def test_discord_400_returns_and_logs_response_body(caplog):
    result = send_discord_payload(
        {"content": "測試"},
        webhook_url=WEBHOOK,
        post=lambda *args, **kwargs: Response(400, "Invalid Form Body"),
    )

    assert result == {
        "success": False,
        "status_code": 400,
        "error": "Invalid Form Body",
    }
    assert "status=400" in caplog.text
    assert "Invalid Form Body" in caplog.text
    assert "content_length=2" in caplog.text


def test_discord_404_returns_failure():
    result = send_discord_payload(
        {"content": "測試"},
        webhook_url=WEBHOOK,
        post=lambda *args, **kwargs: Response(404, "Unknown Webhook"),
    )

    assert result["success"] is False
    assert result["status_code"] == 404
    assert result["error"] == "Unknown Webhook"


def test_three_stocks_count_only_actual_successes():
    outcomes = iter([True, False, True])

    def sender(message):
        success = next(outcomes)
        return {
            "success": success,
            "status_code": 204 if success else 400,
            "error": None if success else "Invalid Form Body",
        }

    result = send_stock_notifications([
        {"stock_code": "1111", "stock_name": "甲", "message": "A"},
        {"stock_code": "2222", "stock_name": "乙", "message": "B"},
        {"stock_code": "3333", "stock_name": "丙", "message": "C"},
    ], sender=sender)

    assert result["matched_count"] == 3
    assert result["success_count"] == 2
    assert result["failed_count"] == 1
    assert result["failed_items"][0]["stock_code"] == "2222"


def test_one_stock_with_three_parts_fails_if_one_part_fails():
    responses = iter([Response(204), Response(400, "Invalid Form Body")])

    result = send_discord_message(
        ("段落內容。\n" * 800),
        webhook_url=WEBHOOK,
        post=lambda *args, **kwargs: next(responses),
    )

    assert result["success"] is False
    assert result["parts_total"] >= 3
    assert result["parts_sent"] == 1


def test_error_output_never_exposes_webhook_url(caplog):
    body = f"request failed for {WEBHOOK}"
    result = send_discord_payload(
        {"content": "測試"},
        webhook_url=WEBHOOK,
        post=lambda *args, **kwargs: Response(400, body),
    )

    assert WEBHOOK not in result["error"]
    assert WEBHOOK not in caplog.text
    assert "https://discord.com/api/webhooks/***/***" in result["error"]
