from typing import Any

from app.market_data import is_finite_number
from app.macd_analysis import analyze_macd, format_macd_summary


def build_technical_summary(data: dict) -> str:
    """將技術指標整理成適合終端機與通知顯示的簡短摘要。"""

    rsi = data.get("rsi")
    if not is_finite_number(rsi):
        rsi_text = "RSI 資料不足"
    elif rsi >= 70:
        rsi_text = f"RSI {rsi:.2f}（超買）"
    elif rsi <= 30:
        rsi_text = f"RSI {rsi:.2f}（超賣）"
    else:
        rsi_text = f"RSI {rsi:.2f}（中性）"

    macd_result = data.get("macd_analysis") or analyze_macd(
        data.get("macd"),
        data.get("macd_signal"),
        data.get("macd_histogram"),
        data.get("previous_macd_histogram"),
        data.get("previous_macd"),
        data.get("previous_macd_signal"),
    )
    macd_text = format_macd_summary(macd_result)

    kd_j = data.get("kd_j")
    if not is_finite_number(kd_j):
        kd_text = "KD J 資料不足"
    elif kd_j < 0:
        kd_text = f"KD J {kd_j:.2f}（弱）"
    elif kd_j <= 20:
        kd_text = f"KD J {kd_j:.2f}（低）"
    elif kd_j >= 80:
        kd_text = f"KD J {kd_j:.2f}（高）"
    else:
        kd_text = f"KD J {kd_j:.2f}（中性）"

    # The strategy includes Close vs MA5 as well as MA ordering. Reuse its
    # same-bar state, as RuleEngine/base summary do; do not classify it again.
    ma_text = (data.get("analysis") or {}).get("trend") or "資料不足"

    summary = f"{rsi_text} / {macd_text} / {kd_text} / 日線趨勢：{ma_text}"
    if "atr" in data:
        atr, percent = data.get("atr"), data.get("atr_percent")
        if is_finite_number(atr) and is_finite_number(percent):
            summary += f" / ATR14：{atr:.2f}（ATR% {percent:.2f}%｜{data.get('volatility_level', '資料不足')}）"
        else:
            summary += " / ATR14：資料不足"
    return summary


def generate_analysis(
    trend: str,
    signal: str,
    score: int,
    matched_rules: list[Any],
    analysis_is_valid: bool = True,
) -> dict[str, str]:
    """
    根據趨勢、訊號、分數與命中規則，產生綜合技術分析。

    Args:
        trend: 均線趨勢，例如「多頭排列」、「空頭排列」、「均線糾結」
        signal: 目前訊號，例如「偏多」、「偏空」、「觀望」
        score: Rule Engine 計算出的總分
        matched_rules: Rule Engine 回傳的命中規則

    Returns:
        包含方向、強度、摘要與建議的字典
    """

    if not analysis_is_valid:
        return {
            "market_bias": "資料不足",
            "strength": "資料不足",
            "strength_label": "基礎訊號強度",
            "summary": "本次資料不足，不產生操作方向，請等待下一次有效行情資料。",
            "suggestion": "",
        }

    rule_bias = _analyze_rule_bias(matched_rules)

    market_bias = _determine_market_bias(
        trend=trend,
        signal=signal,
        bullish_count=rule_bias["bullish_count"],
        bearish_count=rule_bias["bearish_count"],
    )

    strength = _determine_strength(
        bullish_count=rule_bias["bullish_count"],
        bearish_count=rule_bias["bearish_count"],
    )

    summary = _build_summary(
        trend=trend,
        signal=signal,
        market_bias=market_bias,
        strength=strength,
        bullish_count=rule_bias["bullish_count"],
        bearish_count=rule_bias["bearish_count"],
    )

    return {
        "market_bias": market_bias,
        "strength": strength,
        "strength_label": "基礎訊號強度",
        "summary": summary,
        "suggestion": "",
    }


def _analyze_rule_bias(matched_rules: list[Any]) -> dict[str, int]:
    """
    從命中規則的文字中，判斷偏多與偏空規則數量。

    目前先使用關鍵字辨識。
    未來可以改成由每個 Rule 直接回傳 bullish / bearish / risk。
    """

    bullish_keywords = [
        "黃金交叉",
        "多方動能增強",
        "多方動能轉強",
        "短線動能轉強",
        "偏多",
        "突破",
        "站上",
        "轉強",
    ]

    bearish_keywords = [
        "死亡交叉",
        "空方動能增強",
        "空方動能轉強",
        "短線動能轉弱",
        "偏空",
        "跌破",
        "轉弱",
    ]

    bullish_count = 0
    bearish_count = 0

    for rule in matched_rules:
        rule_text = _extract_rule_text(rule)

        if "｜多方" in rule_text:
            bullish_count += 1
            continue
        if "｜空方" in rule_text:
            bearish_count += 1
            continue
        if any(keyword in rule_text for keyword in bullish_keywords):
            bullish_count += 1
        if any(keyword in rule_text for keyword in bearish_keywords):
            bearish_count += 1

    return {
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
    }


def _extract_rule_text(rule: Any) -> str:
    """
    將規則轉換成文字。

    目前 matched_rules 是字串，但保留對字典格式的相容性，
    避免未來 Rule Engine 改格式後立刻壞掉。
    """

    if isinstance(rule, str):
        return rule

    if isinstance(rule, dict):
        return str(
            rule.get("message")
            or rule.get("description")
            or rule.get("reason")
            or rule
        )

    return str(rule)


def _determine_market_bias(
    trend: str,
    signal: str,
    bullish_count: int,
    bearish_count: int,
) -> str:
    """
    綜合均線趨勢、原始訊號以及命中規則方向，
    判斷目前市場偏向。
    """

    bullish_points = 0
    bearish_points = 0

    # 均線趨勢的權重較高
    if trend == "多頭排列":
        bullish_points += 2
    elif trend == "空頭排列":
        bearish_points += 2

    # 原始訊號
    if signal == "偏多":
        bullish_points += 1
    elif signal == "偏空":
        bearish_points += 1

    # 技術規則
    bullish_points += bullish_count
    bearish_points += bearish_count

    difference = bullish_points - bearish_points

    if difference >= 3:
        return "明顯偏多"

    if difference >= 1:
        return "中性偏多"

    if difference <= -3:
        return "明顯偏空"

    if difference <= -1:
        return "中性偏空"

    return "中性觀望"


def _determine_strength(
    bullish_count: int,
    bearish_count: int,
) -> str:
    """
    判斷訊號強度。

    除了總分，也參考具有方向性的規則數量。
    """

    directional_rule_count = bullish_count + bearish_count

    if directional_rule_count >= 3:
        return "強"

    if directional_rule_count >= 2:
        return "中等"

    return "弱"


def _build_summary(
    trend: str,
    signal: str,
    market_bias: str,
    strength: str,
    bullish_count: int,
    bearish_count: int,
) -> str:
    """
    產生人類可讀的分析摘要。
    """

    if bullish_count > bearish_count:
        rule_text = (
            f"基礎規則中偏多 {bullish_count} 個、偏空 {bearish_count} 個，"
            "指標訊號以偏多為主。"
        )
    elif bearish_count > bullish_count:
        rule_text = (
            f"基礎規則中偏空 {bearish_count} 個、偏多 {bullish_count} 個，"
            "指標訊號以偏空為主。"
        )
    elif bullish_count == 0 and bearish_count == 0:
        rule_text = "目前尚未出現明確的多空技術規則。"
    else:
        rule_text = (
            f"目前偏多與偏空規則各有 {bullish_count} 個，"
            "技術訊號存在分歧。"
        )

    trend_text = {
        "多頭排列": "均線結構偏多",
        "空頭排列": "均線結構偏空",
        "均線糾結": "均線仍處於糾結狀態，方向尚未完全明朗",
    }.get(trend, f"均線狀態為「{trend}」")
    conflict = (
        (signal == "偏多" and bearish_count > bullish_count)
        or (signal == "偏空" and bullish_count > bearish_count)
    )
    conflict_text = "，且基礎訊號與規則方向存在分歧" if conflict else ""
    return f"{rule_text}{trend_text}{conflict_text}；基礎方向為「{market_bias}」。"


def _get_trend_text(trend: str) -> str:
    if trend == "多頭排列":
        return "均線維持多頭排列，中期趨勢相對偏強；"

    if trend == "空頭排列":
        return "均線維持空頭排列，中期趨勢相對偏弱；"

    if trend == "均線糾結":
        return "目前均線糾結，中期方向尚未明確；"

    return f"目前均線趨勢為「{trend}」；"


def _build_suggestion(
    market_bias: str,
    strength: str,
    trend: str,
) -> str:
    """
    根據方向與強度產生觀察建議。
    """

    if market_bias == "明顯偏多":
        if strength == "強":
            return (
                "目前趨勢與技術動能同步偏多，但仍應留意股價是否已經漲多，"
                "不宜在短線急漲後盲目追價。"
            )

        return (
            "目前技術面偏多，但訊號強度仍有限，"
            "可繼續觀察股價是否站穩重要均線或突破關鍵壓力。"
        )

    if market_bias == "中性偏多":
        return (
            "中期方向尚未完全確認，但短線技術訊號稍微偏多。"
            "建議等待更多偏多條件出現，避免只根據單一指標進場。"
        )

    if market_bias == "明顯偏空":
        return (
            "目前趨勢與短線動能同步偏弱，不建議急於進場。"
            "應等待空方動能減弱，或股價重新站回重要均線後再觀察。"
        )

    if market_bias == "中性偏空":
        if trend == "均線糾結":
            return (
                "目前中期方向仍不明確，但短線技術動能已經偏弱。"
                "建議先觀察支撐是否守住，避免在弱勢訊號尚未解除時搶進。"
            )

        return (
            "目前技術面稍微偏空，建議降低追價意願，"
            "並觀察空方訊號是否持續增加。"
        )

    return (
        "目前市場方向不明，技術訊號強度也不足，"
        "暫時以觀望為主。"
    )
