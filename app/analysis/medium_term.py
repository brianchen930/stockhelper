"""20～60 個交易日的中期波段結構分析。"""

from __future__ import annotations

import pandas as pd

from app.analysis.common import (
    TimeframeResult,
    insufficient_result,
    is_number,
    pct_distance,
    score_to_view,
    split_directional_factors,
    unique_sentences,
)

MEDIUM_TERM_MIN_SCORE = -9
MEDIUM_TERM_MAX_SCORE = 9


def analyze_medium_term(data: pd.DataFrame) -> TimeframeResult:
    """依月季線、MACD、波段高低點與量價結構評估中期趨勢。"""
    quality = {} if data is None else data.attrs.get("data_quality", {})
    raw_count = 0 if data is None else int(quality.get("raw_count", len(data)))
    valid_count = 0 if data is None or "Close" not in data else int(data["Close"].map(is_number).sum())
    if data is None or valid_count < 60:
        return insufficient_result(
            "中期", 60, valid_count, raw_count=raw_count,
            latest_valid_date=quality.get("latest_valid_date"),
        )

    latest = data.iloc[-1]
    required = ("Close", "ma20", "ma60")
    missing_required = [
        column for column in required
        if column not in data.columns or not is_number(latest.get(column))
    ]
    if missing_required:
        return insufficient_result(
            "中期",
            60,
            valid_count,
            raw_count=raw_count,
            missing_fields=missing_required,
            latest_valid_date=quality.get("latest_valid_date"),
        )

    score = 0
    reasons: list[str] = []
    warnings: list[str] = []
    close = float(latest["Close"])
    ma20 = float(latest["ma20"])
    ma60 = float(latest["ma60"])

    if close > ma20 > ma60:
        score += 3
        reasons.append("股價站上月線與季線，且中期均線呈多頭排列")
    elif close < ma20 < ma60:
        score -= 3
        reasons.append("股價位於月線與季線下方，且中期均線呈空頭排列")
        warnings.append("均線仍為空頭排列，僅憑短期指標改善尚不能確認波段反轉")
    else:
        if close > ma20:
            score += 1
            reasons.append("股價位於月線上方")
        else:
            score -= 1
            reasons.append("股價尚未站回月線")
            warnings.append("股價跌破月線，需觀察季線或波段低點能否守穩")
        if close > ma60:
            score += 1
            reasons.append("股價仍守在季線上方")
        else:
            score -= 1
            reasons.append("股價仍位於季線下方")
            warnings.append("尚未站回季線，中期轉強仍缺乏確認")

    old_ma20, old_ma60 = data["ma20"].iloc[-6], data["ma60"].iloc[-6]
    if is_number(old_ma20):
        if ma20 > float(old_ma20):
            score += 1
            reasons.append("月線方向向上")
        else:
            score -= 1
            reasons.append("月線方向向下")
    if is_number(old_ma60):
        if ma60 > float(old_ma60):
            score += 1
            reasons.append("季線方向向上")
        else:
            score -= 1
            reasons.append("季線方向向下")

    macd_value = latest.get("MACD")
    if is_number(macd_value):
        macd = float(macd_value)
        if macd > 0:
            score += 1
            reasons.append("MACD 位於零軸上方")
        else:
            score -= 1
            reasons.append("MACD 位於零軸下方")
            warnings.append("MACD 尚未站回零軸，中期動能仍偏弱")
        old_macd = data["MACD"].iloc[-6] if "MACD" in data else None
        if is_number(old_macd):
            if macd > float(old_macd):
                score += 1
                reasons.append("MACD 最近 5 日方向向上")
            else:
                score -= 1
                reasons.append("MACD 最近 5 日方向向下")
    else:
        warnings.append("MACD 資料不足，本次未納入中期評分")

    structure_score, structure = _evaluate_market_structure(data)
    score += structure_score
    reasons.append(structure["description"])

    window = data.iloc[-60:]
    low_60, high_60 = float(window["Low"].min()), float(window["High"].max())
    swing_gain = pct_distance(close, low_60)
    if swing_gain >= 30:
        warnings.append(f"股價較 60 日低點已上漲 {swing_gain:.1f}%，需留意波段追價風險")
    if close >= high_60 * 0.97:
        warnings.append(f"股價接近 60 日高點 {high_60:.2f}，需確認是否能有效突破")

    ma20_bias, ma60_bias = pct_distance(close, ma20), pct_distance(close, ma60)
    if abs(ma20_bias) >= 12:
        warnings.append(f"股價與月線乖離 {ma20_bias:+.1f}%，中期乖離偏大")
    if abs(ma60_bias) >= 20:
        warnings.append(f"股價與季線乖離 {ma60_bias:+.1f}%，波段位置風險升高")

    _append_volume_trend(data, reasons, warnings)
    reasons = unique_sentences(reasons)
    warnings = unique_sentences(warnings)
    bullish_factors, bearish_factors = split_directional_factors(reasons)
    view, label = score_to_view(score)
    summary = _build_summary(view, bullish_factors, bearish_factors, warnings)
    return {
        "view": view,
        "label": label,
        "score": score,
        "score_min": MEDIUM_TERM_MIN_SCORE,
        "score_max": MEDIUM_TERM_MAX_SCORE,
        "reasons": reasons,
        "bullish_factors": bullish_factors,
        "bearish_factors": bearish_factors,
        "warnings": warnings,
        "summary": summary,
        "market_structure": structure,
        "data_quality": {
            "raw_count": raw_count,
            "valid_count": valid_count,
            "required_count": 60,
            "latest_valid_date": quality.get("latest_valid_date"),
            "missing_fields": quality.get("missing_fields", []),
        },
    }


def _evaluate_market_structure(data: pd.DataFrame) -> tuple[int, dict[str, str]]:
    """比較兩個固定 30 日區間極值；此方法不宣稱辨識 swing points。"""
    older = data.iloc[-60:-30]
    recent = data.iloc[-30:]
    old_high, old_low = float(older["High"].max()), float(older["Low"].min())
    new_high, new_low = float(recent["High"].max()), float(recent["Low"].min())
    if new_high > old_high and new_low > old_low:
        description = "最近 30 日區間高點與低點皆高於前一個 30 日區間"
        latest_close = float(data["Close"].iloc[-1])
        if latest_close <= new_high * 0.9:
            description += "，但近期價格已由區間高點明顯回落"
        return 2, {
            "method": "rolling_30d_range_comparison",
            "status": "higher_range",
            "description": description,
        }
    if new_high < old_high and new_low < old_low:
        return -2, {
            "method": "rolling_30d_range_comparison",
            "status": "lower_range",
            "description": "最近 30 日區間高點與低點皆低於前一個 30 日區間",
        }
    return 0, {
        "method": "rolling_30d_range_comparison",
        "status": "mixed_range",
        "description": "前後兩個 30 日區間高低點未呈現一致方向",
    }


def _append_volume_trend(
    data: pd.DataFrame,
    reasons: list[str],
    warnings: list[str],
) -> None:
    if "Volume" not in data.columns:
        warnings.append("缺少成交量資料，無法判斷中期量價趨勢")
        return
    recent = data["Volume"].iloc[-20:].mean()
    older = data["Volume"].iloc[-40:-20].mean()
    price_change = pct_distance(float(data["Close"].iloc[-1]), float(data["Close"].iloc[-21]))
    if not is_number(recent) or not is_number(older) or older <= 0:
        warnings.append("成交量資料不足，無法判斷中期量價趨勢")
    elif recent > older * 1.2 and price_change > 0:
        reasons.append("近 20 日價格上漲且平均量能增加")
    elif price_change > 3 and recent < older * 0.85:
        warnings.append("波段價格上漲但平均量能下降，需留意價漲量縮")
    elif price_change < -3 and recent > older * 1.2:
        warnings.append("波段價格下跌且平均量能增加，賣壓尚未緩解")


def _build_summary(
    view: str,
    bullish_factors: list[str],
    bearish_factors: list[str],
    warnings: list[str],
) -> str:
    """將中期條件解釋為波段狀態，不複製因素清單。"""
    if view in {"bearish", "slightly_bearish"}:
        text = "目前波段結構仍偏弱，中期重要均線與價格結構尚未完成轉強。"
        if bullish_factors:
            text += "仍有部分支撐條件，因此中期架構尚未全面轉空；後續重點是重要支撐與前波低點能否守穩。"
        else:
            text += "目前尚未看到足以支撐波段止跌的條件。"
    elif view in {"bullish", "slightly_bullish"}:
        text = "中期均線與波段結構整體偏多，主要上升架構目前仍占優勢。"
        if bearish_factors:
            text += "局部轉弱條件顯示波段並非毫無壓力，仍需追蹤重要支撐。"
    else:
        text = "中期均線與波段高低點尚未形成一致方向，目前較接近區間整理。"
    return text
