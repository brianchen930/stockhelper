from typing import Any


def build_technical_summary(data: dict) -> str:
    """將技術指標整理成適合終端機與通知顯示的簡短摘要。"""

    rsi = data.get("rsi")
    if rsi is None:
        rsi_text = "RSI 資料不足"
    elif rsi >= 70:
        rsi_text = f"RSI {rsi:.2f}（超買）"
    elif rsi <= 30:
        rsi_text = f"RSI {rsi:.2f}（超賣）"
    else:
        rsi_text = f"RSI {rsi:.2f}（中性）"

    macd = data.get("macd")
    macd_histogram = data.get("macd_histogram")
    previous_macd_histogram = data.get("previous_macd_histogram")
    if macd is None:
        macd_text = "MACD 資料不足"
    else:
        macd_state = "多" if macd > 0 else "空" if macd < 0 else "中性"
        histogram_state = ""
        if macd_histogram is not None and previous_macd_histogram is not None:
            if macd_histogram > previous_macd_histogram:
                histogram_state = "，柱狀體上升"
            elif macd_histogram < previous_macd_histogram:
                histogram_state = "，柱狀體下降"
            else:
                histogram_state = "，柱狀體持平"
        macd_text = f"MACD {macd:.2f}（{macd_state}{histogram_state}）"

    kd_j = data.get("kd_j")
    if kd_j is None:
        kd_text = "KD J 資料不足"
    elif kd_j < 0:
        kd_text = f"KD J {kd_j:.2f}（弱）"
    elif kd_j <= 20:
        kd_text = f"KD J {kd_j:.2f}（低）"
    elif kd_j >= 80:
        kd_text = f"KD J {kd_j:.2f}（高）"
    else:
        kd_text = f"KD J {kd_j:.2f}（中性）"

    ma5 = data.get("ma5")
    ma20 = data.get("ma20")
    ma60 = data.get("ma60")
    if None in (ma5, ma20, ma60):
        ma_text = "資料不足"
    elif ma5 > ma20 > ma60:
        ma_text = "多頭排列"
    elif ma5 < ma20 < ma60:
        ma_text = "空頭排列"
    else:
        ma_text = "均線糾結"

    return f"{rsi_text} / {macd_text} / {kd_text} / 均線：{ma_text}"


def generate_analysis(
    trend: str,
    signal: str,
    score: int,
    matched_rules: list[Any],
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

    rule_bias = _analyze_rule_bias(matched_rules)

    market_bias = _determine_market_bias(
        trend=trend,
        signal=signal,
        bullish_count=rule_bias["bullish_count"],
        bearish_count=rule_bias["bearish_count"],
    )

    strength = _determine_strength(
        score=score,
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

    suggestion = _build_suggestion(
        market_bias=market_bias,
        strength=strength,
        trend=trend,
    )

    return {
        "market_bias": market_bias,
        "strength": strength,
        "summary": summary,
        "suggestion": suggestion,
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
    score: int,
    bullish_count: int,
    bearish_count: int,
) -> str:
    """
    判斷訊號強度。

    除了總分，也參考具有方向性的規則數量。
    """

    directional_rule_count = bullish_count + bearish_count

    if score >= 5 or directional_rule_count >= 3:
        return "強"

    if score >= 3 or directional_rule_count >= 2:
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

    trend_text = _get_trend_text(trend)

    if bullish_count > bearish_count:
        rule_text = (
            f"目前命中 {bullish_count} 個偏多規則、"
            f"{bearish_count} 個偏空規則，短線技術動能偏多。"
        )
    elif bearish_count > bullish_count:
        rule_text = (
            f"目前命中 {bearish_count} 個偏空規則、"
            f"{bullish_count} 個偏多規則，短線技術動能偏空。"
        )
    elif bullish_count == 0 and bearish_count == 0:
        rule_text = "目前尚未出現明確的多空技術規則。"
    else:
        rule_text = (
            f"目前偏多與偏空規則各有 {bullish_count} 個，"
            "技術訊號存在分歧。"
        )

    signal_text = {
        "偏多": "系統目前的基礎訊號偏多。",
        "偏空": "系統目前的基礎訊號偏空。",
        "觀望": "系統目前的基礎訊號以觀望為主。",
    }.get(signal, f"目前基礎訊號為「{signal}」。")

    return (
        f"{trend_text}{signal_text}"
        f"{rule_text}"
        f"整體判斷為「{market_bias}」，訊號強度為「{strength}」。"
    )


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
