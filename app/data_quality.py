from typing import Any

from app.market_data import is_finite_number
from app.rules.signal_change_rule import VALID_SIGNAL_STATES, VALID_TREND_STATES


def assess_analysis_quality(data: dict[str, Any]) -> dict[str, Any]:
    """判斷本次結果能否參與市場事件、分數與有效狀態更新。"""
    issues: list[str] = []
    strategy = data.get("analysis") or {}
    source_quality = data.get("data_quality") or {}

    final_close = data.get("realtime_price")
    if not is_finite_number(final_close):
        final_close = data.get("close")

    latest_missing_fields = source_quality.get("latest_missing_fields", [])
    if "Close" in latest_missing_fields and not is_finite_number(final_close):
        issues.append("latest_close_missing")
    elif not is_finite_number(final_close):
        issues.append("latest_close_missing")

    if not is_finite_number(data.get("price_change_percent")):
        issues.append("price_change_unavailable")

    if strategy.get("signal") not in VALID_SIGNAL_STATES:
        issues.append("invalid_signal_state")
    if strategy.get("trend") not in VALID_TREND_STATES:
        issues.append("invalid_trend_state")

    timeframes = data.get("timeframe_analysis") or {}
    short_label = (timeframes.get("short_term") or {}).get("label")
    medium_label = (timeframes.get("medium_term") or {}).get("label")
    if short_label == "資料不足" and medium_label == "資料不足":
        issues.append("insufficient_timeframe_data")

    return {
        "is_valid": not issues,
        "issues": list(dict.fromkeys(issues)),
        "should_notify_system_alert": False,
    }
