"""5～10 個交易日的短期動能分析。"""

from __future__ import annotations

import pandas as pd
from app.macd_analysis import analyze_macd

from app.analysis.common import (
    TimeframeResult,
    insufficient_result,
    is_number,
    pct_distance,
    score_to_view,
    split_directional_factors,
    unique_sentences,
)

SHORT_TERM_MIN_SCORE = -8
SHORT_TERM_MAX_SCORE = 8


def analyze_short_term(data: pd.DataFrame) -> TimeframeResult:
    """依價格、短均線、動能與量能計算短期獨立分數。"""
    quality = {} if data is None else data.attrs.get("data_quality", {})
    raw_count = 0 if data is None else int(quality.get("raw_count", len(data)))
    valid_count = 0 if data is None or "Close" not in data else int(data["Close"].map(is_number).sum())
    if data is None or valid_count < 20:
        return insufficient_result(
            "短期", 20, valid_count, raw_count=raw_count,
            latest_valid_date=quality.get("latest_valid_date"),
        )

    latest = data.iloc[-1]
    previous = data.iloc[-2]
    five_days_ago = data.iloc[-6]
    required = ("Close", "ma5", "ma10")
    missing_required = [
        column for column in required
        if column not in data.columns or not is_number(latest.get(column))
    ]
    if missing_required:
        return insufficient_result(
            "短期",
            20,
            valid_count,
            raw_count=raw_count,
            missing_fields=missing_required,
            latest_valid_date=quality.get("latest_valid_date"),
        )

    score = 0
    reasons: list[str] = []
    warnings: list[str] = []
    close = float(latest["Close"])
    ma5 = float(latest["ma5"])
    ma10 = float(latest["ma10"])

    if close > ma5 > ma10:
        score += 2
        reasons.append("股價站上 5 日線，且 5 日線高於 10 日線")
    elif close > ma5:
        score += 1
        reasons.append("股價重新站上 5 日線")
    elif close < ma5 < ma10:
        score -= 2
        reasons.append("股價位於 5 日線下方，且短均線呈空頭排列")
    else:
        score -= 1
        reasons.append("股價尚未站回 5 日線")

    ma5_base = data["ma5"].iloc[-4]
    ma10_base = data["ma10"].iloc[-4]
    if is_number(ma5_base) and is_number(ma10_base):
        if ma5 > float(ma5_base) and ma10 > float(ma10_base):
            score += 1
            reasons.append("5 日線與 10 日線同步向上")
        elif ma5 < float(ma5_base) and ma10 < float(ma10_base):
            score -= 1
            reasons.append("5 日線與 10 日線同步向下")

    rsi_value = latest.get("RSI14")
    previous_rsi = previous.get("RSI14")
    if is_number(rsi_value):
        rsi = float(rsi_value)
        if rsi >= 70:
            warnings.append(f"RSI 為 {rsi:.1f}，已進入偏熱區，需留意追高風險")
        elif rsi <= 30:
            warnings.append(
                f"RSI 為 {rsi:.1f}，雖進入超賣區，但不代表股價已完成止跌"
            )
        elif is_number(previous_rsi) and rsi > float(previous_rsi) and float(previous_rsi) < 45:
            score += 1
            reasons.append("RSI 從相對低檔回升")
        elif is_number(previous_rsi) and rsi < float(previous_rsi) and rsi > 55:
            score -= 1
            reasons.append("RSI 自高檔轉弱")
    else:
        warnings.append("RSI 資料不足，本次未納入短期評分")

    k_value, d_value = latest.get("KD_K"), latest.get("KD_D")
    previous_k, previous_d = previous.get("KD_K"), previous.get("KD_D")
    if all(is_number(value) for value in (k_value, d_value, previous_k, previous_d)):
        k, d = float(k_value), float(d_value)
        if float(previous_k) <= float(previous_d) and k > d:
            score += 1
            reasons.append("KD 出現黃金交叉")
        elif float(previous_k) >= float(previous_d) and k < d:
            score -= 1
            reasons.append("KD 出現死亡交叉")
            if k >= 70:
                warnings.append("KD 在高檔轉弱，短線動能可能降溫")
    else:
        warnings.append("KD 資料不足，本次未納入短期評分")

    histogram_value = latest.get("MACD_HIST")
    previous_histogram = previous.get("MACD_HIST")
    macd_value, signal_value = latest.get("MACD"), latest.get("MACD_SIGNAL")
    old_macd, old_signal = previous.get("MACD"), previous.get("MACD_SIGNAL")
    macd_analysis = analyze_macd(
        macd_value, signal_value, histogram_value, previous_histogram,
        old_macd, old_signal,
    )
    if macd_analysis["momentum"] != "data_insufficient":
        if macd_analysis["momentum"] in {"bullish_strengthening", "bearish_weakening"}:
            score += 1
        elif macd_analysis["momentum"] in {"bearish_strengthening", "bullish_weakening"}:
            score -= 1
        reasons.append(f"MACD {macd_analysis['label']}")
        if macd_analysis["cross"] == "golden_cross":
            score += 1
            reasons.append("MACD 短期上穿訊號線")
        elif macd_analysis["cross"] == "death_cross":
            score -= 1
            reasons.append("MACD 短期跌破訊號線")
    else:
        warnings.append("MACD 資料不足，本次未納入短期評分")

    momentum = pct_distance(close, float(five_days_ago["Close"]))
    if momentum >= 3:
        score += 1
        reasons.append(f"最近 5 日價格上漲 {momentum:.1f}%，短期動能轉強")
    elif momentum <= -3:
        score -= 1
        reasons.append(f"最近 5 日價格下跌 {abs(momentum):.1f}%，短期動能偏弱")

    _append_volume_signals(data, momentum, reasons, warnings)
    recent_high = float(data["High"].iloc[-21:-1].max())
    recent_low = float(data["Low"].iloc[-10:].min())
    if close >= recent_high * 0.98 and close < recent_high:
        warnings.append(f"股價接近近期高點 {recent_high:.2f}，仍需確認能否突破")
    if close <= recent_low * 1.01:
        warnings.append(f"股價接近短期低點 {recent_low:.2f}，尚未出現明確止跌確認")

    bias = pct_distance(close, ma5)
    if abs(bias) >= 6:
        warnings.append(f"股價與 5 日線乖離 {bias:+.1f}%，短期波動風險較高")

    reasons = unique_sentences(reasons)
    warnings = unique_sentences(warnings)
    bullish_factors, bearish_factors = split_directional_factors(reasons)
    view, label = score_to_view(score)
    summary = _build_summary(view, bullish_factors, bearish_factors, warnings)
    return {
        "view": view,
        "label": label,
        "score": score,
        "score_min": SHORT_TERM_MIN_SCORE,
        "score_max": SHORT_TERM_MAX_SCORE,
        "reasons": reasons,
        "bullish_factors": bullish_factors,
        "bearish_factors": bearish_factors,
        "warnings": warnings,
        "summary": summary,
        "data_quality": {
            "raw_count": raw_count,
            "valid_count": valid_count,
            "required_count": 20,
            "latest_valid_date": quality.get("latest_valid_date"),
            "missing_fields": quality.get("missing_fields", []),
        },
    }


def _append_volume_signals(
    data: pd.DataFrame,
    momentum: float,
    reasons: list[str],
    warnings: list[str],
) -> None:
    if "Volume" not in data.columns:
        warnings.append("缺少成交量資料，無法確認短期量價配合")
        return
    latest_volume = data["Volume"].iloc[-1]
    average_volume = data["Volume"].iloc[-20:-1].mean()
    if not is_number(latest_volume) or not is_number(average_volume) or average_volume <= 0:
        warnings.append("成交量資料不足，無法確認短期量價配合")
    elif latest_volume >= average_volume * 1.5:
        if momentum > 0:
            reasons.append("近期上漲且成交量高於 20 日均量")
        else:
            warnings.append("價格偏弱且成交量放大，賣壓風險升高")
    elif momentum > 1:
        warnings.append("近期價格上漲但成交量未明顯放大")


def _build_summary(
    view: str,
    bullish_factors: list[str],
    bearish_factors: list[str],
    warnings: list[str],
) -> str:
    """描述指標組合代表的市場狀態，避免逐條重述因素。"""
    if view in {"bearish", "slightly_bearish"}:
        text = "短期價格、均線與動能整體偏弱，近期賣壓尚未解除。"
        if bullish_factors and any("改善" in factor or "轉強" in factor or "回升" in factor for factor in bullish_factors):
            text += "雖有局部改善訊號，但目前不足以確認短線反轉。"
        else:
            text += "在未出現止跌型態或重新站回短均線前，仍應以弱勢延續看待。"
    elif view in {"bullish", "slightly_bullish"}:
        text = "短期價格與動能正在改善，反彈或轉強條件逐漸增加。"
        if bearish_factors:
            text += "不過仍有部分弱勢條件，現階段應等待價格結構進一步確認。"
    else:
        text = "短期多空訊號尚未形成一致方向，目前較接近整理或轉折確認階段。"
    return text
