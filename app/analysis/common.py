"""多週期分析共用型別、評分與安全取值工具。"""

from __future__ import annotations

from typing import NotRequired, TypedDict

import pandas as pd

from app.market_data import is_finite_number


class TimeframeResult(TypedDict):
    view: str
    label: str
    score: int
    score_min: int | None
    score_max: int | None
    reasons: list[str]
    bullish_factors: list[str]
    bearish_factors: list[str]
    warnings: list[str]
    summary: str
    market_structure: NotRequired[dict[str, str]]
    data_quality: NotRequired[dict[str, object]]


VIEW_LABELS = {
    "bullish": "偏多",
    "slightly_bullish": "中性偏多",
    "neutral": "中性",
    "slightly_bearish": "中性偏空",
    "bearish": "偏空",
}


def score_to_view(score: int) -> tuple[str, str]:
    """將各週期獨立分數轉為一致的五級看法。"""
    if score >= 4:
        view = "bullish"
    elif score >= 2:
        view = "slightly_bullish"
    elif score > -2:
        view = "neutral"
    elif score > -4:
        view = "slightly_bearish"
    else:
        view = "bearish"
    return view, VIEW_LABELS[view]


def is_number(value: object) -> bool:
    return is_finite_number(value)


def pct_distance(price: float, reference: float) -> float:
    return (price / reference - 1) * 100 if reference else 0.0


def insufficient_result(
    period_name: str,
    required: int,
    actual: int,
    *,
    raw_count: int | None = None,
    missing_fields: list[str] | None = None,
    latest_valid_date: str | None = None,
) -> TimeframeResult:
    raw = actual if raw_count is None else raw_count
    message = (
        f"{period_name}分析資料不足：原始資料 {raw} 筆、"
        f"有效資料 {actual} 筆、最低需求 {required} 筆"
    )
    return {
        "view": "neutral",
        "label": "資料不足",
        "score": 0,
        "score_min": None,
        "score_max": None,
        "reasons": [],
        "bullish_factors": [],
        "bearish_factors": [],
        "warnings": [message],
        "summary": f"{message}，目前不進行方向判斷。",
        "data_quality": {
            "raw_count": raw,
            "valid_count": actual,
            "required_count": required,
            "latest_valid_date": latest_valid_date,
            "missing_fields": missing_fields or [],
        },
    }


def unique_sentences(items: list[str]) -> list[str]:
    """保留順序並移除相同或僅標點不同的重複句子。"""
    result: list[str] = []
    normalized: set[str] = set()
    for item in items:
        clean = item.strip().rstrip("。；")
        key = clean.replace("，", "").replace(" ", "")
        if clean and key not in normalized:
            normalized.add(key)
            result.append(clean)
    return result


def split_directional_factors(reasons: list[str]) -> tuple[list[str], list[str]]:
    """將既有客觀原因分成多方與空方；不參與或改變原有評分。"""
    bullish_terms = (
        "站上", "上方", "向上", "高於", "回升", "改善", "黃金交叉",
        "上穿", "上漲", "轉強", "墊高", "較高高點", "量能增加",
    )
    bearish_terms = (
        "下方", "尚未站回", "向下", "低於", "轉弱", "死亡交叉",
        "跌破", "下跌", "偏弱", "下移", "空頭排列", "空方動能增強",
    )
    bullish: list[str] = []
    bearish: list[str] = []
    for reason in unique_sentences(reasons):
        has_bullish = any(term in reason for term in bullish_terms)
        has_bearish = any(term in reason for term in bearish_terms)
        if has_bearish and not has_bullish:
            bearish.append(reason)
        elif has_bullish and not has_bearish:
            bullish.append(reason)
        elif "整理" in reason or "未形成一致方向" in reason:
            continue
        elif has_bearish:
            bearish.append(reason)
        else:
            bullish.append(reason)
    return bullish, bearish
