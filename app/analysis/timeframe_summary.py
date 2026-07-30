"""整合短期與中期結果並解釋週期間的關係。"""

from __future__ import annotations

from typing import TypedDict

import pandas as pd

from app.analysis.common import TimeframeResult
from app.analysis.medium_term import analyze_medium_term
from app.analysis.short_term import analyze_short_term


class TimeframeAnalysis(TypedDict):
    short_term: TimeframeResult
    medium_term: TimeframeResult
    overall_summary: str
    overall_warnings: list[str]
    operation_reference: dict[str, str | list[str]]


def analyze_timeframes(data: pd.DataFrame) -> TimeframeAnalysis:
    """執行兩個獨立分析器，再產生跨週期判斷。"""
    short = analyze_short_term(data)
    medium = analyze_medium_term(data)
    summary, warnings = summarize_timeframes(short, medium)
    operation_reference = build_operation_reference(short, medium)
    return {
        "short_term": short,
        "medium_term": medium,
        "overall_summary": summary,
        "overall_warnings": warnings,
        "operation_reference": operation_reference,
    }


def summarize_timeframes(
    short: TimeframeResult,
    medium: TimeframeResult,
) -> tuple[str, list[str]]:
    """依兩個分數的方向與強弱動態組合，而非列舉固定案例。"""
    if short["label"] == "資料不足" or medium["label"] == "資料不足":
        return (
            "目前部分週期資料不足，無法完成可靠的短中期交叉確認。",
            ["請補足至少 60 個交易日的價格與成交量資料"],
        )

    short_direction = _direction(short["score"])
    medium_direction = _direction(medium["score"])
    warnings: list[str] = []

    if short_direction == medium_direction and short_direction != 0:
        direction_text = "偏多" if short_direction > 0 else "偏空"
        summary = f"短期與中期方向一致{direction_text}，代表近期動能與波段結構皆{direction_text}。"
        if short_direction > 0:
            summary += "目前趨勢互相確認，但仍需檢查乖離、波段漲幅與前高壓力。"
        else:
            summary += (
                "目前尚未出現足以扭轉弱勢的短期修復訊號。"
                "即使 RSI 或 KD 進入低檔，也只代表跌幅可能較深，仍須等待價格止跌確認。"
            )
    elif short_direction > 0 and medium_direction < 0:
        summary = (
            "短期動能改善，但中期趨勢仍偏弱，目前較接近弱勢波段中的技術性反彈。"
            "需觀察股價能否站回月線、季線並改善高低點結構。"
        )
        warnings.append("短期與中期方向不一致，反彈尚未確認為波段反轉")
    elif short_direction < 0 and medium_direction > 0:
        summary = (
            "短期動能轉弱，但中期多頭架構仍占優勢，目前較可能是波段上升中的整理。"
            "需留意月線與重要波段低點是否守穩。"
        )
        warnings.append("短期正在修正，若跌破中期支撐，波段趨勢可能轉弱")
    elif short_direction == 0 and medium_direction != 0:
        trend = "偏多" if medium_direction > 0 else "偏空"
        summary = (
            f"短期方向暫不明確，中期波段仍{trend}。"
            "目前宜等待短期量價與動能重新表態，再評估進出場時機。"
        )
    elif short_direction != 0 and medium_direction == 0:
        trend = "改善" if short_direction > 0 else "轉弱"
        summary = (
            f"短期動能已{trend}，但中期方向仍不明確，可能處於區間整理或趨勢形成初期。"
            "需等待中期均線與波段高低點形成一致方向。"
        )
    else:
        summary = "短期與中期方向皆不明確，目前較可能處於整理階段，宜等待價格、量能與均線結構形成共識。"

    if medium["score"] < 0 and not any("中期" in item for item in warnings):
        warnings.append("中期結構尚未翻多，短期正向訊號需降低解讀強度")
    return summary, warnings


def build_operation_reference(
    short: TimeframeResult,
    medium: TimeframeResult,
) -> dict[str, str | list[str]]:
    """依週期關係與實際風險產生簡短、非指令式的情境參考。"""
    if short["label"] == "資料不足" or medium["label"] == "資料不足":
        return {
            "for_non_holder": "分析資料不足，可等待資料補齊與方向確認後再評估。",
            "for_holder": "目前無法形成可靠判斷，宜先依既有風險控管並補足歷史資料。",
            "observation_conditions": [],
        }

    short_direction = _direction(short["score"])
    medium_direction = _direction(medium["score"])
    all_warnings = short["warnings"] + medium["warnings"]
    overheated = any(
        term in warning
        for warning in all_warnings
        for term in ("追高", "偏熱", "高點", "乖離", "波段位置")
    )

    if short_direction < 0 and medium_direction < 0:
        non_holder = "短中期皆偏弱，暫不急著搶反彈。可等待不再破低或重新站回 5 日線再觀察。"
        holder = "留意近期低點與中期支撐；若放量跌破前低，短中期風險將進一步升高。"
    elif short_direction > 0 and medium_direction < 0:
        non_holder = "目前較接近弱勢波段中的短線反彈，可觀察能否站回月線或季線再提高評價。"
        holder = "短線動能改善但中期尚未翻多，需留意短線修復失敗或無法站穩重要均線的風險。"
    elif short_direction < 0 and medium_direction > 0:
        non_holder = "中期架構尚可但短期仍在整理，可等待支撐確認或短線動能重新轉強。"
        holder = "觀察月線、季線或前波低點是否守穩，支撐未破壞前可持續追蹤整理狀況。"
    elif short_direction > 0 and medium_direction > 0:
        non_holder = "短中期趨勢偏多，仍應確認前高、乖離與量價風險，避免追價。"
        holder = "短中期結構維持偏多，可追蹤均線支撐與量價是否保持健康。"
    elif short_direction == 0 and medium_direction == 0:
        non_holder = "目前方向不明，可等待區間突破或更明確的量價訊號。"
        holder = "留意區間支撐與壓力，在方向確認前以風險控管為主。"
    elif medium_direction > 0:
        non_holder = "中期結構偏多但短期尚未確認，可等待短線動能與支撐訊號改善。"
        holder = "中期架構仍有支撐，可持續追蹤短期整理是否破壞重要均線。"
    elif medium_direction < 0:
        non_holder = "中期結構偏弱且短期方向未明，可等待價格止跌與均線改善。"
        holder = "中期風險仍在，需留意月線、季線與波段低點是否失守。"
    else:
        non_holder = "中期方向未明，可等待短期訊號獲得均線與量價確認。"
        holder = "目前以追蹤支撐與控制波動風險為主，避免只依單一短期訊號判斷。"

    if overheated and short_direction > 0:
        non_holder = non_holder.rstrip("。") + "，目前有位階或乖離風險，宜暫緩追價。"
    observation_conditions = _select_observation_conditions(short, medium)
    existing_text = non_holder + holder
    extra_conditions = [
        condition
        for condition in observation_conditions
        if not _condition_already_covered(condition, existing_text)
    ][:2]
    if extra_conditions:
        non_holder = non_holder.rstrip("。") + f"；優先觀察{'、'.join(extra_conditions)}。"
    return {
        "for_non_holder": non_holder,
        "for_holder": holder,
        "observation_conditions": observation_conditions,
    }


def _select_observation_conditions(
    short: TimeframeResult,
    medium: TimeframeResult,
) -> list[str]:
    """依支撐、均線、量能、位階的優先順序選出最多兩項觀察點。"""
    candidates: list[tuple[int, str]] = []
    combined = short["warnings"] + medium["warnings"]
    factors = short["bearish_factors"] + medium["bearish_factors"]

    if any("低點" in item or "前低" in item for item in combined):
        candidates.append((1, "近期低點或前波低點能否守穩"))
    if any("5 日線" in item for item in factors):
        candidates.append((2, "能否重新站回 5 日線"))
    if any("月線" in item or "季線" in item for item in factors):
        candidates.append((2, "月線與季線是否維持有效支撐或壓力位"))
    if any("成交量" in item or "量價" in item for item in combined):
        candidates.append((3, "成交量是否確認價格方向"))
    if any("高點" in item or "乖離" in item for item in combined):
        candidates.append((4, "前高壓力與均線乖離是否降溫"))

    selected: list[str] = []
    for _, text in sorted(candidates, key=lambda item: item[0]):
        if text not in selected:
            selected.append(text)
        if len(selected) == 2:
            break
    return selected


def _condition_already_covered(condition: str, text: str) -> bool:
    keyword_groups = {
        "近期低點或前波低點能否守穩": ("近期低點", "前波低點", "前低"),
        "能否重新站回 5 日線": ("5 日線",),
        "月線與季線是否維持有效支撐或壓力位": ("月線", "季線", "重要均線"),
        "成交量是否確認價格方向": ("成交量", "放量", "量價"),
        "前高壓力與均線乖離是否降溫": ("前高", "乖離"),
    }
    return any(keyword in text for keyword in keyword_groups.get(condition, (condition,)))


def format_timeframe_discord(analysis: TimeframeAnalysis) -> list[str]:
    """把結構化結果轉成可直接加入 Discord 訊息的行列表。"""
    lines: list[str] = []
    for title, key in (("短期看法", "short_term"), ("中期看法", "medium_term")):
        result = analysis[key]
        lines.extend([
            f"【{title}】",
            _format_score_line(result),
            "",
        ])
        primary_key = "bullish_factors" if result["score"] >= 0 else "bearish_factors"
        secondary_key = "bearish_factors" if result["score"] >= 0 else "bullish_factors"
        primary_title = "偏多因素" if result["score"] >= 0 else "偏空因素"
        secondary_title = "壓力因素" if result["score"] >= 0 else "支撐因素"
        _append_section(lines, primary_title, result[primary_key], 5)
        _append_section(lines, secondary_title, result[secondary_key], 3)
        _append_section(lines, "注意", result["warnings"], 3)
        lines.extend(["解釋：", result["summary"], ""])
    lines.extend(["【短中期綜合判斷】", analysis["overall_summary"]])
    if analysis["overall_warnings"]:
        _append_section(lines, "綜合注意", analysis["overall_warnings"], 3)
    operation = analysis["operation_reference"]
    lines.extend([
        "",
        "【操作參考】",
        f"空手者：{operation['for_non_holder']}",
        f"持有者：{operation['for_holder']}",
    ])
    return lines


def _format_score_line(result: TimeframeResult) -> str:
    score_range = ""
    if result["score_min"] is not None and result["score_max"] is not None:
        score_range = f"｜範圍：{result['score_min']:+d}～{result['score_max']:+d}"
    return f"{result['label']}｜分數：{result['score']:+d}{score_range}"


def _append_section(lines: list[str], title: str, items: list[str], limit: int) -> None:
    if not items:
        return
    lines.append(f"{title}：")
    lines.extend(f"・{item}" for item in items[:limit])
    lines.append("")


def _direction(score: int) -> int:
    if score >= 2:
        return 1
    if score <= -2:
        return -1
    return 0
